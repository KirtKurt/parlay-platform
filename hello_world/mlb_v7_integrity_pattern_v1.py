from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

VERSION = "MLB-V7-INTEGRITY-PATTERN-v1"


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def strict_binary_label(row: Mapping[str, Any], key: str = "homeWon") -> int:
    value = row.get(key)
    if isinstance(value, bool):
        return int(value)
    if value in (0, 1, "0", "1"):
        return int(value)
    raise ValueError(f"invalid_or_missing_binary_label:{key}")


def _parse_dt(value: Any) -> datetime:
    if not value:
        raise ValueError("missing_timestamp")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonicalize_slots(observations: Iterable[Mapping[str, Any]], *, lock_at: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    lock = _parse_dt(lock_at)
    source = list(observations or [])
    by_slot: Dict[str, Dict[str, Any]] = {}
    rejected = defaultdict(int)
    for raw in source:
        row = dict(raw)
        try:
            observed = _parse_dt(row.get("observedAt") or row.get("pulledAt") or row.get("timestamp"))
        except Exception:
            rejected["invalid_timestamp"] += 1
            continue
        if observed > lock:
            rejected["post_lock"] += 1
            continue
        slot_epoch = int(observed.timestamp()) // 900 * 900
        slot = datetime.fromtimestamp(slot_epoch, tz=timezone.utc).isoformat()
        existing = by_slot.get(slot)
        if existing is None:
            row["_canonicalObservedAt"] = observed.isoformat()
            by_slot[slot] = row
            continue
        existing_dt = _parse_dt(existing["_canonicalObservedAt"])
        if observed >= existing_dt:
            row["_canonicalObservedAt"] = observed.isoformat()
            by_slot[slot] = row
        rejected["duplicate_slot"] += 1
    canonical = [by_slot[key] for key in sorted(by_slot)]
    payload = [{k: v for k, v in row.items() if k != "_canonicalObservedAt"} for row in canonical]
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return canonical, {
        "version": VERSION,
        "uniqueSlotCount": len(canonical),
        "inputObservationCount": len(source),
        "rejected": dict(rejected),
        "fingerprint": fingerprint,
        "trainingEligible": bool(canonical) and rejected["post_lock"] == 0,
    }


def _series(observations: Sequence[Mapping[str, Any]], key: str) -> List[float]:
    out: List[float] = []
    for row in observations:
        value = _finite(row.get(key))
        if value is not None:
            out.append(value)
    return out


def temporal_pattern_features(observations: Sequence[Mapping[str, Any]], probability_key: str = "deVigProbability") -> Dict[str, float]:
    values = _series(observations, probability_key)
    if len(values) < 2:
        return {"patternObservationCount": float(len(values)), "movementNet": 0.0, "movementPathLength": 0.0, "movementEfficiency": 0.0, "reversalCount": 0.0, "reversalMagnitude": 0.0, "largestSingleMove": 0.0, "lateMoveShare": 0.0, "trendPersistence": 0.0, "volatility": 0.0, "accelerationEnergy": 0.0}
    deltas = [b - a for a, b in zip(values, values[1:])]
    nonzero = [d for d in deltas if abs(d) > 1e-12]
    reversals = 0
    reversal_magnitude = 0.0
    for previous, current in zip(nonzero, nonzero[1:]):
        if previous * current < 0:
            reversals += 1
            reversal_magnitude += min(abs(previous), abs(current))
    path = sum(abs(d) for d in deltas)
    net = values[-1] - values[0]
    late_start = max(0, len(deltas) * 2 // 3)
    late_path = sum(abs(d) for d in deltas[late_start:])
    mean = sum(deltas) / len(deltas)
    volatility = math.sqrt(sum((d - mean) ** 2 for d in deltas) / len(deltas))
    accelerations = [b - a for a, b in zip(deltas, deltas[1:])]
    same_direction = sum(1 for a, b in zip(nonzero, nonzero[1:]) if a * b > 0)
    transitions = max(1, len(nonzero) - 1)
    return {"patternObservationCount": float(len(values)), "movementNet": net, "movementPathLength": path, "movementEfficiency": abs(net) / path if path else 0.0, "reversalCount": float(reversals), "reversalMagnitude": reversal_magnitude, "largestSingleMove": max(abs(d) for d in deltas), "lateMoveShare": late_path / path if path else 0.0, "trendPersistence": same_direction / transitions, "volatility": volatility, "accelerationEnergy": sum(a * a for a in accelerations) / max(1, len(accelerations))}


def interaction_features(base: Mapping[str, Any]) -> Dict[str, float]:
    f = lambda name: _finite(base.get(name)) or 0.0
    reversal = f("reversalMagnitude")
    late = f("lateMoveShare")
    coverage = f("coverageRatio")
    divergence = f("bookDivergence")
    volatility = f("volatility")
    efficiency = f("movementEfficiency")
    return {"reversalLateInteraction": reversal * late, "reversalVolatilityInteraction": reversal * volatility, "coverageDivergenceInteraction": coverage * divergence, "efficientLateMoveInteraction": efficiency * late, "lowCoveragePenaltySignal": max(0.0, 0.8 - coverage) * (1.0 + divergence + volatility), "stableConsensusSignal": coverage * max(0.0, 1.0 - divergence) * max(0.0, 1.0 - volatility)}


def candidate_rank(metrics: Mapping[str, Any]) -> Tuple[float, ...]:
    def f(name: str, default: float = 0.0) -> float:
        value = _finite(metrics.get(name))
        return default if value is None else value
    return (f("meanDailyAccuracy"), f("overallAccuracy"), -f("brierScore", 1.0), -f("logLoss", 10.0), f("dailyPassRate"), f("minimumDailyAccuracy"))


def validate_training_rows(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    accepted: List[Dict[str, Any]] = []
    rejected = defaultdict(int)
    seen = set()
    for raw in records or []:
        row = dict(raw)
        try:
            row["homeWon"] = strict_binary_label(row)
        except ValueError:
            rejected["invalid_label"] += 1
            continue
        game_id = str(row.get("officialGamePk") or row.get("gameId") or "")
        day = str(row.get("slateDateEt") or "")
        if not game_id or not day:
            rejected["missing_identity"] += 1
            continue
        identity = (day, game_id)
        if identity in seen:
            rejected["duplicate_game"] += 1
            continue
        if row.get("postLockDataExcluded") is not True:
            rejected["post_lock_proof_missing"] += 1
            continue
        if row.get("gameSpecificLockClipping") is not True:
            rejected["lock_clipping_proof_missing"] += 1
            continue
        seen.add(identity)
        accepted.append(row)
    return {"version": VERSION, "accepted": accepted, "acceptedCount": len(accepted), "rejected": dict(rejected), "trainingEligible": bool(accepted)}
