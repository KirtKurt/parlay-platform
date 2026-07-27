from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPAIRS = ROOT / "hello_world" / "mlb_historical_v7_priority_repairs_v1.py"
SCRIPT = ROOT / "scripts" / "run_mlb_historical_supervised_v9_shadow.py"
WORKFLOW = ROOT / ".github" / "workflows" / "mlb-historical-supervised-v9-shadow.yml"
TEMPLATE = ROOT / "mlb_historical_optimizer" / "template.yaml"


def test_priority_repairs_cover_feature_sparsity_and_missingness():
    source = REPAIRS.read_text(encoding="utf-8")
    for name in (
        "starterDiff",
        "bullpenDiff",
        "lineupDiff",
        "starterAvailable",
        "bullpenAvailable",
        "lineupAvailable",
        "firstFiveAvailable",
        "spreadAvailable",
        "fullHistoryAvailable",
    ):
        assert name in source
    assert "feature_population_report" in source
    assert "degenerateFeatures" in source


def test_shadow_workflow_checks_out_triggering_sha_and_runs_hourly():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "github.event.pull_request.head.sha || github.sha" in source
    assert "cron: '17 * * * *'" in source
    assert "branches: [main]" in source


def test_shadow_refit_is_gated_and_canonical_audit_remains_200():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "WAITING_FOR_50_NEW_ELIGIBLE_GAMES" in source
    assert "datasetFingerprint" in source
    assert "canonicalFreshAuditIncrementGames" in source
    assert "candidate_handoff" in source


def test_operations_and_accuracy_views_are_separate():
    source = REPAIRS.read_text(encoding="utf-8")
    assert "rejection_and_lease_report" in source
    assert "selective_accuracy_report" in source
    assert "fullSlateAccuracy" in source
    assert "selectiveThresholds" in source


def test_range_and_round_ceiling_are_extended_without_reducing_audit():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "Default: '2026-12-31'" in source
    assert "Default: 24" in source
    assert "MaxValue: 36" in source
    assert "MinValue: 200" in source
