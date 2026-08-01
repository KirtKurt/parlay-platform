"""Bridge immutable V8 historical context into the V7/V9 shadow learner.

The canonical historical rows keep fundamentals in a record-level frozen snapshot,
while the legacy V7/V9 learner reads team values from ``homeSignal`` and
``awaySignal``. This module performs a read-only, point-in-time-safe projection of
validated target-game context into copied training signals and makes dataset
fingerprints sensitive to material feature-overlay changes.

It never writes a champion, prediction, cutover, lock, or wager.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Dict, Mapping, Sequence

VERSION = "MLB-HISTORICAL-V7-FEATURE-BRIDGE-v1-feature-aware-refit"
TARGET_ROLE = "HISTORICAL_POINT_IN_TIME_RECONSTRUCTION_AT_T_MINUS_45"
COMPOSITE_ROLE = "HISTORICAL_COMPOSITE_POINT_IN_TIME_AT_T_MINUS_45"
TARGET_FAMILY = "targetGame"

TARGET_SIDE_FIELDS = (
    "starterQuality",
    "starterRecentForm",
    "starterVelocity",
    "starterCommand",
    "starterExpectedInnings",
    "bullpenQuality",
    "bullpenFreshness",
    "lineupQuality",
    "lineupAbsenceImpact",
    "platoonMatchup",
    "defenseRating",
    "travelRestRating",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _present(value: Any) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return True


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _strict_binary_label(value: Any) -> int | None:
    if value is True or value == 1 or value == 1.0 or value == "1":
        return 1
    if value is False or value == 0 or value == 0.0 or value == "0":
        return 0
    return None


def _record_identity(record: Mapping[str, Any]) -> str:
    return str(
        record.get("officialGamePk")
        or record.get("gameId")
        or record.get("eventId")
        or record.get("id")
        or ""
    )


def _target_snapshot(record: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = _mapping(record.get("frozenFundamentalsSnapshot"))
    if not snapshot:
        return {}
    if snapshot.get("trainingEligible") is not True:
        return {}
    if snapshot.get("pointInTimeVerified") is not True:
        return {}
    if snapshot.get("postgameFieldsExcluded") is not True:
        return {}
    if snapshot.get("productionAuthorityChanged") is not False:
        return {}
    role = str(snapshot.get("snapshotRole") or "")
    family = _mapping(_mapping(snapshot.get("featureFamilies")).get(TARGET_FAMILY))
    target_metadata = _mapping(record.get("historicalTargetGameContext"))
    target_available = bool(
        target_metadata.get("trainingEligible") is True
        or (
            family.get("available") is True
            and family.get("trainingEligible") is True
            and family.get("pointInTimeVerified") is True
        )
        or role in {TARGET_ROLE, COMPOSITE_ROLE}
    )
    if not target_available:
        return {}
    if snapshot.get("targetGameOutcomeUsed") is not False:
        return {}
    return snapshot


def _expansion_payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    home = _mapping(record.get("homeSignal"))
    away = _mapping(record.get("awaySignal"))
    for value in (
        record.get("oddsMarketExpansionFeatures"),
        home.get("oddsMarketExpansionFeatures"),
        away.get("oddsMarketExpansionFeatures"),
    ):
        if isinstance(value, Mapping) and value:
            return value
    return {}


def _feature_material(record: Mapping[str, Any]) -> Dict[str, Any]:
    snapshot = _mapping(record.get("frozenFundamentalsSnapshot"))
    target = _mapping(record.get("historicalTargetGameContext"))
    bbs = _mapping(record.get("historicalBbsFundamentals"))
    home = _mapping(record.get("homeSignal"))
    away = _mapping(record.get("awaySignal"))
    expansion = _expansion_payload(record)
    return {
        "frozenFundamentalsFingerprint": str(snapshot.get("fingerprint") or ""),
        "targetContextSnapshotFingerprint": str(
            target.get("compositeFingerprint")
            or target.get("snapshotFingerprint")
            or ""
        ),
        "targetContextManifestDigest": str(target.get("manifestDigest") or ""),
        "bbsSnapshotFingerprint": str(bbs.get("snapshotFingerprint") or ""),
        "bbsManifestDigest": str(bbs.get("manifestDigest") or ""),
        "oddsMarketExpansionDigest": _sha(expansion) if expansion else "",
        "homeV8TrainableDigest": _sha(home.get("v8TrainableFeatures"))
        if isinstance(home.get("v8TrainableFeatures"), Mapping)
        else "",
        "awayV8TrainableDigest": _sha(away.get("v8TrainableFeatures"))
        if isinstance(away.get("v8TrainableFeatures"), Mapping)
        else "",
    }


def dataset_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    """Fingerprint canonical identity, labels, and immutable overlay material."""
    rows = []
    for record in records:
        rows.append(
            {
                "date": str(record.get("slateDateEt") or ""),
                "game": _record_identity(record),
                "homeWon": _strict_binary_label(record.get("homeWon")),
                "canonicalFeatureFingerprint": str(
                    record.get("fingerprint")
                    or record.get("featureVectorFingerprint")
                    or ""
                ),
                "featureMaterial": _feature_material(record),
            }
        )
    rows.sort(
        key=lambda item: (
            item["date"],
            item["game"],
            item["canonicalFeatureFingerprint"],
            str(item["homeWon"]),
        )
    )
    return _sha(rows)


def _materialize_v8(
    record: Mapping[str, Any],
    signal: Dict[str, Any],
    side: str,
    learner: Any,
    expansion: Mapping[str, Any],
) -> bool:
    if not expansion:
        return False
    signal["oddsMarketExpansionFeatures"] = copy.deepcopy(dict(expansion))
    generator = getattr(learner, "_v8_trainable", None)
    if not callable(generator):
        return False
    generated = generator(
        record,
        {"oddsMarketExpansionFeatures": expansion},
        side,
    )
    if not isinstance(generated, Mapping):
        return False
    existing = _mapping(signal.get("v8TrainableFeatures"))
    merged = copy.deepcopy(dict(existing))
    for key, value in generated.items():
        if _present(value) or key in {"available", "observationCount", "version"}:
            merged[key] = copy.deepcopy(value)
    signal["v8TrainableFeatures"] = merged
    return bool(
        merged.get("available") is True
        or int(merged.get("observationCount") or 0) > 0
    )


def materialize_training_signals(
    records: Sequence[Mapping[str, Any]], learner: Any
) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """Project validated record-level context into copied team training signals."""
    output: list[Dict[str, Any]] = []
    target_records = 0
    target_signal_pairs = 0
    starter_pairs = 0
    bullpen_pairs = 0
    lineup_pairs = 0
    v8_expansion_records = 0
    v8_trainable_pairs = 0

    for raw in records:
        record = copy.deepcopy(dict(raw))
        snapshot = _target_snapshot(record)
        expansion = _expansion_payload(record)
        if expansion:
            v8_expansion_records += 1
        side_availability: Dict[str, Dict[str, bool]] = {}
        v8_sides = 0

        for side in ("home", "away"):
            signal_key = f"{side}Signal"
            signal = copy.deepcopy(dict(_mapping(record.get(signal_key))))
            availability = {"starter": False, "bullpen": False, "lineup": False}
            if snapshot:
                side_payload = _mapping(snapshot.get(side))
                fundamentals = copy.deepcopy(dict(_mapping(signal.get("fundamentals"))))
                for name in TARGET_SIDE_FIELDS:
                    value = side_payload.get(name)
                    if _present(value):
                        fundamentals[name] = copy.deepcopy(value)
                if _present(snapshot.get("parkRunFactor")):
                    fundamentals["parkRunFactor"] = snapshot.get("parkRunFactor")
                if _present(snapshot.get("weatherRunFactor")):
                    fundamentals["weatherRunFactor"] = snapshot.get("weatherRunFactor")
                signal["fundamentals"] = fundamentals
                signal["fundamentalsSnapshotV2"] = copy.deepcopy(dict(side_payload))
                signal["historicalTargetContextApplied"] = True
                signal["historicalTargetContextFingerprint"] = snapshot.get("fingerprint")
                availability = {
                    "starter": _present(side_payload.get("starterQuality")),
                    "bullpen": _present(side_payload.get("bullpenQuality")),
                    "lineup": _present(side_payload.get("lineupQuality")),
                }
            if _materialize_v8(record, signal, side, learner, expansion):
                v8_sides += 1
            record[signal_key] = signal
            side_availability[side] = availability

        if snapshot:
            target_records += 1
            if all(
                _mapping(record.get(f"{side}Signal")).get(
                    "historicalTargetContextApplied"
                )
                is True
                for side in ("home", "away")
            ):
                target_signal_pairs += 1
            if all(side_availability[side]["starter"] for side in ("home", "away")):
                starter_pairs += 1
            if all(side_availability[side]["bullpen"] for side in ("home", "away")):
                bullpen_pairs += 1
            if all(side_availability[side]["lineup"] for side in ("home", "away")):
                lineup_pairs += 1
        if v8_sides == 2:
            v8_trainable_pairs += 1
        output.append(record)

    proof = {
        "version": VERSION,
        "recordCount": len(output),
        "targetSnapshotRecordCount": target_records,
        "targetSignalPairCount": target_signal_pairs,
        "starterPairAvailableCount": starter_pairs,
        "bullpenPairAvailableCount": bullpen_pairs,
        "lineupPairAvailableCount": lineup_pairs,
        "v8ExpansionRecordCount": v8_expansion_records,
        "v8TrainablePairCount": v8_trainable_pairs,
        "datasetFingerprint": dataset_fingerprint(output),
        "providerCallsMade": 0,
        "selectionUsedOutcomes": False,
        "productionAuthorityChanged": False,
    }
    return output, proof


def install(repairs_module: Any) -> Any:
    """Install the feature-aware fingerprint used by the V7/V9 cadence guard."""
    if getattr(repairs_module, "_INQSI_V7_FEATURE_BRIDGE_INSTALLED", False):
        return repairs_module
    repairs_module.dataset_fingerprint = dataset_fingerprint
    repairs_module.V7_FEATURE_BRIDGE_VERSION = VERSION
    repairs_module._INQSI_V7_FEATURE_BRIDGE_INSTALLED = True
    return repairs_module
