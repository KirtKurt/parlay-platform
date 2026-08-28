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


def _api(body: dict, status_code: int = 200) -> dict:
    return {
        "ok": True,
        "functionName": "fixture",
        "payload": {"statusCode": status_code, "body": json.dumps(body)},
    }


def _no_champion_body(*, include_predictions: bool = False) -> dict:
    body = {
        "ok": False,
        "status": "NO_QUALIFIED_CHAMPION",
        "error": "NO_QUALIFIED_CHAMPION",
        "publicationClosed": True,
        "productionSelectionAllowed": False,
        "qualifiedChampionRequired": True,
        "qualifiedChampionPresent": False,
        "r7ChampionQualified": False,
        "primaryAlgorithmActive": False,
        "retiredAuthoritySuppressed": True,
        "retiredV15_10Eligible": False,
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "requestedAuthority": "AWS_ML_PROSPECTIVE_R7",
        "model_version": None,
        "primaryAlgorithm": None,
        "soleProductionAlgorithm": None,
        "game_winner_model": None,
        "r7DeploymentIdentity": None,
    }
    if include_predictions:
        body.update({"count": 0, "winner_predictions": [], "predictions": []})
    return body


def _qualified_body() -> dict:
    return {
        "ok": True,
        "publicationClosed": False,
        "productionSelectionAllowed": True,
        "qualifiedChampionRequired": True,
        "qualifiedChampionPresent": True,
        "r7ChampionQualified": True,
        "primaryAlgorithmActive": True,
        "requestedAuthority": "AWS_ML_PROSPECTIVE_R7",
        "model_version": "mlb-r7-qualified",
        "primaryAlgorithm": "AWS_ML_PROSPECTIVE_R7",
        "r7DeploymentIdentity": "sha256:qualified",
    }


def _state(
    *,
    audit,
    autonomy: dict,
    now: datetime = datetime(2026, 8, 26, 20, 0, tzinfo=timezone.utc),
    auto_overrides: dict | None = None,
    model_body: dict | None = None,
    model_http_status: int = 503,
    today_body: dict | None = None,
    today_http_status: int = 503,
) -> dict:
    auto = {
        "ok": True,
        "slateDateEt": "2026-08-26",
        "targetDailyAccuracy": 0.70,
        "scheduledGames": 15,
        "cardPublished": True,
        "deadline": {
            "firstGameUtc": "2026-08-26T22:00:00+00:00",
            "publishDeadlineUtc": "2026-08-26T21:50:00+00:00",
        },
        "card": {
            "gameCount": 15,
            "decisionAuthority": "BEDROCK_LLM",
            "picks": [],
        },
        "audit": audit,
        "autonomyState": autonomy,
    }
    if auto_overrides:
        auto.update(auto_overrides)
    return reporter._extract_state(
        r7_invocation={"ok": True, "payload": {"ok": True}},
        model_invocation=_api(
            model_body if model_body is not None else _no_champion_body(),
            model_http_status,
        ),
        today_invocation=_api(
            today_body
            if today_body is not None
            else _no_champion_body(include_predictions=True),
            today_http_status,
        ),
        auto_invocation=_api(auto),
        auto_invocations_35m=7,
        auto_errors_35m=0,
        continuity_run={"runId": 123, "workflowKind": "canonical_unified_recovery"},
        discovery_errors=[],
        now=now,
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
    assert auto["gradingTargetMet"] is None
    assert auto["accuracy"] is None
    assert "ACCURACY_COUNT_MISMATCH" in auto["gradingErrors"]
    assert any(
        blocker.startswith("MLB_AUTO_CURRENT_SLATE_GRADING_INVALID:")
        for blocker in state["blockers"]
    )


def test_valid_grading_distinguishes_missed_accuracy_target() -> None:
    state = _state(audit=None, autonomy=_trailing())

    trailing = state["mlbAuto"]["trailing14DayGrading"]
    assert trailing["valid"] is True
    assert trailing["targetMet"] is False
    assert state["mlbAuto"]["gradingValid"] is True
    assert state["mlbAuto"]["gradingTargetMet"] is False
    assert any(
        blocker.startswith("MLB_AUTO_TRAILING_14_DAY_ACCURACY_BELOW_TARGET:5/15:")
        for blocker in state["blockers"]
    )
    comment = reporter._comment(state, None)
    assert "telemetry valid · 🔴 target missed" in comment


def test_valid_grading_reports_target_met_separately() -> None:
    autonomy = {
        "recentDays": 1,
        "recentGradedPicks": 10,
        "recentCorrectPicks": 8,
        "recentAccuracy": 0.8,
        "targetDailyAccuracy": 0.7,
    }
    state = _state(audit=None, autonomy=autonomy)

    trailing = state["mlbAuto"]["trailing14DayGrading"]
    assert trailing["valid"] is True
    assert trailing["targetMet"] is True
    assert not any("ACCURACY_BELOW_TARGET" in item for item in state["blockers"])


def test_unpublished_card_before_final_window_is_collecting_not_due() -> None:
    state = _state(
        audit=None,
        autonomy=_trailing(),
        now=datetime(2026, 8, 26, 21, 0, tzinfo=timezone.utc),
        auto_overrides={"cardPublished": False, "card": None},
    )

    auto = state["mlbAuto"]
    assert auto["publicationPhase"] == "COLLECTING_NOT_DUE"
    assert auto["finalWindowStartUtc"] == "2026-08-26T21:30:00+00:00"
    assert auto["minutesUntilFinalWindow"] == 30.0
    assert auto["minutesUntilDeadline"] == 50.0
    assert "MLB_AUTO_PUBLICATION_DEADLINE_MISSED" not in state["blockers"]
    prior = json.loads(json.dumps(state))
    overall, _, _ = reporter._overall_direction(state, prior)
    assert overall == "🟡 COLLECTING / NOT DUE"
    comment = reporter._comment(state, prior)
    assert "⚪ collecting; not due" in comment
    assert "phase `COLLECTING_NOT_DUE`" in comment


def test_unpublished_card_inside_final_window_is_due_but_not_late() -> None:
    state = _state(
        audit=None,
        autonomy=_trailing(),
        now=datetime(2026, 8, 26, 21, 30, tzinfo=timezone.utc),
        auto_overrides={"cardPublished": False, "card": None},
    )

    assert state["mlbAuto"]["publicationPhase"] == "FINAL_WINDOW"
    assert "MLB_AUTO_PUBLICATION_DEADLINE_MISSED" not in state["blockers"]


def test_unpublished_card_after_deadline_is_readiness_gated_when_fail_closed() -> None:
    state = _state(
        audit=None,
        autonomy=_trailing(),
        now=datetime(2026, 8, 26, 21, 50, 1, tzinfo=timezone.utc),
        auto_overrides={"cardPublished": False, "card": None},
    )

    assert state["mlb"]["authorityReadinessState"] == "NO_QUALIFIED_CHAMPION_SUPPRESSED"
    assert state["mlb"]["authorityReadinessValid"] is True
    assert state["mlbAuto"]["publicationPhase"] == "AUTHORITY_READINESS_GATED"
    assert "MLB_AUTO_PUBLICATION_DEADLINE_MISSED" not in state["blockers"]
    overall, _, _ = reporter._overall_direction(state, None)
    assert overall == "🟡 AUTHORITY READINESS GATED"


def test_qualified_authority_without_postdeadline_card_is_deadline_missed() -> None:
    state = _state(
        audit=None,
        autonomy=_trailing(),
        now=datetime(2026, 8, 26, 21, 50, 1, tzinfo=timezone.utc),
        auto_overrides={"cardPublished": False, "card": None},
        model_body=_qualified_body(),
        model_http_status=200,
        today_body={"ok": True, "count": 0, "winner_predictions": []},
        today_http_status=200,
    )

    assert state["mlb"]["authorityReadinessState"] == "QUALIFIED_AUTHORITY_READY"
    assert state["mlbAuto"]["publicationPhase"] == "DEADLINE_MISSED"
    assert "MLB_AUTO_PUBLICATION_DEADLINE_MISSED" in state["blockers"]


def test_incomplete_no_champion_evidence_does_not_suppress_deadline_failure() -> None:
    incomplete = _no_champion_body()
    incomplete.pop("productionSelectionAllowed")
    state = _state(
        audit=None,
        autonomy=_trailing(),
        now=datetime(2026, 8, 26, 21, 50, 1, tzinfo=timezone.utc),
        auto_overrides={"cardPublished": False, "card": None},
        model_body=incomplete,
    )

    assert state["mlb"]["authorityReadinessState"] == "AUTHORITY_READINESS_UNKNOWN"
    assert state["mlbAuto"]["publicationPhase"] == "DEADLINE_MISSED"
    assert "MLB_AUTO_PUBLICATION_DEADLINE_MISSED" in state["blockers"]
    assert any(
        blocker.startswith("MLB_AUTHORITY_READINESS_UNKNOWN:")
        for blocker in state["blockers"]
    )


def test_nonzero_fail_closed_predictions_do_not_suppress_deadline_failure() -> None:
    leaking_today = _no_champion_body(include_predictions=True)
    leaking_today.update(
        {"count": 1, "winner_predictions": [{"gamePk": 1}], "predictions": [{"gamePk": 1}]}
    )
    state = _state(
        audit=None,
        autonomy=_trailing(),
        now=datetime(2026, 8, 26, 21, 50, 1, tzinfo=timezone.utc),
        auto_overrides={"cardPublished": False, "card": None},
        today_body=leaking_today,
    )

    assert state["mlb"]["authorityReadinessState"] == "AUTHORITY_READINESS_UNKNOWN"
    assert "TODAY_WINNER_PREDICTION_COUNT_NOT_ZERO" in state["mlb"]["authorityReadinessErrors"]
    assert state["mlbAuto"]["publicationPhase"] == "DEADLINE_MISSED"


def test_no_champion_count_must_be_exact_integer_zero() -> None:
    for invalid_count in (False, 0.0, "0"):
        malformed_today = _no_champion_body(include_predictions=True)
        malformed_today["count"] = invalid_count
        state = _state(
            audit=None,
            autonomy=_trailing(),
            now=datetime(2026, 8, 26, 21, 50, 1, tzinfo=timezone.utc),
            auto_overrides={"cardPublished": False, "card": None},
            today_body=malformed_today,
        )

        assert state["mlb"]["authorityReadinessState"] == "AUTHORITY_READINESS_UNKNOWN"
        assert "TODAY_WINNER_PREDICTION_COUNT_NOT_ZERO" in state["mlb"]["authorityReadinessErrors"]
        assert state["mlbAuto"]["publicationPhase"] == "DEADLINE_MISSED"


def test_current_authority_evidence_cannot_suppress_a_prior_auto_slate() -> None:
    state = _state(
        audit=None,
        autonomy=_trailing(),
        now=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        auto_overrides={"cardPublished": False, "card": None},
    )

    assert state["mlb"]["authorityReadinessState"] == "NO_QUALIFIED_CHAMPION_SUPPRESSED"
    assert state["mlb"]["authorityEvidenceSlateDateEt"] == "2026-08-27"
    assert state["mlb"]["authorityEvidenceAppliesToAutoSlate"] is False
    assert state["mlbAuto"]["publicationPhase"] == "DEADLINE_MISSED"
    assert "MLB_AUTO_PUBLICATION_DEADLINE_MISSED" in state["blockers"]
    assert any(
        blocker.startswith("MLB_AUTO_AUTHORITY_EVIDENCE_SLATE_MISMATCH:")
        for blocker in state["blockers"]
    )


def test_published_card_remains_published_after_deadline() -> None:
    state = _state(
        audit=None,
        autonomy=_trailing(),
        now=datetime(2026, 8, 26, 22, 0, tzinfo=timezone.utc),
    )

    assert state["mlbAuto"]["publicationPhase"] == "PUBLISHED"
    assert "MLB_AUTO_PUBLICATION_DEADLINE_MISSED" not in state["blockers"]


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


def test_pick_count_delta_does_not_compare_different_slates() -> None:
    current = {"mlbAuto": {"slateDateEt": "2026-08-28", "pickCount": 0}}
    prior = {"mlbAuto": {"slateDateEt": "2026-08-27", "pickCount": 7}}

    assert reporter._slate_delta(current, prior, "pickCount") is None


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
