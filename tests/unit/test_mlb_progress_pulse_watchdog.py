from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_mlb_progress_pulse_staleness.py"
SPEC = importlib.util.spec_from_file_location("check_mlb_progress_pulse_staleness", SCRIPT)
assert SPEC and SPEC.loader
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)

NOW = datetime(2026, 8, 26, 22, 0, tzinfo=timezone.utc)


def _comment(created_at: str) -> dict:
    return {
        "id": 1,
        "created_at": created_at,
        "html_url": "https://github.example/pulse",
        "body": f"<!-- {watchdog.STATE_MARKER}:fixture -->",
    }


def _evaluate(comments, runs=()):
    return watchdog.evaluate_staleness(
        comments,
        runs,
        now=NOW,
        stale_after_minutes=40,
        retry_cooldown_minutes=10,
    )


def test_stale_visible_pulse_requires_recovery_dispatch() -> None:
    result = _evaluate([_comment("2026-08-26T20:41:21Z")])

    assert result["stale"] is True
    assert result["dispatchRequired"] is True
    assert result["reason"] == "VISIBLE_PULSE_STALE"


def test_fresh_visible_pulse_does_not_dispatch() -> None:
    result = _evaluate([_comment("2026-08-26T21:37:00Z")])

    assert result["stale"] is False
    assert result["dispatchRequired"] is False
    assert result["reason"] == "VISIBLE_PULSE_FRESH"


def test_active_pulse_run_suppresses_duplicate_recovery_dispatch() -> None:
    result = _evaluate(
        [_comment("2026-08-26T20:41:21Z")],
        [{"id": 99, "status": "in_progress", "created_at": "2026-08-26T21:58:00Z"}],
    )

    assert result["dispatchRequired"] is False
    assert result["reason"] == "PULSE_RUN_ALREADY_ACTIVE"
    assert result["activeRunId"] == 99


def test_recent_failed_attempt_observes_retry_cooldown() -> None:
    result = _evaluate(
        [_comment("2026-08-26T20:41:21Z")],
        [{"id": 100, "status": "completed", "created_at": "2026-08-26T21:55:00Z"}],
    )

    assert result["dispatchRequired"] is False
    assert result["reason"] == "RECENT_PULSE_ATTEMPT_IN_COOLDOWN"


def test_current_fallback_run_is_excluded_from_active_run_check() -> None:
    result = watchdog.evaluate_staleness(
        [_comment("2026-08-26T20:41:21Z")],
        [{"id": 101, "status": "in_progress", "created_at": "2026-08-26T21:59:00Z"}],
        now=NOW,
        stale_after_minutes=40,
        retry_cooldown_minutes=10,
        current_run_id="101",
    )

    assert result["dispatchRequired"] is True
    assert result["activeRunId"] is None


def test_decision_only_fallback_run_does_not_create_false_retry_cooldown() -> None:
    result = _evaluate(
        [_comment("2026-08-26T20:41:21Z")],
        [
            {
                "id": 102,
                "event": "workflow_run",
                "status": "completed",
                "created_at": "2026-08-26T21:55:00Z",
            },
            {
                "id": 103,
                "event": "push",
                "status": "completed",
                "created_at": "2026-08-26T21:57:00Z",
            },
        ],
    )

    assert result["dispatchRequired"] is True
    assert result["reason"] == "VISIBLE_PULSE_STALE"


def test_workflow_uses_offset_schedule_and_staleness_gated_event_fallbacks() -> None:
    pulse = (ROOT / ".github/workflows/mlb-30m-progress-pulse.yml").read_text()

    assert "cron: '11,41 * * * *'" in pulse
    assert "cron: '7,37 * * * *'" not in pulse
    assert "cron: '0,30 * * * *'" not in pulse
    assert "workflow_run:" in pulse
    for producer in (
        "MLB Canonical Runtime Health Watch",
        "MLB Scoring Guard",
        "Deploy SAM to AWS",
        "MLB Production Source Contract",
        "Unified MLB learning recovery once",
    ):
        assert producer in pulse
    assert "runtime_reports/mlb_*.json" in pulse
    assert '[ "$EVENT_NAME" = "workflow_run" ] || [ "$EVENT_NAME" = "push" ]' in pulse
    assert "scripts/check_mlb_progress_pulse_staleness.py" in pulse
    assert "actions: read" in pulse
    assert "needs.pulse_decision.outputs.run_pulse == 'true'" in pulse
