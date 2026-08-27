from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "report_mlb_30m_progress.py"
SPEC = importlib.util.spec_from_file_location("report_mlb_30m_progress", SCRIPT)
assert SPEC and SPEC.loader
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


def _api(body: dict) -> dict:
    return {
        "ok": True,
        "functionName": "fixture",
        "payload": {"statusCode": 200, "body": json.dumps(body)},
    }


def _state(*, audit, autonomy: dict) -> dict:
    auto = {
        "ok": True,
        "slateDateEt": "2026-08-26",
        "targetDailyAccuracy": 0.70,
        "scheduledGames": 15,
        "cardPublished": True,
        "card": {
            "gameCount": 15,
            "decisionAuthority": "BEDROCK_LLM",
            "picks": [],
        },
        "audit": audit,
        "autonomyState": autonomy,
    }
    return reporter._extract_state(
        r7_invocation={"ok": True, "payload": {"ok": True}},
        model_invocation=_api(
            {
                "status": "NO_QUALIFIED_CHAMPION",
                "qualifiedChampionPresent": False,
                "publicationClosed": True,
                "retiredAuthoritySuppressed": True,
                "retiredV15_10Eligible": False,
            }
        ),
        today_invocation=_api({"count": 0, "winner_predictions": []}),
        auto_invocation=_api(auto),
        auto_invocations_35m=7,
        auto_errors_35m=0,
        continuity_run={"runId": 123, "workflowKind": "canonical_unified_recovery"},
        discovery_errors=[],
    )


def _trailing() -> dict:
    return {
        "recentDays": 1,
        "recentGradedPicks": 15,
        "recentCorrectPicks": 5,
        "recentAccuracy": 0.333333,
        "targetDailyAccuracy": 0.70,
    }


def test_current_slate_zero_correct_is_not_replaced_by_trailing_cohort() -> None:
    state = _state(
        audit={"graded": 1, "correct": 0, "accuracy": 0.0},
        autonomy=_trailing(),
    )

    auto = state["mlbAuto"]
    assert auto["gradingCohort"] == "current_slate"
    assert auto["gradedPicks"] == 1
    assert auto["correctPicks"] == 0
    assert auto["accuracy"] == 0.0
    assert auto["currentSlateGrading"]["valid"] is True
    assert auto["trailing14DayGrading"]["gradedPicks"] == 15
    assert auto["trailing14DayGrading"]["correctPicks"] == 5


def test_zero_graded_current_slate_remains_primary_and_does_not_fallback() -> None:
    state = _state(
        audit={"graded": 0, "correct": 0, "accuracy": None},
        autonomy=_trailing(),
    )

    auto = state["mlbAuto"]
    assert auto["gradingCohort"] == "current_slate"
    assert auto["gradedPicks"] == 0
    assert auto["correctPicks"] == 0
    assert auto["accuracy"] is None
    assert auto["gradingValid"] is True


def test_trailing_cohort_is_primary_only_when_current_audit_is_absent() -> None:
    state = _state(audit=None, autonomy=_trailing())

    auto = state["mlbAuto"]
    assert auto["gradingCohort"] == "trailing_14_days"
    assert auto["gradedPicks"] == 15
    assert auto["correctPicks"] == 5
    assert auto["accuracy"] == 0.333333
    assert auto["currentSlateGrading"]["available"] is False


def test_inconsistent_grading_tuple_is_blocked_and_accuracy_is_not_trusted() -> None:
    state = _state(
        audit={"graded": 1, "correct": 0, "accuracy": 0.333333},
        autonomy=_trailing(),
    )

    auto = state["mlbAuto"]
    assert auto["gradingValid"] is False
    assert auto["accuracy"] is None
    assert "ACCURACY_COUNT_MISMATCH" in auto["gradingErrors"]
    assert any(
        blocker.startswith("MLB_AUTO_CURRENT_SLATE_GRADING_INVALID:")
        for blocker in state["blockers"]
    )


def test_grading_delta_requires_same_valid_cohort() -> None:
    current = {
        "mlbAuto": {
            "gradingCohortKey": "current_slate:2026-08-26",
            "gradingValid": True,
            "gradedPicks": 2,
        }
    }
    prior_same = {
        "mlbAuto": {
            "gradingCohortKey": "current_slate:2026-08-26",
            "gradingValid": True,
            "gradedPicks": 1,
        }
    }
    prior_other = {
        "mlbAuto": {
            "gradingCohortKey": "trailing_14_days:as_of:2026-08-26",
            "gradingValid": True,
            "gradedPicks": 15,
        }
    }

    assert reporter._grading_delta(current, prior_same, "gradedPicks") == 1
    assert reporter._grading_delta(current, prior_other, "gradedPicks") is None


def test_latest_r7_run_prefers_canonical_unified_recovery(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run(args, **_kwargs):
        calls.append(args[-1])
        payload = {
            "workflow_runs": [
                {
                    "id": 42,
                    "status": "in_progress",
                    "event": "workflow_dispatch",
                    "html_url": "https://github.example/run/42",
                }
            ]
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr(reporter, "_run", fake_run)
    result = reporter._latest_continuity_run()

    assert result["runId"] == 42
    assert result["workflowKind"] == "canonical_unified_recovery"
    assert result["workflowFile"] == "unified-mlb-learning-recovery-once.yml"
    assert len(calls) == 1
    assert "unified-mlb-learning-recovery-once.yml" in calls[0]


def test_reporting_continuity_exposes_stale_visible_gap() -> None:
    result = reporter._reporting_continuity(
        {
            "createdAtUtc": "2026-08-26T20:41:21Z",
            "url": "https://github.example/pulse",
        },
        now=datetime(2026, 8, 26, 22, 0, tzinfo=timezone.utc),
    )

    assert result["previousPulseAgeMinutes"] == 78.65
    assert result["cadenceBreach"] is True
    assert result["targetCadenceMinutes"] == 30
    assert result["cadenceGraceMinutes"] == 5
    assert result["staleAfterMinutes"] == 35


def test_reporting_cadence_allows_exactly_30m_plus_5m_grace() -> None:
    result = reporter._reporting_continuity(
        {"createdAtUtc": "2026-08-26T21:25:00Z"},
        now=datetime(2026, 8, 26, 22, 0, tzinfo=timezone.utc),
    )

    assert result["previousPulseAgeMinutes"] == 35.0
    assert result["cadenceBreach"] is False
    assert result["staleAfterMinutes"] == 35


def test_reporting_cadence_breaches_immediately_after_35m_boundary() -> None:
    result = reporter._reporting_continuity(
        {"createdAtUtc": "2026-08-26T21:24:59Z"},
        now=datetime(2026, 8, 26, 22, 0, tzinfo=timezone.utc),
    )

    assert result["previousPulseAgeMinutes"] == 35.017
    assert result["cadenceBreach"] is True
    assert result["previousPulseAgeMinutes"] > result["staleAfterMinutes"]


def test_latest_visible_pulse_ignores_non_pulse_comments() -> None:
    state = {"generatedAtUtc": "2026-08-26T20:41:00Z"}
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    result = reporter._latest_visible_pulse(
        [
            {"body": f"<!-- {reporter.STATE_MARKER}:{encoded} -->", "id": 1},
            {"body": "ordinary comment", "id": 2},
        ]
    )

    assert result is not None
    assert result["commentId"] == 1
    assert result["state"] == state
