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


def test_current_supervised_workflow_retains_shadow_only_authority():
    source = Path(
        ".github/workflows/mlb-supervised-shadow-v2-recurring.yml"
    ).read_text()
    assert "productionAuthorityChanged') is False" in source
    assert "'automaticWagerAllowed':False" in source
    assert "productionPromotionEligible') is False" in source
    assert "selectionUsedUntouchedAudit') is False" in source
    assert "cancel-in-progress: false" in source
    assert "MLB V8 Historical BBD Prior-Game Backfill" in source
    assert "MLB V8 Historical Point-In-Time Context Backfill" in source


def test_recovery_workflow_deploys_current_event_id_dataset_contract():
    source = Path(".github/workflows/mlb-historical-v7-recovery.yml").read_text()
    entrypoint = Path(
        "hello_world/mlb_historical_optimizer_v7_recovery_entrypoint.py"
    ).read_text()
    patch = Path("hello_world/mlb_supervised_v8_dataset_patch_v1.py").read_text()
    assert "mlb_historical_optimizer_v7_recovery_entrypoint.py" in source
    assert "mlb_supervised_v8_dataset_patch_v1" in entrypoint
    assert "MLB-HISTORICAL-FEATURE-DATASET-v9-v8-event-id-trainable" in patch
