"""KS1 calibration comparison and publish adapter; raw models stay unchanged."""
import argparse
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression

from ks1.features import utc
from ks1.inventory import encode
from src.temperature_calibrator import calibrate_p, fit_from_ledger, validate_model as validate_temperature_model

MODEL_PATH = Path(__file__).resolve().parents[1]/'data/models/platt.json'
TEMPERATURE_PATH = MODEL_PATH.with_name('temperature.json')
EPS = 1e-6


def raw_model_version():
    refs = json.loads((Path(__file__).parent/'model_refs.json').read_bytes())
    return 'KS1-LGB-'+refs['lightgbm']['sha256'][:12]+'-DP-'+refs['poisson']['sha256'][:12]


def identity():
    return {'A': 1.0, 'B': 0.0, 'n': 0, 'fitted_at': None, 'status': 'unfitted_no_locked_rows',
            'raw_model_version': raw_model_version(), 'last_attempt_game_ids': [], 'fit_game_ids': []}


def temperature_identity():
    return {'T': 1.0, 'n': 0, 'fitted_at': None, 'status': 'waiting_for_30_graded_official_rows',
            'raw_model_version': raw_model_version(), 'fit_game_ids': []}


def probabilities(p):
    p = np.asarray(p, float)
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError('raw probabilities must be finite and in [0,1]')
    return p


def validate_model(model, as_of=None):
    if not np.isfinite([model['A'], model['B']]).all() or not 0 <= model['n'] <= 100:
        raise ValueError('invalid Platt coefficients/sample count')
    if model['raw_model_version'] != raw_model_version():
        raise ValueError('Platt belongs to a different raw model')
    if model['n'] and not model['fitted_at']:
        raise ValueError('fitted Platt model requires fitted_at')
    if as_of and model['fitted_at'] and utc(model['fitted_at']) > utc(as_of):
        raise ValueError('future Platt model cannot be applied')
    return model


def apply(p, model):
    p = probabilities(p)
    if model['A'] == 1 and model['B'] == 0:
        return p.copy()  # Identity must preserve even exact 0/1 and stored bits.
    return expit(float(model['A'])*logit(np.clip(p, EPS, 1-EPS))+float(model['B']))


def metrics(y, p):
    y, p = np.asarray(y, int), probabilities(p)
    if len(y) != len(p) or not np.isin(y, [0, 1]).all():
        raise ValueError('invalid aligned binary outcomes')
    if not len(y):
        return {'n': 0, 'brier': None, 'logloss': None}
    clipped = np.clip(p, EPS, 1-EPS)
    return {'n': len(y), 'brier': float(np.mean((y-p)**2)),
            'logloss': float(-np.mean(y*np.log(clipped)+(1-y)*np.log1p(-clipped)))}


def ordered(rows, as_of, version=None):
    rows = sorted(deepcopy(rows), key=lambda r: (utc(r['locked_at']), str(r['game_id'])))
    if len({str(r['game_id']) for r in rows}) != len(rows):
        raise ValueError('duplicate locked games cannot count toward cadence')
    for r in rows:
        if r['raw_model_version'] != (version or raw_model_version()):
            raise ValueError('mixed raw engines/models cannot train this calibrator')
        if not (utc(r['as_of']) <= utc(r['locked_at']) < utc(r['graded_at']) <= utc(as_of)):
            raise ValueError('ungraded, future, or invalid locked record')
        if r['home_win'] not in (0, 1):
            raise ValueError('invalid graded outcome')
        probabilities([r['p_raw']])
    return rows


def refit(rows, previous, as_of):
    """Fit only after seven new distinct graded picks; never refit LightGBM.

    The requested last-50 gate overlaps the rolling fitting window and is
    reported as such. It is a rejection diagnostic, not out-of-sample accuracy.
    `compare` separately produces chronological, strictly prior-label metrics.
    """
    previous = validate_model(previous, as_of)
    rows = ordered(rows, as_of)
    ids = [str(r['game_id']) for r in rows]
    new = sorted(set(ids)-set(previous.get('last_attempt_game_ids', [])))
    result = deepcopy(previous)
    result.setdefault('label_first_seen', {})
    for row in rows:
        if row.get('signature'):
            result['label_first_seen'].setdefault(str(row['game_id']), {'signature': row['signature'], 'graded_at': row['graded_at']})
    if len(new) < 7:
        return result, {'status': 'waiting_for_7_new_graded_picks', 'new_graded_picks': len(new)}
    result['last_attempt_game_ids'] = sorted(set(ids) | set(previous.get('last_attempt_game_ids', [])))
    train = rows[-100:]
    y = np.asarray([r['home_win'] for r in train], int)
    c = .3 if len(rows) < 100 else 1.0
    report = {'status': 'single_class_keep_prior', 'new_graded_picks': len(new), 'C': c,
              'train_game_ids': [str(r['game_id']) for r in train], 'fitted_at': as_of,
              'train_n': len(train), 'shrink_old_weight': .8 if len(rows) < 100 else 0.0}
    if len(set(y)) == 2:
        x = logit(np.clip([r['p_raw'] for r in train], EPS, 1-EPS)).reshape(-1, 1)
        fitted = LogisticRegression(C=c, solver='lbfgs', max_iter=2000, tol=1e-9).fit(x, y)
        old_weight = report['shrink_old_weight']
        a = old_weight*previous['A']+(1-old_weight)*float(fitted.coef_[0, 0])
        b = old_weight*previous['B']+(1-old_weight)*float(fitted.intercept_[0])
        candidate = {'A': a, 'B': b, 'n': len(train), 'fitted_at': as_of,
                     'status': 'fitted', 'raw_model_version': raw_model_version(),
                     'fit_game_ids': report['train_game_ids']}
        gate = rows[-50:]
        raw = np.asarray([r['p_raw'] for r in gate])
        outcomes = [r['home_win'] for r in gate]
        raw_metrics, cal_metrics = metrics(outcomes, raw), metrics(outcomes, apply(raw, candidate))
        accepted = cal_metrics['brier'] <= raw_metrics['brier']
        report.update(status='accepted' if accepted else 'worse_brier_keep_prior',
                      candidate_A=a, candidate_B=b, fitted_A=float(fitted.coef_[0, 0]),
                      fitted_B=float(fitted.intercept_[0]),
                      gate={'kind': 'recent_50_overlaps_fit_not_out_of_sample',
                            'game_ids': [str(r['game_id']) for r in gate],
                            'raw': raw_metrics, 'platt': cal_metrics})
        if accepted:
            result.update(candidate)
    result['last_attempt'] = report
    return result, report


def apply_temperature(p, model):
    validate_temperature_model(model)
    p = probabilities(p)
    return np.asarray([calibrate_p(v, T=model['T']) for v in p.ravel()]).reshape(p.shape)


def refit_temperature(rows, previous, as_of):
    """Post-ledger hook/replay only, separate from Platt and the raw scorer."""
    rows = ordered(rows, as_of)
    if previous['raw_model_version'] != raw_model_version():
        raise ValueError('temperature belongs to a different raw model')
    return fit_from_ledger(rows, previous=previous, as_of=as_of, persist=False)


def compare(rows, as_of):
    """Prequential replay: every prediction sees only labels already available."""
    rows = ordered(rows, as_of)
    previous, predictions, decisions = identity(), [], []
    previous_temperature = temperature_identity()
    for target in sorted(rows, key=lambda r: (utc(r['as_of']), str(r['game_id']))):
        prior = [r for r in rows if utc(r['graded_at']) < utc(target['as_of'])]
        previous, decision = refit(prior, previous, target['as_of'])
        decisions.append({'test_game_id': target['game_id'],
                          'train_game_ids': [r['game_id'] for r in prior[-100:]], 'decision': decision['status']})
        previous_temperature, _ = refit_temperature(prior, previous_temperature, target['as_of'])
        t = previous_temperature['T']
        p = float(target['p_raw'])
        predictions.append({'game_id': target['game_id'], 'home_win': target['home_win'],
                            'raw': p, 'temperature': float(expit(logit(np.clip(p, EPS, 1-EPS))/t)) if t != 1 else p,
                            'platt': float(apply(p, previous)), 'T': t,
                            'A': previous['A'], 'B': previous['B'], 'fitted_at': previous['fitted_at']})
    # Same last min(50, all) test IDs, ordered by lock time, for all engines.
    selected = {r['game_id'] for r in rows[-50:]}
    test = [r for r in predictions if r['game_id'] in selected]
    y = [r['home_win'] for r in test]
    return {'kind': 'chronological_prior_labels_only_last_50_locked',
            'table': [{'method': method, **metrics(y, [r[method] for r in test])}
                      for method in ('raw', 'temperature', 'platt')],
            'predictions': test, 'decisions': decisions,
            'temperature_scope': 'separate calibration, fitted on strictly prior labels; T=1 during startup',
            'startup_identity_rows': sum(r['fitted_at'] is None for r in test)}


def prepare_rows(rows, model, as_of, method='platt'):
    """Publish boundary only. Never call from the LightGBM feature/scoring path."""
    if method not in ('platt', 'temperature'):
        raise ValueError('unknown calibration method')
    if method == 'platt':
        validate_model(model, as_of)
    else:
        apply_temperature([.5], model)
        if (model['raw_model_version'] != raw_model_version()
                or (model['fitted_at'] and utc(model['fitted_at']) > utc(as_of))):
            raise ValueError('future or different-model temperature')
    if not model['n'] and method == 'platt':
        return deepcopy(rows), []
    parameters = ('A', 'B') if method == 'platt' else ('T',)
    version = hashlib.sha256(encode({'method': method, **{k: model[k] for k in
        (*parameters, 'n', 'fitted_at', 'raw_model_version')}})).hexdigest()
    output, changed = [], []
    for original in rows:
        row = deepcopy(original)
        if utc(row['commence_time'])-timedelta(minutes=10) < utc(as_of):
            output.append(row); continue  # Exact existing freeze inequality.
        if model['n'] and row['model_version'] != model['raw_model_version']:
            raise ValueError('publish-time raw model mismatch')
        if row.get('calibration_version') == version:
            output.append(row); continue
        if row.get('calibration_version') and row.get('p_raw') is None:
            raise ValueError('cannot recalibrate without original raw probability')
        raw = row.get('p_raw') if row.get('p_raw') is not None else row['p_home']
        cal = float(apply(raw, model) if method == 'platt' else apply_temperature(raw, model))
        row.update(p_raw=float(raw), p_home=cal, calibration_version=version,
                   calibration_method=method, as_of=as_of)
        if row.get('market_home_prob') is not None:
            row['edge_home'] = cal-row['market_home_prob']
        output.append(row); changed.append(row['game_id'])
    return output, changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--model', type=Path, default=MODEL_PATH)
    parser.add_argument('--temperature-model', type=Path, default=TEMPERATURE_PATH)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from ks1.platt_inputs import dataset
    capture = json.loads(args.inputs.read_bytes())
    rows, admission = dataset(capture)
    if capture.get('committed_ledger'):
        from ks1.nightly import ledger_rows
        rows = ledger_rows(capture['committed_ledger'], capture['as_of'])
        admission['eligible_graded_rows'] = len(rows)
        admission['source'] = 'committed_nightly_ledger'
    previous = capture.get('platt_model') or identity()
    model, decision = refit(rows, previous, capture['as_of'])
    temperature_model = capture.get('temperature_model') or temperature_identity()
    temperature_decision = {'status': 'retained_nightly_temperature' if capture.get('committed_ledger')
                            else 'waiting_for_first_nightly_ledger'}
    comparison = compare(rows, capture['as_of'])
    args.model.parent.mkdir(parents=True, exist_ok=True)
    args.model.write_bytes(encode(model))
    args.temperature_model.parent.mkdir(parents=True, exist_ok=True)
    args.temperature_model.write_bytes(encode(temperature_model))
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'admission': admission, 'decision': decision, 'temperature_decision': temperature_decision,
              'comparison': comparison,
              'model_status': model['status'], 'model_path': str(args.model),
              'trained_LightGBM': False, 'published': False, 'aws_writes': 0}
    (args.output/'comparison.json').write_bytes(encode(report))
    print(json.dumps({'decision': decision, 'model_status': model['status'], 'table': comparison['table']}, indent=2))


if __name__ == '__main__':
    main()
