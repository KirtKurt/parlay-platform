"""Provider-neutral point-in-time context bridge for MLB V7 through V10.

This module never calls BigBallsData.  It may read an already-frozen BBD manifest
when one exists, but target-game starter, bullpen, lineup, injury, park, weather,
and team context come from the isolated point-in-time context manifest.  Missing
BBD API access is therefore a capability state, not a training-execution failure.

The bridge is shadow-only.  It cannot write predictions, champions, cutovers, or
wagering authority, and it never exposes target outcomes to feature construction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Dict, Mapping, Sequence, Tuple

try:
    import mlb_v8_historical_bbs_overlay_v1 as prior_overlay
    import mlb_v8_historical_context_overlay_v1 as context_overlay
except ImportError:  # package imports used by tests
    from . import mlb_v8_historical_bbs_overlay_v1 as prior_overlay
    from . import mlb_v8_historical_context_overlay_v1 as context_overlay

VERSION = "MLB-NO-BBD-CONTEXT-BRIDGE-v1-provider-neutral-point-in-time"
TARGET_FAMILY = "targetGame"


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _sha(value: Any) -> str:
    payload = json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _identity(record: Mapping[str, Any]) -> Tuple[str, str]:
    return (
        str(record.get("officialGamePk") or record.get("gameId") or record.get("eventId") or ""),
        str(record.get("predictionLockAtUtc") or ""),
    )


def _target_family(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    families = snapshot.get("featureFamilies")
    if isinstance(families, Mapping) and isinstance(families.get(TARGET_FAMILY), Mapping):
        return families[TARGET_FAMILY]
    role = str(snapshot.get("snapshotRole") or "")
    if "POINT_IN_TIME" in role and any(
        key in (snapshot.get("home") or {})
        for key in ("starterQuality", "bullpenQuality", "lineupQuality")
    ):
        return {
            "available": True,
            "trainingEligible": snapshot.get("trainingEligible") is True,
            "pointInTimeVerified": snapshot.get("pointInTimeVerified") is True,
            "inferredFromLegacySnapshot": True,
        }
    return {}


def valid_target_snapshot(
    record: Mapping[str, Any], snapshot: Any
) -> Tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(snapshot, Mapping):
        return False, ["point_in_time_snapshot_missing"]
    if _identity(record) != _identity(snapshot):
        errors.append("point_in_time_snapshot_identity_mismatch")
    if snapshot.get("trainingEligible") is not True:
        errors.append("point_in_time_snapshot_not_training_eligible")
    if snapshot.get("pointInTimeVerified") is not True:
        errors.append("point_in_time_snapshot_unverified")
    if snapshot.get("postgameFieldsExcluded") is not True:
        errors.append("point_in_time_postgame_exclusion_missing")
    if snapshot.get("selectionUsedOutcomes") is not False:
        errors.append("point_in_time_outcome_selection_contract_missing")
    if snapshot.get("targetGameOutcomeUsed") not in (None, False):
        errors.append("target_game_outcome_used")
    family = _target_family(snapshot)
    if family.get("available") is not True:
        errors.append("target_context_family_missing")
    if family.get("trainingEligible") is not True:
        errors.append("target_context_family_not_training_eligible")
    if family.get("pointInTimeVerified") is not True:
        errors.append("target_context_family_point_in_time_unverified")
    for side in ("home", "away"):
        if not isinstance(snapshot.get(side), Mapping):
            errors.append(f"target_context_{side}_side_missing")
    return not errors, sorted(set(errors))


def apply_stored_overlays(
    records: Sequence[Mapping[str, Any]],
    *,
    table_name: str | None = None,
    ddb_resource: Any = None,
    s3_client: Any = None,
) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """Apply immutable stored overlays without making provider network calls."""
    os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED"] = "true"
    os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED"] = "false"
    os.environ["MLB_V8_HISTORICAL_CONTEXT_OVERLAY_ENABLED"] = "true"
    os.environ["MLB_V8_HISTORICAL_CONTEXT_OVERLAY_REQUIRED"] = "false"

    copied = [copy.deepcopy(dict(row)) for row in records]
    proofs: Dict[str, Any] = {
        "version": VERSION,
        "liveBbdApiAvailable": False,
        "liveBbdApiRequired": False,
        "liveBbdHttpRequestsMade": 0,
        "providerMode": "STORED_OPTIONAL_PLUS_OFFICIAL_POINT_IN_TIME_CONTEXT",
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
    }
    try:
        copied, prior_proof = prior_overlay.load_and_apply(
            copied,
            table_name=table_name,
            ddb_resource=ddb_resource,
            s3_client=s3_client,
        )
    except Exception as exc:
        prior_proof = {
            "status": "STORED_PRIOR_MANIFEST_UNAVAILABLE",
            "errorType": type(exc).__name__,
            "appliedGameCount": 0,
            "productionAuthorityChanged": False,
        }
    try:
        copied, context_proof = context_overlay.load_and_apply(
            copied,
            table_name=table_name,
            ddb_resource=ddb_resource,
            s3_client=s3_client,
        )
    except Exception as exc:
        context_proof = {
            "status": "POINT_IN_TIME_CONTEXT_MANIFEST_UNAVAILABLE",
            "errorType": type(exc).__name__,
            "appliedGameCount": 0,
            "productionAuthorityChanged": False,
        }
    proofs["storedPriorGameOverlay"] = prior_proof
    proofs["targetGameContextOverlay"] = context_proof
    proofs["targetContextAppliedGameCount"] = int(
        context_proof.get("appliedGameCount") or 0
    )
    return copied, proofs


def augment_v7_v9_records(
    records: Sequence[Mapping[str, Any]],
) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """Expose frozen target context through V7/V9's existing signal interface."""
    output: list[Dict[str, Any]] = []
    applied = 0
    rejected: Dict[str, int] = {}
    fingerprint_rows = []

    for raw in records:
        record = copy.deepcopy(dict(raw))
        snapshot = record.get("frozenFundamentalsSnapshot")
        valid, errors = valid_target_snapshot(record, snapshot)
        if valid:
            snapshot = dict(snapshot)
            for side in ("home", "away"):
                signal_key = f"{side}Signal"
                signal = copy.deepcopy(dict(record.get(signal_key) or {}))
                fundamentals = copy.deepcopy(
                    dict(signal.get("fundamentalsSnapshotV2") or {})
                )
                fundamentals.update(copy.deepcopy(dict(snapshot.get(side) or {})))
                signal["fundamentalsSnapshotV2"] = fundamentals
                signal["historicalPointInTimeContext"] = {
                    "version": VERSION,
                    "available": True,
                    "trainingEligible": True,
                    "pointInTimeVerified": True,
                    "snapshotFingerprint": snapshot.get("fingerprint"),
                    "providerIndependent": True,
                    "liveBbdApiRequired": False,
                }
                record[signal_key] = signal
            applied += 1
            fingerprint_rows.append(
                {
                    "identity": _identity(record),
                    "snapshotFingerprint": snapshot.get("fingerprint"),
                    "targetFamily": _target_family(snapshot),
                }
            )
        else:
            for error in errors:
                rejected[error] = rejected.get(error, 0) + 1
        output.append(record)

    proof = {
        "version": VERSION,
        "recordCount": len(output),
        "eligibleFeatureGameCount": applied,
        "featureFingerprint": _sha(fingerprint_rows),
        "rejectionCounts": dict(sorted(rejected.items())),
        "providerMode": "OFFICIAL_POINT_IN_TIME_CONTEXT_WITH_OPTIONAL_STORED_PRIOR",
        "liveBbdApiAvailable": False,
        "liveBbdApiRequired": False,
        "providerCallsMade": 0,
        "selectionUsedOutcomes": False,
        "targetGameOutcomeUsed": False,
        "productionAuthorityChanged": False,
    }
    return output, proof


def v10_side_feature_view(record: Mapping[str, Any], side: str) -> Dict[str, Any]:
    """Return a side-applicable pregame view including frozen target context."""
    signal = copy.deepcopy(dict(record.get(f"{side}Signal") or {}))
    snapshot = record.get("frozenFundamentalsSnapshot")
    valid, _ = valid_target_snapshot(record, snapshot)
    if valid:
        signal["historicalPointInTimeContext"] = copy.deepcopy(
            dict(snapshot.get(side) or {})
        )
        signal["historicalPointInTimeContextMeta"] = {
            "available": 1.0,
            "pointInTimeVerified": 1.0,
            "providerIndependent": 1.0,
        }
    return signal


def context_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    material = []
    for record in records:
        snapshot = record.get("frozenFundamentalsSnapshot")
        valid, _ = valid_target_snapshot(record, snapshot)
        if valid:
            material.append(
                {
                    "identity": _identity(record),
                    "snapshotFingerprint": snapshot.get("fingerprint"),
                }
            )
    material.sort(key=lambda row: tuple(row["identity"]))
    return _sha(material)
