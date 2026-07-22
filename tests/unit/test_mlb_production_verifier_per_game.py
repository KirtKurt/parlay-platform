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


NOW = datetime(2026, 7, 21, 22, 7, tzinfo=timezone.utc)
SLATE = "2026-07-21"


def _game_status(
    game_id: str,
    lock_at: datetime,
    *,
    outcome_recorded: bool,
    state: str,
) -> dict:
    return {
        "gameIdentity": game_id,
        "gameId": game_id,
        "scheduledLockAtUtc": lock_at.isoformat(),
        "state": state,
        "lockStatus": state,
        "lockOutcomeRecorded": outcome_recorded,
        "lockedPrediction": outcome_recorded,
    }


def _install_runtime(monkeypatch, lock_status: dict, *, game_count: int = 2) -> None:
    status_ids = []
    for row in lock_status.get("perGameStatus") or []:
        identity = str(row.get("gameIdentity") or row.get("gameId") or "")
        if identity and identity not in status_ids:
            status_ids.append(identity)
    while len(status_ids) < game_count:
        status_ids.append(f"missing-official-game-{len(status_ids) + 1}")
    official_ids = status_ids[:game_count]
    authority_fingerprint = "official-schedule-authority-fixture"
    lock_status.update(
        {
            "ok": True,
            "officialScheduleBacked": True,
            "officialScheduleGameCount": game_count,
            "manifestGameCount": game_count,
            "verifiedFullSlateGameCount": game_count,
            "officialScheduleAuthorityFingerprint": authority_fingerprint,
        }
    )
    monkeypatch.setattr(verifier, "_now_utc", lambda: NOW)
    monkeypatch.setattr(
        verifier.history,
        "query_pulls",
        lambda *args: [{"pulled_at": NOW.isoformat()}],
    )
    monkeypatch.setattr(
        verifier.history,
        "verified_full_slate_manifest",
        lambda *args: {
            "games": [{"game_id": game_id} for game_id in official_ids],
            "fullSlateGameCount": game_count,
            "officialScheduleGameCount": game_count,
            "officialScheduleBacked": True,
            "immutableReadbackVerified": True,
            "fullAuthorityFingerprint": "full-authority-fixture",
            "officialScheduleAuthorityFingerprint": authority_fingerprint,
        },
    )
    monkeypatch.setattr(
        verifier.mlb_game_winner_engine,
        "predict_all",
        lambda *args, **kwargs: {
            "ok": True,
            "gameCount": game_count,
            "count": game_count,
            "promotedCount": 0,
            "allGamesPredicted": True,
            "predictions": [{"gameId": game_id} for game_id in official_ids],
        },
    )
    monkeypatch.setattr(
        verifier.mlb_daily_pick_lock,
        "_status_payload",
        lambda *args, **kwargs: lock_status,
    )
    monkeypatch.setattr(
        verifier,
        "_locked_row_integrity",
        lambda slate_date, expected, due, evaluate: {
            "evaluated": evaluate,
            "authoritySafe": True,
            "dueCoverageComplete": True,
            "coverageComplete": True,
        },
    )


def test_mixed_due_and_pending_games_do_not_require_the_future_game_lock(
    monkeypatch,
) -> None:
    lock_status = {
        "gameCount": 2,
        "locked": False,
        # This legacy slate-wide signal was true after the first cutoff and
        # caused the production false blocker. Per-game status is authoritative.
        "lockDue": True,
        "lockStatusComplete": False,
        "dailyCardComplete": False,
        "perGameStatus": [
            _game_status(
                "game-due",
                NOW - timedelta(minutes=12),
                outcome_recorded=True,
                state="LOCKED_CANONICAL",
            ),
            _game_status(
                "game-pending",
                NOW + timedelta(minutes=48),
                outcome_recorded=False,
                state="PENDING",
            ),
        ],
    }
    _install_runtime(monkeypatch, lock_status)

    result = verifier._verification_payload(SLATE, "lock", "test")

    progress = result["lock"]["perGameProgress"]
    assert result["ok"] is True
    assert "LOCK_DUE_BUT_NOT_LOCKED" not in result["blockers"]
    assert progress["dueGameCount"] == 1
    assert progress["dueTerminalGameCount"] == 1
    assert progress["dueMissingGameCount"] == 0
    assert progress["pendingGameCount"] == 1
    assert progress["fullSlateVectorEvaluationDue"] is False
    assert result["lockedRowIntegrity"]["evaluated"] is False


def test_missing_individually_due_lock_is_a_blocker(monkeypatch) -> None:
    lock_status = {
        "gameCount": 2,
        "locked": False,
        "lockDue": True,
        "lockStatusComplete": False,
        "dailyCardComplete": False,
        "perGameStatus": [
            _game_status(
                "game-missing",
                NOW - timedelta(minutes=12),
                outcome_recorded=False,
                state="DUE_NOT_STAGED",
            ),
            _game_status(
                "game-pending",
                NOW + timedelta(minutes=48),
                outcome_recorded=False,
                state="PENDING",
            ),
        ],
    }
    _install_runtime(monkeypatch, lock_status)

    result = verifier._verification_payload(SLATE, "lock", "test")

    progress = result["lock"]["perGameProgress"]
    assert result["ok"] is False
    assert result["blockers"] == ["LOCK_DUE_BUT_NOT_LOCKED"]
    assert progress["dueGameCount"] == 1
    assert progress["dueTerminalGameCount"] == 0
    assert progress["dueMissingGameCount"] == 1
    assert progress["dueMissingGames"][0]["gameId"] == "game-missing"
    assert progress["pendingGameCount"] == 1
    assert progress["fullSlateVectorEvaluationDue"] is False


def test_duplicate_per_game_identity_cannot_hide_a_missing_game(monkeypatch) -> None:
    lock_status = {
        "gameCount": 2,
        "locked": False,
        "lockDue": True,
        "lockStatusComplete": False,
        "dailyCardComplete": False,
        "perGameStatus": [
            _game_status(
                "game-duplicated",
                NOW - timedelta(minutes=12),
                outcome_recorded=True,
                state="LOCKED_CANONICAL",
            ),
            _game_status(
                "game-duplicated",
                NOW + timedelta(minutes=48),
                outcome_recorded=False,
                state="PENDING",
            ),
        ],
    }
    _install_runtime(monkeypatch, lock_status)

    result = verifier._verification_payload(SLATE, "lock", "test")

    progress = result["lock"]["perGameProgress"]
    assert result["ok"] is False
    assert result["blockers"] == [
        "PER_GAME_LOCK_ROSTER_MEMBERSHIP_MISMATCH",
        "PER_GAME_LOCK_STATUS_MISSING_OR_INVALID",
    ]
    assert progress["statusComplete"] is False
    assert progress["statusCount"] == 2
    assert progress["uniqueGameCount"] == 1
    assert progress["invalidStatusCount"] == 1
    assert progress["invalidStatuses"][0]["validationErrors"] == [
        "duplicate_game_identity"
    ]


def test_final_per_game_cutoff_activates_full_slate_vector_coverage(
    monkeypatch,
) -> None:
    lock_status = {
        "gameCount": 2,
        "locked": False,
        "lockDue": False,
        "lockStatusComplete": False,
        "dailyCardComplete": False,
        "perGameStatus": [
            _game_status(
                "game-one",
                NOW - timedelta(hours=2),
                outcome_recorded=True,
                state="LOCKED_CANONICAL",
            ),
            _game_status(
                "game-two",
                NOW - timedelta(minutes=1),
                outcome_recorded=True,
                state="LOCKED_CANONICAL",
            ),
        ],
    }
    _install_runtime(monkeypatch, lock_status)
    observed = {}

    def incomplete_integrity(
        slate_date, expected_identities, due_identities, evaluate_full_slate
    ):
        observed.update(
            {
                "slateDate": slate_date,
                "expectedIdentities": expected_identities,
                "dueIdentities": due_identities,
                "evaluateFullSlate": evaluate_full_slate,
            }
        )
        return {
            "evaluated": evaluate_full_slate,
            "authoritySafe": True,
            "dueCoverageComplete": True,
            "coverageComplete": False,
        }

    monkeypatch.setattr(verifier, "_locked_row_integrity", incomplete_integrity)

    result = verifier._verification_payload(SLATE, "lock", "test")

    progress = result["lock"]["perGameProgress"]
    assert progress["finalPerGameCutoffReached"] is True
    assert progress["fullSlateVectorEvaluationDue"] is True
    assert progress["dueGameCount"] == 2
    assert progress["dueMissingGameCount"] == 0
    assert observed == {
        "slateDate": SLATE,
        "expectedIdentities": ["game-one", "game-two"],
        "dueIdentities": ["game-one", "game-two"],
        "evaluateFullSlate": True,
    }
    assert result["blockers"] == [
        "LOCKED_ROWS_MISSING_VALID_FROZEN_FINGERPRINTS"
    ]
