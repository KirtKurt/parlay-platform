"""Install trainable V8 metadata into rematerialized historical records.

The patch reuses immutable archived snapshots and makes no provider calls. It
propagates already lock-bounded V8 expansion payloads and the provider event ID
needed for separately budgeted historical first-five enrichment. Missing data is
explicit, and the version bump forces a zero-cost rebuild before enrichment.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

VERSION = "MLB-SUPERVISED-V8-DATASET-PATCH-v2-provider-event-id"
FEATURE_DATASET_VERSION = "MLB-HISTORICAL-FEATURE-DATASET-v9-v8-event-id-trainable"
REMATERIALIZATION_VERSION = "MLB-HISTORICAL-FEATURE-REMATERIALIZATION-v3-v8-event-id"


def install(optimizer: Any, rematerialization: Any) -> None:
    if getattr(optimizer, "_INQSI_SUPERVISED_V8_DATASET_PATCH_INSTALLED", False):
        return
    original_signal = optimizer._signal
    original_build = optimizer.build_slate_dataset

    def patched_signal(game, observations, side, expected_slots):
        signal = original_signal(game, observations, side, expected_slots)
        latest = observations[-1] if observations else {}
        provider_event_id = str(latest.get("providerEventId") or "").strip() if isinstance(latest, Mapping) else ""
        signal["providerEventId"] = provider_event_id or None
        signal["providerEventIdAvailable"] = bool(provider_event_id)
        payload = latest.get("oddsMarketExpansionFeatures") if isinstance(latest, Mapping) else None
        if isinstance(payload, Mapping):
            signal["oddsMarketExpansionFeatures"] = copy.deepcopy(dict(payload))
            signal["oddsMarketExpansionAvailable"] = True
            signal["oddsMarketExpansionVersion"] = str(
                latest.get("oddsMarketExpansionVersion")
                or payload.get("version")
                or "unknown"
            )
        else:
            signal["oddsMarketExpansionFeatures"] = {}
            signal["oddsMarketExpansionAvailable"] = False
            signal["oddsMarketExpansionVersion"] = None
        return signal

    def patched_build(*args, **kwargs):
        dataset = original_build(*args, **kwargs)
        records = dataset.get("records") or []
        available = 0
        event_ids = 0
        mismatches = []
        for row in records:
            if not isinstance(row, dict):
                continue
            home = row.get("homeSignal") or {}
            away = row.get("awaySignal") or {}
            if isinstance(home.get("oddsMarketExpansionFeatures"), Mapping) and bool(
                home.get("oddsMarketExpansionFeatures")
            ):
                available += 1
            home_id = str(home.get("providerEventId") or "").strip()
            away_id = str(away.get("providerEventId") or "").strip()
            if home_id and away_id and home_id != away_id:
                mismatches.append(str(row.get("officialGamePk") or "unknown"))
                continue
            provider_event_id = home_id or away_id
            row["providerEventId"] = provider_event_id or None
            row["providerEventIdAvailable"] = bool(provider_event_id)
            if provider_event_id:
                event_ids += 1
        if mismatches:
            raise RuntimeError(
                "provider_event_id_home_away_mismatch:" + ",".join(mismatches[:10])
            )
        dataset["supervisedFeatureContractVersion"] = VERSION
        dataset["featureDatasetVersion"] = FEATURE_DATASET_VERSION
        dataset["v8TrainableRecordCount"] = int(available)
        dataset["v8TrainableCoverage"] = round(available / len(records), 8) if records else 0.0
        dataset["providerEventIdRecordCount"] = int(event_ids)
        dataset["providerEventIdCoverage"] = round(event_ids / len(records), 8) if records else 0.0
        dataset["historicalFirstFiveEnrichmentReady"] = bool(records) and event_ids == len(records)
        dataset["strictlyPastTeamHistoryDerivedAtTraining"] = True
        dataset["sameSlateOutcomeFeaturesProhibited"] = True
        dataset["paidHistoricalCallsMade"] = 0
        return dataset

    optimizer._signal = patched_signal
    optimizer.build_slate_dataset = patched_build
    optimizer.SUPERVISED_V8_DATASET_VERSION = VERSION
    optimizer._INQSI_SUPERVISED_V8_DATASET_PATCH_INSTALLED = True
    rematerialization.FEATURE_DATASET_VERSION = FEATURE_DATASET_VERSION
    rematerialization.VERSION = REMATERIALIZATION_VERSION
