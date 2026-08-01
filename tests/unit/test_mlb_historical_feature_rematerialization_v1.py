from pathlib import Path


def test_rematerialization_reuses_archived_snapshots_without_provider_calls():
    source = Path("hello_world/mlb_historical_feature_rematerialization_v1.py").read_text()
    assert "already archived lock-bounded raw snapshots" in source
    assert "paidHistoricalCallsMade" in source
    assert 'dataset["paidHistoricalCallsMade"] = 0' in source
    assert "_fetch_historical(" not in source
    assert "ODDS_API_KEY" not in source


def test_rematerialization_quarantines_invalid_archived_slots():
    source = Path("hello_world/mlb_historical_feature_rematerialization_v1.py").read_text()
    assert "QUARANTINED_STALE" in source
    assert "QUARANTINED_FUTURE_TIMESTAMP" in source
    assert "QUARANTINED_INVALID_TIMESTAMP" in source
    assert "QUARANTINED_MALFORMED_PAYLOAD" in source
    assert '"usableForFeatures": False' in source


def test_rematerialization_can_reconcile_new_slate_while_waiting_for_horizon():
    source = Path("hello_world/mlb_historical_feature_rematerialization_v1.py").read_text()
    assert '"WAITING_FOR_SETTLED_HORIZON"' in source


def test_supervised_dataset_patch_versions_event_ids_for_enrichment():
    source = Path("hello_world/mlb_supervised_v8_dataset_patch_v1.py").read_text()
    assert 'FEATURE_DATASET_VERSION = "MLB-HISTORICAL-FEATURE-DATASET-v9-v8-event-id-trainable"' in source
    assert 'REMATERIALIZATION_VERSION = "MLB-HISTORICAL-FEATURE-REMATERIALIZATION-v3-v8-event-id"' in source
    assert 'signal["providerEventId"]' in source
    assert 'row["providerEventId"]' in source
    assert 'dataset["providerEventIdCoverage"]' in source
    assert 'dataset["historicalFirstFiveEnrichmentReady"]' in source
    assert 'signal["oddsMarketExpansionAvailable"] = True' in source
    assert 'signal["oddsMarketExpansionAvailable"] = False' in source
    assert 'dataset["sameSlateOutcomeFeaturesProhibited"] = True' in source
    assert "rematerialization.FEATURE_DATASET_VERSION = FEATURE_DATASET_VERSION" in source


def test_recovery_entrypoint_installs_dataset_patch_before_run_once():
    source = Path("hello_world/mlb_historical_optimizer_v7_recovery_entrypoint.py").read_text()
    install = "supervised_v8_dataset.install(base.optimizer_handler.optimizer, rematerialization)"
    assert install in source
    assert source.index(install) < source.index("migration = rematerialization.run_once()")
