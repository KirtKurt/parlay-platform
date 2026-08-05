from pathlib import Path


def test_recovery_entrypoint_installs_trainable_v8_dataset_before_rematerialization():
    source = Path("hello_world/mlb_historical_optimizer_v7_recovery_entrypoint.py").read_text()
    import_line = "import mlb_supervised_v8_dataset_patch_v1 as supervised_v8_dataset"
    install_line = "supervised_v8_dataset.install(base.optimizer_handler.optimizer, rematerialization)"
    assert import_line in source
    assert install_line in source
    assert source.index("odds_market_v8.install(") < source.index(install_line)
    assert source.index(install_line) < source.index("def lambda_handler")
    assert '"authority": "SHADOW_ONLY"' in source
    assert '"productionAuthorityChanged": False' in source
    assert '"providerCallsRequiredForRematerialization": 0' in source


def test_historical_template_accelerates_no_cost_rematerialization_safely():
    source = Path("mlb_historical_optimizer/template.yaml").read_text()
    assert "Handler: mlb_historical_optimizer_v7_recovery_entrypoint.lambda_handler" in source
    assert "MLB_HISTORICAL_REMATERIALIZE_SLATES_PER_RUN: '5'" in source
    assert "Timeout: 900" in source
    assert "HistoricalMaxOptimizationRounds:" in source
    assert "Default: 24" in source
    assert "MaxValue: 36" in source


def test_event_id_installer_preserves_single_controller_authority():
    installer = Path(".github/workflows/mlb-v9-event-id-install-once.yml").read_text()
    assert "MLB-HISTORICAL-FEATURE-DATASET-v8-supervised-trainable" in installer
    assert "MLB-HISTORICAL-FEATURE-DATASET-v9-v8-event-id-trainable" in installer
    assert "mlb-supervised-shadow-v2-recurring.yml" in installer
    assert "mlb-historical-v7-recovery.yml" in installer

    compatibility = Path(
        ".github/workflows/mlb-supervised-shadow-v2-recurring.yml"
    ).read_text()
    controller = Path(
        ".github/workflows/mlb-v8-autonomous-controller.yml"
    ).read_text()
    assert "schedule:" not in compatibility
    assert "mlb-v8-autonomous-controller.yml" in compatibility
    assert "delegate-to-v8-autonomous-controller" in compatibility
    assert "contents: read" in compatibility
    assert "cancel-in-progress: false" in compatibility
    assert "productionAuthorityChanged') is False" in controller
    assert "automaticWagerAllowed') is False" in controller
    assert "selectionUsedProspectiveOutcomes') is False" in controller


def test_recurring_compatibility_never_publishes_or_competes_with_controller():
    compatibility = Path(
        ".github/workflows/mlb-supervised-shadow-v2-recurring.yml"
    ).read_text()
    controller = Path(
        ".github/workflows/mlb-v8-autonomous-controller.yml"
    ).read_text()
    assert "workflow_run:" not in compatibility
    assert "schedule:" not in compatibility
    assert "git push" not in compatibility
    assert "Publish monotonic latest state" not in compatibility
    assert "gh workflow run mlb-v8-autonomous-controller.yml" in compatibility
    assert "cancel-in-progress: false" in compatibility
    assert "Publish monotonic latest state" in controller
    assert "git reset --hard refs/remotes/origin/main" in controller
    assert "git clean -fd" in controller
