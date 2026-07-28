from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "mlb-historical-supervised-v9-shadow.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_does_not_depend_only_on_cloudformation_outputs():
    text = _text()
    assert "lambda_handler_scan" in text
    assert "resolve_mlb_historical_artifacts_bucket.py" in text
    assert "resolved=true" in text


def test_workflow_cannot_reuse_stale_checked_in_report_as_current_evidence():
    text = _text()
    assert 'rm -f "$REPORT_PATH" "$HANDOFF_PATH"' in text
    assert "v7_v9_report_missing_after_evaluation" in text
    assert "V7_V9_RUNTIME_RESOLUTION" in text


def test_workflow_preserves_previous_report_only_as_frozen_input():
    text = _text()
    assert "/tmp/mlb-supervised-v9-previous.json" in text
    assert "--previous-report /tmp/mlb-supervised-v9-previous.json" in text
    assert "previousReportPreservedSeparately" in text


def test_workflow_enforces_fresh_run_identity():
    text = _text()
    assert "assert value.get('runId') == os.environ.get('GITHUB_RUN_ID')" in text
    assert "assert value.get('sourceSha') == os.environ.get('GITHUB_SHA')" in text
