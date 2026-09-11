"""Frozen-version upgrade and final-refit orchestration regressions.

These synthetic fixtures test software behavior, not MLB performance.
"""
import copy
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'mlb_research'))
import mlb_research_feature_programs_v1 as programs


def rows(n=300):
    rng = np.random.default_rng(421)
    data = []
    for i in range(n):
        first, second = rng.normal(size=2)
        label = int(first*second + rng.normal(scale=.25) > 0)
        data.append({'officialGamePk': str(i),
            'slateDateEt': (date(2026,1,1)+timedelta(days=i//10)).isoformat(),
            'features': {'marketHomeProbability': .5, 'homeStarterEra7d': float(first), 'awayLineupOps30d': float(second)},
            'homeWon': label, 'homeRuns': 6 if label else 2, 'awayRuns': 2 if label else 6,
            'originalObservation': False, 'slateComplete': True})
    return data


def test_frozen_model_survives_discovery_generator_and_limit_upgrades(monkeypatch):
    import mlb_research_models_v1 as models
    import mlb_research_feature_replay_v1 as replay
    data = rows()
    model = json.loads(json.dumps(models.fit(data, 'adaptive_linear', 1.)))
    before = models.predict(data, model)
    def forbidden(*_args, **_kwargs):
        raise AssertionError('frozen inference must never call current discovery')
    monkeypatch.setattr(programs, 'python_source', forbidden)
    monkeypatch.setattr(programs, 'apply_rows', forbidden)
    monkeypatch.setattr(programs, 'transform', forbidden)
    monkeypatch.setattr(programs, 'validate', forbidden)
    monkeypatch.setattr(programs, 'VERSION', 'FUTURE-DISCOVERY-VERSION')
    monkeypatch.setattr(programs, 'LIMITS', {**programs.LIMITS, 'maximumGeneratedFeatures': 2, 'normalizationClip': 3.})
    assert replay.LIMITS['maximumGeneratedFeatures'] == 4
    assert replay.LIMITS['normalizationClip'] == 5.
    np.testing.assert_array_equal(models.predict(data, model), before)
    invalid = copy.deepcopy(model)
    invalid['featureProgram']['version'] = 'UNSUPPORTED-FUTURE-VERSION'
    with pytest.raises(ValueError, match='unsupported frozen'):
        models.predict(data, invalid)


def test_final_development_refit_failure_falls_back_before_single_holdout(monkeypatch):
    import mlb_research_models_v1 as models
    data = rows(n=800)
    final_attempts, holdout_calls = [], []
    def fit(training, kind, parameter):
        if len(training) == 640:
            final_attempts.append(kind)
            if kind == 'adaptive_linear':
                raise ValueError('no eligible feature discovery inputs')
        return {'kind': kind, 'parameter': parameter}
    def predict(validation, model):
        if len(validation) == 160:
            holdout_calls.append(model['kind'])
        # Artificial scores exercise orchestration, not predictive ability.
        if model['kind'] == 'adaptive_linear':
            return np.asarray([.9 if row['homeWon'] else .1 for row in validation])
        return np.full(len(validation), .5)
    monkeypatch.setattr(models, 'fit', fit)
    monkeypatch.setattr(models, 'predict', predict)
    result = models.research(data)
    assert final_attempts[0] == 'adaptive_linear'
    assert len(final_attempts) == 2
    assert result['chosen']['kind'] != 'adaptive_linear'
    assert holdout_calls == [result['chosen']['kind']]
    assert result['finalFitFailures'] == [{'kind': 'adaptive_linear', 'parameter': 1.,
        'reason': 'no eligible feature discovery inputs', 'stage': 'development_final_refit'}]
    assert result['productionAuthorityChanged'] is False


def test_poor_holdout_does_not_trigger_fallback_or_another_holdout(monkeypatch):
    import mlb_research_models_v1 as models
    data = rows(n=800)
    holdout_calls = []
    monkeypatch.setattr(models, 'fit', lambda training, kind, parameter: {'kind': kind})
    def predict(validation, model):
        if len(validation) == 160:
            holdout_calls.append(model['kind'])
            return np.asarray([.1 if row['homeWon'] else .9 for row in validation])
        return np.asarray([.9 if row['homeWon'] else .1 for row in validation]) if model['kind'] == 'adaptive_linear' else np.full(len(validation), .5)
    monkeypatch.setattr(models, 'predict', predict)
    result = models.research(data)
    assert result['status'] == 'HISTORICAL_SCREEN_FAILED'
    assert result['chosen']['kind'] == 'adaptive_linear'
    assert holdout_calls == ['adaptive_linear']
    assert result['finalFitFailures'] == []


def test_all_final_refit_failures_fail_closed_without_consulting_holdout(monkeypatch):
    import mlb_research_models_v1 as models
    def fit(training, kind, parameter):
        if len(training) == 640:
            raise ValueError('fixture convergence failure')
        return {'kind': kind}
    def predict(validation, model):
        assert len(validation) != 160, 'no holdout may be read after all refits fail'
        return np.full(len(validation), .5)
    monkeypatch.setattr(models, 'fit', fit)
    monkeypatch.setattr(models, 'predict', predict)
    with pytest.raises(ValueError, match='all selected development refits failed'):
        models.research(rows(n=800))
