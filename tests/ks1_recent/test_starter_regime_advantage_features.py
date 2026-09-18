import numpy as np
import pandas as pd

from ks1.forensic_features import CONTRACT, admit, derive_frame, derive_mapping


def _row(offset=0.0):
    return {
        'home_starter_fip_7d': 3.0 + offset,
        'home_starter_fip_30d': 4.0,
        'away_starter_fip_7d': 5.0 - offset,
        'away_starter_fip_30d': 4.5,
        'home_starter_era_7d': 3.5 + offset,
        'home_starter_era_30d': 4.5,
        'away_starter_era_7d': 5.5 - offset,
        'away_starter_era_30d': 4.8,
        'home_starter_xwoba_7d': 0.280 + offset / 100.0,
        'home_starter_xwoba_30d': 0.320,
        'away_starter_xwoba_7d': 0.350 - offset / 100.0,
        'away_starter_xwoba_30d': 0.330,
    }


def test_symmetric_starter_regime_advantages_are_home_oriented_and_label_free():
    derived = derive_mapping(_row())
    assert CONTRACT == 'KS1-forensic-derived-features-v7'
    assert np.isclose(derived['forensic_starter_fip_regime_advantage'], 1.5)
    assert np.isclose(derived['forensic_starter_era_regime_advantage'], 1.7)
    assert np.isclose(derived['forensic_starter_xwoba_regime_advantage'], 0.06)
    assert derive_mapping({**_row(), 'home_win': 1}) == derive_mapping({**_row(), 'home_win': 0})


def test_symmetric_starter_regimes_require_admitted_pregame_parents_and_fail_closed():
    frame = pd.DataFrame([_row(0.0), _row(0.1), _row(0.2)])
    parents = set(frame.columns)
    admitted, rejected, derived = admit(frame, admitted_raw=parents, minimum_nonmissing=3)
    name = 'forensic_starter_fip_regime_advantage'
    assert name in admitted
    assert name not in rejected
    assert np.allclose(derived[name].to_numpy(), [1.5, 1.3, 1.1])

    missing = frame.drop(columns=['away_starter_fip_30d'])
    admitted2, rejected2, derived2 = admit(
        missing,
        admitted_raw=parents - {'away_starter_fip_30d'},
        minimum_nonmissing=3,
    )
    assert name not in admitted2
    assert derived2[name].isna().all()
    assert any(
        reason.startswith('parent_not_admitted:')
        for reason in rejected2[name]['reasons']
    )


def test_frame_and_mapping_paths_match_for_symmetric_starter_regimes():
    frame = pd.DataFrame([_row(0.0), _row(0.25)])
    derived = derive_frame(frame)
    for index, row in frame.iterrows():
        mapping = derive_mapping(row.to_dict())
        for name in (
            'forensic_starter_fip_regime_advantage',
            'forensic_starter_era_regime_advantage',
            'forensic_starter_xwoba_regime_advantage',
        ):
            assert np.isclose(derived.loc[index, name], mapping[name])
