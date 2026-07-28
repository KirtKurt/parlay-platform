from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mlb-historical-supervised-v9-shadow.yml"
WRAPPER = ROOT / "scripts" / "run_mlb_historical_supervised_v9_shadow_v2.py"
VALIDATOR = ROOT / "scripts" / "validate_mlb_v9_shadow_workflow.py"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_does_not_depend_only_on_cloudformation_outputs():
    text = _text()
    assert "lambda_handler_scan" in text
    assert "resolve_mlb_historical_artifacts_bucket.py" in text
    assert "resolved=true" in text


def test_workflow_cannot_reuse_stale_checked_in_report_as_current_evidence():
    text = _text()
    assert 'rm -f "$REPORT_PATH" "$HANDOFF_PATH" "$VALIDATION_PATH"' in text
    assert "v7_v9_evaluation_failed_before_report_write" in text
    assert "V7_V9_RUNTIME_RESOLUTION" in text
    assert "V7_V9_VALIDATION" in text
    assert "validationEvidence" in text


def test_workflow_preserves_previous_report_only_as_frozen_input():
    text = _text()
    assert "/tmp/mlb-supervised-v9-previous.json" in text
    assert "--previous-report /tmp/mlb-supervised-v9-previous.json" in text
    assert "previousReportPreservedSeparately" in text


def test_workflow_enforces_fresh_run_identity():
    text = _text()
    assert "str(value.get('runId')) == str(os.environ.get('GITHUB_RUN_ID'))" in text
    assert "value.get('sourceSha') == os.environ.get('GITHUB_SHA')" in text


def test_workflow_runs_and_enforces_durable_v9_artifact():
    text = _text()
    wrapper = WRAPPER.read_text(encoding="utf-8")
    validator = VALIDATOR.read_text(encoding="utf-8")
    assert "run_mlb_historical_supervised_v9_shadow_v2.py" in text
    assert "validate_mlb_v9_shadow_workflow.py" in text
    assert "MLB-V9-SHADOW-MODEL-ARTIFACT-v1" in text
    assert "FROZEN_SHADOW_MODEL" in text
    assert "modelDigest" in text
    assert "candidate.get(\"policy\")" in wrapper
    assert "holdoutLabelsUsedForFitOrSelection" in wrapper
    assert "test_failed:" in validator
