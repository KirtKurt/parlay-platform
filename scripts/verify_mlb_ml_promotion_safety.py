#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_champion_challenger_v1 as champion
import mlb_ml_champion_runtime_v1 as runtime
import mlb_accuracy_target_policy_v1 as accuracy_policy


def _threshold():
    return {
        "ok": True,
        "threshold": 0.7,
        "selectedCount": 100,
        "coveragePct": 50.0,
        "accuracyPct": 100.0,
        "selectionSource": "validation_only",
    }


def _outcome_model(version="outcome-latest"):
    return {"ok": True, "version": version, "target": "homeWon"}


def _reliability_model(version="reliability-latest", valid_threshold=True):
    model = {"ok": True, "version": version, "target": "pickCorrect"}
    if valid_threshold:
        model["selectedThreshold"] = _threshold()
        model["thresholdSelectedOnValidationOnly"] = True
    return model


def _trained_dual(outcome=True, reliability=True, valid_threshold=True):
    threshold = _threshold()
    return {
        "ok": bool(outcome and reliability),
        "status": "CHALLENGER_TRAINED_AWAITING_SEPARATE_PROMOTION_GATES",
        "outcomeModel": _outcome_model() if outcome else {"ok": False, "reason": "no outcome"},
        "reliabilityModel": _reliability_model(valid_threshold=valid_threshold) if reliability else {"ok": False, "reason": "no reliability"},
        "split": {"counts": {"train": 300, "validation": 100, "test": 100}},
        "testWasUntouchedDuringFitAndThresholdSelection": True,
        "validation": {"selectedReliability": threshold},
        "untouchedTest": {
            "outcome": {
                "count": 100,
                "accuracyPct": 90.0,
                "accuracyLiftPctPoints": 2.0,
                "brierSkillPct": 1.0,
                "logLoss": 0.60,
                "calibrationError": 0.05,
                "baseline": {"logLoss": 0.65},
            },
            "selectedReliability": {
                "count": 100,
                "accuracyPct": 90.0,
                "exactOddsCoveragePct": 90.0,
                "calibrationError": 0.05,
            },
        },
        "dataQuality": {
            "modelScope": "MARKET_MOVEMENT_ONLY_WITH_MISSINGNESS",
            "averageFundamentalsCompletenessPct": 10.0,
        },
    }


def _runtime_row():
    return {
        "id": "game-1",
        "gameId": "game-1",
        "homeTeam": "Home",
        "awayTeam": "Away",
        "predictedWinner": "Home",
        "predictedSide": "home",
        "score": 80.0,
        "americanOdds": -150,
        "lockedAmericanOdds": -150,
        "priceBook": "draftkings",
        "priceSource": "real_book",
        "homeSignal": {"score": 80.0, "marketConsensusProbability": 0.6, "delta": 0.02, "bookDivergence": 0.01, "reversalCount": 0, "americanOdds": -150, "priceBook": "draftkings", "priceSource": "real_book"},
        "awaySignal": {"score": 25.0, "marketConsensusProbability": 0.4, "delta": -0.02, "bookDivergence": 0.01, "reversalCount": 1, "americanOdds": 130, "priceBook": "fanduel", "priceSource": "real_book"},
        "frozenFeatureVector": {
            "version": "test-vector",
            "features": {
                "homeMarketProb": 0.6,
                "awayMarketProb": 0.4,
                "homeDelta": 0.02,
                "awayDelta": -0.02,
                "homeBookDivergence": 0.01,
                "awayBookDivergence": 0.01,
                "homeReversalCount": 0,
                "awayReversalCount": 1,
                "selectedScore": 80.0,
            },
        },
    }


def main() -> int:
    policy = accuracy_policy.install()
    assert policy["ok"] is True
    assert champion.VERSION == accuracy_policy.CHAMPION_GATE_VERSION
    assert "v1.5" in champion.VERSION and "90pct" in champion.VERSION
    champion.AUTO_PROMOTE = True
    result = champion.promote_if_allowed({
        "promotionGate": {
            "promotionDecision": "RETAIN_CURRENT_CHAMPION",
            "directionPromotionEligible": False,
            "playabilityPromotionEligible": False,
        }
    })
    assert result["promoted"] is False
    assert result["reason"] == "automatic_promotion_disabled_or_not_eligible"

    # Outcome and reliability availability must gate only their own authority.
    reliability_only = _trained_dual(outcome=False, reliability=True)
    gate = champion.evaluate(reliability_only, clean_count=500, playable_evidence_count=200, rolling_slate_accuracy_pct=90.0)
    assert gate["directionPromotionEligible"] is False
    assert gate["playabilityPromotionEligible"] is True, gate
    assert "OUTCOME_MODEL_NOT_TRAINED" in {item["code"] for item in gate["directionBlockers"]}

    # Playability is impossible when the threshold was not successfully selected
    # on validation data, even if untouched-test summary numbers look strong.
    invalid_threshold = _trained_dual(outcome=True, reliability=True, valid_threshold=False)
    gate = champion.evaluate(invalid_threshold, clean_count=500, playable_evidence_count=200, rolling_slate_accuracy_pct=90.0)
    assert gate["directionPromotionEligible"] is True, gate
    assert gate["playabilityPromotionEligible"] is False
    assert "RELIABILITY_THRESHOLD_NOT_VALIDATION_SELECTED" in {item["code"] for item in gate["playabilityBlockers"]}

    valid = _trained_dual(outcome=True, reliability=True, valid_threshold=True)
    gate = champion.evaluate(valid, clean_count=500, playable_evidence_count=200, rolling_slate_accuracy_pct=90.0)
    assert gate["directionPromotionEligible"] is True
    assert gate["playabilityPromotionEligible"] is True, gate
    assert gate["playabilityChecks"]["reliabilityThresholdValidated"] is True

    # Automatic promotion replaces only model artifacts whose independent gates
    # passed and preserves the other authority's incumbent model.
    latest = {
        "promotionGate": {"directionPromotionEligible": True, "playabilityPromotionEligible": True},
        "dualModel": {
            "outcomeModel": _outcome_model("outcome-latest"),
            "reliabilityModel": _reliability_model("reliability-latest"),
        },
    }
    current = {
        "directionAuthorityEnabled": False,
        "playabilityAuthorityEnabled": True,
        "dualModel": {
            "outcomeModel": _outcome_model("outcome-current-shadow"),
            "reliabilityModel": _reliability_model("reliability-current"),
        },
    }
    captured = {}
    original_loader = champion.load_champion
    original_store = champion._store_champion
    try:
        champion.load_champion = lambda: current

        def capture_store(payload, approval_mode):
            captured["writeCount"] = int(captured.get("writeCount") or 0) + 1
            captured["payload"] = copy.deepcopy(payload)
            captured["approvalMode"] = approval_mode
            return {"ok": True, "promoted": True}

        champion._store_champion = capture_store

        champion.load_champion = lambda: {}
        fully_eligible_bundle = copy.deepcopy(latest)
        fully_eligible_bundle["promotionGate"] = gate
        fully_promoted = champion.promote_if_allowed(fully_eligible_bundle)
        assert fully_promoted["promoted"] is True
        assert captured["payload"]["directionAuthorityEnabled"] is True
        assert captured["payload"]["playabilityAuthorityEnabled"] is True

        champion.load_champion = lambda: current
        direction_bundle = copy.deepcopy(latest)
        direction_bundle["promotionGate"] = {
            "promotionDecision": "PROMOTE",
            "directionPromotionEligible": True,
            "playabilityPromotionEligible": False,
        }
        promoted = champion.promote_if_allowed(direction_bundle)
        assert promoted["promoted"] is True
        payload = captured["payload"]
        assert payload["dualModel"]["outcomeModel"]["version"] == "outcome-latest"
        assert payload["dualModel"]["reliabilityModel"]["version"] == "reliability-current"
        assert payload["playabilityAuthorityEnabled"] is True
        assert payload["partialPromotionModelIsolationApplied"] is True
        assert captured["approvalMode"] == "automatic_gate_promotion"

        current_playability = {
            "directionAuthorityEnabled": True,
            "playabilityAuthorityEnabled": False,
            "dualModel": {
                "outcomeModel": _outcome_model("outcome-current"),
                "reliabilityModel": _reliability_model("reliability-current-shadow"),
            },
        }
        champion.load_champion = lambda: current_playability
        playability_bundle = copy.deepcopy(latest)
        playability_bundle["promotionGate"] = {
            "promotionDecision": "PROMOTE",
            "directionPromotionEligible": False,
            "playabilityPromotionEligible": True,
        }
        promoted = champion.promote_if_allowed(playability_bundle)
        assert promoted["promoted"] is True
        payload = captured["payload"]
        assert payload["dualModel"]["outcomeModel"]["version"] == "outcome-current"
        assert payload["dualModel"]["reliabilityModel"]["version"] == "reliability-latest"
        assert payload["directionAuthorityEnabled"] is True

        # A forged PROMOTE decision with no passed authority is rejected before
        # any champion write.
        no_gate = champion.promote_if_allowed({
            "promotionGate": {
                "promotionDecision": "PROMOTE",
                "directionPromotionEligible": False,
                "playabilityPromotionEligible": False,
            }
        })
        assert no_gate["promoted"] is False and no_gate["reason"] == "no_applicable_promotion_gate_passed"

        # Payload validation remains fail-closed even when a gate claims success.
        unsafe = copy.deepcopy(playability_bundle)
        unsafe["dualModel"]["reliabilityModel"] = _reliability_model("unsafe", valid_threshold=False)
        unsafe["reliabilityModel"] = unsafe["dualModel"]["reliabilityModel"]
        rejected = champion.promote_if_allowed(unsafe)
        assert rejected["promoted"] is False
        assert "playability_authority_requires_validation_selected_reliability_threshold" in rejected["safetyErrors"]

        # A previously enabled champion must be suspended when the common
        # rolling official-card slate prerequisite falls into a 50-80 shadow band.
        enabled_current = {
            "directionAuthorityEnabled": True,
            "playabilityAuthorityEnabled": True,
            "dualModel": {
                "outcomeModel": _outcome_model("enabled-outcome"),
                "reliabilityModel": _reliability_model("enabled-reliability"),
            },
        }
        champion.load_champion = lambda: enabled_current
        low_rolling_gate = champion.evaluate(
            valid,
            clean_count=500,
            playable_evidence_count=200,
            rolling_slate_accuracy_pct=80.0,
        )
        suspended = champion.promote_if_allowed({"promotionGate": low_rolling_gate})
        assert suspended["suspended"] is True and suspended["promoted"] is False
        suspended_payload = captured["payload"]
        assert suspended_payload["directionAuthorityEnabled"] is False
        assert suspended_payload["playabilityAuthorityEnabled"] is False
        assert suspended_payload["dualModel"] == enabled_current["dualModel"]
        assert suspended_payload["automaticAuthoritySuspension"]["preservedModelArtifactsForShadow"] is True

        # Local/ad-hoc runs keep the fail-safe default and must never write even
        # when the same rolling-slate suspension condition is observed.
        writes_before = captured["writeCount"]
        champion.AUTO_PROMOTE = False
        local = champion.promote_if_allowed({"promotionGate": low_rolling_gate})
        assert local["promoted"] is False and local.get("suspended") is not True
        assert captured["writeCount"] == writes_before
        champion.AUTO_PROMOTE = True
    finally:
        champion.load_champion = original_loader
        champion._store_champion = original_store

    # Runtime models remain independently usable. A champion direction flip
    # recomputes selectedScore for shadow reliability, but cannot become playable
    # because reliability and exact selected-side odds were validated on the
    # incumbent selections, not on a direction-flipped side.
    original_loader = runtime.champion_store.load_champion
    original_score = runtime.dual_model.score
    reliability_records = []
    try:
        both_champion = {
            "directionAuthorityEnabled": True,
            "playabilityAuthorityEnabled": True,
            "outcomeModel": _outcome_model("runtime-outcome"),
            "reliabilityModel": _reliability_model("runtime-reliability"),
        }
        runtime.champion_store.load_champion = lambda: both_champion

        def fake_score(record, model):
            if model.get("version") == "runtime-outcome":
                return 0.4
            reliability_records.append(copy.deepcopy(record))
            return 0.95

        runtime.dual_model.score = fake_score
        result = runtime.enhance_result({"predictions": [_runtime_row()]})
        row = result["predictions"][0]
        assert row["predictedSide"] == "away" and row["score"] == 25.0
        assert row["americanOdds"] == 130.0 and row["lockedAmericanOdds"] == 130.0
        assert row["priceBook"] == "fanduel" and row["priceSource"] == "real_book"
        assert reliability_records[-1]["selectedScore"] == 25.0
        assert row["optimizedPickReliabilityPct"] == 95.0
        assert row["championPlayable"] is False
        assert "direction_flip_not_validated_for_reliability_or_selected_side_exact_odds" in row["championPlayabilitySafetyReasons"]
        assert result["mlOptimizationRuntime"]["directionFlipPlayabilityBlockedCount"] == 1

        outcome_only = {
            "directionAuthorityEnabled": True,
            "playabilityAuthorityEnabled": False,
            "outcomeModel": _outcome_model("runtime-outcome"),
        }
        runtime.champion_store.load_champion = lambda: outcome_only
        result = runtime.enhance_result({"predictions": [_runtime_row()]})
        assert result["predictions"][0]["predictedSide"] == "away"
        assert result["mlOptimizationRuntime"]["directionAuthorityEnabled"] is True
        assert result["mlOptimizationRuntime"]["reliabilityModelAvailable"] is False

        reliability_only_champion = {
            "directionAuthorityEnabled": False,
            "playabilityAuthorityEnabled": True,
            "reliabilityModel": _reliability_model("runtime-reliability"),
        }
        runtime.champion_store.load_champion = lambda: reliability_only_champion
        result = runtime.enhance_result({"predictions": [_runtime_row()]})
        assert result["predictions"][0]["predictedSide"] == "home"
        assert result["predictions"][0]["championPlayable"] is True
        assert result["mlOptimizationRuntime"]["outcomeModelAvailable"] is False
        assert result["mlOptimizationRuntime"]["playabilityAuthorityEnabled"] is True

        invalid_threshold_champion = copy.deepcopy(reliability_only_champion)
        invalid_threshold_champion["reliabilityModel"] = _reliability_model("runtime-reliability", valid_threshold=False)
        runtime.champion_store.load_champion = lambda: invalid_threshold_champion
        result = runtime.enhance_result({"predictions": [_runtime_row()]})
        assert result["predictions"][0]["championPlayable"] is False
        assert result["mlOptimizationRuntime"]["playabilityAuthorityEnabled"] is False
        assert "playability_authority_requested_without_validation_selected_threshold" in result["mlOptimizationRuntime"]["authoritySafetyErrors"]
    finally:
        runtime.champion_store.load_champion = original_loader
        runtime.dual_model.score = original_score

    print("MLB ML promotion safety verified: 90% automatic gates, independent authority promotion, no early writes, payload validation, and direction-flip fail-closed behavior")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
