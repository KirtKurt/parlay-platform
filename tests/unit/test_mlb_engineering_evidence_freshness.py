from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from engineering_agent.evidence_freshness import (
    filter_superseded_fundamentals_evidence,
    recent_fundamentals_repair_cutoff,
)


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def _history(*lines: str) -> str:
    return json.dumps(
        {
            "kind": "recent_main_history",
            "currentCommit": "head",
            "content": "\n".join(lines),
        }
    )


def _source_history(*lines: str) -> str:
    return json.dumps(
        {
            "kind": "operational_source_history",
            "topic": "mlb_fundamentals_runtime",
            "readOnly": True,
            "productionAuthorityChanged": False,
            "content": "\n".join(lines),
        }
    )


def _report(path: str, at: str, content: str) -> str:
    return json.dumps(
        {
            "kind": "current_runtime_report",
            "path": path,
            "observedEpoch": _epoch(at),
            "content": content,
        }
    )


def test_newer_reviewed_fundamentals_repair_supersedes_older_scoring_snapshot() -> None:
    evidence = "\n".join(
        [
            _history(
                "abc 2026-09-11T14:40:31+00:00 Merge PR #786: bind MLB provenance diagnostics to durable prediction proof"
            ),
            _report(
                "runtime_reports/mlb_scoring_fix_post_deploy_latest.json",
                "2026-09-11T13:16:50Z",
                '{"scoringSummary":{"fundamentalsNotActiveCount":15,"fundamentalsShadowEvaluatedCount":0}}',
            ),
            json.dumps(
                {
                    "kind": "planner_feature_pipeline_evidence_summary",
                    "hits": [
                        {
                            "path": "runtime_reports/mlb_scoring_fix_post_deploy_latest.json",
                            "observedEpoch": _epoch("2026-09-11T13:16:50Z"),
                            "status": "FUNDAMENTALS_FEATURE_PIPELINE_INACTIVE",
                        }
                    ],
                }
            ),
        ]
    ) + "\n"

    filtered = filter_superseded_fundamentals_evidence(evidence)

    assert "mlb_scoring_fix_post_deploy_latest.json" not in filtered
    assert "FUNDAMENTALS_FEATURE_PIPELINE_INACTIVE" not in filtered
    assert "evidence_supersession_receipt" in filtered
    assert "removedRuntimeReportCount\": 1" in filtered


def test_path_specific_source_history_is_durable_when_recent_log_has_only_noise() -> None:
    evidence = "\n".join(
        [
            _history(
                "noise1 2026-09-11T15:00:00+00:00 Publish complete tennis daily card [skip ci]",
                "noise2 2026-09-11T14:58:00+00:00 Merge PR #787: disable unsafe manual MLB planner dispatch",
            ),
            _source_history(
                "source1 2026-09-11T14:40:31+00:00 operational-source-change",
                "source2 2026-09-11T14:35:00+00:00 operational-source-change",
            ),
            _report(
                "runtime_reports/mlb_scoring_guard_status_latest.json",
                "2026-09-11T13:46:00Z",
                '{"blockers":["source_failure"]}',
            ),
        ]
    ) + "\n"

    assert recent_fundamentals_repair_cutoff(evidence) == _epoch("2026-09-11T14:40:31Z")
    filtered = filter_superseded_fundamentals_evidence(evidence)
    assert "mlb_scoring_guard_status_latest.json" not in filtered
    assert "evidence_supersession_receipt" in filtered


def test_source_history_outranks_later_subject_only_repair_text() -> None:
    evidence = "\n".join(
        [
            _history(
                "later 2026-09-11T15:05:00+00:00 Fix MLB fundamentals documentation and notes"
            ),
            _source_history(
                "source1 2026-09-11T14:40:31+00:00 operational-source-change"
            ),
            _report(
                "runtime_reports/mlb_scoring_guard_status_latest.json",
                "2026-09-11T14:50:00Z",
                '{"blockers":["source_failure"]}',
            ),
        ]
    ) + "\n"

    assert recent_fundamentals_repair_cutoff(evidence) == _epoch("2026-09-11T14:40:31Z")
    filtered = filter_superseded_fundamentals_evidence(evidence)
    assert "mlb_scoring_guard_status_latest.json" in filtered
    assert "source_failure" in filtered


def test_new_observation_after_repair_remains_authoritative_evidence() -> None:
    evidence = "\n".join(
        [
            _history(
                "abc 2026-09-11T14:00:00+00:00 Merge PR #784: align MLB read-only post-persistence proof with canonical lock authority"
            ),
            _report(
                "runtime_reports/mlb_scoring_guard_status_latest.json",
                "2026-09-11T14:15:00Z",
                '{"blockers":["source_failure"]}',
            ),
        ]
    ) + "\n"

    filtered = filter_superseded_fundamentals_evidence(evidence)

    assert "mlb_scoring_guard_status_latest.json" in filtered
    assert "source_failure" in filtered
    assert "evidence_supersession_receipt" not in filtered


def test_nonrepair_planner_commit_does_not_supersede_runtime_evidence() -> None:
    evidence = "\n".join(
        [
            _history(
                "abc 2026-09-11T14:00:00+00:00 Merge PR #776: MLB planner surface source-honest fundamentals feature gap"
            ),
            _report(
                "runtime_reports/mlb_scoring_guard_status_latest.json",
                "2026-09-11T13:00:00Z",
                '{"blockers":["source_failure"]}',
            ),
        ]
    ) + "\n"

    assert recent_fundamentals_repair_cutoff(evidence) is None
    assert filter_superseded_fundamentals_evidence(evidence) == evidence


def test_unrelated_tennis_or_controller_security_commit_does_not_supersede_mlb_runtime() -> None:
    evidence = "\n".join(
        [
            _history(
                "abc 2026-09-11T14:47:40+00:00 Merge PR #787: disable unsafe manual MLB planner dispatch",
                "def 2026-09-11T14:50:19+00:00 Publish complete tennis daily card [skip ci]",
            ),
            _report(
                "runtime_reports/mlb_scoring_guard_status_latest.json",
                "2026-09-11T13:46:00Z",
                '{"blockers":["source_failure"]}',
            ),
        ]
    ) + "\n"

    assert recent_fundamentals_repair_cutoff(evidence) is None
    assert "source_failure" in filter_superseded_fundamentals_evidence(evidence)


def test_only_stale_fundamentals_hits_are_removed_from_mixed_focus_summary() -> None:
    cutoff = "2026-09-11T14:00:00+00:00"
    evidence = "\n".join(
        [
            _history(
                f"abc {cutoff} Merge PR #784: align MLB post-persistence scoring guard to canonical lock authority"
            ),
            json.dumps(
                {
                    "kind": "planner_focus_evidence_summary",
                    "hits": [
                        {
                            "domain": "data_capture",
                            "path": "runtime_reports/mlb_scoring_guard_status_latest.json",
                            "observedEpoch": _epoch("2026-09-11T13:50:00Z"),
                            "matchedSignals": ["source_failure"],
                        },
                        {
                            "domain": "challenger_model",
                            "path": "runtime_reports/mlb_successor_v2_verification_latest.json",
                            "observedEpoch": _epoch("2026-09-11T13:50:00Z"),
                            "matchedSignals": ["insufficient_observed_starter"],
                        },
                    ],
                }
            ),
        ]
    ) + "\n"

    filtered = filter_superseded_fundamentals_evidence(evidence)

    assert "source_failure" not in filtered
    assert "insufficient_observed_starter" in filtered
    assert "removedDerivedHitCount\": 1" in filtered


def test_malformed_or_unstructured_lines_are_preserved_and_never_create_cutoff() -> None:
    evidence = "not-json\n" + _history("malformed commit history line") + "\n"
    assert recent_fundamentals_repair_cutoff(evidence) is None
    assert filter_superseded_fundamentals_evidence(evidence) == evidence


def test_planner_workflow_tracks_report_dependencies_at_first_parent_landing() -> None:
    workflow = Path(".github/workflows/mlb-engineering-planner.yml").read_text(encoding="utf-8")
    assert "'scripts/mlb_scoring_guard_status.py'," in workflow
    assert "'hello_world/mlb_fundamentals_lock_authority_v1.py'," in workflow
    assert "'--first-parent'" in workflow
    assert "'--diff-merges=first-parent'" in workflow
    assert "'--no-patch'" in workflow
    assert "%cI operational-source-change" in workflow
    assert "%ad operational-source-change" not in workflow
