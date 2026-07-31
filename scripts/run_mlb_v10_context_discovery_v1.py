#!/usr/bin/env python3
"""Run V10 discovery over canonical signals plus frozen point-in-time context.

V10's statistical controls are unchanged.  This wrapper only broadens the pregame
feature view so official starter, bullpen, lineup, injury, park, weather, and team
context can generate atomic and interaction rules.  It performs no live BBD calls.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Dict, Mapping, Sequence

import mlb_no_bbd_context_bridge_v1 as bridge
import mlb_v10_autonomous_signal_discovery_v1 as v10
import run_mlb_v10_autonomous_signal_discovery as runner

VERSION = "MLB-V10-CONTEXT-DISCOVERY-BRIDGE-v1-no-bbd"
_ORIGINAL_ATOMIC = v10._atomic_rules
_ORIGINAL_FINGERPRINT = v10.dataset_fingerprint
_ORIGINAL_LOADER = runner._load_canonical_records
_CONTEXT_PROOF: Dict[str, Any] = {}


def _atomic_with_context(record: Mapping[str, Any]) -> list[dict]:
    value = copy.deepcopy(dict(record))
    value["homeSignal"] = bridge.v10_side_feature_view(record, "home")
    value["awaySignal"] = bridge.v10_side_feature_view(record, "away")
    return _ORIGINAL_ATOMIC(value)


def _fingerprint_with_context(records: Sequence[Mapping[str, Any]]) -> str:
    material = {
        "version": VERSION,
        "canonicalSignalFingerprint": _ORIGINAL_FINGERPRINT(records),
        "pointInTimeContextFingerprint": bridge.context_fingerprint(records),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_with_context(handler: Any, state: Mapping[str, Any]):
    records, proof = _ORIGINAL_LOADER(handler, state)
    records, overlay_proof = bridge.apply_stored_overlays(records)
    _, context_proof = bridge.augment_v7_v9_records(records)
    global _CONTEXT_PROOF
    _CONTEXT_PROOF = {
        **context_proof,
        "overlayProof": overlay_proof,
        "atomicRuleContextEnabled": True,
        "interactionRuleContextEnabled": True,
        "statisticalGatesChanged": False,
        "providerCallsMade": 0,
        "liveBbdApiRequired": False,
    }
    proof = dict(proof)
    proof["providerNeutralPointInTimeContext"] = dict(_CONTEXT_PROOF)
    return records, proof


def _install() -> None:
    os.environ["BBS_API_DISABLED"] = "true"
    os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED"] = "false"
    v10._atomic_rules = _atomic_with_context
    v10.dataset_fingerprint = _fingerprint_with_context
    runner._load_canonical_records = _load_with_context


def main() -> int:
    _install()
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
