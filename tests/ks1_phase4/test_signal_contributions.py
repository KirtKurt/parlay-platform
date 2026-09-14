"""Live proof must distinguish performance from missing-data effects."""
import lightgbm as lgb
import hashlib
import json
import numpy as np
import pandas as pd
import pytest
import pyarrow as pa

from ks1.daily import signal_contributions, publication_proof, upgrade_signal_evidence
from ks1.publish import parquet_bytes


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


def test_publication_proof_uses_verified_post_calibration_bytes(tmp_path):
    # The raw classifier output differs from the official calibrated probability.
    body = parquet_bytes(pa.Table.from_pylist([{
        'date': '2026-09-14', 'game_id': '123', 'p_home': .56789123456789,
        'p_raw': .6, 'as_of': '2026-09-14T17:00:00Z',
    }]))
    (tmp_path/'predictions.parquet').write_bytes(body)
    report = {'as_of': '2026-09-14T17:01:00Z', 'published': True,
              'publication': {'readback_verified': True},
              'parquet_sha256': hashlib.sha256(body).hexdigest()}
    proof = publication_proof(tmp_path, report)
    assert proof['rows'][0]['p_home'] == .56789123456789
    assert proof['rows'][0]['p_raw'] == .6
    assert proof['rows'][0]['as_of'] != proof['publication_as_of']
    assert proof['rows'][0]['team_context'] == {}
    assert proof['rows'][0]['signal_contributions'] is None
    assert proof['rows'][0]['signal_evidence_status'] == 'legacy_or_unavailable'
    with pytest.raises(ValueError, match='requires successful AWS readback'):
        publication_proof(tmp_path, dict(report, published=False))
    with pytest.raises(ValueError, match='hash mismatch'):
        publication_proof(tmp_path, dict(report, parquet_sha256='0'*64))


def test_upgrade_explains_retained_raw_probability_without_republishing_a_pick(fitted):
    model, frame = fitted
    features = frame.iloc[0].to_dict()
    raw = float(model.predict(frame.iloc[:1])[0])
    old = {'p_raw': raw, 'p_home': .57, 'as_of': '2026-09-14T12:00:00Z',
           'signal_contributions_json': json.dumps({'scale': 'raw_log_odds_SHAP'})}
    upgraded = upgrade_signal_evidence(model, features, old)
    assert upgraded != old
    assert {k: v for k, v in upgraded.items() if k != 'signal_contributions_json'} == {
        k: v for k, v in old.items() if k != 'signal_contributions_json'}
    assert json.loads(upgraded['signal_contributions_json'])['schema_version'] == 2
    assert upgrade_signal_evidence(model, features, upgraded) is upgraded
    with pytest.raises(ValueError, match='retained raw probability'):
        upgrade_signal_evidence(model, features, dict(old, p_raw=raw/2))
