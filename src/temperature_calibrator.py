"""Temperature-only publish calibration. Run this file to execute self-tests.

fit_from_ledger accepts already verified, locked official rows with game_id,
p_raw, home_win, locked_at and graded_at. It never writes a ledger or a pick.
Call it only after the nightly ledger write has succeeded. Its sole persisted
artifact is data/models/temperature.json (or an explicitly supplied model_path).
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile

MODEL_PATH = Path(__file__).resolve().parents[1]/'data/models/temperature.json'
MIN_GRADED = 30
WINDOW = 100
OLD_WEIGHT = .8
NEW_WEIGHT = .2
EPS = 1e-12


def _utc(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('calibration timestamps must include a timezone')
    return result.astimezone(timezone.utc)


def identity():
    return {'T': 1.0, 'n': 0, 'fitted_at': None, 'status': 'waiting_for_30_graded_official_rows'}


def _read(path):
    return json.loads(path.read_text()) if path.exists() else identity()


def validate_model(model):
    if not math.isfinite(float(model['T'])) or not .05 <= float(model['T']) <= 20:
        raise ValueError('temperature must be finite and in [0.05,20]')
    if not isinstance(model['n'], int) or not 0 <= model['n'] <= WINDOW:
        raise ValueError('invalid temperature sample count')
    if model['n'] and not model['fitted_at']:
        raise ValueError('fitted temperature requires fitted_at')
    if model['T'] != 1 and (model['n'] < MIN_GRADED or not model['fitted_at']):
        raise ValueError('at least 30 fitted official rows are required before T moves')
    return model


def calibrate_p(p_raw, *, T=None, model_path=None):
    """p_lock = sigmoid(logit(p_raw)/T). T=1 preserves p_raw exactly."""
    p = float(p_raw)
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError('p_raw must be finite and in [0,1]')
    if T is None:
        T = validate_model(_read(Path(model_path) if model_path else MODEL_PATH))['T']
    T = float(T)
    if not math.isfinite(T) or not .05 <= T <= 20:
        raise ValueError('temperature must be finite and in [0.05,20]')
    if T == 1:
        return p
    p = min(1-EPS, max(EPS, p))
    z = (math.log(p)-math.log1p(-p))/T
    return 1/(1+math.exp(-z)) if z >= 0 else math.exp(z)/(1+math.exp(z))


def _metrics(rows, T):
    p = [calibrate_p(r['p_raw'], T=T) for r in rows]
    n = len(rows)
    return {'n': n, 'brier': sum((v-r['home_win'])**2 for v, r in zip(p, rows))/n if n else None,
            'logloss': -sum(r['home_win']*math.log(max(EPS, v))+(1-r['home_win'])*math.log(max(EPS, 1-v))
                            for v, r in zip(p, rows))/n if n else None}


def _fit_t(rows):
    # Deterministic bounded golden-section minimization in log(T). No vendor,
    # sklearn model replacement, random CV, isotonic, or extra features.
    left, right = math.log(.05), math.log(20)
    ratio = (math.sqrt(5)-1)/2
    x1, x2 = right-ratio*(right-left), left+ratio*(right-left)
    f1, f2 = _metrics(rows, math.exp(x1))['logloss'], _metrics(rows, math.exp(x2))['logloss']
    for _ in range(80):
        if f1 < f2:
            right, x2, f2 = x2, x1, f1
            x1 = right-ratio*(right-left)
            f1 = _metrics(rows, math.exp(x1))['logloss']
        else:
            left, x1, f1 = x1, x2, f2
            x2 = left+ratio*(right-left)
            f2 = _metrics(rows, math.exp(x2))['logloss']
    return math.exp((left+right)/2)


def fit_from_ledger(locked_official_rows, *, model_path=None, previous=None, as_of=None, persist=True):
    """After ledger commit: fit last 100, shrink 80% to old T, gate both metrics.

    The gate uses the fitting window and is labeled as a recent-data rejection
    check, not out-of-sample accuracy. KS1's separate chronological comparison
    evaluates only probabilities fitted before each target snapshot.
    """
    path = Path(model_path) if model_path else MODEL_PATH
    old = validate_model(dict(previous) if previous is not None else _read(path))
    now = _utc(as_of) if as_of else datetime.now(timezone.utc)
    if old['fitted_at'] and _utc(old['fitted_at']) > now:
        raise ValueError('future temperature parameters')
    rows = sorted(list(locked_official_rows), key=lambda r: (_utc(r['locked_at']), str(r['game_id'])))
    if len({str(r['game_id']) for r in rows}) != len(rows):
        raise ValueError('duplicate official game IDs')
    for r in rows:
        if not _utc(r['locked_at']) < _utc(r['graded_at']) <= now or r['home_win'] not in (0, 1):
            raise ValueError('requires time-ordered, graded official locks')
        calibrate_p(r['p_raw'], T=1)
    train = rows[-WINDOW:]
    result = dict(old)
    decision = {'status': 'waiting_for_30_graded_official_rows', 'eligible_rows': len(rows)}
    if len(train) >= MIN_GRADED:
        binding = [{k: r[k] for k in ('game_id', 'p_raw', 'home_win', 'locked_at', 'graded_at')} for r in train]
        digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if old.get('last_attempt_signature') == digest:
            return result, {'status': 'unchanged_ledger_no_refit', 'eligible_rows': len(rows)}
        fitted = _fit_t(train)
        candidate = OLD_WEIGHT*old['T']+NEW_WEIGHT*fitted
        raw, incumbent, proposed = _metrics(train, 1), _metrics(train, old['T']), _metrics(train, candidate)
        accepted = all(proposed[k] <= incumbent[k] and proposed[k] <= raw[k] for k in ('brier', 'logloss'))
        decision = {'status': 'accepted' if accepted else 'metric_regression_keep_prior',
                    'eligible_rows': len(rows), 'n': len(train), 'fitted_T': fitted,
                    'candidate_T': candidate, 'old_weight': OLD_WEIGHT,
                    'game_ids': [str(r['game_id']) for r in train],
                    'gate_kind': 'recent_100_overlaps_fit_not_out_of_sample',
                    'raw': raw, 'incumbent': incumbent, 'candidate': proposed}
        if accepted:
            result.update(T=candidate, n=len(train), fitted_at=now.isoformat(), status='fitted',
                          fit_game_ids=decision['game_ids'])
        result.update(last_attempt_signature=digest, last_attempt=decision)
    if persist:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False)
        # Local atomic replacement; no pick/ledger, AWS, or GitHub writes.
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name+'.', delete=False) as handle:
            handle.write(body)
            temporary = Path(handle.name)
        temporary.replace(path)
    return result, decision


def self_test():
    for p in (0., .01, .1, .25, .5, .8, .99, 1.):
        assert calibrate_p(p, T=1) == p
    from datetime import timedelta
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    synthetic = []
    for i in range(100):
        home_favored = bool(i % 2)
        favorite_won = (i//2) % 5 < 3
        at = base+timedelta(days=i)
        synthetic.append({'game_id': str(i), 'p_raw': .99 if home_favored else .01,
                          'home_win': int(favorite_won if home_favored else not favorite_won),
                          'locked_at': at.isoformat(), 'graded_at': (at+timedelta(hours=4)).isoformat()})
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)/'temperature.json'
        before, _ = fit_from_ledger(synthetic[:29], model_path=path)
        assert before['T'] == 1 and before['n'] == 0
        fitted, decision = fit_from_ledger(synthetic, model_path=path)
        assert decision['status'] == 'accepted' and fitted['T'] > 1 and fitted['n'] == 100
        assert fitted['T'] == OLD_WEIGHT+NEW_WEIGHT*decision['fitted_T']
        assert all(decision['candidate'][k] <= decision['incumbent'][k] for k in ('brier', 'logloss'))
        assert fit_from_ledger(synthetic, model_path=path)[0] == fitted
    print('temperature_calibrator self-test PASS: identity, 30-row minimum, T>1, 80% shrink, both metric gates, idempotence')


if __name__ == '__main__':
    self_test()
