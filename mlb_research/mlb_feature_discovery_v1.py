"""Chronological challenger-only screening of existing pregame features.

This module does not add features to a serving model and never sees the final
holdout. It asks a narrower question: does one already-captured feature add
repeatable out-of-sample probability skill beyond the same-time market prior?
Results are research-backlog evidence only.
"""
import math
import numpy as np
from mlb_research_sources_v1 import number

VERSION = 'MLB-FEATURE-DISCOVERY-v1-market-residual-univariate'
DEFAULT = {
    'version': VERSION,
    'minimumCoverage': .80,
    'minimumValidationRows': 120,
    'minimumPositiveFolds': 2,
    'minimumBrierImprovement': .00025,
    'ridge': 1.0,
    'maxReportedCandidates': 20,
}


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1-1e-6)
    return np.log(p)-np.log1p(-p)


def _sigmoid(z):
    return 1/(1+np.exp(-np.clip(z, -30, 30)))


def _prepare(rows, feature, mean=None, scale=None):
    values = [number(r['features'].get(feature)) for r in rows]
    observed = [v for v in values if v is not None]
    if mean is None:
        if not observed:
            raise ValueError('feature has no observations')
        mean = float(np.mean(observed))
        scale = float(np.std(observed))
    if not math.isfinite(mean) or not math.isfinite(scale) or scale <= 1e-9:
        raise ValueError('feature has no usable variance')
    x = np.asarray([(mean if v is None else v) for v in values], dtype=float)
    return np.clip((x-mean)/scale, -10, 10), mean, scale


def _fit_weight(rows, feature, ridge):
    x, mean, scale = _prepare(rows, feature)
    y = np.asarray([r['homeWon'] for r in rows], dtype=float)
    offset = _logit([r['features']['marketHomeProbability'] for r in rows])
    w = 0.0
    for _ in range(40):
        p = _sigmoid(offset+w*x)
        gradient = float(np.mean((p-y)*x) + ridge*w)
        hessian = float(np.mean(p*(1-p)*x*x) + ridge)
        step = gradient/hessian
        w -= step
        if abs(step) < 1e-8:
            break
    if not math.isfinite(w):
        raise ValueError('non-finite feature weight')
    return w, mean, scale


def _score(rows, feature, fitted):
    weight, mean, scale = fitted
    x, _, _ = _prepare(rows, feature, mean, scale)
    y = np.asarray([r['homeWon'] for r in rows], dtype=float)
    market = np.asarray([r['features']['marketHomeProbability'] for r in rows], dtype=float)
    candidate = _sigmoid(_logit(market)+weight*x)
    return {
        'rows': len(rows),
        'weight': float(weight),
        'marketBrier': float(np.mean((market-y)**2)),
        'candidateBrier': float(np.mean((candidate-y)**2)),
        'brierImprovement': float(np.mean((market-y)**2)-np.mean((candidate-y)**2)),
    }


def screen(rows, config=None):
    """Return stable feature hypotheses using expanding chronological folds.

    Rows must already be restricted to development data by the caller. Missing
    feature values are mean-imputed from each training fold only. Labels and
    market probabilities must be valid; otherwise the research input is bad.
    """
    cfg = {**DEFAULT, **(config or {})}
    rows = sorted(rows, key=lambda r:(r['slateDateEt'], str(r['officialGamePk'])))
    dates = sorted({r['slateDateEt'] for r in rows})
    for row in rows:
        p = number(row.get('features', {}).get('marketHomeProbability'))
        if row.get('homeWon') not in (0, 1) or p is None or not 0 < p < 1:
            raise ValueError('feature discovery requires labeled rows and a valid market prior')
    report = {'version': VERSION, 'status': 'INSUFFICIENT_DEVELOPMENT_DATA', 'rows': len(rows),
              'slates': len(dates), 'config': cfg, 'evaluatedFeatures': 0,
              'candidates': [], 'rejected': []}
    if len(rows) < cfg['minimumValidationRows'] or len(dates) < 20:
        return report
    # Expanding train windows; each validation interval is a later whole-slate
    # block. This screen never receives the model-selection final holdout.
    cuts = [int(len(dates)*f) for f in (.50, .67, .84, 1.0)]
    if len(set(cuts)) != 4 or cuts[0] < 10:
        return report
    names = sorted({k for r in rows for k in r['features'] if k != 'marketHomeProbability'})
    for name in names:
        observed = [number(r['features'].get(name)) for r in rows]
        coverage = sum(v is not None for v in observed)/len(rows)
        finite = [v for v in observed if v is not None]
        if coverage < cfg['minimumCoverage'] or len(finite) < 2 or np.std(finite) <= 1e-9:
            report['rejected'].append({'feature': name, 'reason': 'LOW_COVERAGE_OR_VARIANCE', 'coverage': coverage})
            continue
        folds=[]
        try:
            for left, right in zip(cuts[:-1], cuts[1:]):
                train = [r for r in rows if r['slateDateEt'] in dates[:left]]
                valid = [r for r in rows if r['slateDateEt'] in dates[left:right]]
                if not train or not valid:
                    raise ValueError('empty chronological fold')
                folds.append(_score(valid, name, _fit_weight(train, name, cfg['ridge'])))
        except ValueError:
            report['rejected'].append({'feature': name, 'reason': 'FOLD_FIT_FAILED', 'coverage': coverage})
            continue
        report['evaluatedFeatures'] += 1
        total = sum(f['rows'] for f in folds)
        weighted = sum(f['brierImprovement']*f['rows'] for f in folds)/total
        positive = sum(f['brierImprovement'] > 0 for f in folds)
        signs = [1 if f['weight'] > 0 else -1 if f['weight'] < 0 else 0 for f in folds]
        stable_direction = len({s for s in signs if s}) <= 1
        evidence = {'feature': name, 'coverage': coverage, 'validationRows': total,
                    'positiveFolds': positive, 'stableDirection': stable_direction,
                    'meanBrierImprovement': weighted, 'folds': folds,
                    'direction': 'HOME_POSITIVE' if sum(f['weight'] for f in folds) > 0 else 'HOME_NEGATIVE'}
        if (total >= cfg['minimumValidationRows'] and positive >= cfg['minimumPositiveFolds']
                and stable_direction and weighted >= cfg['minimumBrierImprovement']):
            report['candidates'].append(evidence)
        else:
            report['rejected'].append({**evidence, 'reason': 'INSUFFICIENT_REPEATABLE_OOS_SKILL'})
    report['candidates'].sort(key=lambda r:(-r['meanBrierImprovement'], r['feature']))
    report['candidates'] = report['candidates'][:cfg['maxReportedCandidates']]
    report['rejected'].sort(key=lambda r:r['feature'])
    report['status'] = 'CANDIDATES_FOUND' if report['candidates'] else 'NO_REPEATABLE_CANDIDATES'
    report['promotionAuthority'] = False
    report['nextStep'] = 'Candidates require a separately registered multivariate challenger and untouched holdout/prospective validation.'
    return report
