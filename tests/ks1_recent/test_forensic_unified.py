import numpy as np
import pandas as pd

from ks1.forensic_unified import forensic_groups, forensic_intensity, sample_weights


def admitted():
    return [
        'market_home_prob',
        'home_starter_era_7d', 'home_starter_era_30d',
        'away_starter_era_7d', 'away_starter_era_30d',
        'home_starter_fip_7d', 'home_starter_fip_30d',
        'away_starter_fip_7d', 'away_starter_fip_30d',
        'home_starter_xwoba_7d', 'home_starter_xwoba_30d',
        'away_starter_xwoba_7d', 'away_starter_xwoba_30d',
        'home_pitcher_context_expected_innings', 'away_pitcher_context_expected_innings',
        'home_lineup_ops_7d', 'away_lineup_ops_7d',
        'home_lineup_xwoba_7d', 'away_lineup_xwoba_7d',
        'home_lineup_top4_ops', 'away_lineup_top4_ops',
        'home_bullpen_context_fip_7d', 'away_bullpen_context_fip_7d',
        'home_bullpen_context_era_7d', 'away_bullpen_context_era_7d',
        'home_bullpen_context_available_count', 'away_bullpen_context_available_count',
    ]


def frame():
    return pd.DataFrame({
        'market_home_prob': [.50, .68],
        'home_starter_era_7d': [3.0, 7.0], 'home_starter_era_30d': [3.1, 3.0],
        'away_starter_era_7d': [3.2, 2.5], 'away_starter_era_30d': [3.1, 4.5],
        'home_starter_fip_7d': [3.1, 5.8], 'home_starter_fip_30d': [3.0, 3.3],
        'away_starter_fip_7d': [3.2, 2.9], 'away_starter_fip_30d': [3.1, 4.4],
        'home_starter_xwoba_7d': [.310, .370], 'home_starter_xwoba_30d': [.312, .315],
        'away_starter_xwoba_7d': [.315, .285], 'away_starter_xwoba_30d': [.314, .330],
        'home_pitcher_context_expected_innings': [5.8, 2.8],
        'away_pitcher_context_expected_innings': [5.7, 6.2],
        'home_lineup_ops_7d': [.760, .690], 'away_lineup_ops_7d': [.750, .840],
        'home_lineup_xwoba_7d': [.320, .285], 'away_lineup_xwoba_7d': [.318, .345],
        'home_lineup_top4_ops': [.800, .730], 'away_lineup_top4_ops': [.795, .880],
        'home_bullpen_context_fip_7d': [3.5, 5.2], 'away_bullpen_context_fip_7d': [3.6, 3.1],
        'home_bullpen_context_era_7d': [3.4, 5.6], 'away_bullpen_context_era_7d': [3.5, 2.9],
        'home_bullpen_context_available_count': [6.0, 3.0],
        'away_bullpen_context_available_count': [6.0, 7.0],
    })


def test_forensic_groups_cover_all_prespecified_signal_families():
    groups = forensic_groups(admitted())
    assert set(groups) == {'market', 'starter_regime', 'starter_workload', 'lineup', 'bullpen'}
    assert all(groups.values())
    assert groups['market'] == ['market_home_prob']
    assert 'home_pitcher_context_expected_innings' in groups['starter_workload']
    assert 'home_lineup_ops_7d' in groups['lineup']
    assert 'home_bullpen_context_fip_7d' in groups['bullpen']


def test_forensic_weighting_emphasizes_strong_pregame_signal_regime_without_labels():
    values = frame()
    intensity = forensic_intensity(values, admitted())
    assert np.isfinite(intensity).all()
    assert intensity.iloc[1] > intensity.iloc[0]
    weights = sample_weights(values, admitted(), .5)
    assert (weights >= 1.0).all()
    assert weights.iloc[1] > weights.iloc[0]
    with_labels = values.assign(home_win=[0, 1])
    np.testing.assert_allclose(
        sample_weights(with_labels, admitted(), .5), weights, atol=0, rtol=0)


def test_missing_forensic_inputs_remain_missing_not_zero_signal():
    values = frame()
    values.loc[1, ['home_lineup_ops_7d', 'away_lineup_ops_7d']] = np.nan
    intensity = forensic_intensity(values, admitted())
    assert np.isfinite(intensity).all()
    # Other observed pregame components still contribute; missing lineup values
    # are excluded from the row denominator rather than interpreted as zero.
    assert intensity.iloc[1] > 0
