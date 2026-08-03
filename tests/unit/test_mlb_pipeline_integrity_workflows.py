from __future__ import annotations

from datetime import timezone
from pathlib import Path

from scripts.publish_monotonic_json import evidence_time


ROOT = Path(__file__).resolve().parents[2]
V9_WORKFLOW = ROOT / ".github" / "workflows" / "mlb-historical-supervised-v9-shadow.yml"
V10_WORKFLOW = ROOT / ".github" / "workflows" / "mlb-v10-autonomous-signal-discovery.yml"
POST_DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "mlb-post-deploy-fix-verification.yml"


def test_monotonic_publisher_accepts_completed_timestamp():
    stamp = evidence_time({"completedAtUtc": "2026-08-03T12:34:56Z"})
    assert stamp is not None
    assert stamp.tzinfo == timezone.utc
    assert stamp.isoformat() == "2026-08-03T12:34:56+00:00"


def test_v9_uses_candidate_files_and_never_deletes_latest_pointer():
    text = V9_WORKFLOW.read_text(encoding="utf-8")
    assert 'REPORT_CANDIDATE: /tmp/mlb-supervised-v9-report-candidate.json' in text
    assert 'HANDOFF_CANDIDATE: /tmp/mlb-supervised-v9-handoff-candidate.json' in text
    assert 'rm -f "$REPORT_PATH"' not in text
    assert '--output "$REPORT_CANDIDATE"' in text
    assert '--handoff-output "$HANDOFF_CANDIDATE"' in text
    assert text.count("python scripts/publish_monotonic_json.py") == 2


def test_v10_wakes_on_context_and_publishes_only_valid_candidate():
    text = V10_WORKFLOW.read_text(encoding="utf-8")
    assert "runtime_reports/mlb_v8_historical_context_backfill_latest.json" in text
    assert 'CANDIDATE_PATH: /tmp/mlb-v10-autonomous-signal-discovery-candidate.json' in text
    assert 'rm -f "$REPORT_PATH"' not in text
    assert '--output "$CANDIDATE_PATH"' in text
    assert "python scripts/publish_monotonic_json.py" in text
    assert 'test -s "$REPORT_PATH"' in text
    assert "python -m json.tool \"$REPORT_PATH\"" in text


def test_post_deploy_separates_runtime_health_from_model_readiness():
    text = POST_DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    assert "deployment_blockers = []" in text
    assert "readiness_blockers = []" in text
    assert "readiness_blockers.append('scoring_guard_not_ready')" in text
    assert "deployment_blockers.append('scoring_guard_not_ready')" not in text
    assert "DEPLOYMENT_HEALTHY_READINESS_GATED" in text
    assert "'deploymentHealthy': deployment_healthy" in text
    assert "'promotionReady': promotion_ready" in text
    assert "'productionAuthorityChanged': False" in text
    assert "assert value.get('deploymentHealthy') is True" in text
    assert "assert value.get('promotionReady') is True" not in text


def test_post_deploy_still_fails_closed_on_identity_and_runtime_defects():
    text = POST_DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    for blocker in (
        "timeout_not_600",
        "memory_not_2048",
        "deploy_sha_mismatch",
        "lifecycle_storage_disposition_incomplete",
        "new_timeout_after_deploy",
        "new_scheduled_scoring_failure_after_deploy",
    ):
        assert f"deployment_blockers.append('{blocker}')" in text
    assert "if deployment_blockers:" in text
    assert "MLB deployment health verification failed" in text
