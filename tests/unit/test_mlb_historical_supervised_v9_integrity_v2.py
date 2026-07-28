from __future__ import annotations

from pathlib import Path

import pytest

from hello_world import mlb_historical_supervised_v9 as learner
from hello_world import mlb_historical_supervised_v9_integrity_v2 as patch
from hello_world import mlb_v7_integrity_pattern_v1 as integrity


def test_patch_adds_pattern_complete_feature_contract():
    patch.install(learner)
    assert learner.VERSION == patch.MODEL_VERSION
    assert learner.FEATURE_VERSION == patch.FEATURE_VERSION
    assert all(name in learner.FEATURES for name in patch.EXTRA_FEATURES)
    assert len(learner.FEATURES) == len(set(learner.FEATURES))
    assert learner.INTEGRITY_PATCH_VERSION == patch.VERSION


def test_v8_fallback_reads_immutable_expansion_payload():
    patch.install(learner)
    home = {
        "side": "home",
        "team": "Home Club",
        "oddsMarketExpansionFeatures": {
            "h2h_Home_ClubMedianImpliedProbability": 0.61,
            "h2h_1st_5_innings_Home_ClubMedianImpliedProbability": 0.59,
            "spreads_Home_ClubMedianPoint": -1.5,
            "spreads_1st_5_innings_Home_ClubMedianPoint": -0.5,
            "homeStarterBullpenSpreadDivergence": 0.35,
        },
    }
    away = {
        "marketSide": "away",
        "teamName": "Away Club",
        "oddsMarketExpansionFeatures": {
            "h2h_Away_ClubMedianImpliedProbability": 0.43,
            "homeStarterBullpenSpreadDivergence": 0.35,
        },
    }
    assert learner._v8(home, "h2hMedianImpliedProbability") == pytest.approx(0.61)
    assert learner._v8(home, "firstFiveH2HMedianImpliedProbability") == pytest.approx(0.59)
    assert learner._v8(home, "fullGameSpreadMedian") == pytest.approx(-1.5)
    assert learner._v8(home, "firstFiveSpreadMedian") == pytest.approx(-0.5)
    assert learner._v8(home, "starterBullpenSpreadDivergence") == pytest.approx(0.35)
    assert learner._v8(away, "starterBullpenSpreadDivergence") == pytest.approx(-0.35)
    assert learner._v8(away, "h2hMedianImpliedProbability") == pytest.approx(0.43)


def test_v8_fallback_rejects_non_finite_values():
    signal = {
        "team": "Home Club",
        "oddsMarketExpansionFeatures": {
            "h2h_Home_ClubMedianImpliedProbability": "nan",
            "homeStarterBullpenSpreadDivergence": float("inf"),
        },
    }
    assert patch._fallback_v8(signal, "h2hMedianImpliedProbability") is None
    assert patch._fallback_v8(signal, "starterBullpenSpreadDivergence") is None


def test_strict_binary_label_never_coerces_missing_to_away_win():
    with pytest.raises(ValueError, match="invalid_or_missing_binary_label"):
        integrity.strict_binary_label({"homeWon": None})
    with pytest.raises(ValueError, match="invalid_or_missing_binary_label"):
        integrity.strict_binary_label({})
    assert integrity.strict_binary_label({"homeWon": 0}) == 0
    assert integrity.strict_binary_label({"homeWon": 1}) == 1


def test_v9_search_fails_closed_on_empty_input():
    class Optimizer:
        VERSION = "test"

        def __init__(self):
            self.search_called = False

        def search(self, records, config=None, *, untouched_holdout_dates=None):
            self.search_called = True
            return {"ok": True}

    optimizer = Optimizer()
    patch.install(learner)
    learner.install(optimizer, object())
    result = optimizer.search([])
    assert result["ok"] is False
    assert result["status"] == "DATA_INTEGRITY_BLOCKED"
    assert "training_data_empty" in result["promotionGate"]["errors"]
    assert "no_integrity_eligible_training_rows" in result["promotionGate"]["errors"]
    assert optimizer.search_called is False


def test_runtime_installs_integrity_before_supervised_search():
    source = Path("hello_world/mlb_historical_optimizer_v7_recovery_entrypoint.py").read_text()
    dataset_install = "supervised_v8_dataset.install(base.optimizer_handler.optimizer, rematerialization)"
    integrity_install = "supervised_integrity_v2.install(supervised_v9)"
    supervised_install = "supervised_v9.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)"
    assert dataset_install in source
    assert integrity_install in source
    assert supervised_install in source
    assert source.index(dataset_install) < source.index(integrity_install) < source.index(supervised_install)
    assert "promotionRequiresEverySlateAtLeast80Pct" in source
    assert '"productionAuthorityChanged": False' in source


def test_shadow_evaluator_requires_integrity_diagnostics():
    source = Path("scripts/run_mlb_historical_supervised_v9_shadow.py").read_text()
    wrapper = Path("scripts/run_mlb_historical_supervised_v9_shadow_v2.py").read_text()
    assert "integrity_v2.install(supervised_v9)" in source
    assert "strict_binary_label_contract_missing" in source
    assert "training_integrity_rejected_rows" in source
    assert '"providerCallsMade": 0' in source
    assert '"productionAuthorityChanged": False' in source
    assert "v8_expansion_fallback_not_enabled" in wrapper
    assert "integrityEnforcedByWrapper" in wrapper
