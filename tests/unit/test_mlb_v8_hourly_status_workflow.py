from pathlib import Path


WORKFLOW = Path(".github/workflows/mlb-v8-hourly-status.yml")


def test_hourly_schedule_and_permanent_issue_channel():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "cron: '7 * * * *'" in text
    assert "STATUS_ISSUE: '457'" in text
    assert "issues: write" in text
    assert "actions: read" in text
    assert "cancel-in-progress: false" in text


def test_reporter_retrieves_live_aws_state_and_labels_fallback():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '\"mode\":\"status\"' in text
    assert "AWS_LAMBDA_STATUS" in text
    assert "REPOSITORY_STATUS_FALLBACK" in text
    assert "fallbackClearlyLabeled" in text
    assert "docs/runtime/mlb-historical-live-status.json" in text


def test_reporter_preserves_lifecycle_separation_and_does_not_commit_state():
    text = WORKFLOW.read_text(encoding="utf-8")

    for path in (
        "mlb_v8_autonomous_training_latest.json",
        "mlb_v8_prospective_audit_latest.json",
        "mlb_v8_historical_context_backfill_latest.json",
        "mlb_v8_shadow_realtime_72_latest.json",
        "mlb_v8_autonomous_promotion_latest.json",
    ):
        assert path in text
    assert "build_mlb_v8_hourly_report.py" in text
    assert "git push" not in text
    assert "git commit" not in text


def test_reporter_updates_issue_comments_and_archives_exact_evidence():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--method PATCH" in text
    assert "--method POST" in text
    assert "actions/upload-artifact@v4" in text
    assert "retention-days: 90" in text
    assert "mlb-v8-hourly-status-${{ github.run_id }}" in text


def test_pull_requests_only_run_validation():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "if: github.event_name != 'pull_request'" in text
    assert "needs: validate" in text
