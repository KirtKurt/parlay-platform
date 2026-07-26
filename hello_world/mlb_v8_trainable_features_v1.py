"""Expose V8 market-expansion observations to shadow training without authority.

The Odds API V8 normalizer attaches per-event expanded-market fields. The legacy
historical signal builder previously discarded those fields. This installer
preserves them on both side signals so supervised shadow training can consume
them after no-paid-call rematerialization. Production V7 selection remains
unchanged because the incumbent policy ignores these fields.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

VERSION = "MLB-V8-TRAINABLE-SHADOW-FEATURES-v1.0"
DATASET_VERSION = "MLB-HISTORICAL-DAILY-DATASET-v1.2-v8-shadow-trainable"


def install(optimizer: Any) -> None:
    if getattr(optimizer, "_INQSI_V8_TRAINABLE_SHADOW_FEATURES_V1_INSTALLED", False):
        return
    original_signal = optimizer._signal

    def patched_signal(game, observations, side, expected_slots):
        out = original_signal(game, observations, side, expected_slots)
        latest = observations[-1] if observations else {}
        expansion = (
            latest.get("oddsMarketExpansionFeatures")
            if isinstance(latest, Mapping)
            else None
        )
        if isinstance(expansion, Mapping):
            out["oddsMarketExpansionFeatures"] = copy.deepcopy(dict(expansion))
            out["oddsMarketExpansionVersion"] = latest.get(
                "oddsMarketExpansionVersion"
            )
            out["v8ShadowFeaturesAvailable"] = True
        else:
            out["oddsMarketExpansionFeatures"] = {}
            out["oddsMarketExpansionVersion"] = None
            out["v8ShadowFeaturesAvailable"] = False
        out["v8ShadowFeatureContractVersion"] = VERSION
        out["v8ShadowOnly"] = True
        return out

    optimizer._signal = patched_signal
    optimizer.DATASET_VERSION = DATASET_VERSION
    optimizer._INQSI_V8_TRAINABLE_SHADOW_FEATURES_V1_INSTALLED = True
