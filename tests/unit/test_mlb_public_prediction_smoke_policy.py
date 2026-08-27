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
        "commenceTime": start.isoformat(),
        "lockStatus": status,
        "officialPredictionStatus": status,
        "predictedWinner": winner,
        "predictedSide": "home" if winner else None,
        "selectionFingerprint": "a" * 64 if winner else None,
        "lockedPrediction": locked,
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
