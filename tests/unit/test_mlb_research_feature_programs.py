"""Bounded feature discovery, generated-code replay, and chronological isolation.

Synthetic data below are adversarial test fixtures, NOT MLB performance claims.
"""
import ast
import copy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'mlb_research'))
import mlb_research_feature_programs_v1 as programs


def rows(n=300, extra=0, seed=421):
    rng = np.random.default_rng(seed)
    result = []
    for i in range(n):
        first, second = rng.normal(size=2)
        label = int(first*second + rng.normal(scale=.25) > 0)
        features = {'marketHomeProbability': .5,
                    'homeStarterEra7d': float(first), 'awayLineupOps30d': float(second)}
        features.update({'context'+str(k): float(rng.normal()) for k in range(extra)})
        result.append({'officialGamePk': str(i), 'slateDateEt': (date(2026,1,1)+timedelta(days=i//10)).isoformat(),
                       'features': features, 'homeWon': label,
                       'homeRuns': 6 if label else 2, 'awayRuns': 2 if label else 6,
                       'originalObservation': False, 'slateComplete': True})
    return result


def rehash(plan):
    result = copy.deepcopy(plan)
    result.pop('sha256', None)
    return {**result, 'sha256': programs.digest(result)}


def execute_generated(plan):
    # Only compiler output from the trusted mathematical DSL runs in this test.
    # Production inference uses the checked interpreter, never this test helper.
    source = programs.python_source(plan)
    namespace = {}
    exec(compile(source, 'generated_mlb_features.py', 'exec'), namespace)
    return namespace['transform']


def test_discovery_is_deterministic_order_independent_and_does_not_mutate_training_rows():
    data = rows(); original = copy.deepcopy(data)
    plan = programs.discover(data)
    assert plan == programs.discover(data)
    assert plan == programs.discover(list(reversed(data)))
    assert original == data
    assert programs.validate(plan) == plan
    assert plan['performanceClaim'] is False and plan['productionAuthorityChanged'] is False


def test_generated_program_and_retired_inputs_are_bounded():
    plan = programs.discover(rows(extra=38))
    assert len(plan['baseFeatures']) <= 24
    assert len(plan['programs']) <= 4
    assert plan['candidatePrograms'] <= 96
    assert len(plan['interactionPoolFeatures']) <= 8
    assert plan['retiredFeatures']
    assert all(item['feature'] not in plan['baseFeatures'] for item in plan['retiredFeatures'])
    assert any(recipe['op'] == 'product' for recipe in programs.discover(rows())['programs'])


def test_exploration_uses_only_training_game_identity_and_remains_repeatable():
    data = rows(extra=38)
    first = programs.discover(data)
    changed = copy.deepcopy(data)
    for row in changed:
        row['officialGamePk'] = 'new-cohort-' + row['officialGamePk']
    second = programs.discover(changed)
    assert first['trainingCohortKey'] != second['trainingCohortKey']
    assert second == programs.discover(changed)
    assert len(second['interactionPoolFeatures']) <= programs.LIMITS['interactionPool']


@pytest.mark.parametrize('bad', ['homeWon', 'home_win', 'HOME_WON', 'Final_Score', 'isWinner', 'homeRuns',
                               'outcome', 'target', 'label', 'auto_prior', '__import__', 'x);raise RuntimeError()'])
def test_outcomes_metadata_and_code_shaped_names_cannot_be_program_inputs(bad):
    data = rows()
    for row in data:
        row['features'][bad] = row['homeWon']
    plan = programs.discover(data)
    assert bad not in plan['baseFeatures']
    assert bad not in plan['normalization']
    assert all(bad not in recipe['inputs'] for recipe in plan['programs'])
    assert not programs.safe_name(bad)


def test_missing_sparse_constant_and_nonfinite_inputs_are_not_invented():
    data = rows()
    for i, row in enumerate(data):
        row['features'].update(sparse=float(i) if i < 10 else None, constant=1., bad=float('inf'), absent=None)
    plan = programs.discover(data)
    assert not {'sparse', 'constant', 'bad', 'absent'} & set(plan['baseFeatures'])
    transformed = programs.apply_rows([{'features': {'marketHomeProbability': .5}}], plan)[0]['features']
    assert transformed['marketHomeProbability'] == .5
    assert all(value is None for name, value in transformed.items() if name != 'marketHomeProbability')


@pytest.mark.parametrize('n', [0, 20, 59, 1201])
def test_training_work_is_bounded(n):
    with pytest.raises(ValueError, match='bound'):
        programs.discover(rows(n))


def test_duplicate_training_games_are_not_extra_samples():
    data = rows(); data[-1]['officialGamePk'] = data[0]['officialGamePk']
    with pytest.raises(ValueError, match='duplicate'):
        programs.discover(data)


def test_input_feature_search_is_bounded():
    with pytest.raises(ValueError, match='input bound'):
        programs.discover(rows(n=100, extra=513))


def test_test_rows_never_refit_normalization_or_require_outcomes():
    plan = programs.discover(rows()); original = copy.deepcopy(plan)
    samples = [{'features': {'marketHomeProbability': .5, 'homeStarterEra7d': 1e100, 'awayLineupOps30d': -1e100}}]
    first = programs.apply_rows(samples, plan)
    samples[0]['homeWon'] = 0
    assert first[0]['features'] == programs.apply_rows(samples, plan)[0]['features']
    samples[0]['homeWon'] = 1
    assert first[0]['features'] == programs.apply_rows(samples, plan)[0]['features']
    assert plan == original
    assert all(value is None or np.isfinite(value) for value in first[0]['features'].values())
    assert all(abs(first[0]['features'][r['name']]) <= 25 for r in plan['programs'])


def test_python_source_exactly_replays_frozen_recipe_and_is_hash_bound():
    data = rows(); plan = programs.discover(data); generated = execute_generated(plan)
    source = programs.python_source(plan)
    assert programs.source_sha256(source) == hashlib.sha256(source.encode()).hexdigest()
    assert 'homeWon' not in source and 'officialGamePk' not in source
    assert source == programs.python_source(plan)
    for row in data:
        assert generated(row['features']) == programs.transform(row['features'], plan)
    for value in [None, True, False, np.bool_(True), float('nan'), float('inf'), 'bad', 1e308, -1e308, '2.5']:
        sample = {name: value for name in plan['baseFeatures']}
        sample['marketHomeProbability'] = .5
        assert generated(sample) == programs.transform(sample, plan)


@pytest.mark.parametrize('op', ['product', 'difference', 'square', 'absolute'])
def test_every_supported_operation_has_identical_generated_source_and_interpreter(op):
    plan = programs.discover(rows())
    names = plan['baseFeatures'][:programs.OPS[op]]
    plan['programs'] = [{'op': op, 'inputs': names, 'name': programs.expression_name(op, names)}]
    plan = rehash(plan)
    generated = execute_generated(plan)
    for row in rows(n=70, seed=2):
        assert generated(row['features']) == programs.transform(row['features'], plan)


@pytest.mark.parametrize('damage', ['checksum', 'unknown_op', 'missing_input', 'zero_scale', 'limits', 'duplicate_program'])
def test_invalid_or_tampered_program_cannot_run(damage):
    plan = programs.discover(rows())
    if damage == 'checksum':
        plan['sha256'] = '0'*64
    elif damage == 'unknown_op':
        plan['programs'][0]['op'] = 'eval'
    elif damage == 'missing_input':
        plan['programs'][0]['inputs'][0] = 'homeWon'
    elif damage == 'zero_scale':
        plan['normalization'][plan['baseFeatures'][0]]['scale'] = 0.
    elif damage == 'limits':
        plan['limits']['maximumGeneratedFeatures'] = 999
    else:
        plan['programs'].append(copy.deepcopy(plan['programs'][0]))
    if damage != 'checksum':
        plan = rehash(plan)
    with pytest.raises(ValueError):
        programs.apply_rows(rows(n=1), plan)


def test_real_model_keeps_raw_coverage_separate_from_generated_matrix_and_replays():
    import mlb_research_models_v1 as models
    data = rows()
    model = models.fit(data, 'adaptive_linear', 1.)
    assert model['features'] == model['featureProgram']['baseFeatures']
    assert not any(name.startswith(programs.PREFIX) for name in model['features'])
    assert any(name.startswith(programs.PREFIX) for name in model['matrixFeatures'])
    assert model['generatedFeatureSourceSha256'] == programs.source_sha256(model['generatedFeaturePython'])
    forecasts = models.predict(data, model)
    assert np.isfinite(forecasts).all() and ((forecasts > 0) & (forecasts < 1)).all()
    restored = json.loads(json.dumps(model, allow_nan=False))
    np.testing.assert_allclose(models.predict(data, restored), forecasts, atol=1e-12, rtol=0)
    changed_outcomes = copy.deepcopy(data)
    for row in changed_outcomes:
        row['homeWon'] = 1-row['homeWon']
    np.testing.assert_array_equal(models.predict(changed_outcomes, restored), forecasts)
    restored['generatedFeaturePython'] += '# corruption\n'
    with pytest.raises(ValueError, match='source or input contract'):
        models.predict(data, restored)


def test_real_model_legacy_frozen_probabilities_remain_unchanged():
    import mlb_research_models_v1 as models
    data = rows()
    legacy = {'kind': 'linear', 'features': ['homeStarterEra7d'],
              'means': {'homeStarterEra7d': 0.}, 'scales': {'homeStarterEra7d': 1.},
              'weights': [.12, -.4]}
    old_matrix = np.column_stack([np.ones(len(data)),
        np.clip([float(r['features']['homeStarterEra7d'])-0. for r in data], -10., 10.)/1.])
    expected = models.sigmoid(models.logit([r['features']['marketHomeProbability'] for r in data])
                              + old_matrix @ legacy['weights'])
    np.testing.assert_array_equal(models.predict(data, legacy), expected)
    assert 'matrixFeatures' not in legacy and 'featureProgram' not in legacy


def test_real_model_research_discovers_only_inside_training_folds_and_never_changes_authority(monkeypatch):
    import mlb_research_models_v1 as models
    data = rows(n=800)
    called = []
    original = programs.discover
    def record(training):
        called.append((max(r['slateDateEt'] for r in training), tuple(r['officialGamePk'] for r in training)))
        return original(training)
    monkeypatch.setattr(programs, 'discover', record)
    result = models.research(data)
    assert 3 <= len(called) <= 4
    assert result['productionAuthorityChanged'] is False
    assert result['prospectiveQualificationEvidence'] is False
    assert result['nextRequiredEvidence'] == '100 new immutable pregame predictions after model freeze'
    candidates = [c for c in result['comparisons'] if c['kind'] == 'adaptive_linear']
    assert len(candidates) == 1
    adaptive = candidates[0]
    assert len(adaptive['featureDiscoveryFolds']) == 3
    for evidence in adaptive['featureDiscoveryFolds']:
        assert evidence['trainingLastSlate'] < evidence['validationFirstSlate']
        assert evidence['validationLastSlate'] < result['partitionDates']['holdout'][0]
        assert evidence['sourceSha256'] == programs.source_sha256(programs.python_source(evidence['plan']))
    assert all(last < result['partitionDates']['holdout'][0] for last, _ in called)
    assert adaptive['minimumRequiredBrierGain'] == .001
    assert models.PROTOCOL['automaticPromotionEnabled'] is False
    assert models.PROTOCOL['freshTestMinimum'] == 100


def test_real_model_new_complexity_must_beat_legacy_validation_before_holdout():
    import mlb_research_models_v1 as models
    result = models.research(rows(n=600, extra=2, seed=71))
    adaptive = next(c for c in result['comparisons'] if c['kind'] == 'adaptive_linear')
    legacy = adaptive['legacyComparison']['validation']
    expected = (adaptive['validation']['brier'] <= legacy['brier']-.001
                and adaptive['validation']['logLoss'] < legacy['logLoss'])
    assert adaptive['eligibleForHoldoutSelection'] == expected
    if not expected:
        assert result['chosen']['kind'] != 'adaptive_linear'
