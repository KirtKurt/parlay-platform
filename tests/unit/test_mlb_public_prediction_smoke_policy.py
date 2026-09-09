from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.mlb_public_prediction_smoke_policy import (
    qualified_champion_readiness_blockers,
    reconcile_public_prediction_lifecycle,
)
from scripts.verify_mlb_authority_response import AUTHORITY_CONTRACT


NOW = datetime(2026, 8, 27, 2, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "states,expected",
    [
        (["NO_QUALIFIED_CHAMPION"], ["no_qualified_champion"]),
        (["QUALIFIED_R7_CHAMPION"], []),
        (["QUALIFIED_R7_CHAMPION", "NO_QUALIFIED_CHAMPION"], ["no_qualified_champion"]),
        ([], ["qualified_champion_authority_not_verified"]),
        ([None], ["qualified_champion_authority_not_verified"]),
        (["INVALID"], ["qualified_champion_authority_not_verified"]),
    ],
)
def test_complete_internal_scoring_does_not_supply_champion_authority(states, expected):
    results = [
        {"publicAuthorityState": state, "ok": True, "allGamesPredicted": True,
         "preLockStorageComplete": True, "preLockStoredCount": 15}
        for state in states
    ]
    assert qualified_champion_readiness_blockers(results) == expected


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


def _successor_payload():
    from scripts.mlb_public_prediction_smoke_policy import SUCCESSOR_CONSUMER
    start = NOW + timedelta(minutes=40)
    row = {"gameId": "g1", "officialGamePk": "824000", "homeTeam": "Home", "awayTeam": "Away",
           "predictedWinner": "Away", "predictedSide": "away", "homeProbability": .4, "awayProbability": .6,
           "commenceTime": start.isoformat(), "capturedAtUtc": NOW.isoformat(),
           "featureLockAtUtc": (NOW-timedelta(minutes=5)).isoformat(),
           "artifactDigest": "a"*64, "inputFingerprint": "b"*64, "immutable": True,
           "automaticWagerAllowed": False, "playabilityAuthorityEnabled": False}
    payload = _no_champion(ok=True, status="QUALIFIED_CHAMPION", error=None,
        publicationClosed=False, productionSelectionAllowed=True, primaryAlgorithmActive=True,
        qualifiedChampionPresent=True, r7ChampionQualified=True, r7DeploymentIdentity={"gitSha": "c"*40},
        model_version="MLB-SUCCESSOR", primaryAlgorithm="MLB-SUCCESSOR",
        successorConsumerVersion=SUCCESSOR_CONSUMER, predictionSource=SUCCESSOR_CONSUMER,
        artifactDigest="a"*64, readOnly=True, playabilityAuthorityEnabled=False,
        predictions=[row], winner_predictions=[row], count=1)
    status = {**_row("g1", start, winner="Home", locked=True, status="LOCKED_CANONICAL"),
              "officialGamePk": "824000", "homeTeam": "Home", "awayTeam": "Away"}
    return payload, [status]


def test_qualified_successor_can_differ_without_rewriting_old_winner():
    from copy import deepcopy
    payload, statuses = _successor_payload()
    before = deepcopy((payload, statuses))
    result = reconcile_public_prediction_lifecycle(200, payload, statuses, 1, now=NOW)
    assert result["successorPredictionAuthorityVerified"] is True
    assert result["authorityClosedStatusProjectionUsed"] is False
    assert result["publicPayload"]["predictions"][0]["predictedWinner"] == "Away"
    assert result["lifecyclePayload"]["predictions"][0]["predictedWinner"] == "Home"
    assert result["statusProjectionPersisted"] is False
    assert (payload, statuses) == before


@pytest.mark.parametrize("field,value", [("artifactDigest", "wrong"), ("officialGamePk", "999"),
    ("awayProbability", .9), ("homeProbability", float("nan")), ("predictedSide", "home"),
    ("inputFingerprint", "missing"), ("automaticWagerAllowed", True), ("immutable", False),
    ("capturedAtUtc", "2026-08-28T04:00:00+00:00")])
def test_successor_consumer_projection_rejects_bad_public_evidence(field, value):
    payload, statuses = _successor_payload()
    payload["predictions"][0][field] = value
    with pytest.raises(ValueError):
        reconcile_public_prediction_lifecycle(200, payload, statuses, 1, now=NOW)


def test_qualified_successor_can_wait_for_canonical_locks_without_old_winner_fallback():
    payload, statuses = _successor_payload()
    payload.update(predictions=[], winner_predictions=[], count=0)
    result = reconcile_public_prediction_lifecycle(200, payload, statuses, 1, now=NOW)
    assert result["successorPredictionAuthorityVerified"] is True
    assert result["publicWinnerCount"] == 0
    assert result["publicPayload"]["predictions"] == []
