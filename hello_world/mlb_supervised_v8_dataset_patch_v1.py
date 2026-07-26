"""Install trainable V8 metadata into rematerialized historical records.

The patch reuses immutable archived snapshots and makes no provider calls. It
propagates the already lock-bounded V8 expansion payload into each side signal,
records explicit missingness, and advances the feature dataset version so the
existing rematerializer rebuilds old slates before supervised evaluation.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

VERSION = "MLB-SUPERVISED-V8-DATASET-PATCH-v1"
FEATURE_DATASET_VERSION = "MLB-HISTORICAL-FEATURE-DATASET-v8-supervised-trainable"
REMATERIALIZATION_VERSION = "MLB-HISTORICAL-FEATURE-REMATERIALIZATION-v2-v8-supervised"


def install(optimizer: Any, rematerialization: Any) -> None:
    if getattr(optimizer, "_INQSI_SUPERVISED_V8_DATASET_PATCH_INSTALLED", False):
        return
    original_signal = optimizer._signal
    original_build = optimizer.build_slate_dataset

    def patched_signal(game, observations, side, expected_slots):
        signal = original_signal(game, observations, side, expected_slots)
        latest = observations[-1] if observations else {}
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
        available = sum(
            isinstance((row.get("homeSignal") or {}).get("oddsMarketExpansionFeatures"), Mapping)
            and bool((row.get("homeSignal") or {}).get("oddsMarketExpansionFeatures"))
            for row in records
            if isinstance(row, Mapping)
        )
        dataset["supervisedFeatureContractVersion"] = VERSION
        dataset["featureDatasetVersion"] = FEATURE_DATASET_VERSION
        dataset["v8TrainableRecordCount"] = int(available)
        dataset["v8TrainableCoverage"] = round(available / len(records), 8) if records else 0.0
        dataset["strictlyPastTeamHistoryDerivedAtTraining"] = True
        dataset["sameSlateOutcomeFeaturesProhibited"] = True
        return dataset

    optimizer._signal = patched_signal
    optimizer.build_slate_dataset = patched_build
    optimizer.SUPERVISED_V8_DATASET_VERSION = VERSION
    optimizer._INQSI_SUPERVISED_V8_DATASET_PATCH_INSTALLED = True
    rematerialization.FEATURE_DATASET_VERSION = FEATURE_DATASET_VERSION
    rematerialization.VERSION = REMATERIALIZATION_VERSION
