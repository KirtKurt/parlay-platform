from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mlb-prospective-backlog-reconcile-once.yml"
SCRIPT = ROOT / "scripts" / "reconcile_mlb_prospective_backlog.py"


def test_workflow_runs_bounded_reconciliation_before_training():
    source = WORKFLOW.read_text(encoding="utf-8")

    reconcile_at = source.index("Reconcile release-cutoff prospective slate backlog")
    trainer_at = source.index("Invoke trainer and prove usable prospective rows")
    assert reconcile_at < trainer_at
    assert "--max-slate-days 14" in source
    assert "prospective_backlog_reconciled_training" in source
    assert "accepted <= 0" in source
    assert "still has zero accepted rows" in source


def test_workflow_preserves_shadow_and_production_authority():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "liveInferenceAuthority" in source
    assert "productionAuthorityChanged" in source
    assert "unexpectedly activated live inference authority" in source
    assert "unexpectedly changed production authority" in source
    assert "INQSI_MLB_ML_AUTO_PROMOTE" not in source
    assert "update-function-configuration" not in source
    assert "sam deploy" not in source


def test_reconciliation_script_has_no_direct_storage_writer():
    source = SCRIPT.read_text(encoding="utf-8")

    for forbidden in (
        "put_item(",
        "update_item(",
        "delete_item(",
        "batch_write_item(",
        "transact_write_items(",
        ".client(\"dynamodb\"",
        ".client('dynamodb'",
        ".resource(\"dynamodb\"",
        ".resource('dynamodb'",
    ):
        assert forbidden not in source
    assert "MLBDailyPickLockFunction" in source
    assert "MLBResultsSchedulerFunction" in source
    assert '"force": True' in source
    assert "postStartPredictionCreationAllowed" in source
    assert "immutablePredictionRewriteAllowed" in source
