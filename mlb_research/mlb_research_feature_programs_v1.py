"""Bounded, train-only feature-program discovery; no arbitrary code execution.

The existing research owner can add nonlinear features and retire weak inputs
without editing serving code or asking a chat session to keep running. Every
program is a small typed mathematical recipe fitted only on development rows.
Python source is emitted for audit/replay; inference uses the checked recipe,
never eval/exec or an LLM-produced script.
"""
from itertools import combinations
import hashlib
import json
import math
import re

import numpy as np

VERSION = 'MLB-FEATURE-PROGRAMS-v1'
BASELINE = 'marketHomeProbability'
PREFIX = 'auto_'
LIMITS = {'minimumTrainingRows': 60, 'maximumTrainingRows': 1200, 'minimumCoverage': .8,
          'maximumInputFeatures': 512, 'maximumBaseFeatures': 24,
          'interactionPool': 8, 'coreInteractionFeatures': 4, 'maximumCandidatePrograms': 96,
          'maximumGeneratedFeatures': 4, 'normalizationClip': 5.,
          'maximumRedundancyCorrelation': .98}
OPS = {'product': 2, 'difference': 2, 'square': 1, 'absolute': 1}
FORBIDDEN = {'homewon', 'homewin', 'awaywon', 'awaywin', 'homeruns', 'awayruns',
             'winner', 'iswinner', 'winnerid', 'outcome', 'result', 'target', 'label',
             'finalscore', 'gameresult', 'homescore', 'awayscore'}
NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,127}$')


def digest(value):
    body = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(body).hexdigest()


def source_sha256(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def numeric(value):
    if value is None or isinstance(value, bool) or type(value).__name__ in ('bool', 'bool_'):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def safe_name(name):
    return (isinstance(name, str) and NAME.fullmatch(name) is not None
            and name != BASELINE and not name.startswith(PREFIX)
            and name.lower().replace('_', '') not in FORBIDDEN)


def association(values, residual):
    mask = np.isfinite(values)
    if mask.sum() < LIMITS['minimumTrainingRows'] or mask.mean() < LIMITS['minimumCoverage']:
        return None
    x, y = values[mask], residual[mask]
    x, y = x-x.mean(), y-y.mean()
    denominator = float(np.sqrt(np.dot(x, x)*np.dot(y, y)))
    if not math.isfinite(denominator) or denominator <= 1e-12:
        return None
    return float(np.dot(x, y)/denominator)


def expression_name(op, inputs):
    return PREFIX + digest({'op': op, 'inputs': inputs})[:16]


def operation(op, values):
    if op == 'product':
        return values[0]*values[1]
    if op == 'difference':
        return values[0]-values[1]
    if op == 'square':
        return values[0]*values[0]
    if op == 'absolute':
        return np.abs(values[0])
    raise ValueError('unsupported feature operation')


def discover(rows):
    """Generate and rank recipes using only the supplied TRAINING fold.

    Ranking is exploratory, not evidence of improved prediction performance.
    The caller must evaluate the resulting model on later unseen slates.
    """
    if not LIMITS['minimumTrainingRows'] <= len(rows) <= LIMITS['maximumTrainingRows']:
        raise ValueError('feature discovery training-row bound exceeded')
    if any(not isinstance(row, dict) or not isinstance(row.get('features'), dict)
           or row.get('officialGamePk') is None for row in rows):
        raise ValueError('feature discovery requires identified training rows')
    if len({str(row['officialGamePk']) for row in rows}) != len(rows):
        raise ValueError('duplicate feature discovery training game')
    rows = sorted(rows, key=lambda row: (str(row.get('slateDateEt', '')), str(row['officialGamePk'])))
    labels = np.asarray([r['homeWon'] for r in rows], dtype=float)
    market = np.asarray([r['features'].get(BASELINE) for r in rows], dtype=float)
    if (not np.isin(labels, [0, 1]).all() or set(labels) != {0., 1.}
            or not np.isfinite(market).all() or ((market <= 0) | (market >= 1)).any()):
        raise ValueError('feature discovery requires binary labels and valid market probabilities')
    names = sorted({name for row in rows for name in row['features'] if safe_name(name)})
    if len(names) > LIMITS['maximumInputFeatures']:
        raise ValueError('feature discovery input bound exceeded')
    residual = labels-market
    ranked, unavailable = [], []
    arrays, normalization = {}, {}
    for name in names:
        values = np.asarray([numeric(r['features'].get(name)) for r in rows], dtype=float)
        score = association(values, residual)
        finite = values[np.isfinite(values)]
        scale = float(finite.std()) if len(finite) else 0.
        if score is None or not math.isfinite(scale) or scale <= 1e-9:
            unavailable.append({'feature': name, 'reason': 'insufficient_coverage_or_variation'})
            continue
        mean = float(finite.mean())
        normalization[name] = {'mean': mean, 'scale': scale}
        arrays[name] = np.clip((values-mean)/scale, -LIMITS['normalizationClip'], LIMITS['normalizationClip'])
        ranked.append((abs(score), name, score))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked:
        raise ValueError('no eligible feature discovery inputs')
    # Explore some weak main-effect inputs as well: useful interactions can
    # have little individual correlation. The seed uses TRAINING game IDs,
    # never validation labels, future rows, wall clock, or uncontrolled RNG.
    cohort_key = digest(sorted(str(row['officialGamePk']) for row in rows))
    core = [entry[1] for entry in ranked[:LIMITS['coreInteractionFeatures']]]
    exploration = sorted((entry[1] for entry in ranked if entry[1] not in core),
                         key=lambda name: (digest({'cohort': cohort_key, 'feature': name}), name))
    pool = core + exploration[:LIMITS['interactionPool']-len(core)]
    chosen_raw = (pool + [entry[1] for entry in ranked if entry[1] not in pool])[:LIMITS['maximumBaseFeatures']]
    recipes = []
    for name in pool:
        for op in ('square', 'absolute'):
            recipes.append((op, [name]))
    for left, right in combinations(pool, 2):
        for op in ('product', 'difference'):
            recipes.append((op, [left, right]))
    recipes = recipes[:LIMITS['maximumCandidatePrograms']]
    scored = []
    for op, inputs in recipes:
        values = operation(op, [arrays[name] for name in inputs])
        score = association(values, residual)
        if score is not None:
            scored.append((abs(score), expression_name(op, inputs), op, inputs, score, values))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected, vectors = [], [arrays[name] for name in chosen_raw]
    for _, name, op, inputs, score, values in scored:
        redundant = False
        for other in vectors:
            mask = np.isfinite(values) & np.isfinite(other)
            if mask.sum() >= LIMITS['minimumTrainingRows']:
                x, y = values[mask], other[mask]
                x, y = x-x.mean(), y-y.mean()
                den = float(np.sqrt(np.dot(x, x)*np.dot(y, y)))
                if den > 1e-12 and abs(float(np.dot(x, y)/den)) >= LIMITS['maximumRedundancyCorrelation']:
                    redundant = True
                    break
        if redundant:
            continue
        selected.append({'name': name, 'op': op, 'inputs': inputs,
                         'trainingResidualCorrelation': score,
                         'trainingCoverage': float(np.isfinite(values).mean()),
                         'hypothesis': 'A nonlinear or interacting training signal may explain residual error beyond market odds.'})
        vectors.append(values)
        if len(selected) == LIMITS['maximumGeneratedFeatures']:
            break
    plan = {'version': VERSION, 'limits': dict(LIMITS), 'trainingRows': len(rows),
            'selectionTarget': 'homeWon_minus_same_time_market_probability_training_fold_only',
            'baseFeatures': sorted(chosen_raw),
            'normalization': {name: normalization[name] for name in sorted(chosen_raw)},
            'programs': selected, 'candidatePrograms': len(recipes),
            'trainingCohortKey': cohort_key, 'interactionPoolFeatures': pool,
            'retiredFeatures': unavailable + [{'feature': item[1], 'reason': 'bounded_training_rank'}
                                               for item in ranked if item[1] not in chosen_raw],
            'performanceClaim': False, 'productionAuthorityChanged': False}
    return {**plan, 'sha256': digest(plan)}


def validate(plan):
    if not isinstance(plan, dict) or plan.get('version') != VERSION:
        raise ValueError('unsupported feature program version')
    if plan.get('sha256') != digest({k: v for k, v in plan.items() if k != 'sha256'}):
        raise ValueError('feature program checksum mismatch')
    # Frozen program limits cannot silently inherit changed discovery settings.
    limits = plan.get('limits', {})
    if limits != LIMITS:
        raise ValueError('feature program limit contract changed')
    names = plan.get('baseFeatures', [])
    if (not isinstance(names, list) or not 0 < len(names) <= LIMITS['maximumBaseFeatures']
            or len(set(names)) != len(names) or not all(safe_name(name) for name in names)):
        raise ValueError('invalid raw feature contract')
    normalization = plan.get('normalization', {})
    if set(normalization) != set(names):
        raise ValueError('feature normalization contract mismatch')
    for value in normalization.values():
        if (numeric(value.get('mean')) is None or numeric(value.get('scale')) is None
                or value['scale'] <= 1e-9):
            raise ValueError('invalid training-only normalization')
    recipes = plan.get('programs', [])
    if not isinstance(recipes, list) or len(recipes) > LIMITS['maximumGeneratedFeatures']:
        raise ValueError('feature program bound exceeded')
    seen = set()
    for recipe in recipes:
        op, inputs = recipe.get('op'), recipe.get('inputs')
        if (op not in OPS or not isinstance(inputs, list) or len(inputs) != OPS[op]
                or any(name not in names for name in inputs) or len(set(inputs)) != len(inputs)
                or recipe.get('name') != expression_name(op, inputs) or recipe['name'] in seen):
            raise ValueError('invalid generated feature recipe')
        seen.add(recipe['name'])
    return plan


def transform(features, plan):
    """Pure checked transform. Caller validates the plan once per batch."""
    result = {name: numeric(features.get(name)) for name in plan['baseFeatures']}
    result[BASELINE] = numeric(features.get(BASELINE))
    normalized = {}
    for name, moments in plan['normalization'].items():
        value = result[name]
        normalized[name] = (None if value is None else max(-LIMITS['normalizationClip'],
            min(LIMITS['normalizationClip'], (value-moments['mean'])/moments['scale'])))
    for recipe in plan['programs']:
        values = [normalized[name] for name in recipe['inputs']]
        result[recipe['name']] = None if any(value is None for value in values) else float(operation(recipe['op'], values))
    return result


def apply_rows(rows, plan):
    validate(plan)
    return [{**row, 'features': transform(row['features'], plan)} for row in rows]


def python_source(plan):
    """Emit portable audit code from trusted templates, never arbitrary text."""
    validate(plan)
    lines = ['"""Generated MLB feature transform; requires its frozen model for prediction."""',
             'import math', '', 'def _number(value):',
             "    if value is None or isinstance(value, bool) or type(value).__name__ in ('bool', 'bool_'): return None",
             '    try: result = float(value)',
             '    except (ValueError, TypeError, OverflowError): return None',
             '    return result if math.isfinite(result) else None', '',
             'def _z(value, mean, scale):',
             '    return None if value is None else max(-5.0, min(5.0, (value-mean)/scale))', '',
             'def transform(features):', '    result = {}']
    for name in [*plan['baseFeatures'], BASELINE]:
        quoted = json.dumps(name)
        lines.append(f'    result[{quoted}] = _number(features.get({quoted}))')
    for index, recipe in enumerate(plan['programs']):
        variables = []
        for position, name in enumerate(recipe['inputs']):
            moments = plan['normalization'][name]
            variable = f'v_{index}_{position}'
            lines.append(f'    {variable} = _z(result[{json.dumps(name)}], {moments["mean"]!r}, {moments["scale"]!r})')
            variables.append(variable)
        op = recipe['op']
        expression = (f'{variables[0]} * {variables[1]}' if op == 'product' else
                      f'{variables[0]} - {variables[1]}' if op == 'difference' else
                      f'{variables[0]} * {variables[0]}' if op == 'square' else f'abs({variables[0]})')
        missing = ' or '.join(variable + ' is None' for variable in variables)
        lines.append(f'    result[{json.dumps(recipe["name"])}] = None if {missing} else {expression}')
    lines.append('    return result')
    return '\n'.join(lines) + '\n'
