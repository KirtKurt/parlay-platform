from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts import verify_mlb_postdeploy_scheduled_pull as observer


def _event(stream, stamp, message):
    return {
        "logStreamName": stream,
        "timestamp": int(stamp.timestamp() * 1000),
        "message": message,
    }


def _row(game_id, start, *, winner=None, locked=False, status="OPEN_PRE_LOCK"):
    return {
        "gameId": game_id,
        "gameIdentity": game_id,
        "commenceTime": start.isoformat().replace("+00:00", "Z"),
        "predictedWinner": winner,
        "lockedPrediction": locked,
        "lockStatus": status,
        "officialPredictionStatus": status,
        "perGameCanonicalLock": {"status": status},
    }


def test_select_fresh_pull_uses_first_slot_after_baseline():
    baseline = datetime(2026, 7, 23, 2, 0, tzinfo=timezone.utc)
    rows = [
        {"record_type": "pull_run", "SK": "PULL#SLOT#2026-07-23T02:00:00+00:00"},
        {"record_type": "pull_run", "SK": "PULL#SLOT#2026-07-23T02:30:00+00:00"},
        {"record_type": "pull_run", "SK": "PULL#SLOT#2026-07-23T02:15:00+00:00"},
    ]

    selected = observer.select_fresh_pull(rows, baseline)

    assert selected["SK"].endswith("02:15:00+00:00")


def test_matching_invocation_completion_binds_start_and_report_to_same_request():
    pull_at = datetime(2026, 7, 23, 2, 15, 10, tzinfo=timezone.utc)
    events = [
        _event(
            "stream-a",
            pull_at - timedelta(seconds=5),
            "START RequestId: request-a Version: $LATEST",
        ),
        _event(
            "stream-a",
            pull_at + timedelta(seconds=120),
            "REPORT RequestId: request-a Duration: 120000 ms",
        ),
        _event(
            "stream-b",
            pull_at + timedelta(seconds=1),
            "REPORT RequestId: unrelated Duration: 1 ms",
        ),
    ]

    result = observer.matching_invocation_completion(events, pull_at)

    assert result["complete"] is True
    assert result["failed"] is False
    assert result["requestId"] == "request-a"


def test_matching_invocation_completion_fails_closed_on_protected_writer_error():
    pull_at = datetime(2026, 7, 23, 2, 15, 10, tzinfo=timezone.utc)
    events = [
        _event(
            "stream-a",
            pull_at - timedelta(seconds=5),
            "START RequestId: request-a Version: $LATEST",
        ),
        _event(
            "stream-a",
            pull_at + timedelta(seconds=100),
            "MLB_SCHEDULED_PULL_FAILED:injected",
        ),
        _event(
            "stream-a",
            pull_at + timedelta(seconds=101),
            "REPORT RequestId: request-a Duration: 101000 ms",
        ),
    ]

    result = observer.matching_invocation_completion(events, pull_at)

    assert result["complete"] is False
    assert result["failed"] is True
    assert "MLB_SCHEDULED_PULL_FAILED" in result["failureMessage"]


def test_disposition_requires_every_open_candidate_to_have_persisted_winner():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(hours=4)
    status = [_row("g1", start, status="OPEN_PRE_LOCK")]
    predictions = [_row("g1", start, winner=None, status="OPEN_PRE_LOCK")]

    result = observer.classify_dispositions(status, predictions, now=now)

    assert result["complete"] is False
    assert "g1:open_prelock_prediction_missing" in result["errors"]


def test_disposition_accepts_locked_winner_and_explicit_no_backfill_lifecycle():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(hours=4)
    status = [
        _row(
            "g1",
            past,
            winner="Home",
            locked=True,
            status="OFFICIAL_LOCKED_PREDICTION",
        ),
        _row(
            "g2",
            past,
            winner=None,
            locked=False,
            status="MISSED_NOT_BACKFILLED",
        ),
    ]
    predictions = [
        _row(
            "g1",
            past,
            winner="Home",
            locked=True,
            status="OFFICIAL_LOCKED_PREDICTION",
        ),
        _row(
            "g2",
            past,
            winner=None,
            locked=False,
            status="MISSED_NOT_BACKFILLED",
        ),
    ]

    result = observer.classify_dispositions(status, predictions, now=now)

    assert result == {
        "gameCount": 2,
        "candidateCount": 0,
        "storedCandidateCount": 0,
        "canonicalLockedCount": 1,
        "lifecycleCount": 1,
        "dispositionCount": 2,
        "complete": True,
        "errors": [],
    }
