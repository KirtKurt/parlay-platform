from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import build_mlb_v8_hourly_report as hourly


def _base(now):
    return hourly.build_report(
        historical={
            "eligibleGameCount": 4114,
            "completeSlateCount": 332,
            "endDate": "2026-08-04",
            "currentDate": "2026-08-05",
            "targetSettledGames": 4149,
            "gamesUntilNextOptimization": 35,
            "phase": "WAITING_FOR_SETTLED_HORIZON",
            "revision": 5652,
            "networkRequestCount": 26320,
            "creditsConsumed": 263200,
            "stateUpdatedAtUtc": now.isoformat(),
            "latestAccuracy": {"settledGameCount": 3899},
            "championValidation": {"errors": ["no_active_champion"]},
            "cutoverValidation": {
                "errors": ["not_cut_over_before_first_promotion"]
            },
        },
        training={
            "ok": True,
            "createdAtUtc": now.isoformat(),
            "recordCountLoaded": 4114,
            "learningStatus": "LEARNING_EXECUTED_MARKET_BASELINE_RETAINED",
            "learningExecution": {
                "totalOptimizationSteps": 63360,
                "learnedCandidateCount": 96,
                "learnedEligibleCandidateCount": 0,
                "learnedCandidateSelected": False,
                "marketBaselineRetainedByGuard": True,
                "selectedFeatureGroup": "market_baseline",
            },
            "promotionGate": {
                "passed": False,
                "errors": ["learned_candidate_not_selected"],
            },
        },
        validation={},
        controller={
            "fullyAutonomous": True,
            "normalOperationManualInterventionRequired": False,
            "nextAction": "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH",
            "promotionRequested": False,
            "blockers": [],
        },
        prospective={
            "status": "WAITING_FOR_RETROSPECTIVE_GATE",
            "wins": 7,
            "losses": 3,
            "blockers": ["retrospective_promotion_gate_not_passed"],
        },
        context={
            "createdAtUtc": now.isoformat(),
            "authority": "V8_HISTORICAL_OFFICIAL_CONTEXT_SHADOW_ONLY",
            "processedGameCount": 25,
            "eligibleGameCount": 0,
            "newEligibleGameCount": 0,
            "remainingGameCount": 4089,
            "providerCallsMade": 36,
            "activePointerRevision": 64,
            "progressMade": True,
            "blockers": [],
        },
        shadow={},
        promotion={"promoted": False, "productionAuthorityChanged": False},
        workflows={
            "workflow_runs": [
                {
                    "id": 123,
                    "name": "MLB V8 Autonomous Controller",
                    "path": ".github/workflows/mlb-v8-autonomous-controller.yml",
                    "status": "completed",
                    "conclusion": "success",
                    "updated_at": now.isoformat(),
                    "head_sha": "abc",
                }
            ],
            "artifacts": [
                {
                    "name": "evidence",
                    "size_in_bytes": 100,
                    "workflow_run": {"id": 123},
                }
            ],
        },
        previous={
            "historicalEligibleGames": 4099,
            "completedSlateCount": 331,
            "optimizerRevision": 5649,
            "networkRequests": 26300,
            "creditsConsumed": 263000,
            "settledGames": 3899,
        },
        source_status={},
        now=now,
        issue_number=457,
    )


def test_report_preserves_deltas_and_does_not_calculate_accuracy():
    report = _base(datetime(2026, 8, 5, 14, tzinfo=timezone.utc))

    metrics = report["historicalBackfill"]["metrics"]
    assert metrics["eligibleGames"]["delta"] == 15
    assert metrics["completedSlates"]["delta"] == 1
    assert metrics["revision"]["delta"] == 3
    assert metrics["networkRequests"]["delta"] == 20
    assert metrics["creditsConsumed"]["delta"] == 200
    assert metrics["remainingSlates"]["status"] == "UNAVAILABLE"
    assert report["prospectiveAudit"]["wins"] == 7
    assert report["prospectiveAudit"]["losses"] == 3
    assert report["prospectiveAudit"]["sourceReportedOverallAccuracy"] is None
    assert report["sourcePolicy"]["accuracyDerivedByReporter"] is False


def test_report_keeps_historical_and_settled_populations_incomparable():
    report = _base(datetime(2026, 8, 5, 14, tzinfo=timezone.utc))

    assert (
        report["historicalBackfill"][
            "eligibleAndSettledPopulationsComparable"
        ]
        is False
    )
    assert report["trainer"]["metrics"]["settledGames"]["value"] == 3899
    assert report["historicalBackfill"]["metrics"]["eligibleGames"]["value"] == 4114


def test_marker_round_trip_is_machine_readable():
    report = _base(datetime(2026, 8, 5, 14, tzinfo=timezone.utc))

    restored = hourly.extract_previous_state(report["markdown"])

    assert restored["historicalEligibleGames"] == 4114
    assert restored["contextPointerRevision"] == 64


def test_stale_source_is_labeled_stale():
    now = datetime(2026, 8, 5, 14, tzinfo=timezone.utc)
    report = hourly.build_report(
        historical={
            "eligibleGameCount": 1,
            "completeSlateCount": 1,
            "endDate": "2026-08-01",
            "currentDate": "2026-08-02",
            "phase": "WAITING_FOR_SETTLED_HORIZON",
            "stateUpdatedAtUtc": (now - timedelta(hours=3)).isoformat(),
        },
        training={},
        validation={},
        controller={},
        prospective={},
        context={},
        shadow={},
        promotion={},
        workflows={},
        previous={},
        source_status={},
        now=now,
        issue_number=457,
    )

    assert report["freshness"]["historical"] == "STALE"


def test_explicit_source_accuracy_is_reported_without_recomputation():
    now = datetime(2026, 8, 5, 14, tzinfo=timezone.utc)
    report = hourly.build_report(
        historical={},
        training={
            "walkForwardMetrics": {
                "gameCount": 267,
                "correctCount": 156,
                "overallAccuracy": 0.58426966,
            }
        },
        validation={},
        controller={},
        prospective={"overallAccuracy": 0.61, "wins": 10, "losses": 9},
        context={},
        shadow={"overallAccuracy": 0.59},
        promotion={},
        workflows={},
        previous={},
        source_status={},
        now=now,
        issue_number=457,
    )

    assert (
        report["trainer"]["retrospectiveValidation"]["walkForward"][
            "accuracy"
        ]
        == 0.58426966
    )
    assert report["prospectiveAudit"]["sourceReportedOverallAccuracy"] == 0.61
    assert report["shadowEvaluation"]["sourceReportedOverallAccuracy"] == 0.59


def test_json_output_excludes_rendered_markdown(tmp_path, monkeypatch):
    files = {}
    for name in (
        "historical",
        "training",
        "validation",
        "controller",
        "prospective",
        "context",
        "shadow",
        "promotion",
        "workflows",
    ):
        path = tmp_path / f"{name}.json"
        path.write_text("{}")
        files[name] = path
    previous = tmp_path / "previous.md"
    previous.write_text("")
    output_json = tmp_path / "report.json"
    output_md = tmp_path / "report.md"
    argv = [
        "prog",
        "--historical-status",
        str(files["historical"]),
        "--training-report",
        str(files["training"]),
        "--validation-report",
        str(files["validation"]),
        "--controller-report",
        str(files["controller"]),
        "--prospective-audit",
        str(files["prospective"]),
        "--context-report",
        str(files["context"]),
        "--shadow-report",
        str(files["shadow"]),
        "--promotion-report",
        str(files["promotion"]),
        "--workflows",
        str(files["workflows"]),
        "--previous-markdown",
        str(previous),
        "--output-json",
        str(output_json),
        "--output-markdown",
        str(output_md),
        "--issue-number",
        "457",
    ]
    monkeypatch.setattr("sys.argv", argv)

    assert hourly.main() == 0
    payload = json.loads(output_json.read_text())
    assert "markdown" not in payload
    assert output_md.read_text().startswith("# MLB V8 Hourly Numerical Status")
