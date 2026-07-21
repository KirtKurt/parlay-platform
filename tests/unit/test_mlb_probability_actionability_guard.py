from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import inqsi_pull_history as history
import mlb_prediction_probability_contract_v1 as probability_contract
import mlb_probability_actionability_guard as guard


def _row(*, strong: bool, shadow: dict | None = None) -> dict:
    probability = 0.80 if strong else 0.55
    score = 80.0 if strong else 55.0
    pulls = 30 if strong else 5
    market = 0.75 if strong else 0.53
    row = {
        "gameId": "777001",
        "gameIdentity": "777001",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedSide": "home",
        "predictedWinner": "Home Club",
        "opponent": "Away Club",
        "score": score,
        "winProbability": probability,
        "pullCountForGame": pulls,
        "tags": ["BOOK_AGREEMENT"],
        "homeSignal": {
            "side": "home",
            "team": "Home Club",
            "marketProbability": market,
            "probLatest": market,
            "score": score,
            "bookDivergence": 0.01,
            "reversalCount": 0,
        },
        "awaySignal": {
            "side": "away",
            "team": "Away Club",
            "marketProbability": round(1.0 - market, 4),
            "probLatest": round(1.0 - market, 4),
            "score": round(100.0 - score, 2),
            "bookDivergence": 0.01,
            "reversalCount": 0,
        },
        "providerShadowContext": copy.deepcopy(shadow),
        "homeModelWinProbability": probability,
        "awayModelWinProbability": round(1.0 - probability, 4),
        "homeMarketDeVigProbability": market,
        "awayMarketDeVigProbability": round(1.0 - market, 4),
        "predictionSourcePullAt": "2026-07-21T20:00:00+00:00",
        "predictionSourcePullId": "pull-1",
        "predictionSourceCanonicalSlot": {
            "version": history.PULL_SLOT_VERSION,
            "canonicalPullFingerprint": "f" * 64,
        },
        "playable": True,
        "trainingEligible": True,
    }
    row["homeSignal"].update(
        {
            "americanOdds": -125,
            "priceBook": "fanduel",
            "priceSource": "real_book",
        }
    )
    row["awaySignal"].update(
        {
            "americanOdds": 115,
            "priceBook": "fanduel",
            "priceSource": "real_book",
        }
    )
    return probability_contract.normalize_row(row)


def test_guard_preserves_direction_and_signal_payloads() -> None:
    original = _row(strong=True, shadow={"arbitrary": "must-not-score"})
    result = guard.guard_prediction(original)

    assert result["predictedSide"] == original["predictedSide"]
    assert result["predictedWinner"] == original["predictedWinner"]
    assert result["opponent"] == original["opponent"]
    assert result["homeSignal"] == original["homeSignal"]
    assert result["awaySignal"] == original["awaySignal"]
    assert result["providerShadowContext"] == original["providerShadowContext"]
    assert result["calibration"]["fundamentalsBoost"] == 0.0
    assert result["winnerOptimizer"]["fundamentalsApplied"] is False
    assert result["fundamentalsLayer"]["applied"] is False


def test_shadow_payload_cannot_change_calibration_or_actionability() -> None:
    without_shadow = guard.guard_prediction(_row(strong=True))
    with_shadow = guard.guard_prediction(
        _row(
            strong=True,
            shadow={
                "predictedWinner": "Away Club",
                "winProbability": 0.999,
                "fundamentalsComplete": True,
            },
        )
    )
    for key in (
        "predictedSide",
        "predictedWinner",
        "winProbability",
        "officialPick",
        "actionablePick",
        "actionability",
    ):
        assert with_shadow[key] == without_shadow[key]


def test_strong_market_row_remains_actionable_after_fallback_calibration() -> None:
    result = guard.guard_prediction(_row(strong=True))

    assert result["winProbability"] == pytest.approx(0.80)
    assert result["calibratedWinProbability"] == pytest.approx(0.7419)
    assert result["calibratedWinProbabilityPct"] == pytest.approx(74.19)
    assert result["calibration"]["riskPenalty"] == pytest.approx(0.02)
    assert result["calibration"]["riskReasons"] == ["MISSING_FUNDAMENTALS"]
    assert result["officialPick"] is False
    assert result["actionablePick"] is True
    assert result["playable"] is True
    assert result["playablePick"] is True
    assert result["playabilityStatus"] == "PLAYABLE"
    assert result["accuracyTargetEligible"] is True
    assert result["playableAccuracyEligible"] is False
    assert result["actionability"] == "STRONG_ACTIONABLE_PICK"
    assert "ACTIONABLE_PICK" in result["tags"]
    assert "PLAYABLE_PREDICTION" in result["tags"]
    assert "NOT_PLAYABLE" not in result["tags"]


def test_weak_low_depth_row_remains_visible_but_not_actionable() -> None:
    result = guard.guard_prediction(_row(strong=False))

    assert result["predictedWinner"] == "Home Club"
    assert result["officialPick"] is False
    assert result["actionablePick"] is False
    assert result["playable"] is False
    assert result["playablePick"] is False
    assert result["playabilityStatus"] == "NOT_PLAYABLE"
    assert result["accuracyTargetEligible"] is False
    assert result["actionability"] == "NO_PICK"
    assert "needs_more_pull_depth" in result["pickDiscipline"]["noPickReasons"]
    assert "MISSING_FUNDAMENTALS" in result["tags"]
    assert "NO_PICK" in result["tags"]
    assert "ACTIONABLE_PICK" not in result["tags"]
    assert "PLAYABLE_PREDICTION" not in result["tags"]


def test_official_lock_semantics_are_independent_from_actionability() -> None:
    prelock = _row(strong=True)
    prelock.update({"officialPrediction": False, "officialPick": False})
    guarded_prelock = guard.guard_prediction(prelock)

    assert guarded_prelock["officialPrediction"] is False
    assert guarded_prelock["officialPick"] is False
    assert guarded_prelock["actionablePick"] is True

    locked = _row(strong=False)
    locked.update({"officialPrediction": True, "officialPick": True})
    guarded_locked = guard.guard_prediction(locked)

    assert guarded_locked["officialPrediction"] is True
    assert guarded_locked["officialPick"] is True
    assert guarded_locked["actionablePick"] is False
    assert guarded_locked["playable"] is False
    assert guarded_locked["recommendationStatus"] == (
        "OFFICIAL_PREDICTION_NOT_PLAYABLE"
    )


@pytest.mark.parametrize(
    ("updates", "expected_reason"),
    [
        ({"blockedReasons": ["engine_market_block"]}, "engine_market_block"),
        (
            {
                "predictionReleaseBlocked": True,
                "predictionBlockReason": "prediction_release_policy",
            },
            "prediction_release_policy",
        ),
        (
            {"wagerReleaseBlockReasons": ["wager_release_policy"]},
            "wager_release_policy",
        ),
        (
            {
                "predictionIntentionallyBlocked": True,
                "predictionBlockReason": "intentional_policy_hold",
            },
            "intentional_policy_hold",
        ),
        (
            {
                "predictionBlockStatus": "INTENTIONAL_POLICY_BLOCK",
                "predictionBlockReason": "status_policy_hold",
            },
            "status_policy_hold",
        ),
        (
            {"hardConfidenceBlockers": ["hard_confidence_stop"]},
            "hard_confidence_stop",
        ),
        (
            {"contextActionabilityBlockers": ["context_release_stop"]},
            "context_release_stop",
        ),
        ({"tags": ["BOOK_AGREEMENT", "RELEASE_BLOCKED"]}, "RELEASE_BLOCKED"),
        (
            {"tags": ["BOOK_AGREEMENT", "WAGER_RELEASE_BLOCKED"]},
            "WAGER_RELEASE_BLOCKED",
        ),
    ],
)
def test_every_upstream_release_channel_fails_closed_and_preserves_reason(
    updates: dict,
    expected_reason: str,
) -> None:
    row = _row(strong=True)
    row.update(copy.deepcopy(updates))

    result = guard.guard_prediction(row)

    assert result["officialPick"] is False
    assert result["actionablePick"] is False
    assert result["playable"] is False
    assert result["playabilityStatus"] == "BLOCKED"
    assert "upstream_release_blocked" in result["pickDiscipline"][
        "mandatoryBlockReasons"
    ]
    assert expected_reason in result["pickDiscipline"][
        "upstreamReleaseBlockReasons"
    ]
    for field in (
        "blockedReasons",
        "releaseBlockReasons",
        "playabilityBlockReasons",
        "wagerReleaseBlockReasons",
    ):
        assert expected_reason in result[field]
    assert "RELEASE_BLOCKED" in result["tags"]
    assert "ACTIONABLE_PICK" not in result["tags"]


def test_apply_is_idempotent_and_reports_provider_neutral_policy() -> None:
    module = SimpleNamespace(
        predict_all=lambda: {
            "predictions": [_row(strong=False), _row(strong=True)],
            "modelVersion": "base",
        }
    )
    guard.apply(module)
    first = module.predict_all
    guard.apply(module)

    assert module.predict_all is first
    result = module.predict_all()
    assert result["count"] == 2
    assert result["predictions"][0]["actionablePick"] is True
    assert result["predictions"][0]["rank"] == 1
    assert result["actionablePickCount"] == 1
    assert result["noPickCount"] == 1
    assert result["calibrationPolicy"]["providerNeutral"] is True
    assert result["accuracyTarget"]["providerShadowCanInfluenceLivePick"] is False
    assert result["modelVersion"].endswith(
        "+provider-neutral-calibration-no-pick-v2"
    )


def test_runtime_wiring_and_source_have_no_retired_or_shadow_provider_client() -> None:
    source = Path(guard.__file__).read_text(encoding="utf-8").lower()
    installer = (HELLO / "mlb_ml_runtime_install_v3.py").read_text(
        encoding="utf-8"
    )

    assert "mlb_probability_actionability_guard" in installer
    assert "mlb_probability_actionability_guard.apply" in installer
    assert "mlb_prediction_probability_contract_v1.apply(engine)" in installer
    assert installer.index("mlb_prediction_probability_contract_v1.apply(engine)") < installer.index(
        "mlb_probability_actionability_guard.apply(engine)"
    )
    assert "sportsdataio" not in source
    assert "bigballsdata" not in source
    assert "bbs_api" not in source


def test_probability_contract_and_guard_compose_without_semantic_drift() -> None:
    original = _row(strong=True)
    original["homeSignal"].update(
        {
            "americanOdds": -125,
            "priceBook": "fanduel",
            "priceSource": "real_book",
        }
    )
    original["awaySignal"].update(
        {
            "americanOdds": 115,
            "priceBook": "fanduel",
            "priceSource": "real_book",
        }
    )
    original.update(
        {
            "homeModelWinProbability": 0.80,
            "awayModelWinProbability": 0.20,
            "homeMarketDeVigProbability": 0.75,
            "awayMarketDeVigProbability": 0.25,
            "predictionSourcePullAt": "2026-07-21T20:00:00+00:00",
            "predictionSourcePullId": "pull-1",
            "predictionSourceCanonicalSlot": {
                "version": history.PULL_SLOT_VERSION,
                "canonicalPullFingerprint": "f" * 64
            },
            "americanOdds": -125,
            "priceBook": "fanduel",
            "priceSource": "real_book",
        }
    )
    normalized = probability_contract.normalize_row(original)
    result = guard.guard_prediction(normalized)

    assert probability_contract.validation_errors(result) == []
    assert result["predictedSide"] == "home"
    assert result["winProbability"] == pytest.approx(0.80)
    assert result["modelWinProbability"] == pytest.approx(0.80)
    assert result["calibratedWinProbability"] < result["winProbability"]
    assert result["actionablePick"] is True


def test_direction_correction_remains_blocked_after_actionability_guard() -> None:
    corrected = _row(strong=True)
    corrected.update(
        {
            "probabilityCorrectionApplied": True,
            "playable": False,
            "trainingEligible": False,
            "blocked": True,
            "releaseBlocked": True,
            "wagerReleaseBlocked": True,
            "playabilityStatus": "BLOCKED",
            "probabilityContract": {"errors": []},
        }
    )

    result = guard.guard_prediction(corrected)

    assert result["officialPick"] is False
    assert result["actionablePick"] is False
    assert result["accuracyTargetEligible"] is False
    assert result["actionability"] == "NO_PICK"
    assert result["playabilityStatus"] == "BLOCKED"
    assert "RELEASE_BLOCKED" in result["tags"]
    assert "probability_direction_integrity_correction" in result[
        "pickDiscipline"
    ]["noPickReasons"]
    assert "upstream_release_blocked" in result["pickDiscipline"][
        "noPickReasons"
    ]


def test_guard_reconciles_stale_upstream_nonplayable_aliases() -> None:
    row = _row(strong=True)
    row.update(
        {
            "playable": False,
            "playablePick": False,
            "actionablePick": False,
            "playabilityStatus": "NOT_PLAYABLE",
            "recommendationStatus": "PRE_LOCK_PREDICTION",
            "tags": sorted(set((row.get("tags") or []) + ["NOT_PLAYABLE"])),
        }
    )

    result = guard.guard_prediction(row)

    assert result["playable"] is True
    assert result["playablePick"] is True
    assert result["actionablePick"] is True
    assert result["playabilityStatus"] == "PLAYABLE"
    assert result["recommendationStatus"] == "PLAYABLE_PREDICTION"
    assert "NOT_PLAYABLE" not in result["tags"]
    assert "PLAYABLE_PREDICTION" in result["tags"]


def test_guard_fails_closed_on_full_contract_validation_error() -> None:
    row = _row(strong=True)
    row["marketProbabilityFingerprint"] = "tampered"

    result = guard.guard_prediction(row)

    assert result["actionablePick"] is False
    assert result["playable"] is False
    assert result["playabilityStatus"] == "BLOCKED"
    assert "probability_contract_invalid" in result["pickDiscipline"][
        "mandatoryBlockReasons"
    ]
    assert "market_probability_fingerprint_mismatch" in result[
        "pickDiscipline"
    ]["probabilityContractValidationErrors"]
    assert "RELEASE_BLOCKED" in result["tags"]


def test_environment_switches_are_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INQSI_MLB_CALIBRATION_ENABLED", "false")
    monkeypatch.setenv("INQSI_MLB_NO_PICK_DISCIPLINE_ENABLED", "false")
    reloaded = importlib.reload(guard)
    result = reloaded.guard_prediction(_row(strong=True))
    assert result["calibration"]["enabled"] is False
    assert result["pickDiscipline"]["enabled"] is False
    assert result["officialPick"] is False

    monkeypatch.delenv("INQSI_MLB_CALIBRATION_ENABLED")
    monkeypatch.delenv("INQSI_MLB_NO_PICK_DISCIPLINE_ENABLED")
    importlib.reload(guard)


def test_disabling_optional_thresholds_cannot_reopen_mandatory_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INQSI_MLB_NO_PICK_DISCIPLINE_ENABLED", "false")
    reloaded = importlib.reload(guard)
    corrected = _row(strong=True)
    corrected.update(
        {
            "probabilityCorrectionApplied": True,
            "playable": False,
            "trainingEligible": False,
            "blocked": True,
            "releaseBlocked": True,
            "wagerReleaseBlocked": True,
            "playabilityStatus": "BLOCKED",
        }
    )

    result = reloaded.guard_prediction(corrected)

    assert result["officialPick"] is False
    assert result["actionablePick"] is False
    assert result["accuracyTargetEligible"] is False
    assert result["actionability"] == "NO_PICK"
    assert result["pickDiscipline"]["enabled"] is False
    assert "probability_direction_integrity_correction" in result[
        "pickDiscipline"
    ]["mandatoryBlockReasons"]

    upstream_blocked = _row(strong=True)
    upstream_blocked["blockedReasons"] = ["manual_release_hold"]
    upstream_result = reloaded.guard_prediction(upstream_blocked)
    assert upstream_result["actionablePick"] is False
    assert upstream_result["playabilityStatus"] == "BLOCKED"
    assert "manual_release_hold" in upstream_result["releaseBlockReasons"]
    assert "upstream_release_blocked" in upstream_result["pickDiscipline"][
        "mandatoryBlockReasons"
    ]

    monkeypatch.delenv("INQSI_MLB_NO_PICK_DISCIPLINE_ENABLED")
    importlib.reload(guard)
