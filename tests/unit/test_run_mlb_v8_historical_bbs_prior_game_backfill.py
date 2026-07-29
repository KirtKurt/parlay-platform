from __future__ import annotations

from datetime import date, datetime, timezone

import mlb_v8_historical_bbs_overlay_v1 as overlay
import run_mlb_v8_historical_bbs_prior_game_backfill as backfill


def canonical():
    return {
        "slateDateEt": "2026-07-27",
        "officialGamePk": "123",
        "predictionLockAtUtc": "2026-07-27T22:15:00Z",
        "homeTeam": "New York Yankees",
        "awayTeam": "Boston Red Sox",
    }


def derived():
    side = {
        "bbsHistoryGames": 20.0,
        "bbsHistoryCoverage": 20.0 / 30.0,
        "bbsWinRate5": 0.6,
        "bbsWinRate10": 0.55,
        "bbsRunDiffPerGame5": 0.8,
        "bbsRunDiffPerGame10": 0.5,
        "bbsStreakNormalized": 0.2,
        "bbsRestDaysNormalized": 2.0 / 7.0,
    }
    return {
        "trainingEligible": True,
        "eligibilityErrors": [],
        "home": dict(side),
        "away": dict(side),
    }


def test_date_range_is_inclusive():
    assert backfill._date_range(date(2026, 7, 1), date(2026, 7, 3)) == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
    ]


def test_prior_game_snapshot_satisfies_overlay_identity_and_leakage_contract():
    row = canonical()
    snapshot = backfill._snapshot(
        row,
        derived(),
        provider_match={"providerMatchId": "bbs-123"},
        history_start="2026-06-12",
        history_end="2026-07-27",
        history_row_count=600,
        history_fingerprint="a" * 64,
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    manifest = {
        "version": overlay.MANIFEST_VERSION,
        "authority": overlay.AUTHORITY,
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
        "eligibleGameCount": 1,
        "records": [
            {
                "officialGamePk": row["officialGamePk"],
                "predictionLockAtUtc": row["predictionLockAtUtc"],
                "providerMatchId": "bbs-123",
                "trainingEligible": True,
                "snapshot": snapshot,
            }
        ],
    }
    manifest["manifestDigest"] = overlay.manifest_digest(manifest)

    enriched, proof = overlay.apply_manifest([row], manifest)

    applied = enriched[0]["frozenFundamentalsSnapshot"]
    assert proof["appliedGameCount"] == 1
    assert applied["priorCompletedGamesUsed"] is True
    assert applied["sameDayResultsExcluded"] is True
    assert applied["targetGameOutcomeUsed"] is False
    assert applied["selectionUsedOutcomes"] is False
    assert applied["trainingEligible"] is True


def test_ineligible_prior_history_never_creates_training_snapshot():
    value = derived()
    value["trainingEligible"] = False
    value["eligibilityErrors"] = ["home_bbs_prior_game_floor_not_met"]

    snapshot = backfill._snapshot(
        canonical(),
        value,
        provider_match=None,
        history_start="2026-06-12",
        history_end="2026-07-27",
        history_row_count=2,
        history_fingerprint="b" * 64,
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )

    assert snapshot["trainingEligible"] is False
    assert snapshot["pointInTimeVerified"] is False
    assert snapshot["eligibilityErrors"] == [
        "home_bbs_prior_game_floor_not_met"
    ]


def test_learning_priority_repairs_earliest_chronological_fold_first():
    rows = [
        {
            "slateDateEt": "2026-06-03",
            "predictionLockAtUtc": "2026-06-03T20:00:00Z",
            "officialGamePk": "3",
        },
        {
            "slateDateEt": "2026-03-02",
            "predictionLockAtUtc": "2026-03-02T22:00:00Z",
            "officialGamePk": "2",
        },
        {
            "slateDateEt": "2026-03-02",
            "predictionLockAtUtc": "2026-03-02T19:00:00Z",
            "officialGamePk": "1",
        },
    ]

    selected = backfill._learning_priority(rows, 2)

    assert [row["officialGamePk"] for row in selected] == ["1", "2"]
    assert [row["officialGamePk"] for row in rows] == ["3", "2", "1"]
