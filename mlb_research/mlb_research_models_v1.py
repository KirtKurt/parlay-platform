"""Fixed development search, whole-slate holdout, and separate future testing."""
import math
import numpy as np
from mlb_research_sources_v1 import number
from mlb_research_store_v1 import digest

VERSION = 'MLB-RESEARCH-MODELS-v1-market-offset-linear-tree-poisson'
PROTOCOL = {'version': VERSION, 'minimumGames': 600, 'minimumSlates': 40,
            'freshTestMinimum': 100, 'newRowsAfterFailedTest': 100,
            'minimumFeatureCoverage': .8, 'wholeSlatePartitions': True,
            'holdoutFraction': .2, 'automaticPromotionEnabled': False,
            'maximumDevelopmentRows': 1200,
            'preferOriginalOnlyWhenMinimumGamesAndSlatesMet': True,
            'configurations': [['linear', .1], ['linear', 1.], ['trees', 15], ['trees', 30], ['poisson', .1]]}


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1-1e-6)
    return np.log(p)-np.log1p(-p)


def sigmoid(z):
    return 1/(1+np.exp(-np.clip(z, -30, 30)))


def metrics(rows, probabilities):
    y = np.asarray([r['homeWon'] for r in rows], dtype=float)
    p = np.asarray(probabilities, dtype=float)
    if (not len(y) or y.ndim != 1 or p.ndim != 1 or len(y) != len(p)
            or not np.isfinite(p).all() or not np.isfinite(y).all()
            or not np.isin(y, [0, 1]).all() or ((p < 0) | (p > 1)).any()):
        raise ValueError('complete finite probabilities and binary labels required')
    q = np.clip(p, 1e-6, 1-1e-6)
    bins = np.minimum((p*10).astype(int), 9)
    ece = sum(np.mean(bins == k)*abs(float(p[bins == k].mean()-y[bins == k].mean()))
              for k in range(10) if (bins == k).any())
    return {'count': len(y), 'accuracy': float(np.mean((p >= .5) == y)),
            'brier': float(np.mean((p-y)**2)), 'logLoss': float(-np.mean(y*np.log(q)+(1-y)*np.log1p(-q))),
            'calibrationError': float(ece)}


def matrix(rows, model):
    return np.column_stack([np.ones(len(rows)), *[
        np.clip([(model['means'][k] if number(r['features'].get(k)) is None else float(r['features'][k]))
                 - model['means'][k] for r in rows], -10*model['scales'][k], 10*model['scales'][k])/model['scales'][k]
        for k in model['features']]])


def fit(rows, kind, parameter):
    model = {'kind': kind, 'parameter': parameter, 'features': [], 'means': {}, 'scales': {}}
    names = sorted({k for r in rows for k in r['features'] if k != 'marketHomeProbability'})
    for key in names:
        vals = [number(r['features'].get(key)) for r in rows]
        observed = [v for v in vals if v is not None]
        if len(observed)/len(rows) >= .8 and np.std(observed) > 1e-9:
            model['features'].append(key)
            model['means'][key], model['scales'][key] = float(np.mean(observed)), float(np.std(observed))
    x = matrix(rows, model)
    y = np.asarray([r['homeWon'] for r in rows])
    if set(y) != {0, 1}:
        raise ValueError('both outcomes required for model fit')
    offset = logit([r['features']['marketHomeProbability'] for r in rows])
    if kind == 'trees':
        z, trees = offset.copy(), []
        for _ in range(int(parameter)):
            p = sigmoid(z); residual = y-p
            best = None
            for j in range(1, x.shape[1]):
                for threshold in np.unique(np.quantile(x[:, j], [.2, .4, .6, .8])):
                    left = x[:, j] <= threshold
                    if min(left.sum(), (~left).sum()) < 20:
                        continue
                    values = [float(residual[m].sum()/(sum(p[m]*(1-p[m]))+10))*.15 for m in (left, ~left)]
                    change = np.where(left, *values)
                    loss = float(np.mean(np.logaddexp(0, z+change)-y*(z+change)))
                    candidate = (loss, j, float(threshold), values, change)
                    if best is None or loss < best[0]:
                        best = candidate
            if best is None:
                break
            _, j, threshold, values, change = best
            trees.append([j, threshold, *values]); z += change
        model['trees'] = trees
    else:
        def optimize(target, base, poisson=False):
            w = np.zeros(x.shape[1])
            def objective(v):
                z = base+x@v
                return float(np.mean(np.exp(np.clip(z, -15, 15))-target*z) if poisson else np.mean(np.logaddexp(0,z)-target*z))+parameter*np.dot(v,v)/2
            for _ in range(80):
                z = base+x@w
                p = np.exp(np.clip(z, -15, 15)) if poisson else sigmoid(z)
                variance = p if poisson else p*(1-p)
                gradient = x.T@(p-target)/len(rows)+parameter*w
                if np.max(np.abs(gradient)) < 1e-7:
                    break
                hessian = (x.T*variance)@x/len(rows)+parameter*np.eye(x.shape[1])
                step = np.linalg.solve(hessian, gradient)
                rate, before = 1., objective(w)
                while rate > 1e-8 and objective(w-rate*step) > before:
                    rate /= 2
                if rate <= 1e-8:
                    raise ValueError('model convergence failed')
                w -= rate*step
            else:
                raise ValueError('model convergence failed')
            return w.tolist()
        if kind == 'poisson':
            if any(number(r.get(s+'Runs')) is None for r in rows for s in ('home', 'away')):
                raise ValueError('Poisson run labels unavailable')
            model['weights'] = [optimize(np.asarray([r[s+'Runs'] for r in rows]), np.full(len(rows), math.log(4.5)), True) for s in ('home','away')]
        else:
            model['weights'] = optimize(y, offset)
    model['trainingRows'] = len(rows)
    return model


def predict(rows, model):
    x = matrix(rows, model)
    if model['kind'] == 'poisson':
        rates = [np.exp(np.clip(math.log(4.5)+x@w, -5, math.log(25))) for w in model['weights']]
        result = []
        for h, a in zip(*rates):
            hp, ap = [math.exp(-h)], [math.exp(-a)]
            for n in range(1, 120):
                hp.append(hp[-1]*h/n); ap.append(ap[-1]*a/n)
            win = sum(hp[i]*sum(ap[:i]) for i in range(1,120))
            tie = sum(u*v for u,v in zip(hp,ap))
            result.append(win+.5*tie)
        return np.asarray(result)
    z = logit([r['features']['marketHomeProbability'] for r in rows])
    if model['kind'] == 'trees':
        for j, threshold, left, right in model['trees']:
            z += np.where(x[:, j] <= threshold, left, right)
    else:
        z += x@model['weights']
    return sigmoid(z)


def comparison(rows, probabilities):
    market = [r['features']['marketHomeProbability'] for r in rows]
    return {'model': metrics(rows, probabilities), 'market': metrics(rows, market)}


def blockers(comp):
    m, b = comp['model'], comp['market']
    result = []
    if m['brier'] >= b['brier']: result.append('NO_POSITIVE_BRIER_SKILL')
    if m['logLoss'] >= b['logLoss']: result.append('LOG_LOSS_NOT_LOWER_THAN_MARKET')
    if m['accuracy'] < b['accuracy']: result.append('ACCURACY_BELOW_MARKET')
    if m['calibrationError'] > .07: result.append('CALIBRATION_ERROR_TOO_HIGH')
    return result


def research(rows):
    supplied=len(rows)
    original=[r for r in rows if r.get('originalObservation') is True]
    original_ready=len(original)>=600 and len({r['slateDateEt'] for r in original})>=40
    if original_ready:rows=original
    rows = sorted(rows, key=lambda r: (r['slateDateEt'], str(r['officialGamePk'])))
    while len(rows)>PROTOCOL['maximumDevelopmentRows']:
        first=rows[0]['slateDateEt']
        rows=[r for r in rows if r['slateDateEt']!=first]
    dates = sorted({r['slateDateEt'] for r in rows})
    if len({str(r['officialGamePk']) for r in rows}) != len(rows):
        raise ValueError('duplicate research game')
    for row in rows:
        p = number(row['features'].get('marketHomeProbability'))
        if p is None or not 0 < p < 1 or row.get('homeWon') not in (0,1):
            raise ValueError('invalid research row')
    report = {'protocol': PROTOCOL, 'rows': len(rows), 'slates': len(dates), 'rowsHash': digest(rows),
              'productionAuthorityChanged': False, 'prospectiveQualificationEvidence': False,
              'suppliedRows':supplied,'sourceCohort':'ORIGINAL_OBSERVATIONS' if original_ready else 'MIXED_DEVELOPMENT'}
    if len(rows) < 600 or len(dates) < 40:
        return {**report, 'status': 'WAITING_FOR_DEVELOPMENT_DATA'}
    partitions = [int(len(dates)*f) for f in (.5,.6,.7,.8)]
    def group(first, last):
        return [r for r in rows if r['slateDateEt'] in dates[first:last]]
    candidates, failures = [], []
    for kind, parameter in PROTOCOL['configurations']:
        try:
            scored, actual = [], []
            for first, last in zip(partitions, partitions[1:]):
                fitted = fit(group(0,first), kind, parameter)
                validation = group(first,last)
                scored.extend(predict(validation, fitted).tolist()); actual.extend(validation)
            candidates.append({'kind':kind, 'parameter':parameter, 'validation':metrics(actual,scored)})
        except ValueError as exc:
            failures.append({'kind':kind,'parameter':parameter,'reason':str(exc)})
    if not candidates:
        raise ValueError('all research model fits failed')
    chosen = min(candidates, key=lambda c:(c['validation']['brier'],c['validation']['logLoss'],c['kind'],c['parameter']))
    model = fit(group(0,partitions[-1]), chosen['kind'], chosen['parameter'])
    holdout = group(partitions[-1],len(dates))
    comp = comparison(holdout,predict(holdout,model))
    reasons = blockers(comp)
    return {**report, 'status':'HISTORICAL_SCREEN_FAILED' if reasons else 'READY_FOR_NEW_FUTURE_TEST',
            'comparisons':candidates,'fitFailures':failures, 'chosen':chosen,
            'holdout':comp, 'holdoutBlockers':reasons, 'model':model,
            'partitionDates':{'development':dates[:partitions[-1]],'holdout':dates[partitions[-1]:]},
            'nextRequiredEvidence':'100 new immutable pregame predictions after model freeze'}
