from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_mlb_progress_pulse_staleness.py"
SPEC = importlib.util.spec_from_file_location("check_mlb_progress_pulse_staleness", SCRIPT)
assert SPEC and SPEC.loader
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)

NOW = datetime(2026, 8, 26, 22, 0, tzinfo=timezone.utc)


def _comment(
    created_at: str,
    *,
    url: str = "https://github.example/pulse",
    login: str = "github-actions[bot]",
    author_id: int = 41898282,
    author_type: str = "Bot",
) -> dict:
    return {
        "id": 1,
        "created_at": created_at,
        "html_url": url,
        "body": f"<!-- {watchdog.STATE_MARKER}:fixture -->",
        "user": {
            "login": login,
            "id": author_id,
            "type": author_type,
        },
    }


def _evaluate(comments, runs=()):
    return watchdog.evaluate_staleness(
        comments,
        runs,
        now=NOW,
        stale_after_minutes=28,
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


def test_exact_28_minute_age_remains_before_dispatch_boundary() -> None:
    result = _evaluate([_comment("2026-08-26T21:32:00Z")])

    assert result["visiblePulseAgeMinutes"] == 28.0
    assert result["staleAfterMinutes"] == 28
    assert result["stale"] is False
    assert result["dispatchRequired"] is False


def test_age_immediately_above_28_minutes_requires_fallback() -> None:
    result = _evaluate([_comment("2026-08-26T21:31:59Z")])

    assert result["visiblePulseAgeMinutes"] == 28.017
    assert result["staleAfterMinutes"] == 28
    assert result["stale"] is True
    assert result["dispatchRequired"] is True


def test_five_minute_phase_geometry_leaves_runtime_margin_inside_35_minutes() -> None:
    poll_seconds = 5 * 60
    dispatch_after_seconds = 28 * 60
    observed_runtime_budget_seconds = 60
    objective_seconds = 35 * 60
    first_stale_ages = []

    # Cover every whole-second phase between a visible pulse and the watchdog.
    for seconds_after_previous_tick in range(poll_seconds):
        age_at_check = poll_seconds - seconds_after_previous_tick
        while age_at_check <= dispatch_after_seconds:
            age_at_check += poll_seconds
        first_stale_ages.append(age_at_check)

    assert max(first_stale_ages) == 33 * 60
    assert max(first_stale_ages) + observed_runtime_budget_seconds == 34 * 60
    assert max(first_stale_ages) + observed_runtime_budget_seconds < objective_seconds


def test_public_commenter_cannot_spoof_a_fresh_pulse_marker() -> None:
    result = _evaluate(
        [
            _comment(
                "2026-08-26T21:59:00Z",
                url="https://github.example/spoof",
                login="public-commenter",
                author_id=991,
                author_type="User",
            ),
            _comment("2026-08-26T20:41:21Z", url="https://github.example/real"),
        ]
    )

    assert result["stale"] is True
    assert result["dispatchRequired"] is True
    assert result["visiblePulseUrl"] == "https://github.example/real"
    assert result["ignoredUntrustedMarkerCount"] == 1


def test_marker_without_exact_immutable_bot_identity_is_not_visible() -> None:
    result = _evaluate(
        [
            _comment(
                "2026-08-26T21:59:00Z",
                login="github-actions[bot]",
                author_id=99999999,
                author_type="Bot",
            )
        ]
    )

    assert result["stale"] is True
    assert result["dispatchRequired"] is True
    assert result["reason"] == "NO_VISIBLE_PULSE"
    assert result["visiblePulseAtUtc"] is None
    assert result["trustedPulseAuthorLogin"] == "github-actions[bot]"


def test_comment_fetch_is_one_bounded_newest_first_repository_request(monkeypatch) -> None:
    calls: list[str] = []

    def fake_gh_json(path: str):
        calls.append(path)
        newest = _comment("2026-08-26T21:59:00Z")
        newest.update(
            {
                "id": 3,
                "issue_url": "https://api.github.com/repos/KirtKurt/parlay-platform/issues/567",
            }
        )
        unrelated = _comment("2026-08-26T21:58:00Z")
        unrelated.update(
            {
                "id": 2,
                "issue_url": "https://api.github.com/repos/KirtKurt/parlay-platform/issues/568",
            }
        )
        older = _comment("2026-08-26T21:57:00Z")
        older.update(
            {
                "id": 1,
                "issue_url": "https://api.github.com/repos/KirtKurt/parlay-platform/issues/567",
            }
        )
        return [newest, unrelated, older]

    monkeypatch.setattr(watchdog, "_gh_json", fake_gh_json)

    comments = watchdog._issue_comments("KirtKurt/parlay-platform", 567)

    assert [comment["id"] for comment in comments] == [3, 1]
    assert calls == [
        "repos/KirtKurt/parlay-platform/issues/comments?"
        "sort=created&direction=desc&per_page=100&page=1"
    ]


def test_checker_source_pins_trusted_bot_and_has_no_history_walk() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'PULSE_AUTHOR_LOGIN = "github-actions[bot]"' in source
    assert "PULSE_AUTHOR_ID = 41898282" in source
    assert 'PULSE_AUTHOR_TYPE = "Bot"' in source
    assert "COMMENT_WINDOW = 100" in source
    assert "sort=created&direction=desc" in source
    assert "for page in range" not in source
    assert "issue_comment_pagination_limit_exceeded" not in source


def test_active_forced_dispatch_suppresses_competing_recovery_race() -> None:
    result = _evaluate(
        [_comment("2026-08-26T20:41:21Z")],
        [
            {
                "id": 99,
                "event": "workflow_dispatch",
                "status": "in_progress",
                "created_at": "2026-08-26T21:58:00Z",
            }
        ],
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
        stale_after_minutes=35,
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


def test_workflow_stale_gates_automatic_triggers_and_forces_explicit_dispatch() -> None:
    pulse = (ROOT / ".github/workflows/mlb-30m-progress-pulse.yml").read_text()

    assert "cron: '11,41 * * * *'" in pulse
    assert "cron: '7,37 * * * *'" not in pulse
    assert "cron: '0,30 * * * *'" not in pulse
    assert "workflow_run:" in pulse
    workflow_run_block = pulse.split("workflow_run:", 1)[1].split(
        "workflow_dispatch:", 1
    )[0]
    assert "branches: [main]" in workflow_run_block
    for producer in (
        "MLB Canonical Runtime Health Watch",
        "MLB Scoring Guard",
        "Deploy SAM to AWS",
        "Verify MLB Scoring Fix After Deploy",
        "MLB Production Source Contract",
        "Unified MLB learning recovery once",
    ):
        assert producer in pulse
    assert "runtime_reports/mlb_*.json" in pulse
    assert (
        'if [ "$EVENT_NAME" = "workflow_dispatch" ] && '
        '[ "$WORKFLOW_DISPATCH_FORCE" = "true" ]; then'
    ) in pulse
    assert "WORKFLOW_DISPATCH_FORCE: ${{ inputs.force }}" in pulse
    assert "reason=EXPLICIT_WORKFLOW_DISPATCH" in pulse
    assert "reason=DIRECT_PULSE_TRIGGER" not in pulse
    assert "scripts/check_mlb_progress_pulse_staleness.py" in pulse
    assert "MLB_PROGRESS_STALE_AFTER_MINUTES: '28'" in pulse
    assert '--stale-after-minutes "$MLB_PROGRESS_STALE_AFTER_MINUTES"' in pulse
    assert '--current-run-id "$GITHUB_RUN_ID"' in pulse
    assert "--retry-cooldown-minutes 10" in pulse
    assert "group: mlb-30m-production-progress-pulse" in pulse
    assert "cancel-in-progress: false" in pulse
    assert "--stale-after-minutes 35" not in pulse
    assert "--stale-after-minutes 40" not in pulse
    assert "actions: read" in pulse
    assert "needs.pulse_decision.outputs.run_pulse == 'true'" in pulse


def test_cli_reads_28_minute_dispatch_threshold_from_environment(monkeypatch) -> None:
    observed: dict[str, int] = {}

    def fake_evaluate(comments, runs, **kwargs):
        observed["stale_after_minutes"] = kwargs["stale_after_minutes"]
        return {"stale": False, "dispatchRequired": False, "reason": "fixture"}

    monkeypatch.setenv("MLB_PROGRESS_STALE_AFTER_MINUTES", "28")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    monkeypatch.setattr(watchdog, "_issue_comments", lambda *_args: [])
    monkeypatch.setattr(watchdog, "_workflow_runs", lambda *_args: [])
    monkeypatch.setattr(watchdog, "evaluate_staleness", fake_evaluate)

    assert watchdog.main() == 0
    assert observed["stale_after_minutes"] == 28


def test_independent_cadence_watchdog_is_stale_gated_and_read_only() -> None:
    watchdog_workflow = (
        ROOT / ".github/workflows/mlb-progress-pulse-cadence-watchdog.yml"
    ).read_text()

    assert "cron: '4/5 * * * *'" in watchdog_workflow
    assert "Best effort only" in watchdog_workflow
    assert "provides no" in watchdog_workflow
    assert "delivery SLA" in watchdog_workflow
    assert '--stale-after-minutes "$MLB_PROGRESS_STALE_AFTER_MINUTES"' in watchdog_workflow
    assert "MLB_PROGRESS_STALE_AFTER_MINUTES: '28'" in watchdog_workflow
    assert "needs.decide.outputs.dispatch_required == 'true'" in watchdog_workflow
    assert "gh workflow run mlb-30m-progress-pulse.yml" in watchdog_workflow
    assert "--ref main" in watchdog_workflow
    assert "--field force=false" in watchdog_workflow
    assert "actions: write" in watchdog_workflow
    assert "issues: write" not in watchdog_workflow
    assert "aws-actions/configure-aws-credentials" not in watchdog_workflow
    assert "AWS_ACCESS_KEY_ID" not in watchdog_workflow
    assert "scripts/check_mlb_progress_pulse_staleness.py" in watchdog_workflow
    assert "--retry-cooldown-minutes 10" in watchdog_workflow
    assert "group: mlb-progress-pulse-cadence-watchdog" in watchdog_workflow
    assert "cancel-in-progress: false" in watchdog_workflow


def test_default_manual_dispatch_remains_explicitly_forced() -> None:
    pulse = (ROOT / ".github/workflows/mlb-30m-progress-pulse.yml").read_text()

    dispatch_block = pulse.split("workflow_dispatch:", 1)[1].split("push:", 1)[0]
    assert "force:" in dispatch_block
    assert "default: true" in dispatch_block
    assert "type: boolean" in dispatch_block

    force_condition = (
        'if [ "$EVENT_NAME" = "workflow_dispatch" ] && '
        '[ "$WORKFLOW_DISPATCH_FORCE" = "true" ]; then'
    )
    decision_tail = pulse.split(force_condition, 1)[1]
    forced_block, stale_gated_tail = decision_tail.split("else", 1)
    stale_gated_block = stale_gated_tail.split("fi", 1)[0]
    assert "dispatch_required=true" in forced_block
    assert "reason=EXPLICIT_WORKFLOW_DISPATCH" in forced_block
    assert "python scripts/check_mlb_progress_pulse_staleness.py" in stale_gated_block
    assert '--current-run-id "$GITHUB_RUN_ID"' in stale_gated_block


def test_watchdog_rechecks_after_decision_dispatch_race_and_suppresses_duplicate() -> None:
    initial_watchdog_decision = _evaluate([_comment("2026-08-26T20:41:21Z")])
    assert initial_watchdog_decision["dispatchRequired"] is True

    # Another primary event posts while the watchdog dispatch is being queued.
    dispatched_primary_recheck = _evaluate([_comment("2026-08-26T21:59:30Z")])

    assert dispatched_primary_recheck["stale"] is False
    assert dispatched_primary_recheck["dispatchRequired"] is False
    assert dispatched_primary_recheck["reason"] == "VISIBLE_PULSE_FRESH"


def test_fresh_fallback_pulse_suppresses_following_scheduled_duplicate() -> None:
    result = _evaluate(
        [_comment("2026-08-26T21:59:30Z")],
        [
            {
                "id": 104,
                "event": "workflow_run",
                "status": "completed",
                "created_at": "2026-08-26T21:59:30Z",
            }
        ],
    )

    assert result["stale"] is False
    assert result["dispatchRequired"] is False
    assert result["reason"] == "VISIBLE_PULSE_FRESH"
