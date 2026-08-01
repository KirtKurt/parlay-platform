"""Project leakage-safe prior-game BBS snapshots into V7 team signals.

The BBS overlay is stored at the canonical record level. Legacy V7 feature code
receives only homeSignal and awaySignal, so copied signal projections are required
before the prior-game history can participate in shadow training.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Sequence

VERSION = "MLB-HISTORICAL-V7-PRIOR-SIGNAL-BRIDGE-v1"
PRIOR_FAMILY = "priorGame"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _prior_snapshot(record: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = _mapping(record.get("historicalBbsFundamentals"))
    snapshot = _mapping(record.get("frozenFundamentalsSnapshot"))
    if metadata.get("trainingEligible") is not True or not snapshot:
        return {}
    if snapshot.get("trainingEligible") is not True:
        return {}
    if snapshot.get("pointInTimeVerified") is not True:
        return {}
    if snapshot.get("postgameFieldsExcluded") is not True:
        return {}
    if snapshot.get("sameDayResultsExcluded") is not True:
        return {}
    if snapshot.get("targetGameOutcomeUsed") is not False:
        return {}
    if snapshot.get("productionAuthorityChanged") is not False:
        return {}
    family = _mapping(_mapping(snapshot.get("featureFamilies")).get(PRIOR_FAMILY))
    role = str(snapshot.get("snapshotRole") or "")
    available = bool(
        (
            family.get("available") is True
            and family.get("trainingEligible") is True
            and family.get("pointInTimeVerified") is True
        )
        or role.startswith("BBD_STRICTLY_PRIOR_COMPLETED_GAME_FEATURES")
        or "bbsHistoryGames" in _mapping(snapshot.get("home"))
    )
    return snapshot if available else {}


def materialize_prior_signals(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    output: list[Dict[str, Any]] = []
    metadata_eligible_count = 0
    snapshot_count = 0
    signal_pair_count = 0
    history_pair_count = 0

    for raw in records:
        record = copy.deepcopy(dict(raw))
        if _mapping(record.get("historicalBbsFundamentals")).get(
            "trainingEligible"
        ) is True:
            metadata_eligible_count += 1
        snapshot = _prior_snapshot(record)
        applied_sides = 0
        history_sides = 0
        if snapshot:
            snapshot_count += 1
        for side in ("home", "away"):
            key = f"{side}Signal"
            signal = copy.deepcopy(dict(_mapping(record.get(key))))
            if snapshot:
                side_payload = copy.deepcopy(dict(_mapping(snapshot.get(side))))
                existing = copy.deepcopy(
                    dict(_mapping(signal.get("fundamentalsSnapshotV2")))
                )
                existing.update(side_payload)
                signal["fundamentalsSnapshotV2"] = existing
                signal["historicalBbsPriorContextApplied"] = True
                signal["historicalBbsPriorContextFingerprint"] = snapshot.get(
                    "fingerprint"
                )
                applied_sides += 1
                try:
                    if float(side_payload.get("bbsHistoryGames")) >= 5:
                        history_sides += 1
                except (TypeError, ValueError):
                    pass
            record[key] = signal
        if applied_sides == 2:
            signal_pair_count += 1
        if history_sides == 2:
            history_pair_count += 1
        output.append(record)

    if snapshot_count != metadata_eligible_count:
        raise RuntimeError(
            "eligible BBS prior snapshots failed point-in-time validation:"
            f"{snapshot_count}/{metadata_eligible_count}"
        )
    if signal_pair_count != snapshot_count:
        raise RuntimeError(
            "eligible BBS prior snapshots were not projected into both team signals:"
            f"{signal_pair_count}/{snapshot_count}"
        )
    if snapshot_count and history_pair_count <= 0:
        raise RuntimeError("BBS prior projection contains no five-game history pairs")

    return output, {
        "version": VERSION,
        "recordCount": len(output),
        "metadataEligibleRecordCount": metadata_eligible_count,
        "priorSnapshotRecordCount": snapshot_count,
        "priorSignalPairCount": signal_pair_count,
        "priorHistoryFiveGamePairCount": history_pair_count,
        "providerCallsMade": 0,
        "selectionUsedOutcomes": False,
        "productionAuthorityChanged": False,
    }


def install(feature_bridge: Any) -> Any:
    """Compose prior-game fields after target projection so both families survive."""
    if getattr(feature_bridge, "_INQSI_V7_PRIOR_SIGNAL_BRIDGE_INSTALLED", False):
        return feature_bridge
    original = feature_bridge.materialize_training_signals

    def wrapped(records: Sequence[Mapping[str, Any]], learner: Any):
        target_records, proof = original(records, learner)
        output, prior_proof = materialize_prior_signals(target_records)
        merged = dict(proof)
        merged["priorSignalMaterialization"] = prior_proof
        merged["priorSnapshotRecordCount"] = prior_proof[
            "priorSnapshotRecordCount"
        ]
        merged["priorSignalPairCount"] = prior_proof["priorSignalPairCount"]
        merged["priorHistoryFiveGamePairCount"] = prior_proof[
            "priorHistoryFiveGamePairCount"
        ]
        merged["priorAndTargetSignalsComposed"] = True
        return output, merged

    feature_bridge.materialize_training_signals = wrapped
    feature_bridge.V7_PRIOR_SIGNAL_BRIDGE_VERSION = VERSION
    feature_bridge._INQSI_V7_PRIOR_SIGNAL_BRIDGE_INSTALLED = True
    return feature_bridge
