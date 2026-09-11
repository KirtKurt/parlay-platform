"""Frozen v1 feature-program replay, independent of future discovery upgrades.

Do not alter these v1 semantics when adding a generator version. Keep v1 for
active/archived models and add a separate versioned replay implementation.
Only checked math is interpreted; generated source is audit evidence.
"""
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
