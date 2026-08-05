from pathlib import Path


WORKFLOW = Path(".github/workflows/mlb-v8-hourly-status.yml")


def test_hourly_schedule_and_permanent_issue_channel():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "cron: '7 * * * *'" in text
    assert "STATUS_ISSUE: '458'" in text
    assert "issues: write" in text
    assert "actions: read" in text
    assert "contents: write" in text
    assert "cancel-in-progress: false" in text


def test_reporter_retrieves_live_aws_state_and_labels_fallback():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '\"mode\":\"status\"' in text
    assert "AWS_LAMBDA_STATUS" in text
    assert "REPOSITORY_STATUS_FALLBACK" in text
    assert "fallbackClearlyLabeled" in text
    assert "docs/runtime/mlb-historical-live-status.json" in text


def test_reporter_preserves_lifecycle_separation_and_publishes_only_status_files():
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
    assert "PUBLISHED_JSON: runtime_reports/mlb_v8_hourly_status_latest.json" in text
    assert "PUBLISHED_MARKDOWN: runtime_reports/mlb_v8_hourly_status_latest.md" in text
    assert "git add \"$PUBLISHED_JSON\" \"$PUBLISHED_MARKDOWN\"" in text
    assert "Publish MLB V8 hourly numerical status [skip ci]" in text
    assert "git push origin HEAD:main" in text
    assert "mlb_v8_autonomous_training_latest.json\" \"$PUBLISHED" not in text


def test_reporter_uses_repository_as_authority_and_issue_as_mirror():
    text = WORKFLOW.read_text(encoding="utf-8")

    repository_step = text.index("Publish authoritative latest report to main")
    issue_step = text.index("Mirror latest report to status issue")
    assert repository_step < issue_step
    assert "continue-on-error: true" in text[issue_step:]
    assert "authoritativeRepositoryPublicationUnaffected" in text
    assert "repository-publication-status.json" in text
    assert "issue-publication-status.json" in text


def test_reporter_updates_correct_issue_and_archives_exact_evidence():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "issues/$STATUS_ISSUE" in text
    assert "--method PATCH" in text
    assert "--method POST" in text
    assert "actions/upload-artifact@v4" in text
    assert "retention-days: 90" in text
    assert "mlb-v8-hourly-status-${{ github.run_id }}" in text


def test_prior_delta_state_prefers_authoritative_repository_report():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'if test -s "$PUBLISHED_MARKDOWN"' in text
    assert 'cp "$PUBLISHED_MARKDOWN" /tmp/mlb-v8-hourly/previous.md' in text
    assert "issues/$STATUS_ISSUE" in text


def test_pull_requests_only_run_validation():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "if: github.event_name != 'pull_request'" in text
    assert "needs: validate" in text
