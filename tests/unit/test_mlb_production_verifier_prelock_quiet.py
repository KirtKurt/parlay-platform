from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import mlb_production_verifier as verifier  # noqa: E402


SLATE = "2026-07-23"
GAME_IDS = ["mlb_statsapi:990001", "mlb_statsapi:990002"]
AUTHORITY = "official-schedule-prelock-fixture"


def _install(monkeypatch, now: datetime, *, empty_lock_status: bool = True):
    starts = [now + timedelta(hours=3), now + timedelta(hours=5)]
    pulls = [{"pulled_at": (now - timedelta(minutes=1)).isoformat()}]
    roster = {
        "games": [
            {"game_id": game_id, "commence_time": start.isoformat()}
            for game_id, start in zip(GAME_IDS, starts)
        ],
        "fullSlateGameCount": 2,
        "officialScheduleGameCount": 2,
        "officialScheduleBacked": True,
        "immutableReadbackVerified": True,
        "fullAuthorityFingerprint": "full-authority-prelock-fixture",
        "officialScheduleAuthorityFingerprint": AUTHORITY,
    }
    predictions = {
        "ok": True,
        "gameCount": 2,
        "count": 2,
        "promotedCount": 0,
        "allGamesPredicted": True,
        "predictions": [{"gameId": game_id} for game_id in GAME_IDS],
    }
    lock_status = {
        "ok": False if empty_lock_status else True,
        "gameCount": 2,
        "locked": False,
        "lockDue": False,
        "lockStatusComplete": False,
        "dailyCardComplete": False,
        "perGameStatus": [],
    }
    monkeypatch.setattr(verifier, "_now_utc", lambda: now)
    monkeypatch.setattr(verifier.history, "query_pulls", lambda *args: pulls)
    monkeypatch.setattr(
        verifier.history, "verified_full_slate_manifest", lambda *args: roster
    )
    monkeypatch.setattr(
        verifier.mlb_game_winner_engine, "predict_all", lambda *args, **kwargs: predictions
    )
    monkeypatch.setattr(
        verifier.mlb_daily_pick_lock, "_status_payload", lambda *args, **kwargs: lock_status
    )
    monkeypatch.setattr(
        verifier,
        "_locked_row_integrity",
        lambda slate_date, expected, due, evaluate: {
            "evaluated": evaluate,
            "authoritySafe": True,
            "dueCoverageComplete": not due,
            "coverageComplete": not evaluate,
            "storedGameIdentities": [],
            "dueGameIdentities": list(due),
            "missingDueGameIdentities": list(due),
            "unexpectedGameIdentities": [],
            "duplicateGameIdentities": [],
        },
    )
    return starts, lock_status


def test_empty_lock_status_is_healthy_before_first_official_tminus45(monkeypatch):
    now = datetime(2026, 7, 23, 13, 15, tzinfo=timezone.utc)
    starts, _lock_status = _install(monkeypatch, now)

    result = verifier._verification_payload(SLATE, "continuous", "unit")

    assert result["ok"] is True
    assert result["blockers"] == []
    assert result["allGamesPredicted"] is True
    assert result["lock"]["lockEvidenceRequired"] is False
    assert result["lock"]["perGameProgress"]["dueGameCount"] == 0
    assert result["lock"]["perGameProgress"]["pendingGameCount"] == 2
    assert result["lock"]["perGameProgress"]["officialLockScheduleComplete"] is True
    assert result["sourceAuthority"]["lockStatus"]["ok"] is True
    assert result["sourceAuthority"]["lockStatus"]["required"] is False
    assert result["sourceAuthority"]["identitySetsEqual"] is True
    assert starts[0] - timedelta(minutes=45) > now


def test_empty_lock_status_fails_closed_when_first_official_cutoff_is_due(monkeypatch):
    now = datetime(2026, 7, 23, 13, 15, tzinfo=timezone.utc)
    starts, _lock_status = _install(monkeypatch, now)
    due_now = starts[0] - timedelta(minutes=44)
    monkeypatch.setattr(verifier, "_now_utc", lambda: due_now)

    result = verifier._verification_payload(SLATE, "continuous", "unit")

    assert result["ok"] is False
    assert result["lock"]["lockEvidenceRequired"] is True
    assert result["lock"]["perGameProgress"]["dueGameCount"] == 1
    assert result["lock"]["perGameProgress"]["dueMissingGameCount"] == 1
    assert "LOCK_STATUS_FAILED" in result["blockers"]
    assert "LOCK_STATUS_ROSTER_AUTHORITY_MISMATCH" in result["blockers"]
    assert "LOCK_DUE_BUT_NOT_LOCKED" in result["blockers"]
