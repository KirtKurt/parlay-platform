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


def test_event_id_installer_updates_workflows_without_authority_change():
    installer = Path(".github/workflows/mlb-v9-event-id-install-once.yml").read_text()
    assert "MLB-HISTORICAL-FEATURE-DATASET-v8-supervised-trainable" in installer
    assert "MLB-HISTORICAL-FEATURE-DATASET-v9-v8-event-id-trainable" in installer
    assert "mlb-supervised-shadow-v2-recurring.yml" in installer
    assert "mlb-historical-v7-recovery.yml" in installer
    source = Path(".github/workflows/mlb-supervised-shadow-v2-recurring.yml").read_text()
    assert "productionAuthorityChanged') is False" in source
    assert "'automaticWagerAllowed':False" in source
    assert "productionPromotionEligible') is False" in source
    assert "selectionUsedUntouchedAudit') is False" in source
    assert "cancel-in-progress: false" in source


def test_recurring_trainer_never_publishes_cancelled_failure_as_latest():
    source = Path(".github/workflows/mlb-supervised-shadow-v2-recurring.yml").read_text()
    assert "workflow_run:" not in source
    assert "if: steps.evaluation.outcome == 'success'" in source
    assert "git reset --hard origin/main" in source
    assert "git clean -fd" in source
    assert "cancel-in-progress: false" in source
