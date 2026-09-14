"""Live proof must distinguish performance from missing-data effects."""
import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from ks1.daily import signal_contributions


@pytest.fixture
def fitted():
    rng = np.random.default_rng(714)
    batter, bullpen = rng.normal(size=(2, 800))
    missing = rng.random(800) < .2
    frame = pd.DataFrame({
        'home_lineup_ops_30d': np.where(missing, np.nan, batter),
        'home_lineup_ops_30d_missing': missing.astype(float),
        'away_bullpen_context_era_30d': bullpen,
        'away_bullpen_context_roster_count': rng.integers(6, 10, 800),
    })
    target = (np.where(missing, -2.0, batter)+bullpen > 0).astype(int)
    model = lgb.LGBMClassifier(n_estimators=30, num_leaves=7, verbosity=-1,
                              n_jobs=1, random_state=714).fit(frame, target)
    return model.booster_, frame


def test_live_proof_separates_observed_performance_and_missing_effects(fitted):
    model, frame = fitted
    reports = signal_contributions(model, frame)
    raw = model.predict(frame, raw_score=True)
    observed_batter_effect = observed_bullpen_effect = False
    for (_, row), report, prediction in zip(frame.iterrows(), reports, raw):
        assert report['additivity_verified'] is True
        assert report['bias']+sum(g['signal_score'] for g in report['groups'].values()) == pytest.approx(prediction)
        batters = report['performance_evidence']['batters']
        assert 'missingness_indicator' in batters
        if pd.isna(row.home_lineup_ops_30d):
            assert 'observed_performance' not in batters
            assert 'missing_value' in batters
            assert all(x['value'] is None for x in batters['missing_value']['top_features'])
        else:
            assert 'missing_value' not in batters
            observed_batter_effect |= batters['observed_performance']['absolute_contribution'] > 0
        bullpen = report['performance_evidence']['bullpen']
        observed_bullpen_effect |= bullpen['observed_performance']['absolute_contribution'] > 0
        assert 'other_observed_context' in bullpen
        for group, categories in report['performance_evidence'].items():
            assert sum(x['signal_score'] for x in categories.values()) == pytest.approx(report['groups'][group]['signal_score'])
    assert observed_batter_effect and observed_bullpen_effect


def test_rejects_contributions_for_a_different_prediction(fitted, monkeypatch):
    model, frame = fitted
    original = model.predict

    def corrupt(values, **kwargs):
        result = original(values, **kwargs)
        if kwargs.get('pred_contrib'):
            result[:, -1] += .1
        return result

    monkeypatch.setattr(model, 'predict', corrupt)
    with pytest.raises(ValueError, match='do not reconstruct'):
        signal_contributions(model, frame.iloc[:2])
