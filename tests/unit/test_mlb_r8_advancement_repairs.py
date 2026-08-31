from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_selection_capture_cadence_has_margin_inside_t_minus_10_window() -> None:
    template = (ROOT / "template.yaml").read_text()
    recurring = (
        ROOT / ".github/workflows/prove-mlb-r8-recurring-cadence.yml"
    ).read_text()
    assert "MLBMLSelectionCaptureEvery2Minutes" in template
    assert "Schedule: cron(1/2 * * * ? *)" in template
    assert "MLBMLSelectionCaptureEvery15Minutes" not in template
    assert "cron(4/15 * * * ? *)" not in template
    assert "MAX_SELECTION_HEARTBEAT_AGE_SECONDS: '300'" in recurring


def test_active_r8_proofs_allow_genuinely_future_rows_to_advance() -> None:
    recurring = (
        ROOT / ".github/workflows/prove-mlb-r8-recurring-cadence.yml"
    ).read_text()
    bootstrap = (
        ROOT / ".github/workflows/bootstrap-mlb-historical-live-r8.yml"
    ).read_text()
    assert "prospectiveAdvancementAllowed" in recurring
    assert "prospectiveAdvancementAllowed" in bootstrap
    assert "prospectiveTest') or 0) == 0" not in recurring
    assert "prospective == 0" not in bootstrap


def test_selection_and_movement_coverage_are_explicit_progress_evidence() -> None:
    trainer = (ROOT / "hello_world/mlb_ml_aws_training_v1.py").read_text()
    reporter = (ROOT / "scripts/report_mlb_30m_progress.py").read_text()
    assert "SELECTION_CAPTURE_STATUS_MAX_AGE = timedelta(minutes=10)" in trainer
    assert '"ledgerCoveredCount": ledger_covered' in trainer
    assert '"movementCoverage": movement_coverage' in trainer
    assert "Movement-feature coverage" in reporter
    assert "Selection ledger covered / eligible / selected" in reporter
