"""Grade immutable observations, then gate calibration on later locked games."""
from copy import deepcopy
import hashlib

import numpy as np
from scipy.special import logit
from sklearn.linear_model import LogisticRegression

from ks1.features import utc
from ks1.inventory import encode
from ks1.simulation import IDENTITY, RECIPE, calibrate


def comparison(rows):
    """One explicit intersection for every model; never synthesize LGB totals."""
    cohort = [r for r in rows if all(r.get(k) is not None for k in (
        'home_score', 'away_score', 'p_home', 'p_home_poisson', 'p_home_sim',
        'proj_total_sim', 'proj_total_poisson'))]
    if len({r['game_id'] for r in cohort}) != len(cohort):
        raise ValueError('duplicate locked test IDs')
    result = []
    y = np.array([r['home_score'] > r['away_score'] for r in cohort], float)
    totals = np.array([r['home_score']+r['away_score'] for r in cohort], float)
    for model, probability, total in [('LightGBM', 'p_home', None),
                                     ('Poisson', 'p_home_poisson', 'proj_total_poisson'),
                                     ('Simulation', 'p_home_sim', 'proj_total_sim')]:
        p = np.array([r[probability] for r in cohort], float)
        if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
            raise ValueError('invalid comparison probabilities')
        result.append({'model': model, 'n_test': len(cohort),
                       'brier': float(np.mean((p-y)**2)) if len(y) else None,
                       'totals_mae': float(np.mean(np.abs(np.array([r[total] for r in cohort])-totals)))
                       if total and len(y) else None})
    ids = [str(r['game_id']) for r in cohort]
    return {'metrics': result, 'game_ids': ids,
            'game_ids_sha256': hashlib.sha256(encode(ids)).hexdigest(),
            'excluded_incomplete_rows': len(rows)-len(cohort)}


def champion_gate(y, incumbent, candidate):
    """Keep incumbent on ties, regression, missing pairs or insufficient evidence."""
    y, incumbent, candidate = [np.asarray(v, float) for v in (y, incumbent, candidate)]
    if y.shape != incumbent.shape or y.shape != candidate.shape or y.ndim != 1:
        raise ValueError('champion gate requires identical paired games')
    if not np.isfinite([y, incumbent, candidate]).all() or not np.isin(y, [0, 1]).all():
        raise ValueError('invalid champion labels/probabilities')
    if ((incumbent < 0) | (incumbent > 1) | (candidate < 0) | (candidate > 1)).any():
        raise ValueError('invalid champion probabilities')
    old = float(np.mean((incumbent-y)**2)) if len(y) else None
    new = float(np.mean((candidate-y)**2)) if len(y) else None
    return {'n_test': len(y), 'incumbent_brier': old, 'candidate_brier': new,
            'keep_candidate': bool(len(y) >= 7 and new < old-1e-12)}


def update(state, locked, finals, as_of):
    """Pure state transition. Published rows are inputs and are never rewritten.

    Records arrive with S3 version evidence, not a self-declared `locked` flag.
    Final scores are independently retained official results. Late results stay
    pending. Every completed group of seven triggers one calibration attempt;
    candidate fitting uses only labels available before the seven test locks.
    Optional full refits are queued, disabled by default, never auto-promoted.
    """
    state = deepcopy(state or {'system': 'KS1', 'recipe': RECIPE, 'mapping': IDENTITY,
                              'grades': {}, 'calibration_attempts': [], 'refit_requests': []})
    if state['recipe'] != RECIPE:
        raise ValueError('keep old recipe: unregistered candidate cannot replace it')
    state.setdefault('result_corrections', {})
    grades = state['grades']
    for entry in locked:
        row, evidence = entry['row'], entry['evidence']
        pk = str(row['game_id'])
        if pk in grades:
            # Preserve the original grade and expose corrected results for review.
            if pk in finals and any(grades[pk][k] != finals[pk][k] for k in ('home_score', 'away_score')):
                state['result_corrections'][pk] = {'original_home': grades[pk]['home_score'],
                    'original_away': grades[pk]['away_score'], 'observed_final': finals[pk],
                    'status': 'requires_review_no_automatic_regrade'}
            if grades[pk]['locked_row_sha256'] != hashlib.sha256(encode(row)).hexdigest():
                raise ValueError('previously graded locked row changed: '+pk)
            continue
        final = finals.get(pk)
        if not final or utc(final['completed_at']) > utc(as_of):
            continue
        if not evidence.get('version_id') or not evidence.get('sha256'):
            raise ValueError('missing prospective locked-row evidence')
        from datetime import timedelta
        cutoff = utc(row['commence_time'])-timedelta(minutes=10)
        if not (utc(row['as_of']) <= utc(evidence['stored_at']) <= cutoff < utc(as_of)):
            raise ValueError('post-lock or backdated snapshot cannot be graded')
        if utc(final['completed_at']) <= utc(row['commence_time']):
            raise ValueError('final precedes locked game start')
        if any(str(final[s+'_id']) != str(row[s+'_id']) for s in ('home', 'away')):
            raise ValueError('final team identity mismatch')
        scores = np.asarray([final['home_score'], final['away_score']], float)
        if not np.isfinite(scores).all() or (scores < 0).any() or (scores != np.floor(scores)).any() or scores[0] == scores[1]:
            continue  # Suspended/tied/unsettled is not a decisive final.
        field = row.get('official_probability_field') or 'p_home'  # Phase 4/5 policy.
        if field not in ('p_home', 'p_home_poisson', 'p_home_sim'):
            raise ValueError('unknown official probability field')
        p = row.get(field)
        if p is None or not np.isfinite(p) or not 0 <= p <= 1:
            raise ValueError('missing original official probability; do not substitute')
        y = int(scores[0] > scores[1])
        grades[pk] = {**row, **final, 'game_id': pk, 'graded_at': as_of,
                      'official_probability_field': field, 'official_p_home': p,
                      'official_brier': (p-y)**2, 'official_correct': int((p >= .5) == bool(y)),
                      'locked_row_sha256': hashlib.sha256(encode(row)).hexdigest(), 'evidence': evidence}
    ordered = sorted(grades.values(), key=lambda r: (r['graded_at'], r['completed_at'], r['game_id']))
    eligible = [r for r in ordered if r.get('sim_recipe') == RECIPE and r.get('p_home_sim_raw') is not None]
    done = len(state['calibration_attempts'])
    for block in range(done+1, len(eligible)//7+1):
        end = block*7
        test = eligible[end-7:end]
        first_lock_asof = min(utc(r['as_of']) for r in test)
        train = [r for r in eligible[:end-7] if utc(r['graded_at']) < first_lock_asof]
        attempt = {'block': block, 'locked_sim_games': end, 'as_of': as_of,
                   'test_game_ids': [r['game_id'] for r in test],
                   'train_game_ids': [r['game_id'] for r in train],
                   'status': 'blocked_result_correction' if state['result_corrections'] else 'insufficient_prior_labels'}
        y = np.array([r['home_score'] > r['away_score'] for r in train], int)
        if len(train) >= 7 and len(set(y)) == 2 and not state['result_corrections']:
            x = logit(np.clip([r['p_home_sim_raw'] for r in train], 1e-6, 1-1e-6)).reshape(-1, 1)
            # Strong shrinkage; keep identity/old mapping if slope reverses.
            fit = LogisticRegression(C=.1, solver='lbfgs', random_state=1729).fit(x, y)
            candidate = {'version': 'cal-'+hashlib.sha256(encode(attempt)).hexdigest()[:16],
                         'slope': float(fit.coef_[0, 0]), 'intercept': float(fit.intercept_[0])}
            if candidate['slope'] > 0:
                raw = np.array([r['p_home_sim_raw'] for r in test])
                # Incumbent probabilities were actually recorded at the lock.
                # Candidate fitting sees only strictly earlier graded labels.
                gate = champion_gate([r['home_score'] > r['away_score'] for r in test],
                                     [r['p_home_sim'] for r in test], calibrate(raw, candidate))
                attempt.update(status='accepted' if gate['keep_candidate'] else 'retained_incumbent', gate=gate)
                if gate['keep_candidate']:
                    state['mapping'] = candidate
            else:
                attempt['status'] = 'retained_incumbent_nonmonotonic'
        state['calibration_attempts'].append(attempt)
    for block in range(len(state['refit_requests'])+1, len(ordered)//50+1):
        state['refit_requests'].append({'after_locked_games': block*50, 'as_of': as_of,
                                       'status': 'optional_disabled', 'automatic_promotion': False,
                                       'reason': 'Full refit requires a separate candidate and later locked champion gate.'})
    state.update(as_of=as_of, comparison=comparison(ordered), locked_graded_games=len(ordered),
                 pending_game_ids=sorted({str(e['row']['game_id']) for e in locked}-set(grades)))
    return state
