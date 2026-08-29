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
import mlb_fundamentals_scoring_bridge_v1 as fundamentals_shadow_bridge
import mlb_fundamentals_snapshot_v2 as fundamentals_snapshot_v2
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


def _source_provenance(dataset: str) -> dict:
    return {
        "provider": "fixture-provider",
        "endpoint": "https://example.invalid/pregame",
        "dataset": dataset,
        "retrievedAtUtc": "2026-07-21T20:00:30+00:00",
        "sourceEffectiveAtUtc": "2026-07-21T19:59:00+00:00",
        "payloadFingerprint": f"fixture-{dataset}",
    }


def _shadow_context(*, complete: bool) -> dict:
    context = {
        context_name: {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "reason": "fixture source unavailable before lock",
        }
        for _output_name, context_name, _fields in fundamentals_snapshot_v2.GROUP_SPECS
    }
    if not complete:
        return context
    context.update(
        {
            "confirmed_probable_pitchers": {
                "source_status": "CONNECTED",
                "home_probable_pitcher": "Home Starter",
                "away_probable_pitcher": "Away Starter",
                "sourceProvenance": _source_provenance("probable"),
            },
            "fip_xfip": {
                "source_status": "CONNECTED",
                "home_starter_fip": 3.0,
                "away_starter_fip": 4.2,
                "home_starter_xfip": 3.2,
                "away_starter_xfip": 4.0,
                "home_starter_k_minus_bb_pct": 0.20,
                "away_starter_k_minus_bb_pct": 0.12,
                "sourceProvenance": _source_provenance("starter"),
            },
            "bullpen_fatigue": {
                "source_status": "CONNECTED",
                "home_reliever_usage_1d_3d_5d": {"oneDay": 12},
                "away_reliever_usage_1d_3d_5d": {"oneDay": 35},
                "home_available_relievers": ["H1"],
                "away_available_relievers": ["A1"],
                "home_bullpen_fatigue_score": 0.2,
                "away_bullpen_fatigue_score": 0.8,
                "sourceProvenance": _source_provenance("bullpen"),
            },
            "confirmed_lineups": {
                "source_status": "CONNECTED",
                "home_lineup_confirmed": True,
                "away_lineup_confirmed": True,
                "home_batting_order": ["H1"],
                "away_batting_order": ["A1"],
                "home_lineup_strength_delta": 0.2,
                "away_lineup_strength_delta": -0.1,
                "sourceProvenance": _source_provenance("lineups"),
            },
        }
    )
    return context


def _bind_fundamentals_shadow(row: dict, shadow: dict) -> None:
    complete = shadow.get("wouldApply") is True
    row.update(
        {
            "predictionPersistedAtUtc": "2026-07-21T20:01:00+00:00",
            "lockedAtUtc": "2026-07-21T20:15:00+00:00",
            "advanced_context": _shadow_context(complete=complete),
        }
    )
    snapshot = fundamentals_snapshot_v2.build(
        row,
        captured_at_utc="2026-07-21T20:00:00+00:00",
    )
    row["fundamentalsSnapshotV2"] = snapshot
    fundamentals_snapshot_v2.enhance_row(row)
    canonical = fundamentals_shadow_bridge.evaluate_shadow(row)
    assert canonical["wouldApply"] is complete
    if shadow.get("liveScoringAuthority") is True:
        canonical["liveScoringAuthority"] = True
    row[guard.FUNDAMENTALS_SHADOW_FIELD] = canonical


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
    assert result["fundamentalsLayer"]["state"] == "NOT_ACTIVE"
    assert result["fundamentalsLayer"]["shadowOnly"] is False
    assert result["fundamentalsLayer"]["liveScoringAuthority"] is False
    assert result["fundamentalsLayer"]["canInfluenceLivePick"] is False


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
    assert result["accuracyTarget"]["fundamentalsAppliedCount"] == 0
    assert result["accuracyTarget"]["fundamentalsShadowEvaluatedCount"] == 0
    assert result["fundamentalsScoringPolicy"] == {
        "mode": guard.FUNDAMENTALS_MODE,
        "shadowMode": guard.FUNDAMENTALS_SHADOW_MODE,
        "authorityMode": "NO_LIVE_SCORING_AUTHORITY",
        "state": "NOT_ACTIVE",
        "shadowOnly": False,
        "liveScoringAuthority": False,
        "upstreamAppliedDetectedCount": 0,
        "upstreamAppliedSuppressedFlagCount": 0,
        "providerShadowCanInfluenceLivePick": False,
        "shadowEvaluatedCount": 0,
        "notActiveCount": 2,
        "shadowWouldApplyCount": 0,
        "sourceIncompleteCount": 0,
        "sourceMissingCount": 0,
        "shadowInvalidCount": 0,
        "shadowAttestationInvalidCount": 0,
    }
    assert result["modelVersion"].endswith(
        "+provider-neutral-calibration-no-pick-v2"
    )


def test_apply_reports_shadow_evidence_without_granting_scoring_authority() -> None:
    would_apply = {
        "evaluated": True,
        "version": guard.FUNDAMENTALS_SHADOW_VERSION,
        "authorityMode": guard.FUNDAMENTALS_AUTHORITY_MODE,
        "shadowOnly": True,
        "liveScoringAuthority": False,
        "canInfluenceLivePick": False,
        "evidenceBounded": True,
        "wouldApply": True,
        "mode": "TIMESTAMPED_FUNDAMENTALS_V2_PARTIAL_SAFE",
        "missingEssentialGroups": [],
        "validationErrors": [],
        "boundedHypotheticalAdjustments": {
            "home": 1.25,
            "away": -1.25,
            "maxAbsolute": 3.0,
        },
        "snapshotFingerprint": "fixture-fingerprint",
        "snapshotRef": {"fingerprint": "fixture-fingerprint"},
    }
    source_incomplete = {
        "evaluated": True,
        "version": guard.FUNDAMENTALS_SHADOW_VERSION,
        "authorityMode": guard.FUNDAMENTALS_AUTHORITY_MODE,
        "shadowOnly": True,
        "liveScoringAuthority": False,
        "canInfluenceLivePick": False,
        "evidenceBounded": True,
        "wouldApply": False,
        "mode": "NEUTRAL_SOURCE_INCOMPLETE",
        "reason": "essential_groups_incomplete",
        "missingEssentialGroups": ["confirmed_lineups"],
        "validationErrors": ["fixture_invalid_snapshot"],
        "boundedHypotheticalAdjustments": {
            "home": None,
            "away": None,
            "maxAbsolute": 3.0,
        },
    }
    strong = _row(strong=True)
    _bind_fundamentals_shadow(strong, would_apply)
    strong["fundamentalsApplied"] = True
    strong["winnerOptimizer"] = {
        "fundamentalsApplied": True,
        "fundamentalsMode": "TIMESTAMPED_FUNDAMENTALS_V2_PARTIAL_SAFE",
    }
    weak = _row(strong=False)
    _bind_fundamentals_shadow(weak, source_incomplete)
    module = SimpleNamespace(
        predict_all=lambda: {"predictions": [strong, weak], "modelVersion": "base"}
    )

    guard.apply(module)
    result = module.predict_all()

    assert result["accuracyTarget"]["fundamentalsAppliedCount"] == 0
    assert result["accuracyTarget"]["fundamentalsAuthorityInactiveCount"] == 2
    assert result["accuracyTarget"]["fundamentalsUpstreamAppliedDetectedCount"] == 1
    assert (
        result["accuracyTarget"][
            "fundamentalsUpstreamAppliedSuppressedFlagCount"
        ]
        == 1
    )
    assert result["accuracyTarget"]["fundamentalsShadowEvaluatedCount"] == 2
    assert result["accuracyTarget"]["fundamentalsShadowOnlyCount"] == 2
    assert result["accuracyTarget"]["fundamentalsNotActiveCount"] == 0
    assert result["accuracyTarget"]["fundamentalsShadowWouldApplyCount"] == 1
    assert result["accuracyTarget"]["fundamentalsSourceIncompleteCount"] == 1
    assert result["accuracyTarget"]["fundamentalsSourceMissingCount"] == 1
    assert result["accuracyTarget"]["fundamentalsShadowInvalidCount"] == 0
    assert (
        result["accuracyTarget"]["fundamentalsShadowAttestationInvalidCount"]
        == 0
    )
    assert result["fundamentalsScoringPolicy"]["shadowEvaluatedCount"] == 2
    assert result["fundamentalsScoringPolicy"]["upstreamAppliedDetectedCount"] == 1
    assert (
        result["fundamentalsScoringPolicy"][
            "upstreamAppliedSuppressedFlagCount"
        ]
        == 1
    )
    assert result["fundamentalsScoringPolicy"]["state"] == "SHADOW_ONLY"
    assert result["fundamentalsScoringPolicy"]["shadowOnly"] is True
    assert result["fundamentalsScoringPolicy"]["notActiveCount"] == 0
    assert result["fundamentalsScoringPolicy"]["shadowWouldApplyCount"] == 1
    assert result["fundamentalsScoringPolicy"]["sourceIncompleteCount"] == 1
    assert result["fundamentalsScoringPolicy"]["shadowInvalidCount"] == 0
    assert all(row["fundamentalsApplied"] is False for row in result["predictions"])
    assert sum(
        (row["fundamentalsLayer"] or {}).get("upstreamAppliedDetected") is True
        for row in result["predictions"]
    ) == 1
    upstream_row = next(
        row
        for row in result["predictions"]
        if row["fundamentalsLayer"]["upstreamAppliedDetected"] is True
    )
    assert upstream_row["actionablePick"] is False
    assert upstream_row["playabilityStatus"] == "BLOCKED"
    assert "upstream_fundamentals_application_detected" in upstream_row[
        "pickDiscipline"
    ]["mandatoryBlockReasons"]
    assert all(
        row["winnerOptimizer"]["fundamentalsApplied"] is False
        for row in result["predictions"]
    )
    shadows = {
        row[guard.FUNDAMENTALS_SHADOW_FIELD]["mode"]
        for row in result["predictions"]
    }
    assert shadows == {
        "TIMESTAMPED_FUNDAMENTALS_V2_PARTIAL_SAFE",
        "NEUTRAL_SOURCE_INCOMPLETE",
    }


def test_invalid_shadow_attestation_is_not_counted_or_trusted() -> None:
    row = _row(strong=True)
    shadow = {
        "evaluated": True,
        "version": guard.FUNDAMENTALS_SHADOW_VERSION,
        "authorityMode": guard.FUNDAMENTALS_AUTHORITY_MODE,
        "shadowOnly": True,
        "liveScoringAuthority": True,
        "canInfluenceLivePick": False,
        "evidenceBounded": True,
        "wouldApply": True,
        "mode": "TIMESTAMPED_FUNDAMENTALS_V2_PARTIAL_SAFE",
        "validationErrors": [],
        "boundedHypotheticalAdjustments": {
            "home": 1.0,
            "away": -1.0,
            "maxAbsolute": 3.0,
        },
    }
    _bind_fundamentals_shadow(row, shadow)
    module = SimpleNamespace(
        predict_all=lambda: {"predictions": [row], "modelVersion": "base"}
    )

    guard.apply(module)
    result = module.predict_all()

    assert result["accuracyTarget"]["fundamentalsShadowEvaluatedCount"] == 0
    assert result["accuracyTarget"]["fundamentalsShadowWouldApplyCount"] == 0
    assert result["accuracyTarget"][
        "fundamentalsShadowAttestationInvalidCount"
    ] == 1
    assert result["fundamentalsScoringPolicy"]["state"] == (
        "INVALID_SHADOW_ATTESTATION"
    )
    layer = result["predictions"][0]["fundamentalsLayer"]
    assert layer["shadowEvaluationAvailable"] is False
    assert layer["shadowAttestationValid"] is False
    assert "shadow_live_authority_invalid" in layer["shadowAttestationErrors"]
    guarded = result["predictions"][0]
    assert guarded["actionablePick"] is False
    assert guarded["playabilityStatus"] == "BLOCKED"
    assert "invalid_fundamentals_shadow_attestation" in guarded[
        "pickDiscipline"
    ]["mandatoryBlockReasons"]
    assert "fundamentals_shadow_live_use_detected" in guarded[
        "pickDiscipline"
    ]["mandatoryBlockReasons"]


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("nonisolated", "shadow_evaluation_input_not_isolated"),
        ("live_candidate", "shadow_live_scoring_used_candidate"),
        ("snapshot_tamper", "shadow_current_snapshot_invalid"),
        ("ref_tamper", "shadow_current_snapshot_ref_invalid"),
    ],
)
def test_shadow_attestation_is_bound_to_isolated_current_v2_snapshot(
    mutation,
    expected_error,
) -> None:
    row = _row(strong=True)
    shadow = {
        "evaluated": True,
        "version": guard.FUNDAMENTALS_SHADOW_VERSION,
        "authorityMode": guard.FUNDAMENTALS_AUTHORITY_MODE,
        "shadowOnly": True,
        "liveScoringAuthority": False,
        "canInfluenceLivePick": False,
        "evidenceBounded": True,
        "wouldApply": False,
        "mode": "NEUTRAL_SOURCE_INCOMPLETE",
        "validationErrors": [],
        "boundedHypotheticalAdjustments": {
            "home": None,
            "away": None,
            "maxAbsolute": 3.0,
        },
    }
    _bind_fundamentals_shadow(row, shadow)
    attached = row[guard.FUNDAMENTALS_SHADOW_FIELD]
    if mutation == "nonisolated":
        attached["evaluationInputIsolatedCopy"] = False
    elif mutation == "live_candidate":
        attached["liveScoringInputUsedShadowCandidate"] = True
    elif mutation == "snapshot_tamper":
        row["fundamentalsSnapshotV2"]["missingGroups"] = []
    else:
        row["fundamentalsSnapshotV2Ref"]["fingerprint"] = "0" * 64

    guarded = guard.guard_prediction(row)

    assert guarded["fundamentalsLayer"]["shadowAttestationValid"] is False
    assert expected_error in guarded["fundamentalsLayer"][
        "shadowAttestationErrors"
    ]
    assert guarded["actionablePick"] is False
    assert guarded["playabilityStatus"] == "BLOCKED"
    assert "invalid_fundamentals_shadow_attestation" in guarded[
        "pickDiscipline"
    ]["mandatoryBlockReasons"]


@pytest.mark.parametrize(
    ("field", "malformed", "expected_error"),
    [
        ("snapshotRef", "not-a-mapping", "shadow_snapshot_ref_invalid_type"),
        (
            "boundedHypotheticalAdjustments",
            ["not", "a", "mapping"],
            "shadow_adjustments_invalid_type",
        ),
    ],
)
def test_malformed_nested_shadow_fields_fail_closed_without_raising(
    field,
    malformed,
    expected_error,
) -> None:
    row = _row(strong=True)
    _bind_fundamentals_shadow(row, {"wouldApply": True})
    row[guard.FUNDAMENTALS_SHADOW_FIELD][field] = malformed

    guarded = guard.guard_prediction(row)

    assert guarded["actionablePick"] is False
    assert guarded["playabilityStatus"] == "BLOCKED"
    assert expected_error in guarded["fundamentalsLayer"][
        "shadowAttestationErrors"
    ]


def test_forged_but_bounded_shadow_adjustment_fails_canonical_comparison() -> None:
    row = _row(strong=True)
    _bind_fundamentals_shadow(row, {"wouldApply": True})
    adjustments = row[guard.FUNDAMENTALS_SHADOW_FIELD][
        "boundedHypotheticalAdjustments"
    ]
    adjustments.update({"home": 0.25, "away": -0.25})

    guarded = guard.guard_prediction(row)

    assert guarded["actionablePick"] is False
    assert "shadow_canonical_evaluation_mismatch" in guarded[
        "fundamentalsLayer"
    ]["shadowAttestationErrors"]


def test_post_persistence_snapshot_provenance_fails_actionability_closed() -> None:
    row = _row(strong=True)
    _bind_fundamentals_shadow(row, {"wouldApply": True})
    row["predictionPersistedAtUtc"] = "2026-07-21T20:00:05+00:00"

    guarded = guard.guard_prediction(row)

    assert guarded["actionablePick"] is False
    assert guarded["playabilityStatus"] == "BLOCKED"
    assert "shadow_current_snapshot_provenance_invalid" in guarded[
        "fundamentalsLayer"
    ]["shadowAttestationErrors"]


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
