"""Bridge immutable V8 feature overlays into the gated V7/V9 learner.

The canonical historical records remain immutable. This module reads the existing
shadow-only prior-game and target-game manifests, composes them, injects only
point-in-time verified target fundamentals into copied home/away signals, and
produces a semantic feature-corpus fingerprint for learning cadence. It has no
champion, prediction, cutover, wager, or production-write authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Mapping, Sequence, Tuple

import mlb_historical_v7_priority_repairs_v1 as repairs
import mlb_v8_historical_bbs_overlay_v1 as bbs_overlay
import mlb_v8_historical_context_overlay_v1 as context_overlay

VERSION = "MLB-HISTORICAL-V7-FEATURE-BRIDGE-v1-point-in-time-signal-wiring"


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _mapping(value: Any) -> Dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _game_id(record: Mapping[str, Any]) -> str:
    return str(
        record.get("officialGamePk")
        or record.get("gameId")
        or record.get("eventId")
        or record.get("id")
        or ""
    )


def attach_target_context_to_signals(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Expose validated record-level target context to signal-level V7 features."""
    output = copy.deepcopy(dict(record))
    snapshot = output.get("frozenFundamentalsSnapshot")
    context = output.get("historicalTargetGameContext")
    if not isinstance(snapshot, Mapping) or not isinstance(context, Mapping):
        return output
    if context.get("trainingEligible") is not True:
        return output
    if snapshot.get("trainingEligible") is not True:
        return output
    if snapshot.get("pointInTimeVerified") is not True:
        return output
    if not context_overlay.has_family(snapshot, context_overlay.TARGET_FAMILY):
        return output

    snapshot_fingerprint = str(snapshot.get("fingerprint") or "")
    for side in ("home", "away"):
        payload = snapshot.get(side)
        if not isinstance(payload, Mapping):
            continue
        signal_key = f"{side}Signal"
        signal = _mapping(output.get(signal_key))
        fundamentals = _mapping(signal.get("fundamentals"))
        fundamentals.update(_mapping(payload))
        fundamentals["parkRunFactor"] = snapshot.get("parkRunFactor")
        fundamentals["weatherRunFactor"] = snapshot.get("weatherRunFactor")
        signal["fundamentals"] = fundamentals
        signal["fundamentalsSnapshotV2"] = copy.deepcopy(fundamentals)
        signal["historicalFundamentalsAvailable"] = True
        signal["historicalFundamentalsPointInTimeVerified"] = True
        signal["historicalFundamentalsSnapshotFingerprint"] = snapshot_fingerprint
        signal["historicalFundamentalsAuthority"] = context_overlay.AUTHORITY
        signal["historicalFundamentalsProductionAuthorityChanged"] = False
        output[signal_key] = signal
    return output


def feature_corpus_state(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Fingerprint only feature material actually wired into the gated learner."""
    material = []
    target_rows = 0
    prior_rows = 0
    starter_rows = 0
    bullpen_rows = 0
    lineup_rows = 0
    for raw in records:
        record = raw if isinstance(raw, Mapping) else {}
        snapshot = record.get("frozenFundamentalsSnapshot")
        target = record.get("historicalTargetGameContext")
        prior = record.get("historicalBbsFundamentals")
        target_eligible = bool(
            isinstance(snapshot, Mapping)
            and isinstance(target, Mapping)
            and target.get("trainingEligible") is True
            and context_overlay.has_family(snapshot, context_overlay.TARGET_FAMILY)
        )
        prior_eligible = bool(
            isinstance(prior, Mapping) and prior.get("trainingEligible") is True
        )
        if prior_eligible:
            prior_rows += 1
        home = snapshot.get("home") if isinstance(snapshot, Mapping) else {}
        away = snapshot.get("away") if isinstance(snapshot, Mapping) else {}
        if target_eligible:
            target_rows += 1
            starter_rows += int(
                isinstance(home, Mapping)
                and isinstance(away, Mapping)
                and home.get("starterQuality") is not None
                and away.get("starterQuality") is not None
            )
            bullpen_rows += int(
                isinstance(home, Mapping)
                and isinstance(away, Mapping)
                and home.get("bullpenQuality") is not None
                and away.get("bullpenQuality") is not None
            )
            lineup_rows += int(
                isinstance(home, Mapping)
                and isinstance(away, Mapping)
                and home.get("lineupQuality") is not None
                and away.get("lineupQuality") is not None
            )
        material.append(
            {
                "date": str(record.get("slateDateEt") or ""),
                "game": _game_id(record),
                "targetEligible": target_eligible,
                "targetSnapshot": str(
                    (target or {}).get("compositeFingerprint")
                    or (target or {}).get("snapshotFingerprint")
                    or (snapshot or {}).get("fingerprint")
                    or ""
                ),
                "priorEligible": bool(prior_eligible and target_eligible),
                "priorSnapshot": str((prior or {}).get("snapshotFingerprint") or "")
                if target_eligible
                else "",
            }
        )
    material.sort(
        key=lambda row: (
            row["date"],
            row["game"],
            row["targetSnapshot"],
            row["priorSnapshot"],
        )
    )
    return {
        "version": VERSION,
        "recordCount": len(records),
        "materializedFeatureRowCount": target_rows,
        "targetGameFeatureRowCount": target_rows,
        "priorGameSupplementalRowCount": prior_rows,
        "starterFeatureRowCount": starter_rows,
        "bullpenFeatureRowCount": bullpen_rows,
        "lineupFeatureRowCount": lineup_rows,
        "fingerprint": _sha(material),
        "selectionUsedOutcomes": False,
        "productionAuthorityChanged": False,
    }


def dataset_fingerprint(
    records: Sequence[Mapping[str, Any]], feature_state: Mapping[str, Any]
) -> str:
    return _sha(
        {
            "canonicalDatasetFingerprint": repairs.dataset_fingerprint(records),
            "featureCorpusFingerprint": feature_state.get("fingerprint"),
            "recordCount": len(records),
        }
    )


def load_and_apply(
    records: Sequence[Mapping[str, Any]],
) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    prior_enriched, prior_proof = bbs_overlay.load_and_apply(records)
    target_enriched, target_proof = context_overlay.load_and_apply(prior_enriched)
    output = [attach_target_context_to_signals(row) for row in target_enriched]
    feature_state = feature_corpus_state(output)
    proof = {
        "version": VERSION,
        "priorGameOverlay": prior_proof,
        "targetGameOverlay": target_proof,
        "featureCorpus": feature_state,
        "providerCallsMade": 0,
        "selectionUsedOutcomes": False,
        "productionAuthorityChanged": False,
    }
    return output, proof
