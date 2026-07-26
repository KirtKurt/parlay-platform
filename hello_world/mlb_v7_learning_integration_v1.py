from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Sequence

import mlb_v7_integrity_pattern_v1 as integrity

VERSION = "MLB-V7-LEARNING-INTEGRATION-v1"


def _parse_dt(value: Any):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _lock_at(game: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]):
    for key in ("lockAtUtc", "lockAt", "featureLockAtUtc"):
        parsed = _parse_dt(game.get(key))
        if parsed:
            return parsed
    commence = _parse_dt(game.get("commenceTime") or game.get("commence_time"))
    if commence:
        return commence - timedelta(minutes=45)
    latest = None
    for row in observations or []:
        parsed = _parse_dt(row.get("observedAt") or row.get("pulledAt") or row.get("timestamp"))
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _probability_key(observations: Sequence[Mapping[str, Any]]) -> str:
    for key in ("deVigProbability", "marketConsensusProbability", "probLatest", "fairProbability"):
        if any(row.get(key) not in (None, "") for row in observations or []):
            return key
    return "deVigProbability"


def augment_signal(game: Mapping[str, Any], observations: Sequence[Mapping[str, Any]], signal: Mapping[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(dict(signal))
    lock = _lock_at(game, observations)
    canonical = list(observations or [])
    proof = {
        "version": integrity.VERSION,
        "uniqueSlotCount": len(canonical),
        "inputObservationCount": len(canonical),
        "rejected": {},
        "fingerprint": None,
        "trainingEligible": False,
    }
    if lock is not None:
        canonical, proof = integrity.canonicalize_slots(observations, lock_at=lock)
    pattern = integrity.temporal_pattern_features(canonical, _probability_key(canonical))
    coverage = out.get("coverageRatio")
    if coverage in (None, ""):
        coverage = ((out.get("temporalFeatures") or {}).get("horizons") or {}).get("full", {}).get("coverageRatio", 0.0)
    pattern["coverageRatio"] = coverage or 0.0
    pattern["bookDivergence"] = out.get("bookDivergence") or 0.0
    interactions = integrity.interaction_features(pattern)
    derived = copy.deepcopy(dict(out.get("derivedFeatures") or {}))
    derived.update(pattern)
    derived.update(interactions)
    out["derivedFeatures"] = derived
    out["v7IntegrityProof"] = proof
    out["v7PatternFeatureVersion"] = integrity.VERSION
    out["v7LearningIntegrationVersion"] = VERSION
    return out


def resolve_v7_authority(state: Mapping[str, Any]) -> Dict[str, Any]:
    champion = state.get("activeChampion") or state.get("champion")
    candidate = state.get("latestCandidate") or state.get("candidate")
    if isinstance(champion, Mapping) and champion.get("policy"):
        return {"ok": True, "authority": "ACTIVE_CHAMPION", "policy": champion["policy"], "policyDigest": champion.get("policyDigest")}
    return {
        "ok": False,
        "authority": "NO_ACTIVE_CHAMPION",
        "policy": None,
        "candidatePresent": isinstance(candidate, Mapping),
        "reason": "rejected_or_unpromoted_candidate_cannot_be_called_v7",
    }


def install(optimizer: Any) -> None:
    if getattr(optimizer, "_INQSI_V7_LEARNING_INTEGRATION_INSTALLED", False):
        return
    original_signal = optimizer._signal
    original_search = optimizer.search

    def signal(game, observations, side, expected_slots):
        base = original_signal(game, observations, side, expected_slots)
        return augment_signal(game, observations, base)

    def search(records, config=None, **kwargs):
        validation = integrity.validate_training_rows(records)
        if not validation["trainingEligible"]:
            return {
                "ok": False,
                "version": getattr(optimizer, "VERSION", VERSION),
                "status": "TRAINING_BLOCKED",
                "reason": "no_integrity_eligible_training_rows",
                "v7IntegrityValidation": {k: v for k, v in validation.items() if k != "accepted"},
            }
        result = original_search(validation["accepted"], config, **kwargs)
        if isinstance(result, Mapping):
            result = copy.deepcopy(dict(result))
            result["v7IntegrityValidation"] = {k: v for k, v in validation.items() if k != "accepted"}
            result["v7LearningIntegrationVersion"] = VERSION
        return result

    optimizer._signal = signal
    optimizer.search = search
    for name in ("_rank", "_candidate_rank", "rank_metrics"):
        if hasattr(optimizer, name):
            setattr(optimizer, name, integrity.candidate_rank)
    optimizer.V7_LEARNING_INTEGRATION_VERSION = VERSION
    optimizer._INQSI_V7_LEARNING_INTEGRATION_INSTALLED = True
