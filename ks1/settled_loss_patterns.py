"""Trace official KS1 errors against immutable pre-T10 signals, without model writes.

Recent settlements nominate research hypotheses only. They must NEVER be appended
as training rows for a model evaluated on the earlier frozen qualification tail.
The frozen game IDs are excluded before labels/profiles enter pattern discovery.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

CONTRACT = 'KS1-settled-loss-patterns-v2'
PREFIX = 'mlb/ks1/loss-patterns-v1/'
MIN_RESEARCH_GROUP = 20  # Descriptive research support, never a promotion gate.
WINDOWS = ('7d', '15d', '30d')
COMPARISONS = (
    ('OPPONENT_LINEUP_OPS_ADVANTAGE', 'lineup_ops_7d', .080, True),
    ('OPPONENT_LINEUP_XWOBA_ADVANTAGE', 'lineup_xwoba_7d', .030, True),
    ('OPPONENT_TOP4_OPS_ADVANTAGE', 'lineup_top4_ops', .080, True),
    ('OPPONENT_BULLPEN_FIP_ADVANTAGE', 'bullpen_context_fip_7d', 1., False),
    ('OPPONENT_BULLPEN_ERA_ADVANTAGE', 'bullpen_context_era_7d', 1.5, False),
)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def utc(value):
    if not isinstance(value, str):
        raise ValueError('missing_timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('naive_timestamp')
    return parsed.astimezone(timezone.utc)


def number(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError('boolean_numeric_signal')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite_numeric_signal')
    return result


def receipt(value):
    if not isinstance(value, dict):
        raise ValueError('missing_source_receipt')
    if value.get('version_id') and value.get('versionId') and value['version_id'] != value['versionId']:
        raise ValueError('conflicting_source_version')
    version = value.get('version_id', value.get('versionId'))
    fields = (value.get('bucket'), value.get('key'), version, value.get('sha256'))
    if any(not isinstance(v, str) or not v for v in fields):
        raise ValueError('incomplete_source_receipt')
    if version == 'null' or not re.fullmatch('[0-9a-f]{64}', fields[3]):
        raise ValueError('invalid_source_receipt')
    return fields


def index(rows, forbidden, *, wrapped=False):
    result, excluded = {}, 0
    if not isinstance(rows, list):
        raise ValueError('invalid_row_inventory')
    for entry in rows:
        if not isinstance(entry, dict):
            raise ValueError('invalid_row_inventory')
        row = entry.get('row') if wrapped else entry
        if not isinstance(row, dict) or not isinstance(row.get('game_id'), str) or not row['game_id']:
            raise ValueError('invalid_game_identity')
        gid = row['game_id']
        # No access to forbidden outcomes, profiles, probabilities or sources here.
        if gid in forbidden:
            excluded += 1
            continue
        if gid in result:
            raise ValueError('duplicate_game_identity')
        result[gid] = entry
    return result, excluded


def profile(row, field, lock_time):
    value = row.get(field+'_json')
    if value is None or value == '':
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or not isinstance(parsed.get('sides'), dict):
        raise ValueError('malformed_pregame_profile')
    if utc(parsed['as_of']) != utc(row['as_of']) or utc(parsed['as_of']) > lock_time:
        raise ValueError('profile_time_mismatch')
    for key in ('history_as_of', 'statcast_as_of'):
        if parsed.get(key) and utc(parsed[key]) > utc(row['as_of']):
            raise ValueError('future_profile_source')
    if parsed.get('game_id') is not None and str(parsed['game_id']) != row['game_id']:
        raise ValueError('profile_game_mismatch')
    if parsed.get('commence_time') and utc(parsed['commence_time']) != utc(row['commence_time']):
        raise ValueError('profile_start_mismatch')
    if parsed.get('sha256') != row.get(field+'_sha256') or not parsed.get('sha256'):
        raise ValueError('profile_receipt_mismatch')
    # The entire profile is bound by the original immutable prediction receipt;
    # its native internal hash is not misrepresented as a hash of the JSON string.
    for side in ('home', 'away'):
        data = parsed['sides'].get(side)
        if not isinstance(data, dict):
            raise ValueError('missing_profile_side')
        if data.get('team_id') is not None and str(data['team_id']) != str(row[side+'_id']):
            raise ValueError('profile_team_mismatch')
        if data.get('starter_id') is not None and str(data['starter_id']) != str(row[side+'_starter_id']):
            raise ValueError('profile_starter_mismatch')
    return parsed


def delta(a, b):
    a, b = number(a), number(b)
    return None if a is None or b is None else a-b


def any_known(values):
    return True if True in values else None if None in values else False


def all_known(values):
    return False if False in values else None if None in values else True


def signals(row, lock_time):
    selected = 'home' if number(row['p_home']) >= .5 else 'away'
    opponent = 'away' if selected == 'home' else 'home'
    starter = profile(row, 'starter_profile', lock_time)
    context = profile(row, 'lineup_bullpen_profile', lock_time)
    flags, values, relievers = {}, {}, {}
    market = number(row.get('market_home_prob'))
    if market is not None and not 0 <= market <= 1:
        raise ValueError('invalid_market_probability')
    p = number(row['p_home'])
    market_selected = market if selected == 'home' else None if market is None else 1-market
    flags['MARKET_FAVORS_OPPOSITE_SIDE'] = (None if market is None else
        market_selected < .5 and abs(p-market) >= .05)
    values['model_minus_market_selected'] = None if market is None else (p-market)*(1 if selected == 'home' else -1)
    for side, code, direction in ((selected, 'SELECTED_STARTER_RECENT_DETERIORATION', 1),
                                  (opponent, 'OPPONENT_STARTER_RECENT_IMPROVEMENT', -1)):
        metrics = starter.get('sides', {}).get(side, {}).get('metrics', {})
        checks = []
        for name, threshold in (('era', 2.), ('fip', 1.5), ('xwoba', .040)):
            value = delta(metrics.get(name+'_7d'), metrics.get(name+'_30d'))
            values[side+'_starter_'+name+'_7d_minus_30d'] = value
            checks.append(None if value is None else direction*value >= threshold)
        flags[code] = any_known(checks)
        if side == selected:
            ip = number(metrics.get('expected_innings_last5'))
            values['selected_starter_expected_innings'] = ip
            flags['SELECTED_STARTER_LOW_EXPECTED_IP'] = None if ip is None else ip <= 3.
    sides = context.get('sides', {})
    sf, of = (sides.get(side, {}).get('features', {}) for side in (selected, opponent))
    confirmed = all(row.get(side+'_lineup_status') == 'confirmed' for side in ('home', 'away'))
    for code, key, threshold, higher in COMPARISONS:
        gap = delta(of.get(key), sf.get(key))
        if key.startswith('lineup_') and not confirmed:
            gap = None  # Projected batting orders are not confirmed-lineup evidence.
        values['opponent_minus_selected_'+key] = gap
        flags[code] = None if gap is None else (gap >= threshold if higher else -gap >= threshold)
    # A roster or a workload-based availability estimate is not a confirmed role.
    # Keep that distinction instead of promoting available_count into performance.
    for side in (selected, opponent):
        entries = sides.get(side, {}).get('reliever_history', [])
        if not isinstance(entries, list):
            raise ValueError('malformed_reliever_history')
        ids = [str(e.get('player_id', '')) for e in entries]
        if any(not pid for pid in ids) or len(ids) != len(set(ids)):
            raise ValueError('duplicate_or_missing_reliever_identity')
        ranked = sorted(entries, key=lambda e: (-(number(e.get('windows', {}).get('30d', {}).get('appearances')) or 0), str(e['player_id'])))[:3]
        relievers[side] = []
        for entry in ranked:
            item = {'player_id': str(entry['player_id']),
                    'availability_state': entry.get('availability_state', 'UNKNOWN'), 'windows': {}}
            for window in WINDOWS:
                w = entry.get('windows', {}).get(window, {})
                count = number(w.get('appearances'))
                if count is not None and (count < 0 or not count.is_integer()):
                    raise ValueError('invalid_reliever_appearances')
                item['windows'][window] = {'appearances': count, **{
                    metric: number(w.get(metric)) if count is not None and count > 0 else None
                    for metric in ('fip', 'era', 'k_bb_pct', 'xwoba')}}
            relievers[side].append(item)
    for rank in range(3):
        for metric, threshold in (('fip', 1.), ('era', 1.5), ('xwoba', .030)):
            pair = []
            for side in (selected, opponent):
                r = relievers[side]
                pair.append(r[rank]['windows']['7d'][metric] if len(r) > rank else None)
            gap = delta(pair[0], pair[1])
            name = f'OPPONENT_RELIEVER_R{rank+1}_{metric.upper()}_ADVANTAGE'
            flags[name] = None if gap is None else gap >= threshold
            values[name.lower()] = gap
    flags['STARTER_DIVERGENCE'] = all_known([flags['SELECTED_STARTER_RECENT_DETERIORATION'], flags['OPPONENT_STARTER_RECENT_IMPROVEMENT']])
    flags['SHORT_STARTER_AND_BULLPEN_RISK'] = all_known([flags['SELECTED_STARTER_LOW_EXPECTED_IP'], any_known([flags['OPPONENT_BULLPEN_FIP_ADVANTAGE'], flags['OPPONENT_BULLPEN_ERA_ADVANTAGE']])])
    flags['MARKET_AND_LINEUP_OPPOSITION'] = all_known([flags['MARKET_FAVORS_OPPOSITE_SIDE'], flags['OPPONENT_LINEUP_OPS_ADVANTAGE']])
    return flags, values, relievers


def attribution(row):
    raw = row.get('signal_contributions_json')
    if not raw:
        return {'status': 'unavailable', 'learned_new_feature_use': 'not_proven'}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or parsed.get('additivity_verified') is not True:
        raise ValueError('unverified_active_attribution')
    groups = parsed.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise ValueError('missing_attribution_groups')
    total = number(parsed['bias'])+sum(number(g['signal_score']) for g in groups.values())
    raw_score = number(parsed['raw_score'])
    if not math.isclose(total, raw_score, rel_tol=1e-8, abs_tol=1e-8):
        raise ValueError('attribution_additivity_mismatch')
    p_raw = number(row['p_raw'])
    expected = .5*(1+math.tanh(raw_score/2))
    if not math.isclose(p_raw, expected, rel_tol=1e-8, abs_tol=1e-8):
        raise ValueError('attribution_probability_mismatch')
    return {'status': 'verified_against_locked_raw_probability', 'model_version': row['model_version'],
            'groups': groups, 'top_features': parsed.get('top_features', []),
            'performance_evidence': parsed.get('performance_evidence', {}),
            'learned_new_feature_use': 'requires_qualified_model_artifact_not_inferred_from_group_names'}


def stats(rows):
    n = len(rows)
    if not n:
        return {'n': 0, 'wins': 0, 'losses': 0, 'loss_rate': None, 'mean_excess_loss': None}
    wins = sum(r['hit'] for r in rows)
    expected = sum(1-r['p_selected'] for r in rows)
    rate = (n-wins)/n
    z = 1.96
    denom = 1+z*z/n
    center = (rate+z*z/(2*n))/denom
    width = z*math.sqrt(rate*(1-rate)/n+z*z/(4*n*n))/denom
    return {'n': n, 'wins': wins, 'losses': n-wins, 'loss_rate': rate,
            'expected_losses': expected, 'mean_excess_loss': (n-wins-expected)/n,
            'brier': sum(r['brier'] for r in rows)/n,
            'log_loss': sum(r['log_loss'] for r in rows)/n,
            'loss_rate_wilson_95_descriptive': [max(0., center-width), min(1., center+width)]}


def matched_delta(exposed, controls):
    buckets = defaultdict(lambda: ([], []))
    for group, rows in enumerate((exposed, controls)):
        for r in rows:
            buckets[(r['confidence_band'], r['selected_side'])][group].append(r)
    differences, weights = [], []
    for e, c in buckets.values():
        if e and c:
            weight = min(len(e), len(c))
            differences.append((stats(e)['mean_excess_loss']-stats(c)['mean_excess_loss'])*weight)
            weights.append(weight)
    return {'matched_support': sum(weights),
            'excess_loss_delta': sum(differences)/sum(weights) if weights else None}


def summarize(rows):
    output = []
    for version in sorted({r['model_version'] for r in rows}):
        cohort = [r for r in rows if r['model_version'] == version]
        dates = sorted({r['date'] for r in cohort})
        split = set(dates[:len(dates)//2])
        for name in sorted({name for r in cohort for name in r['patterns']}):
            exposed = [r for r in cohort if r['patterns'][name] is True]
            controls = [r for r in cohort if r['patterns'][name] is False]
            folds = []
            for early in (True, False):
                e = [r for r in exposed if (r['date'] in split) == early]
                c = [r for r in controls if (r['date'] in split) == early]
                folds.append({'exposed': stats(e), 'controls': stats(c), **matched_delta(e, c)})
            enough = len(exposed) >= MIN_RESEARCH_GROUP and len(controls) >= MIN_RESEARCH_GROUP
            replicated = all(f['matched_support'] >= 5 and f['excess_loss_delta'] is not None and f['excess_loss_delta'] > 0 for f in folds)
            output.append({'pattern': name, 'model_version': version,
                           'exposed': stats(exposed), 'controls': stats(controls),
                           'unknown_count': len(cohort)-len(exposed)-len(controls),
                           'confidence_and_side_matched': matched_delta(exposed, controls),
                           'chronological_descriptive_blocks': folds,
                           'status': 'hypothesis_for_historical_reconstruction' if enough and replicated else 'insufficient_or_unstable_evidence',
                           'qualification_evidence': False, 'automatic_probability_adjustment': False})
    return output


def _validate_persisted_final(grade):
    for side in ('home', 'away'):
        score = grade.get(side+'_score')
        if type(score) is not int or score < 0:
            raise ValueError('invalid_persisted_final_score')
    if grade['home_score'] == grade['away_score']:
        raise ValueError('invalid_persisted_final_tie')
    y = grade.get('home_win')
    if type(y) is not int or y not in (0, 1) or y != int(grade['home_score'] > grade['away_score']):
        raise ValueError('final_label_mismatch')
    if not grade.get('final_evidence'):
        raise ValueError('missing_final_receipts')
    for item in grade['final_evidence']:
        receipt(item)
    return y


def _crosscheck_current_final(source, grade, row, start, as_of):
    """Cross-check a still-retained current final, but do not require it forever.

    Official grading stores the final scores and immutable source receipts in the
    append-only ledger. The research ingestion source intentionally retains only
    the current and previous calendar years, so an older valid grade must remain
    traceable after its convenience copy ages out of ``source['finals']``.
    """
    final = source.get('finals', {}).get(grade['game_id'])
    if final is None:
        return 'persisted_official_grade_only_current_final_aged_out'
    if not isinstance(final, dict):
        raise ValueError('malformed_current_final')
    for side in ('home', 'away'):
        if str(final[side+'_id']) != str(row[side+'_id']):
            raise ValueError('final_team_mismatch')
        if type(final[side+'_score']) is not int or final[side+'_score'] < 0 or final[side+'_score'] != grade[side+'_score']:
            raise ValueError('final_score_mismatch')
    if utc(final['observed_at']) > as_of:
        raise ValueError('future_final_observation')
    if not start < utc(final['completed_at']) <= utc(grade['graded_at']):
        raise ValueError('final_completion_mismatch')
    return 'verified_against_current_retained_final'


def build(source, ledger, forbidden_game_ids):
    if source.get('system') != 'KS1' or ledger.get('system') != 'KS1':
        raise ValueError('not_ks1_evidence')
    if source.get('verification_only') or source.get('errors'):
        raise ValueError('unverified_capture')
    as_of = utc(source['as_of'])
    if utc(ledger['as_of']) > as_of:
        raise ValueError('future_ledger')
    grades, excluded = index(ledger['rows'], forbidden_game_ids)
    locks, _ = index(source['locked'], forbidden_game_ids, wrapped=True)
    observations = []
    for gid, grade in sorted(grades.items()):
        if gid not in locks:
            raise ValueError('graded_lock_missing')
        entry = locks[gid]; row = entry['row']
        if receipt(grade['lock_evidence']) != receipt(entry['evidence']):
            raise ValueError('official_lock_receipt_mismatch')
        if grade.get('official_probability_field') != 'p_home':
            raise ValueError('not_official_probability')
        for key in ('p_home', 'p_raw', 'as_of'):
            if grade.get(key) != row.get(key):
                raise ValueError('official_lock_row_mismatch')
        if grade['raw_model_version'] != row['model_version']:
            raise ValueError('official_model_mismatch')
        p = number(row['p_home']); p_raw = number(row['p_raw'])
        if p is None or p_raw is None or not (0 <= p <= 1 and 0 <= p_raw <= 1):
            raise ValueError('invalid_probability')
        lock = utc(grade['locked_at']); start = utc(row['commence_time'])
        if row['date'] != start.astimezone(ZoneInfo('America/New_York')).date().isoformat():
            raise ValueError('slate_date_mismatch')
        if not (utc(row['as_of']) <= utc(entry['evidence']['stored_at']) <= lock == start-timedelta(minutes=10) < utc(grade['graded_at']) <= as_of):
            raise ValueError('lock_or_label_chronology_mismatch')
        y = _validate_persisted_final(grade)
        final_crosscheck = _crosscheck_current_final(source, grade, row, start, as_of)
        patterns, values, relievers = signals(row, lock)
        active = attribution(row)
        sign = 1 if p >= .5 else -1
        for group in ('starter', 'market', 'bullpen', 'team_form'):
            score = number(active.get('groups', {}).get(group, {}).get('signal_score'))
            patterns['ACTIVE_'+group.upper()+'_OPPOSES_PICK'] = None if score is None else sign*score < 0
        patterns['ACTIVE_STARTER_AND_RECENT_FORM_OPPOSE_PICK'] = all_known([patterns['ACTIVE_STARTER_OPPOSES_PICK'], patterns['SELECTED_STARTER_RECENT_DETERIORATION']])
        patterns['ACTIVE_STARTER_AND_LINEUP_OPPOSE_PICK'] = all_known([patterns['ACTIVE_STARTER_OPPOSES_PICK'], patterns['OPPONENT_LINEUP_OPS_ADVANTAGE']])
        selected = 'home' if p >= .5 else 'away'
        hit = int((p >= .5) == bool(y)); p_selected = p if selected == 'home' else 1-p
        clipped = min(1-1e-15, max(1e-15, p_selected))
        observations.append({'game_id': gid, 'date': row['date'], 'model_version': row['model_version'],
            'as_of': row['as_of'], 'locked_at': grade['locked_at'], 'graded_at': grade['graded_at'],
            'selected_side': selected, 'selected_team': row[selected+'_team'], 'hit': hit,
            'p_home': p, 'p_raw': p_raw, 'p_selected': p_selected,
            'confidence_band': min(9, int(p_selected*10)),
            'residual': hit-p_selected, 'brier': (hit-p_selected)**2,
            'log_loss': -math.log(clipped if hit else 1-clipped),
            'patterns': patterns, 'pregame_signal_values': values, 'individual_relievers': relievers,
            'active_model_attribution': active,
            'calibration_method': row.get('calibration_method'),
            'calibration_version': row.get('calibration_version'),
            'source_row_sha256': digest(row), 'official_lock': entry['evidence'],
            'final_score': {'home': grade['home_score'], 'away': grade['away_score']},
            'final_evidence': grade['final_evidence'], 'current_final_crosscheck': final_crosscheck,
            'signal_role': 'research_diagnostic_not_a_serving_rule'})
    observations.sort(key=lambda r: (r['locked_at'], r['game_id']))
    patterns = summarize(observations)
    return {'contract': CONTRACT, 'system': 'KS1', 'as_of': source['as_of'],
        'frozen_holdout_games_excluded': excluded, 'frozen_holdout_predictions': 0,
        'eligible_source_rows_sha256': digest({gid: locks[gid] for gid in grades}),
        'eligible_grade_rows_sha256': digest(grades),
        'observations': observations, 'summary': stats(observations), 'patterns': patterns,
        'research_hypotheses': [p['pattern'] for p in patterns if p['status'] == 'hypothesis_for_historical_reconstruction'],
        'training_use': 'hypotheses_only_reconstruct_before_frozen_holdout_no_recent_label_training',
        'selection_policy': 'fixed_patterns_descriptive_blocks_not_out_of_sample_qualification',
        'required_next_steps': ['freeze_historical_feature_hypothesis', 'pre_T10_historical_reconstruction',
            'purged_development_selection', 'substantive_feature_use_and_live_transform_parity',
            'one_frozen_300_qualification_lower_Brier_no_worse_log_loss', 'reviewed_promotion_and_AWS_readback'],
        'qualification_run': False, 'trained_LightGBM': False, 'authority_effect': 'none',
        'prediction_writes': 0, 'official_ledger_writes': 0, 'model_ref_writes': 0,
        'lock_writes': 0, 'provider_calls': 0}


def publish(value, s3, bucket):
    from ks1.calibration_store import commit_json
    key = PREFIX+digest(value)+'/report.json'
    stored, proof = commit_json(s3, bucket, key, value)
    if stored != value or receipt(proof)[:2] != (bucket, key):
        raise ValueError('loss_pattern_readback_mismatch')
    return {'published': True, 'aws_readback_verified': True, 'artifact': proof}


def require_workflow():
    if not (os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_REPOSITORY') == 'KirtKurt/parlay-platform'
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_EVENT_NAME') in ('schedule', 'push', 'workflow_dispatch')
            and os.environ.get('GITHUB_WORKFLOW_REF') == 'KirtKurt/parlay-platform/.github/workflows/mlb-research-ingestion.yml@refs/heads/main'):
        raise ValueError('loss_pattern_publication_requires_existing_main_workflow')


def committed_ledger_key(report):
    status = report.get('status')
    date = report.get('night_date')
    if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        return None
    from ks1.calibration_store import PREFIX as GRADE_PREFIX, checkpoint_prefix
    if status == 'completed':
        return GRADE_PREFIX+'date='+date+'/graded_ledger.json'
    if status == 'completed_catchup':
        revision = report.get('catchup_revision')
        if type(revision) is not int:
            raise ValueError('invalid_catchup_revision')
        return checkpoint_prefix(date, revision)+'graded_ledger.json'
    return None


def select_ledger(report, source, root, *, s3=None, bucket=None):
    path = root/'graded_ledger.json'
    ledger = json.loads(path.read_bytes()) if path.exists() else source.get('committed_ledger')
    expected = report.get('ledger_rows')
    if ledger and expected == len(ledger.get('rows', [])):
        return ledger, {'source': 'same_run_artifact' if path.exists() else 'capture_committed_checkpoint'}
    key = committed_ledger_key(report)
    if s3 is None or not bucket or not key:
        raise ValueError('nightly_ledger_count_mismatch')
    from ks1.calibration_store import read_json
    ledger, proof = read_json(s3, bucket, key)
    if not ledger or expected != len(ledger.get('rows', [])):
        raise ValueError('committed_nightly_ledger_count_mismatch')
    if receipt(proof)[:2] != (bucket, key):
        raise ValueError('committed_nightly_ledger_receipt_mismatch')
    return ledger, {'source': 'exact_committed_aws_ledger', 'artifact': proof}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        report = json.loads((args.root/'report.json').read_bytes())
        if report.get('status') == 'not_due_or_already_completed':
            (args.output/'status.json').write_bytes(encoded({'status': 'nightly_not_due', 'published': False}))
            return
        if report.get('status') not in ('completed', 'completed_catchup', 'no_new_final_grades'):
            raise ValueError('nightly_not_successful')
        source_bytes = (args.root/'capture.json').read_bytes()
        source = json.loads(source_bytes)
        s3 = bucket = None
        if args.publish:
            require_workflow()
            from ks1.sources import aws_clients
            _, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
        ledger, ledger_identity = select_ledger(report, source, args.root, s3=s3, bucket=bucket)
        manifest_bytes = Path(__file__).with_name('qualification_holdout_20260914.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        ids = manifest['game_ids']
        if len(ids) != 300 or len(set(ids)) != 300 or any(not isinstance(g, str) or not g for g in ids):
            raise ValueError('invalid_frozen_holdout_manifest')
        value = build(source, ledger, set(ids))
        value['input_files'] = {'capture_sha256': hashlib.sha256(source_bytes).hexdigest(),
                                'frozen_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                                'ledger': ledger_identity}
        value['execution'] = {name: os.environ.get(name) for name in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}
        (args.output/'report.json').write_bytes(encoded(value))
        status = {'published': False, 'aws_readback_verified': False, 'summary': value['summary']}
        if args.publish:
            status.update(publish(value, s3, bucket))
        (args.output/'status.json').write_bytes(encoded(status))
        print(json.dumps(status, sort_keys=True))
    except Exception as exc:
        (args.output/'failure.json').write_bytes(encoded({'status': 'blocked', 'exception_type': type(exc).__name__,
                                                         'published': False, 'authority_effect': 'none'}))
        raise


if __name__ == '__main__':
    main()
