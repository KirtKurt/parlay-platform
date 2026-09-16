import numpy as np
import pandas as pd

from ks1.forensic_features import admit, derive_frame, derive_mapping, groups, raw_parents


def raw_row():
    return {
        'market_home_prob': .62,
        'home_starter_era_7d': 5.5, 'home_starter_era_30d': 3.5,
        'away_starter_era_7d': 2.5, 'away_starter_era_30d': 4.0,
        'home_starter_fip_7d': 4.9, 'home_starter_fip_30d': 3.7,
        'away_starter_fip_7d': 3.0, 'away_starter_fip_30d': 4.1,
        'home_starter_xwoba_7d': .355, 'home_starter_xwoba_30d': .315,
        'away_starter_xwoba_7d': .295, 'away_starter_xwoba_30d': .330,
        'home_pitcher_context_expected_innings': 3.0,
        'away_pitcher_context_expected_innings': 5.8,
        'home_lineup_ops_7d': .710, 'away_lineup_ops_7d': .820,
        'home_lineup_ops_30d': .760, 'away_lineup_ops_30d': .770,
        'home_lineup_xwoba_7d': .295, 'away_lineup_xwoba_7d': .345,
        'home_lineup_xwoba_30d': .320, 'away_lineup_xwoba_30d': .318,
        'home_lineup_top4_ops': .735, 'away_lineup_top4_ops': .875,
        'home_bullpen_context_fip_7d': 5.1, 'away_bullpen_context_fip_7d': 3.2,
        'home_bullpen_context_fip_30d': 3.9, 'away_bullpen_context_fip_30d': 3.8,
        'home_bullpen_context_era_7d': 5.7, 'away_bullpen_context_era_7d': 3.0,
        'home_bullpen_context_era_30d': 4.0, 'away_bullpen_context_era_30d': 3.7,
        'home_bullpen_context_available_count': 3.0,
        'away_bullpen_context_available_count': 7.0,
    }


def test_derived_features_are_signed_label_free_pregame_values():
    row = raw_row()
    derived = derive_mapping(row)
    assert derived['forensic_market_home_strength'] == .12
    assert derived['forensic_home_starter_era_regime_delta'] == 2.0
    assert derived['forensic_away_starter_era_regime_delta'] == -1.5
    assert derived['forensic_home_starter_short_exposure'] == 1.5
    assert derived['forensic_starter_expected_innings_advantage'] == -2.8
    assert np.isclose(derived['forensic_lineup_ops_7d_advantage'], -.11)
    assert np.isclose(derived['forensic_bullpen_fip_7d_advantage'], -1.9)
    assert derived_mapping_with_outcome(row, 1) == derived_mapping_with_outcome(row, 0)


def derived_mapping_with_outcome(row, outcome):
    return derive_mapping({**row, 'home_win': outcome, 'home_score': 99-outcome})


def test_frame_and_mapping_paths_match_and_missing_parents_fail_closed():
    first = raw_row()
    second = raw_row()
    second['home_lineup_ops_7d'] = None
    frame = pd.DataFrame([first, second])
    derived = derive_frame(frame)
    for name, value in derive_mapping(first).items():
        assert np.isclose(derived.loc[0, name], value, equal_nan=True)
    assert pd.isna(derived.loc[1, 'forensic_lineup_ops_7d_advantage'])
    assert pd.isna(derived.loc[1, 'forensic_home_lineup_ops_regime_delta'])


def test_admission_requires_raw_parent_admission_and_nonmissing_floor():
    rows = []
    for index in range(6):
        row = raw_row()
        row['market_home_prob'] += index / 1000
        row['home_starter_era_7d'] += index / 100
        rows.append(row)
    frame = pd.DataFrame(rows)
    admitted_raw = list(frame.columns)
    admitted, rejected, _ = admit(frame, admitted_raw, minimum_nonmissing=5)
    assert 'forensic_market_home_strength' in admitted
    assert 'forensic_home_starter_era_regime_delta' in admitted
    without_market = [column for column in admitted_raw if column != 'market_home_prob']
    admitted2, rejected2, _ = admit(frame, without_market, minimum_nonmissing=5)
    assert 'forensic_market_home_strength' not in admitted2
    assert any(reason.startswith('parent_not_admitted:')
               for reason in rejected2['forensic_market_home_strength']['reasons'])


def test_groups_and_parent_receipts_cover_all_five_signal_families():
    frame = pd.DataFrame([raw_row(), {**raw_row(), 'market_home_prob': .64}])
    admitted, _, _ = admit(frame, list(frame.columns), minimum_nonmissing=2)
    grouped = groups(admitted)
    assert set(grouped) == {'market', 'starter_regime', 'starter_workload', 'lineup', 'bullpen'}
    assert all(grouped.values())
    parents = raw_parents(admitted)
    assert 'market_home_prob' in parents
    assert 'home_pitcher_context_expected_innings' in parents
    assert 'home_lineup_ops_7d' in parents
    assert 'home_bullpen_context_fip_7d' in parents
