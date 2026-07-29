from __future__ import annotations

from datetime import datetime, timezone

import mlb_v8_historical_bbs_overlay_v1 as overlay
import run_mlb_v8_historical_bbs_backfill as backfill


def canonical(pk, start):
    return {
        "slateDateEt": "2026-07-01",
        "officialGamePk": pk,
        "homeTeam": "New York Yankees",
        "awayTeam": "Boston Red Sox",
        "commenceTime": start,
        "predictionLockAtUtc": "2026-07-01T22:15:00Z",
    }


def provider(match_id, start):
    return {
        "match_id": match_id,
        "kickoff_utc": start,
        "home": {"display_name": "New York Yankees"},
        "away": {"display_name": "Boston Red Sox"},
    }


def resource(effective):
    return {
        "data": {},
        "meta": {"asOfUtc": effective, "confirmed": True},
        "error": None,
    }


def normalized_game():
    return {
        "coverage": {
            "trainingEligible": True,
            "missingDomains": [],
            "confirmedLineups": True,
            "confirmedStarters": True,
        },
        "pitchers": {
            "home": {
                "xera": 3.1,
                "fip": 3.2,
                "era": 3.3,
                "kMinusBbPct": 18,
                "velocity": 96,
                "expectedInnings": 6,
                "recentThreeStarts": {"era": 2.9},
            },
            "away": {
                "xera": 4.1,
                "fip": 4.2,
                "era": 4.3,
                "kMinusBbPct": 12,
                "velocity": 94,
                "expectedInnings": 5.2,
                "recentThreeStarts": {"era": 4.4},
            },
        },
        "bullpens": {
            "home": {"qualityScore": 0.8, "freshnessScore": 0.9},
            "away": {"qualityScore": 0.4, "freshnessScore": 0.3},
        },
        "lineups": {
            "home": {"players": [{"wrcPlus": 110}] * 9},
            "away": {"players": [{"wrcPlus": 95}] * 9},
        },
        "injuries": {
            "home": {"players": []},
            "away": {"players": [{"name": "A"}]},
        },
        "teamContext": {
            "home": {
                "restDays": 2,
                "travel": {"miles": 0},
                "defense": {"rating": 1.1},
                "handednessSplits": {"wrcPlus": 108},
            },
            "away": {
                "restDays": 1,
                "travel": {"miles": 500},
                "defense": {"rating": 0.8},
                "handednessSplits": {"wrcPlus": 97},
            },
        },
        "weather": {"runFactor": 1.02},
        "park": {"runsFactor": 1.05},
    }


def test_crosswalk_accepts_documented_snake_case_identity():
    result = backfill.crosswalk_provider_rows(
        [provider("bbs-1", "2026-07-01T23:00:00Z")],
        [canonical("123", "2026-07-01T23:00:00Z")],
    )

    assert result["acceptedCount"] == 1
    assert result["accepted"]["123"]["providerMatchId"] == "bbs-1"
    assert result["selectionUsedOutcomes"] is False


def test_crosswalk_quarantines_ambiguous_doubleheader_identity():
    games = [
        canonical("123", "2026-07-01T23:00:00Z"),
        canonical("124", "2026-07-01T23:00:00Z"),
    ]

    result = backfill.crosswalk_provider_rows(
        [provider("bbs-1", "2026-07-01T23:00:00Z")], games
    )

    assert result["acceptedCount"] == 0
    assert (
        "provider_official_game_crosswalk_not_unique"
        in result["quarantined"][0]["reasons"]
    )


def test_post_lock_resource_timestamp_blocks_training():
    resources = {
        name: resource("2026-07-01T22:30:00Z")
        for name in backfill.REQUIRED_RESOURCES
    }

    errors = backfill.point_in_time_errors(
        resources, "2026-07-01T22:15:00Z"
    )

    assert "pitchers_source_effective_time_after_lock" in errors


def test_point_in_time_snapshot_is_training_eligible_and_outcome_free():
    game = canonical("123", "2026-07-01T23:00:00Z")
    resources = {
        name: resource("2026-07-01T22:10:00Z")
        for name in (*backfill.REQUIRED_RESOURCES, *backfill.OPTIONAL_RESOURCES)
    }

    snapshot = backfill.build_training_snapshot(
        game,
        {
            "providerMatchId": "bbs-1",
            "crosswalkMethod": "UNIQUE_EXACT_TEAM_AND_START_TIME",
        },
        normalized_game(),
        resources,
        retrieved_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )

    assert snapshot["trainingEligible"] is True
    assert snapshot["pointInTimeVerified"] is True
    assert snapshot["selectionUsedOutcomes"] is False
    assert "homeWon" not in str(snapshot)
    assert snapshot["fingerprint"] == overlay.snapshot_fingerprint(snapshot)
