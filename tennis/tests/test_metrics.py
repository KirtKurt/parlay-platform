from __future__ import annotations

import json

from metrics import emit_report_metrics


def test_compact_emf_metrics_cover_quality_quota_and_archive(capsys):
    report = {
        "mode": "RULE_BASED_SHADOW",
        "run_status": "PARTIAL_RETRY_REQUIRED",
        "retry_required": True,
        "odds_endpoint_calls": 2,
        "odds_meta": {"quotaStatus": {"requestsRemaining": 500}},
        "slot_utc": "2026-07-22T10:00:00+00:00",
        "observed_at_utc": "2026-07-22T10:00:03+00:00",
        "slate_runs": [
            {
                "event_count": 4,
                "snapshot_created_count": 2,
                "snapshot_deduped_count": 1,
                "events_without_odds_count": 1,
                "events_started_during_fetch_count": 1,
                "archive_failure_count": 1,
                "complete": False,
            }
        ],
    }

    values = emit_report_metrics(
        report, duration_ms=123.4, namespace="Inqsi/TennisCollector"
    )
    envelope = json.loads(capsys.readouterr().out)

    assert values["CoveragePercent"] == 75.0
    assert values["RetryRequired"] == 1.0
    assert values["QuotaRemaining"] == 500.0
    assert values["CollectorFreshnessSeconds"] == 3.0
    assert values["PostStartExclusions"] == 1.0
    assert values["ArchiveFailures"] == 1.0
    assert envelope["_aws"]["CloudWatchMetrics"][0]["Namespace"] == (
        "Inqsi/TennisCollector"
    )
    assert envelope["Sport"] == "tennis"


def test_retry_coverage_uses_cumulative_covered_events(capsys):
    report = {
        "mode": "RULE_BASED_SHADOW",
        "run_status": "PULL_STORED",
        "retry_required": False,
        "slate_runs": [
            {
                "event_count": 2,
                "covered_event_count": 2,
                "snapshot_created_count": 1,
                "snapshot_deduped_count": 0,
                "complete": True,
            }
        ],
    }

    values = emit_report_metrics(
        report, duration_ms=10.0, namespace="Inqsi/TennisCollector"
    )
    capsys.readouterr()

    assert values["StoredSnapshots"] == 2.0
    assert values["CoveragePercent"] == 100.0
