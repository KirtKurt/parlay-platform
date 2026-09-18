import numpy as np
import pandas as pd

from ks1.forensic_features import CONTRACT, admit, derive_frame, derive_mapping


def _row(offset=0.0):
    return {
        'home_individual_bullpen_rank1_fip_7d': 4.5 + offset,
        'home_individual_bullpen_rank1_fip_15d': 4.1 + offset,
        'home_individual_bullpen_rank1_fip_30d': 3.5,
        'away_individual_bullpen_rank1_fip_7d': 2.9 - offset,
        'away_individual_bullpen_rank1_fip_15d': 3.2 - offset,
        'away_individual_bullpen_rank1_fip_30d': 3.7,
        'home_individual_bullpen_rank1_era_7d': 5.0 + offset,
        'home_individual_bullpen_rank1_era_15d': 4.6 + offset,
        'home_individual_bullpen_rank1_era_30d': 4.0,
        'away_individual_bullpen_rank1_era_7d': 3.0 - offset,
        'away_individual_bullpen_rank1_era_15d': 3.3 - offset,
        'away_individual_bullpen_rank1_era_30d': 3.8,
        'home_individual_bullpen_rank1_k_bb_pct_7d': 18.0 + offset,
        'home_individual_bullpen_rank1_k_bb_pct_15d': 16.0 + offset,
        'home_individual_bullpen_rank1_k_bb_pct_30d': 12.0,
        'away_individual_bullpen_rank1_k_bb_pct_7d': 8.0 - offset,
        'away_individual_bullpen_rank1_k_bb_pct_15d': 10.0 - offset,
        'away_individual_bullpen_rank1_k_bb_pct_30d': 13.0,
    }


def test_individual_reliever_regime_features_use_same_rank_and_strict_prior_windows():
    derived = derive_mapping(_row())
    assert CONTRACT == 'KS1-forensic-derived-features-v6'
    assert np.isclose(
        derived['forensic_home_individual_bullpen_rank1_fip_7d_regime_delta'], 1.0
    )
    assert np.isclose(
        derived['forensic_home_individual_bullpen_rank1_fip_15d_regime_delta'], 0.6
    )
    assert np.isclose(
        derived['forensic_away_individual_bullpen_rank1_fip_7d_regime_delta'], -0.8
    )
    assert np.isclose(
        derived['forensic_home_individual_bullpen_rank1_k_bb_pct_7d_regime_delta'], 6.0
    )
    assert np.isclose(
        derived['forensic_away_individual_bullpen_rank1_k_bb_pct_15d_regime_delta'], -3.0
    )
    assert derive_mapping({**_row(), 'home_win': 1}) == derive_mapping({**_row(), 'home_win': 0})


def test_regime_features_are_development_only_and_fail_closed_on_missing_parent():
    frame = pd.DataFrame([_row(0.0), _row(0.1), _row(0.2)])
    admitted, rejected, derived = admit(frame, admitted_raw=[], minimum_nonmissing=3)
    name = 'forensic_home_individual_bullpen_rank1_fip_7d_regime_delta'
    assert name in admitted
    assert name not in rejected
    assert np.allclose(derived[name].to_numpy(), [1.0, 1.1, 1.2])

    missing = frame.drop(columns=['home_individual_bullpen_rank1_fip_30d'])
    admitted2, rejected2, derived2 = admit(missing, admitted_raw=[], minimum_nonmissing=3)
    assert name not in admitted2
    assert derived2[name].isna().all()
    assert any(
        reason.startswith('development_parent_unavailable:')
        for reason in rejected2[name]['reasons']
    )


def test_frame_and_mapping_paths_match_for_regime_features():
    frame = pd.DataFrame([_row(0.0), _row(0.25)])
    derived = derive_frame(frame)
    for index, row in frame.iterrows():
        mapping = derive_mapping(row.to_dict())
        for name in (
            'forensic_home_individual_bullpen_rank1_era_7d_regime_delta',
            'forensic_away_individual_bullpen_rank1_era_15d_regime_delta',
            'forensic_home_individual_bullpen_rank1_k_bb_pct_15d_regime_delta',
        ):
            assert np.isclose(derived.loc[index, name], mapping[name])
