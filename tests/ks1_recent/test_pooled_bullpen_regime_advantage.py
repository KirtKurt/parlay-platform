import numpy as np
import pandas as pd

from ks1.forensic_features import admit, derive_frame, derive_mapping


def _row():
    return {
        "home_bullpen_context_fip_7d": 5.1,
        "home_bullpen_context_fip_30d": 3.9,
        "away_bullpen_context_fip_7d": 3.2,
        "away_bullpen_context_fip_30d": 3.8,
        "home_bullpen_context_era_7d": 5.7,
        "home_bullpen_context_era_30d": 4.0,
        "away_bullpen_context_era_7d": 3.0,
        "away_bullpen_context_era_30d": 3.7,
        "home_bullpen_context_k_bb_pct_7d": 18.0,
        "home_bullpen_context_k_bb_pct_30d": 14.0,
        "away_bullpen_context_k_bb_pct_7d": 10.0,
        "away_bullpen_context_k_bb_pct_30d": 12.0,
        "home_bullpen_context_xwoba_7d": 0.290,
        "home_bullpen_context_xwoba_30d": 0.310,
        "away_bullpen_context_xwoba_7d": 0.340,
        "away_bullpen_context_xwoba_30d": 0.320,
    }


def test_pooled_bullpen_regime_advantages_are_symmetric_pregame_transforms():
    row = _row()
    derived = derive_mapping(row)
    assert np.isclose(derived["forensic_bullpen_fip_regime_advantage"], -1.8)
    assert np.isclose(derived["forensic_bullpen_era_regime_advantage"], -2.4)
    assert np.isclose(derived["forensic_bullpen_k_bb_pct_7d_advantage"], 8.0)
    assert np.isclose(derived["forensic_bullpen_xwoba_7d_advantage"], 0.05)
    assert np.isclose(derived["forensic_bullpen_k_bb_pct_regime_advantage"], 6.0)
    assert np.isclose(derived["forensic_bullpen_xwoba_regime_advantage"], 0.04)
    assert derive_mapping({**row, "home_win": 1}) == derive_mapping({**row, "home_win": 0})


def test_pooled_bullpen_regime_advantage_frame_path_and_missingness_fail_closed():
    first = _row()
    second = _row()
    second["away_bullpen_context_fip_30d"] = None
    second["away_bullpen_context_xwoba_30d"] = None
    frame = pd.DataFrame([first, second])
    derived = derive_frame(frame)
    assert np.isclose(derived.loc[0, "forensic_bullpen_fip_regime_advantage"], -1.8)
    assert np.isclose(derived.loc[0, "forensic_bullpen_k_bb_pct_regime_advantage"], 6.0)
    assert np.isclose(derived.loc[0, "forensic_bullpen_xwoba_regime_advantage"], 0.04)
    assert pd.isna(derived.loc[1, "forensic_bullpen_fip_regime_advantage"])
    assert pd.isna(derived.loc[1, "forensic_bullpen_xwoba_regime_advantage"])


def test_pooled_bullpen_regime_advantage_requires_all_declared_admitted_parents():
    rows = []
    for index in range(6):
        row = _row()
        row["away_bullpen_context_fip_7d"] += index / 100
        row["away_bullpen_context_era_7d"] += index / 100
        row["home_bullpen_context_k_bb_pct_7d"] += index / 10
        row["away_bullpen_context_xwoba_7d"] += index / 1000
        rows.append(row)
    frame = pd.DataFrame(rows)
    raw = list(frame.columns)
    admitted, _, _ = admit(frame, raw, minimum_nonmissing=5)
    for feature in (
        "forensic_bullpen_fip_regime_advantage",
        "forensic_bullpen_era_regime_advantage",
        "forensic_bullpen_k_bb_pct_7d_advantage",
        "forensic_bullpen_xwoba_7d_advantage",
        "forensic_bullpen_k_bb_pct_regime_advantage",
        "forensic_bullpen_xwoba_regime_advantage",
    ):
        assert feature in admitted

    missing_parent = [name for name in raw if name != "home_bullpen_context_k_bb_pct_30d"]
    admitted2, rejected2, _ = admit(frame, missing_parent, minimum_nonmissing=5)
    feature = "forensic_bullpen_k_bb_pct_regime_advantage"
    assert feature not in admitted2
    assert any(
        reason.startswith("parent_not_admitted:")
        for reason in rejected2[feature]["reasons"]
    )
