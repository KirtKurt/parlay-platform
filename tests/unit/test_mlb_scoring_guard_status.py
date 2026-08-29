from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hello_world import (
    mlb_fundamentals_scoring_bridge_v1 as fundamentals_shadow_bridge,
)
from hello_world import mlb_fundamentals_snapshot_v2 as fundamentals_snapshot_v2

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "mlb_scoring_guard_status.py"
SPEC = importlib.util.spec_from_file_location("mlb_scoring_guard_status", MODULE_PATH)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def game(pk: str, provider_id: str, game_key: str, start: str):
    return {
        "official_game_pk": pk,
        "game_id": provider_id,
        "game_key": game_key,
        "away_team": "Away Club",
        "home_team": "Home Club",
        "commence_time": start,
        "books": {"fanduel": {"ml": {"home": -120, "away": 110}}},
        "moneyline_available": True,
    }


def pull_item(pulled_at: str, games):
    return {
        "record_type": "pull_run",
        "data": {
            "pull_id": f"pull-{pulled_at}",
            "pulled_at": pulled_at,
            "games": games,
            "provider_schedule_manifest": {
                "pullId": f"pull-{pulled_at}",
                "observedAtUtc": pulled_at,
                "gameCount": len(games),
                "fingerprint": f"fp-{pulled_at}",
                "scheduleAuthority": {"verified": True},
                "games": games,
            },
        },
    }


def prediction(pk: str, winner: str, score: float, fundamentals: bool):
    return {
        "record_type": "mlb_single_game_moneyline_prediction",
        "data": {
            "officialGamePk": pk,
            "predictedWinner": winner,
            "score": score,
            "confidenceTier": "Solid",
            "winnerOptimizer": {
                "fundamentalsApplied": fundamentals,
                "fundamentalsMode": "TIMESTAMPED_FUNDAMENTALS_V2" if fundamentals else "NEUTRAL_NOT_ENABLED",
            },
        },
    }


def shadow_source_provenance(dataset: str) -> dict:
    return {
        "provider": "fixture-provider",
        "endpoint": "https://example.invalid/pregame",
        "dataset": dataset,
        "retrievedAtUtc": "2026-07-22T12:00:30+00:00",
        "sourceEffectiveAtUtc": "2026-07-22T11:59:00+00:00",
        "payloadFingerprint": f"fixture-{dataset}",
    }


def shadow_context(*, complete: bool) -> dict:
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
                "sourceProvenance": shadow_source_provenance("probable"),
            },
            "fip_xfip": {
                "source_status": "CONNECTED",
                "home_starter_fip": 3.0,
                "away_starter_fip": 4.2,
                "home_starter_xfip": 3.2,
                "away_starter_xfip": 4.0,
                "home_starter_k_minus_bb_pct": 0.20,
                "away_starter_k_minus_bb_pct": 0.12,
                "sourceProvenance": shadow_source_provenance("starter"),
            },
            "bullpen_fatigue": {
                "source_status": "CONNECTED",
                "home_reliever_usage_1d_3d_5d": {"oneDay": 12},
                "away_reliever_usage_1d_3d_5d": {"oneDay": 35},
                "home_available_relievers": ["H1"],
                "away_available_relievers": ["A1"],
                "home_bullpen_fatigue_score": 0.2,
                "away_bullpen_fatigue_score": 0.8,
                "sourceProvenance": shadow_source_provenance("bullpen"),
            },
            "confirmed_lineups": {
                "source_status": "CONNECTED",
                "home_lineup_confirmed": True,
                "away_lineup_confirmed": True,
                "home_batting_order": ["H1"],
                "away_batting_order": ["A1"],
                "home_lineup_strength_delta": 0.2,
                "away_lineup_strength_delta": -0.1,
                "sourceProvenance": shadow_source_provenance("lineups"),
            },
        }
    )
    return context


def bind_shadow(prediction_item: dict, shadow: dict) -> None:
    data = prediction_item["data"]
    complete = shadow.get("wouldApply") is True
    data.update(
        {
            "predictionSourcePullAt": "2026-07-22T12:00:00+00:00",
            "predictionSourcePullId": "pull-shadow-fixture",
            "predictionPersistedAtUtc": "2026-07-22T12:01:00+00:00",
            "lockedAtUtc": "2026-07-22T12:15:00+00:00",
            "advanced_context": shadow_context(complete=complete),
        }
    )
    snapshot = fundamentals_snapshot_v2.build(
        data,
        captured_at_utc="2026-07-22T12:00:00+00:00",
    )
    data["fundamentalsSnapshotV2"] = snapshot
    fundamentals_snapshot_v2.enhance_row(data)
    canonical = fundamentals_shadow_bridge.evaluate_shadow(data)
    assert canonical["wouldApply"] is complete
    if shadow.get("liveScoringAuthority") is True:
        canonical["liveScoringAuthority"] = True
    data["fundamentalsScoringShadow"] = canonical


def feature(game_key: str, hot_team: str | None = "Home Club", hot_delta: float = 0.01):
    return {
        "entity_type": "HOT_PULL_MOVEMENT_FEATURE",
        "game_key": game_key,
        "latest_asof": "2026-07-22T12:15:00+00:00",
        "hot_team": hot_team,
        "hot_delta": hot_delta,
        "movement_strength": "MEDIUM" if hot_delta else "FLAT",
    }


def fixture():
    # Same teams, different official IDs and starts: this proves the guard does
    # not collapse doubleheaders into one game.
    first = game("1001", "provider-a", "provider-a", "2026-07-22T17:05:00+00:00")
    second = game("1002", "provider-b", "provider-b", "2026-07-22T23:05:00+00:00")
    pulls = [
        pull_item("2026-07-22T11:00:00+00:00", [first, second]),
        pull_item("2026-07-22T11:15:00+00:00", [first, second]),
    ]
    predictions = [
        prediction("1001", "Home Club", 61.2, False),
        prediction("1002", "Away Club", 57.4, False),
    ]
    features = [feature("provider-a"), feature("provider-b")]
    return pulls, predictions, features


def evaluate(predictions=None, features=None):
    pulls, default_predictions, default_features = fixture()
    return GUARD.evaluate_slate(
        slate_date="2026-07-22",
        pull_items=pulls,
        prediction_items=default_predictions if predictions is None else predictions,
        movement_items=default_features if features is None else features,
        created_at=datetime(2026, 7, 22, 12, 30, tzinfo=timezone.utc),
    )


def test_complete_doubleheader_slate_passes():
    report = evaluate()
    assert report["guardPassed"] is True
    assert report["summary"]["officialGameCount"] == 2
    assert report["summary"]["persistedPredictionGameCount"] == 2
    assert report["summary"]["invalidPredictionTeamCount"] == 0
    assert report["summary"]["invalidMovementTeamCount"] == 0
    assert report["summary"]["movementFeatureGameCount"] == 2
    assert report["summary"]["fundamentalsAppliedCount"] == 0
    assert report["summary"]["fundamentalsNeutralOrSourceMissingCount"] == 2
    assert report["summary"]["fundamentalsNotActiveCount"] == 0
    assert report["summary"]["fundamentalsShadowOnlyCount"] == 0
    assert report["summary"]["fundamentalsShadowOnlyNotActiveCount"] == 0
    assert report["summary"]["fundamentalsShadowEvaluatedCount"] == 0
    assert all(row["predictedWinnerInMatchup"] for row in report["games"])
    assert all(row["hotTeamInMatchup"] for row in report["games"])
    assert len({row["gameIdentity"] for row in report["games"]}) == 2


def test_missing_prediction_fails_closed():
    _, predictions, _ = fixture()
    report = evaluate(predictions=predictions[:1])
    assert report["guardPassed"] is False
    assert report["summary"]["missingPredictionCount"] == 1
    assert "PERSISTED_WINNER_PREDICTION_COVERAGE_INCOMPLETE" in report["blockers"]


def test_prediction_for_team_outside_matchup_fails_closed():
    _, predictions, _ = fixture()
    predictions[0] = prediction("1001", "Unrelated Club", 61.2, True)
    report = evaluate(predictions=predictions)
    assert report["guardPassed"] is False
    assert report["summary"]["invalidPredictionTeamCount"] == 1
    assert report["invalidPredictionTeamGameIdentities"] == ["official:1001"]
    assert report["games"][0]["predictedWinnerInMatchup"] is False
    assert "PREDICTED_WINNER_NOT_IN_MATCHUP" in report["blockers"]


def test_live_fundamentals_application_is_an_authority_violation():
    _, predictions, _ = fixture()
    predictions[0] = prediction("1001", "Home Club", 61.2, True)

    report = evaluate(predictions=predictions)

    assert report["guardPassed"] is False
    assert report["summary"]["fundamentalsAppliedCount"] == 1
    assert "FUNDAMENTALS_LIVE_AUTHORITY_VIOLATION" in report["blockers"]


def test_movement_hot_team_outside_matchup_fails_closed():
    _, _, features = fixture()
    features[0] = feature("provider-a", hot_team="Unrelated Club")
    report = evaluate(features=features)
    assert report["guardPassed"] is False
    assert report["summary"]["invalidMovementTeamCount"] == 1
    assert report["invalidMovementTeamGameIdentities"] == ["official:1001"]
    assert report["games"][0]["hotTeamInMatchup"] is False
    assert "MOVEMENT_TEAM_NOT_IN_MATCHUP" in report["blockers"]


def test_flat_movement_without_hot_team_is_valid():
    _, _, features = fixture()
    features[0] = feature("provider-a", hot_team=None, hot_delta=0.0)
    report = evaluate(features=features)
    assert report["guardPassed"] is True
    assert report["summary"]["invalidMovementTeamCount"] == 0
    assert report["games"][0]["hotTeamInMatchup"] is True


def test_missing_movement_feature_fails_closed():
    _, _, features = fixture()
    report = evaluate(features=features[:1])
    assert report["guardPassed"] is False
    assert report["summary"]["missingMovementCount"] == 1
    assert "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE" in report["blockers"]


def test_not_active_and_shadow_only_modes_are_classified_without_false_application():
    _, predictions, _ = fixture()
    modes = (
        "FUNDAMENTALS_V2_NOT_ACTIVE_IN_LIVE_SCORING",
        "FUNDAMENTALS_V2_SHADOW_ONLY_NOT_ACTIVE_IN_LIVE_SCORING",
    )

    for mode in modes:
        predictions[1]["data"]["winnerOptimizer"]["fundamentalsMode"] = mode
        shadow = {
            "evaluated": True,
            "version": GUARD.FUNDAMENTALS_SHADOW_VERSION,
            "authorityMode": GUARD.FUNDAMENTALS_SHADOW_AUTHORITY_MODE,
            "shadowOnly": True,
            "liveScoringAuthority": False,
            "canInfluenceLivePick": False,
            "evidenceBounded": True,
            "wouldApply": False,
            "mode": "NEUTRAL_SOURCE_INCOMPLETE",
            "reason": "essential_groups_incomplete",
            "connectedGroups": ["confirmed_probable_pitchers"],
            "missingGroups": ["confirmed_lineups", "bullpen_availability"],
            "missingEssentialGroups": [
                "confirmed_lineups",
                "bullpen_availability",
            ],
            "validationErrors": [],
        }
        bind_shadow(predictions[1], shadow)

        report = evaluate(predictions=predictions)

        second = next(
            row for row in report["games"] if row["gameIdentity"] == "official:1002"
        )
        assert report["guardPassed"] is True
        assert report["summary"]["fundamentalsAppliedCount"] == 0
        assert report["summary"]["fundamentalsNotActiveCount"] == 0
        assert report["summary"]["fundamentalsShadowOnlyCount"] == 1
        assert report["summary"]["fundamentalsShadowOnlyNotActiveCount"] == 1
        assert report["summary"]["fundamentalsShadowEvaluatedCount"] == 1
        assert report["summary"]["fundamentalsShadowWouldApplyCount"] == 0
        assert report["summary"]["fundamentalsSourceIncompleteCount"] == 1
        assert report["summary"]["fundamentalsShadowInvalidCount"] == 0
        assert second["fundamentalsState"] == "SHADOW_ONLY"
        assert second["fundamentalsMode"] == mode
        assert second["fundamentalsShadowEvaluated"] is True
        assert second["fundamentalsShadowWouldApply"] is False
        assert second["fundamentalsShadowMode"] == "NEUTRAL_SOURCE_INCOMPLETE"
        assert second["fundamentalsConnectedGroups"] == []
        assert "confirmed_lineups" in second["fundamentalsMissingGroups"]
        assert "bullpen_availability" in second["fundamentalsMissingGroups"]

    predictions[1]["data"].pop("fundamentalsScoringShadow")
    predictions[1]["data"]["winnerOptimizer"]["fundamentalsMode"] = (
        "FUNDAMENTALS_V2_NOT_ACTIVE_IN_LIVE_SCORING"
    )

    report = evaluate(predictions=predictions)

    second = next(
        row for row in report["games"] if row["gameIdentity"] == "official:1002"
    )
    assert report["summary"]["fundamentalsNotActiveCount"] == 1
    assert report["summary"]["fundamentalsShadowOnlyCount"] == 0
    assert report["summary"]["fundamentalsShadowOnlyNotActiveCount"] == 1
    assert report["summary"]["fundamentalsShadowEvaluatedCount"] == 0
    assert second["fundamentalsState"] == "NOT_ACTIVE"
    assert second["fundamentalsShadowEvaluated"] is False


def test_invalid_shadow_attestation_is_never_counted_as_shadow_only():
    _, predictions, _ = fixture()
    predictions[1]["data"]["winnerOptimizer"]["fundamentalsMode"] = (
        "FUNDAMENTALS_V2_NOT_ACTIVE_IN_LIVE_SCORING"
    )
    shadow = {
        "evaluated": True,
        "version": GUARD.FUNDAMENTALS_SHADOW_VERSION,
        "authorityMode": GUARD.FUNDAMENTALS_SHADOW_AUTHORITY_MODE,
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
    bind_shadow(predictions[1], shadow)

    report = evaluate(predictions=predictions)

    second = next(
        row for row in report["games"] if row["gameIdentity"] == "official:1002"
    )
    assert report["guardPassed"] is False
    assert report["summary"]["fundamentalsShadowOnlyCount"] == 0
    assert report["summary"]["fundamentalsShadowEvaluatedCount"] == 0
    assert report["summary"]["fundamentalsShadowWouldApplyCount"] == 0
    assert report["summary"]["fundamentalsShadowInvalidCount"] == 1
    assert "FUNDAMENTALS_SHADOW_ATTESTATION_INVALID" in report["blockers"]
    assert second["fundamentalsState"] == "INVALID_SHADOW_ATTESTATION"
    assert "shadow_live_authority_invalid" in second[
        "fundamentalsShadowAttestationErrors"
    ]


def test_shadow_guard_rejects_snapshot_and_isolation_tamper():
    _, predictions, _ = fixture()
    shadow = {
        "evaluated": True,
        "version": GUARD.FUNDAMENTALS_SHADOW_VERSION,
        "authorityMode": GUARD.FUNDAMENTALS_SHADOW_AUTHORITY_MODE,
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
    bind_shadow(predictions[1], shadow)
    data = predictions[1]["data"]
    data["fundamentalsScoringShadow"][
        "liveScoringInputUsedShadowCandidate"
    ] = True
    data["fundamentalsSnapshotV2"]["missingGroups"] = []

    report = evaluate(predictions=predictions)

    second = next(
        row for row in report["games"] if row["gameIdentity"] == "official:1002"
    )
    assert report["guardPassed"] is False
    assert "FUNDAMENTALS_SHADOW_ATTESTATION_INVALID" in report["blockers"]
    assert "shadow_live_scoring_used_candidate" in second[
        "fundamentalsShadowAttestationErrors"
    ]
    assert "shadow_current_snapshot_invalid" in second[
        "fundamentalsShadowAttestationErrors"
    ]


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
def test_scoring_guard_malformed_shadow_fails_closed_without_raising(
    field,
    malformed,
    expected_error,
):
    _, predictions, _ = fixture()
    bind_shadow(predictions[1], {"wouldApply": True})
    predictions[1]["data"]["fundamentalsScoringShadow"][field] = malformed

    report = evaluate(predictions=predictions)

    second = next(
        row for row in report["games"] if row["gameIdentity"] == "official:1002"
    )
    assert report["guardPassed"] is False
    assert "FUNDAMENTALS_SHADOW_ATTESTATION_INVALID" in report["blockers"]
    assert expected_error in second["fundamentalsShadowAttestationErrors"]


def test_scoring_guard_rejects_forged_but_bounded_shadow_adjustment():
    _, predictions, _ = fixture()
    bind_shadow(predictions[1], {"wouldApply": True})
    adjustments = predictions[1]["data"]["fundamentalsScoringShadow"][
        "boundedHypotheticalAdjustments"
    ]
    adjustments.update({"home": 0.25, "away": -0.25})

    report = evaluate(predictions=predictions)

    second = next(
        row for row in report["games"] if row["gameIdentity"] == "official:1002"
    )
    assert report["guardPassed"] is False
    assert "shadow_canonical_evaluation_mismatch" in second[
        "fundamentalsShadowAttestationErrors"
    ]


def test_scoring_guard_rejects_post_persistence_snapshot_provenance():
    _, predictions, _ = fixture()
    bind_shadow(predictions[1], {"wouldApply": True})
    predictions[1]["data"]["predictionPersistedAtUtc"] = (
        "2026-07-22T12:00:05+00:00"
    )

    report = evaluate(predictions=predictions)

    second = next(
        row for row in report["games"] if row["gameIdentity"] == "official:1002"
    )
    assert report["guardPassed"] is False
    assert "shadow_current_snapshot_provenance_invalid" in second[
        "fundamentalsShadowAttestationErrors"
    ]


def test_post_policy_upstream_application_evidence_remains_a_violation():
    _, predictions, _ = fixture()
    data = predictions[0]["data"]
    data["fundamentalsLayer"] = {
        "applied": False,
        "mode": "FUNDAMENTALS_V2_NOT_ACTIVE_IN_LIVE_SCORING",
        "upstreamAppliedDetected": True,
    }
    data["winnerStackV2"] = {
        "components": {"fundamentals": {"applied": True}},
        "weights": {"fundamentals": 0.19},
    }

    report = evaluate(predictions=predictions)

    first = next(
        row for row in report["games"] if row["gameIdentity"] == "official:1001"
    )
    assert report["guardPassed"] is False
    assert report["summary"]["fundamentalsAppliedCount"] == 0
    assert report["summary"]["fundamentalsUpstreamAppliedDetectedCount"] == 1
    assert "FUNDAMENTALS_LIVE_AUTHORITY_VIOLATION" in report["blockers"]
    assert first["fundamentalsState"] == "UPSTREAM_APPLIED_DETECTED"
