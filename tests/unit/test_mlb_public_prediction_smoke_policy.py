from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.mlb_public_prediction_smoke_policy import (
    reconcile_public_prediction_lifecycle,
)
from scripts.verify_mlb_authority_response import AUTHORITY_CONTRACT


NOW = datetime(2026, 8, 27, 2, 0, tzinfo=timezone.utc)


def _no_champion(**overrides):
    payload = {
        "ok": False,
        "sport": "mlb",
        "status": "NO_QUALIFIED_CHAMPION",
        "error": "NO_QUALIFIED_CHAMPION",
        "publicationClosed": True,
        "productionSelectionAllowed": False,
        "model_version": None,
        "primaryAlgorithm": None,
        "primaryAlgorithmActive": False,
        "soleProductionAlgorithm": None,
        "game_winner_model": None,
        "requestedAuthority": "AWS_ML_PROSPECTIVE_R7",
        "qualifiedChampionRequired": True,
        "qualifiedChampionPresent": False,
        "r7ChampionQualified": False,
        "r7DeploymentIdentity": None,
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "legacyRecommendationAuthority": False,
        "retiredAuthoritySuppressed": True,
        "retiredV15_10Eligible": False,
        "automaticWagerAllowed": False,
        "rowLevelAutomaticWagerAllowed": False,
        "authorityContractVersion": AUTHORITY_CONTRACT,
        "winner_predictions": [],
        "predictions": [],
        "count": 0,
    }
    payload.update(overrides)
    return payload


def _row(game_id, start, *, winner=None, locked=False, status="OPEN_PRE_LOCK"):
    return {
        "gameId": game_id,
        "gameIdentity": game_id,
        "officialGamePk": game_id,
        "commenceTime": start.isoformat(),
        "lockStatus": status,
        "officialPredictionStatus": status,
        "predictedWinner": winner,
        "predictedSide": "home" if winner else None,
        "selectionFingerprint": "a" * 64 if winner else None,
        "lockedPrediction": locked,
        "officialPrediction": bool(locked and winner),
        "canonicalPrediction": bool(locked and winner),
        "playable": bool(locked and winner),
        "blocked": not bool(locked and winner),
        "trainingEligible": bool(locked and winner),
        "accuracyEligible": bool(locked and winner),
        "wagerAllowed": False,
        "predictionAdopted": False,
        "operationalDefect": False,
        "canonicalPredictionComplete": bool(locked and winner),
    }


def test_no_champion_503_keeps_public_rows_empty_and_projects_historical_locks():
    past = NOW - timedelta(hours=3)
    status_rows = [
        _row("g1", past, winner="Home", locked=True, status="LOCKED_CANONICAL"),
        _row("g2", past, status="LOCKED_NO_PREDICTION_DATA"),
    ]

    result = reconcile_public_prediction_lifecycle(
        503,
        _no_champion(),
        status_rows,
        2,
        now=NOW,
    )

    assert result["authority"]["state"] == "NO_QUALIFIED_CHAMPION"
    assert result["publicPayload"]["predictions"] == []
    assert result["publicPayload"]["winner_predictions"] == []
    assert result["publicWinnerCount"] == 0
    assert result["historicalStatusProjectionUsed"] is True
    assert result["statusProjectionPersisted"] is False
    lifecycle = result["lifecyclePayload"]
    assert len(lifecycle["predictions"]) == 2
    assert lifecycle["lockedPredictionCount"] == 1
    assert lifecycle["noPredictionDataCount"] == 1


def test_no_champion_503_uses_detached_status_projection_before_cutoff():
    future = NOW + timedelta(hours=3)
    status_rows = [_row("g1", future, winner="Home", status="OPEN_PRE_LOCK")]

    result = reconcile_public_prediction_lifecycle(
        503,
        _no_champion(),
        status_rows,
        1,
        now=NOW,
        status_operational_defect=True,
    )

    assert result["historicalStatusProjectionUsed"] is False
    assert result["authorityClosedStatusProjectionUsed"] is True
    assert result["publicPayload"]["predictions"] == []
    lifecycle = result["lifecyclePayload"]
    assert lifecycle["authorityClosedStatusProjection"] is True
    assert lifecycle["authorityClosedStatusProjectionPersisted"] is False
    assert lifecycle["operationalDefect"] is True
    assert lifecycle["predictions"] == status_rows
    assert lifecycle["predictions"] is not status_rows


def test_arbitrary_503_and_nonempty_fallback_winner_are_rejected():
    status_rows = [_row("g1", NOW, status="LOCKED_NO_PREDICTION_DATA")]
    with pytest.raises(ValueError, match="public_prediction_authority_invalid"):
        reconcile_public_prediction_lifecycle(
            503,
            {"ok": False, "status": "Service Unavailable"},
            status_rows,
            1,
            now=NOW,
        )

    with pytest.raises(ValueError, match="public_prediction_authority_invalid"):
        reconcile_public_prediction_lifecycle(
            503,
            _no_champion(
                predictions=[{"predictedWinner": "Retired fallback"}],
                winner_predictions=[{"predictedWinner": "Retired fallback"}],
                count=1,
            ),
            status_rows,
            1,
            now=NOW,
        )


def test_no_champion_projection_keeps_quarantine_distinct_and_non_predictive():
    past = NOW - timedelta(hours=3)
    quarantine = _row(
        "g1",
        past,
        status="MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED",
    )
    quarantine.update({
        "lockedPrediction": False,
        "officialPrediction": False,
        "canonicalPrediction": False,
        "playable": False,
        "blocked": True,
        "trainingEligible": False,
        "accuracyEligible": False,
        "wagerAllowed": False,
        "predictionAdopted": False,
        "operationalDefect": True,
        "canonicalPredictionComplete": False,
    })
    no_data = _row("g2", past, status="LOCKED_NO_PREDICTION_DATA")

    result = reconcile_public_prediction_lifecycle(
        503,
        _no_champion(),
        [quarantine, no_data],
        2,
        now=NOW,
        status_operational_defect=True,
    )

    lifecycle = result["lifecyclePayload"]
    assert lifecycle["lockedPredictionCount"] == 0
    assert lifecycle["noPredictionDataCount"] == 1
    assert lifecycle["missedLockValidPrelockQuarantineCount"] == 1
    assert lifecycle["terminalExcludedCount"] == 2
    assert lifecycle["lockStatusComplete"] is True
    assert result["publicPayload"]["predictions"] == []
    assert all(
        row.get("predictedWinner") in (None, "")
        and row.get("predictedSide") in (None, "")
        for row in lifecycle["predictions"]
    )



@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.__setitem__("accuracyEligible", True),
        lambda row: row.__setitem__("blocked", False),
        lambda row: row.__setitem__(
            "metadata",
            {"model": {"winner": "Hostile"}},
        ),
        lambda row: row.__setitem__(
            "authority",
            {"labels": {"result": "home"}},
        ),
    ],
)
def test_terminal_status_projection_rejects_unsafe_flags_and_nested_material(
    mutate,
):
    row = _row(
        "g-terminal",
        NOW - timedelta(hours=3),
        status="LOCKED_NO_PREDICTION_DATA",
    )
    mutate(row)

    with pytest.raises(
        ValueError,
        match="terminal_status_projection_authority_invalid",
    ):
        reconcile_public_prediction_lifecycle(
            503,
            _no_champion(),
            [row],
            1,
            now=NOW,
        )


def test_quarantine_status_projection_emits_only_bounded_lifecycle_schema():
    row = _row(
        "g-quarantine",
        NOW - timedelta(hours=3),
        status="MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED",
    )
    row["operationalDefect"] = True
    row["harmlessUnknown"] = {"diagnostic": "must-not-copy"}

    result = reconcile_public_prediction_lifecycle(
        503,
        _no_champion(),
        [row],
        1,
        now=NOW,
    )

    projected = result["lifecyclePayload"]["predictions"][0]
    assert "harmlessUnknown" not in projected
    assert projected["predictedWinner"] is None
    assert projected["predictedSide"] is None
    assert projected["canonicalPrediction"] is False
    assert projected["accuracyEligible"] is False
