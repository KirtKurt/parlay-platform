from __future__ import annotations

from hello_world import mlb_supervised_v8_dataset_patch_v1 as patch


def test_provider_event_id_is_propagated_for_historical_enrichment():
    class Optimizer:
        def _signal(self, game, observations, side, expected_slots):
            return {"side": side}

        def build_slate_dataset(self, *args, **kwargs):
            return {
                "records": [
                    {
                        "officialGamePk": "123",
                        "homeSignal": {
                            "providerEventId": "provider-event-123",
                            "oddsMarketExpansionFeatures": {},
                        },
                        "awaySignal": {
                            "providerEventId": "provider-event-123",
                            "oddsMarketExpansionFeatures": {},
                        },
                    }
                ]
            }

    class Rematerialization:
        FEATURE_DATASET_VERSION = "old"
        VERSION = "old"

    optimizer = Optimizer()
    rematerialization = Rematerialization()
    patch.install(optimizer, rematerialization)
    signal = optimizer._signal(
        {},
        [{"providerEventId": "provider-event-123"}],
        "home",
        4,
    )
    assert signal["providerEventId"] == "provider-event-123"
    assert signal["providerEventIdAvailable"] is True
    dataset = optimizer.build_slate_dataset()
    assert dataset["records"][0]["providerEventId"] == "provider-event-123"
    assert dataset["providerEventIdCoverage"] == 1.0
    assert dataset["historicalFirstFiveEnrichmentReady"] is True
    assert dataset["paidHistoricalCallsMade"] == 0
    assert rematerialization.FEATURE_DATASET_VERSION == patch.FEATURE_DATASET_VERSION


def test_provider_event_id_mismatch_fails_closed():
    class Optimizer:
        def _signal(self, game, observations, side, expected_slots):
            return {"side": side}

        def build_slate_dataset(self, *args, **kwargs):
            return {
                "records": [
                    {
                        "officialGamePk": "123",
                        "homeSignal": {"providerEventId": "a"},
                        "awaySignal": {"providerEventId": "b"},
                    }
                ]
            }

    class Rematerialization:
        FEATURE_DATASET_VERSION = "old"
        VERSION = "old"

    optimizer = Optimizer()
    patch.install(optimizer, Rematerialization())
    try:
        optimizer.build_slate_dataset()
        assert False, "expected provider event ID mismatch to fail"
    except RuntimeError as exc:
        assert "provider_event_id_home_away_mismatch" in str(exc)
