"""Leakage-safe Odds-API market-intelligence learning for the MLB optimizer.

Adds five capabilities using only pre-lock Odds API observations:
1. market-regime classification;
2. curve-shape recognition;
3. sportsbook-leadership analysis;
4. training-only historical fingerprint similarity;
5. nonlinear/interaction derived-feature discovery.

The same bounded formula is used by compiled historical evaluation and live champion
scoring. Similarity priors are fit exclusively from the chronological training block;
walk-forward and untouched-audit outcomes are never consulted.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

VERSION = "MLB-ODDS-MARKET-INTELLIGENCE-v2-five-capabilities-lock-bounded"
SIMILARITY_VERSION = "MLB-ODDS-FINGERPRINT-KNN-v1-training-only"

DERIVED_POLICY_DEFAULTS: Dict[str, float] = {
    "derivedMovementSqrtWeight": 0.0,
    "derivedAgreementMomentumWeight": 0.0,
    "derivedVelocityInteractionWeight": 0.0,
    "derivedAccelerationInteractionWeight": 0.0,
    "derivedInstabilityPenalty": 0.0,
    "derivedVelocityGapWeight": 0.0,
    "regimeTrendWeight": 0.0,
    "regimeChaosPenalty": 0.0,
    "curveEfficiencyWeight": 0.0,
    "curveShockPenalty": 0.0,
    "bookLeadershipWeight": 0.0,
    "bookFollowThroughWeight": 0.0,
    "fingerprintSimilarityWeight": 0.0,
    "fingerprintConfidenceWeight": 0.0,
}

DERIVED_POLICY_BOUNDS: Dict[str, Tuple[float, float]] = {
    "derivedMovementSqrtWeight": (-0.20, 0.20),
    "derivedAgreementMomentumWeight": (-4.0, 4.0),
    "derivedVelocityInteractionWeight": (-0.10, 0.10),
    "derivedAccelerationInteractionWeight": (-0.05, 0.05),
    "derivedInstabilityPenalty": (0.0, 0.20),
    "derivedVelocityGapWeight": (-0.05, 0.05),
    "regimeTrendWeight": (-0.10, 0.10),
    "regimeChaosPenalty": (0.0, 0.15),
    "curveEfficiencyWeight": (-0.10, 0.10),
    "curveShockPenalty": (0.0, 0.10),
    "bookLeadershipWeight": (-0.10, 0.10),
    "bookFollowThroughWeight": (-0.10, 0.10),
    "fingerprintSimilarityWeight": (-0.25, 0.25),
    "fingerprintConfidenceWeight": (-0.10, 0.10),
}

DERIVED_POLICY_CHOICES: Dict[str, Sequence[float]] = {
    "derivedMovementSqrtWeight": (-0.08, -0.04, 0.0, 0.04, 0.08),
    "derivedAgreementMomentumWeight": (-1.5, -0.75, 0.0, 0.75, 1.5, 2.5),
    "derivedVelocityInteractionWeight": (-0.04, -0.02, 0.0, 0.02, 0.04),
    "derivedAccelerationInteractionWeight": (-0.02, -0.01, 0.0, 0.01, 0.02),
    "derivedInstabilityPenalty": (0.0, 0.01, 0.025, 0.05, 0.08),
    "derivedVelocityGapWeight": (-0.02, -0.01, 0.0, 0.01, 0.02),
    "regimeTrendWeight": (-0.04, -0.02, 0.0, 0.02, 0.04),
    "regimeChaosPenalty": (0.0, 0.01, 0.025, 0.05),
    "curveEfficiencyWeight": (-0.04, -0.02, 0.0, 0.02, 0.04),
    "curveShockPenalty": (0.0, 0.01, 0.025, 0.05),
    "bookLeadershipWeight": (-0.04, -0.02, 0.0, 0.02, 0.04),
    "bookFollowThroughWeight": (-0.04, -0.02, 0.0, 0.02, 0.04),
    "fingerprintSimilarityWeight": (-0.12, -0.06, 0.0, 0.06, 0.12, 0.18),
    "fingerprintConfidenceWeight": (-0.04, 0.0, 0.04, 0.08),
}

FEATURE_ORDER = (
    "movementSqrt", "agreementMomentum", "velocityInteraction",
    "accelerationInteraction", "instabilityInteraction", "velocityGap",
    "regimeTrend", "regimeChaos", "curveEfficiency", "curveShock",
    "bookLeadership", "bookFollowThrough", "fingerprintSimilarityEdge",
    "fingerprintSimilarityConfidence",
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _nested(mapping: Any, *path: str) -> Any:
    value = mapping
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _points(observations: Sequence[Mapping[str, Any]], side: str) -> List[Tuple[str, float]]:
    rows = []
    for row in observations:
        at = str(row.get("providerTimestampUtc") or "")
        value = row.get(f"{side}Fair")
        if at and value not in (None, ""):
            rows.append((at, _f(value)))
    return sorted(rows)


def _curve_intelligence(observations: Sequence[Mapping[str, Any]], side: str) -> Dict[str, Any]:
    points = _points(observations, side)
    values = [value for _, value in points]
    if len(values) < 2:
        return {"available": False, "curveShape": "INSUFFICIENT", "regime": "INSUFFICIENT"}
    changes = [current - previous for previous, current in zip(values, values[1:])]
    net = values[-1] - values[0]
    gross = sum(abs(value) for value in changes)
    efficiency = abs(net) / gross if gross > 1e-12 else 0.0
    shocks = [value for value in changes if abs(value) >= 0.008]
    reversals = sum(a * b < 0 for a, b in zip(changes, changes[1:]) if abs(a) > 0.0005 and abs(b) > 0.0005)
    midpoint = max(1, len(changes) // 2)
    early = sum(changes[:midpoint])
    late = sum(changes[midpoint:])
    late_share = abs(late) / gross if gross > 1e-12 else 0.0
    curvature = late - early
    range_width = max(values) - min(values)
    range_position = (values[-1] - min(values)) / range_width if range_width > 1e-12 else 0.5
    direction = 1.0 if net > 0 else -1.0 if net < 0 else 0.0
    aligned_late = direction * late
    if reversals >= 3 or (gross >= 0.025 and efficiency < 0.30):
        regime = "CHAOTIC"
    elif shocks and aligned_late > 0.004 and efficiency >= 0.55:
        regime = "STEAM"
    elif shocks and aligned_late < -0.004:
        regime = "LATE_REVERSAL"
    elif abs(net) < 0.004 and gross < 0.012:
        regime = "STABLE"
    elif efficiency >= 0.70:
        regime = "TRENDING"
    elif late_share >= 0.65:
        regime = "LATE_MOVE"
    else:
        regime = "MIXED"
    if reversals >= 3:
        shape = "OSCILLATION"
    elif shocks and abs(shocks[-1]) >= max(0.012, gross * 0.55):
        shape = "SPIKE"
    elif direction and aligned_late < -0.004:
        shape = "RECOVERY_OR_REVERSAL"
    elif abs(curvature) >= 0.006 and direction * curvature > 0:
        shape = "ACCELERATING"
    elif abs(curvature) >= 0.006 and direction * curvature < 0:
        shape = "DECELERATING"
    elif efficiency >= 0.70:
        shape = "LINEAR_TREND"
    elif abs(net) < 0.004:
        shape = "PLATEAU"
    else:
        shape = "IRREGULAR"
    material = [{"at": at, "p": round(value, 10)} for at, value in points]
    return {
        "available": True, "version": VERSION, "regime": regime, "curveShape": shape,
        "netMovement": round(net, 10), "grossMovement": round(gross, 10),
        "pathEfficiency": round(efficiency, 10), "shockCount": len(shocks),
        "reversalCount": reversals, "lateMovement": round(late, 10),
        "lateShare": round(late_share, 10), "curvature": round(curvature, 10),
        "rangePosition": round(range_position, 10),
        "sourcePathFingerprint": hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def _book_leadership(observations: Sequence[Mapping[str, Any]], side: str) -> Dict[str, Any]:
    histories: Dict[str, List[Tuple[int, float]]] = {}
    for index, row in enumerate(observations):
        books = row.get("books") or {}
        if not isinstance(books, Mapping):
            continue
        for book, quote in books.items():
            if not isinstance(quote, Mapping):
                continue
            fair = quote.get(f"{side}Fair")
            if fair not in (None, ""):
                histories.setdefault(str(book), []).append((index, _f(fair)))
    moves = []
    for book, rows in histories.items():
        if len(rows) < 2:
            continue
        start = rows[0][1]
        threshold = None
        for index, value in rows[1:]:
            if abs(value - start) >= 0.006:
                threshold = index
                break
        if threshold is not None:
            moves.append((threshold, book, rows[-1][1] - start))
    if not moves:
        return {"available": False, "leader": None, "leadScore": 0.0, "followThrough": 0.0}
    moves.sort()
    first_index, leader, leader_move = moves[0]
    direction = 1.0 if leader_move > 0 else -1.0
    followers = [direction * move > 0 for index, _, move in moves[1:] if index >= first_index]
    follow = sum(followers) / len(followers) if followers else 0.0
    lead_gap = (moves[1][0] - first_index) if len(moves) > 1 else 0
    lead_score = min(1.0, max(0.0, lead_gap / 4.0)) * min(1.0, abs(leader_move) / 0.02)
    return {"available": True, "leader": leader, "leaderMove": round(leader_move, 10),
            "leaderDirection": int(direction), "leadScore": round(lead_score, 10),
            "followThrough": round(follow, 10), "movingBookCount": len(moves)}


def derive(signal: Mapping[str, Any]) -> Dict[str, float]:
    delta = _f(signal.get("delta"), 0.0)
    divergence = max(0.0, _f(signal.get("bookDivergence"), 0.0))
    reversals = max(0.0, _f(signal.get("reversalCount"), 0.0))
    coverage = min(1.0, max(0.0, _f(_nested(signal, "temporalFeatures", "horizons", "full", "coverageRatio"), 0.0)))
    velocity60 = _f(_nested(signal, "temporalFeatures", "horizons", "60m", "velocityPpHr"), 0.0)
    velocity_full = _f(_nested(signal, "temporalFeatures", "horizons", "full", "velocityPpHr"), 0.0)
    acceleration180 = _f(_nested(signal, "temporalFeatures", "horizons", "180m", "accelerationPpHr2"), 0.0)
    volatility180 = max(0.0, _f(_nested(signal, "temporalFeatures", "horizons", "180m", "volatilityPpPerPull"), 0.0))
    direction = 1.0 if delta > 0 else -1.0 if delta < 0 else 0.0
    agreement = max(0.0, 1.0 - min(1.0, divergence / 0.075))
    intelligence = signal.get("marketIntelligence") or {}
    curve = intelligence.get("curve") or {}
    leadership = intelligence.get("bookLeadership") or {}
    regime = str(curve.get("regime") or "")
    similarity = signal.get("fingerprintSimilarity") or {}
    values = {
        "movementSqrt": direction * math.sqrt(abs(delta)),
        "agreementMomentum": delta * agreement * coverage,
        "velocityInteraction": delta * velocity60,
        "accelerationInteraction": delta * acceleration180,
        "instabilityInteraction": abs(delta) * volatility180 * (1.0 + min(5.0, reversals)),
        "velocityGap": velocity60 - velocity_full,
        "regimeTrend": direction * (1.0 if regime in {"TRENDING", "STEAM", "LATE_MOVE"} else 0.0) * _f(curve.get("pathEfficiency"), 0.0),
        "regimeChaos": 1.0 if regime in {"CHAOTIC", "LATE_REVERSAL"} else 0.0,
        "curveEfficiency": direction * _f(curve.get("pathEfficiency"), 0.0),
        "curveShock": min(1.0, _f(curve.get("shockCount"), 0.0) / 3.0),
        "bookLeadership": _f(leadership.get("leaderDirection"), 0.0) * _f(leadership.get("leadScore"), 0.0),
        "bookFollowThrough": direction * _f(leadership.get("followThrough"), 0.0),
        "fingerprintSimilarityEdge": _f(similarity.get("sideWinRate"), 0.5) - 0.5,
        "fingerprintSimilarityConfidence": _f(similarity.get("confidence"), 0.0),
    }
    return {name: round(values.get(name, 0.0), 12) for name in FEATURE_ORDER}


def _fingerprint_vector(record: Mapping[str, Any]) -> Tuple[float, ...]:
    home = derive(record.get("homeSignal") or {})
    away = derive(record.get("awaySignal") or {})
    keys = ("movementSqrt", "agreementMomentum", "velocityInteraction", "instabilityInteraction", "regimeTrend", "regimeChaos", "curveEfficiency", "curveShock", "bookLeadership", "bookFollowThrough")
    return tuple(round(home[key] - away[key], 10) for key in keys)


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _apply_similarity(records: List[MutableMapping[str, Any]], train_dates: Iterable[str], k: int = 25) -> None:
    train_set = set(train_dates)
    train = [(row, _fingerprint_vector(row)) for row in records if str(row.get("slateDateEt") or "") in train_set]
    for row in records:
        vector = _fingerprint_vector(row)
        candidates = []
        for neighbor, other in train:
            if neighbor is row:
                continue
            candidates.append((_distance(vector, other), int(neighbor.get("homeWon") or 0)))
        candidates.sort(key=lambda item: item[0])
        nearest = candidates[:k]
        if not nearest:
            home_rate, confidence = 0.5, 0.0
        else:
            weights = [1.0 / (0.05 + distance) for distance, _ in nearest]
            total = sum(weights)
            home_rate = sum(weight * outcome for weight, (_, outcome) in zip(weights, nearest)) / total
            confidence = min(1.0, len(nearest) / float(k)) * (1.0 / (1.0 + sum(distance for distance, _ in nearest) / len(nearest)))
        payload = {"version": SIMILARITY_VERSION, "neighborCount": len(nearest),
                   "homeWinRate": round(home_rate, 8), "confidence": round(confidence, 8),
                   "trainingOnly": True}
        home = row.get("homeSignal") or {}
        away = row.get("awaySignal") or {}
        home["fingerprintSimilarity"] = {**payload, "sideWinRate": payload["homeWinRate"]}
        away["fingerprintSimilarity"] = {**payload, "sideWinRate": round(1.0 - payload["homeWinRate"], 8)}


def adjustment(signal: Mapping[str, Any], policy: Mapping[str, Any]) -> float:
    features = derive(signal)
    value = 0.0
    for feature, policy_name in zip(FEATURE_ORDER, DERIVED_POLICY_DEFAULTS):
        component = features[feature] * _f(policy.get(policy_name))
        if policy_name in {"derivedInstabilityPenalty", "regimeChaosPenalty", "curveShockPenalty"}:
            value -= abs(component)
        else:
            value += component
    return max(-0.15, min(0.15, value))


def _signal_values(signal: Mapping[str, Any]) -> Tuple[float, ...]:
    features = derive(signal)
    return tuple(features[name] for name in FEATURE_ORDER)


def _policy_values(policy: Mapping[str, Any]) -> Tuple[float, ...]:
    return tuple(_f(policy.get(name)) for name in DERIVED_POLICY_DEFAULTS)


def _compiled_adjustment(signal: Tuple[Any, ...], policy: Tuple[float, ...]) -> float:
    features = signal[-len(FEATURE_ORDER):]
    weights = policy[-len(DERIVED_POLICY_DEFAULTS):]
    value = 0.0
    for index, (feature, weight) in enumerate(zip(features, weights)):
        component = feature * weight
        value += -abs(component) if index in {4, 7, 9} else component
    return max(-0.15, min(0.15, value))


def install(optimizer: Any, policy_runtime: Any) -> None:
    if getattr(optimizer, "_INQSI_ODDS_MARKET_INTELLIGENCE_V2_INSTALLED", False):
        return
    policy_runtime.BASELINE_POLICY.update({name: policy_runtime.BASELINE_POLICY.get(name, default) for name, default in DERIVED_POLICY_DEFAULTS.items()})
    policy_runtime._NUMERIC_BOUNDS.update(DERIVED_POLICY_BOUNDS)
    original_signal = optimizer._signal
    original_candidate_policy = optimizer._candidate_policy
    original_compile_signal = optimizer._compile_signal_for_search
    original_compile_policy = optimizer._compile_policy_for_search
    original_score_compiled = optimizer._score_compiled_signal
    original_production_optimized = policy_runtime.production_optimized_signal
    original_search = optimizer.search

    def patched_signal(game, observations, side, expected_slots):
        out = original_signal(game, observations, side, expected_slots)
        out["marketIntelligence"] = {"version": VERSION, "oddsApiOnly": True,
            "curve": _curve_intelligence(observations, side),
            "bookLeadership": _book_leadership(observations, side)}
        out["derivedFeatures"] = derive(out)
        out["derivedFeatureVersion"] = VERSION
        out["derivedFeatureSource"] = "odds_api_game_t_minus_45_clipped_observations_only"
        return out

    def patched_candidate_policy(rng: random.Random):
        candidate = original_candidate_policy(rng)
        for name, values in DERIVED_POLICY_CHOICES.items():
            candidate[name] = rng.choice(values)
        return candidate

    def patched_compile_signal(signal):
        return tuple(original_compile_signal(signal)) + _signal_values(signal)

    def patched_compile_policy(policy):
        return tuple(original_compile_policy(policy)) + _policy_values(policy)

    def patched_score_compiled(signal, policy):
        base_score, _ = original_score_compiled(tuple(signal[:16]), tuple(policy[:27]))
        score = max(0.0, min(100.0, base_score + _compiled_adjustment(signal, policy) * 100.0))
        probability = 1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0))
        return round(score, 4), round(max(0.05, min(0.95, probability)), 8)

    def patched_production_optimized_signal(signal, policy):
        out = original_production_optimized(signal, policy)
        derived = derive(signal)
        derived_adjustment = adjustment(signal, policy)
        base_score = _f(out.get("optimizedWinnerScore"), 50.0)
        score = max(0.0, min(100.0, base_score + derived_adjustment * 100.0))
        probability = 1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0))
        out.update({"derivedFeatures": derived, "derivedFeatureVersion": VERSION,
            "derivedFeatureScoreAdjustment": round(derived_adjustment * 100.0, 6),
            "optimizedWinnerScore": round(score, 4), "score": round(score, 4),
            "winProbability": round(max(0.05, min(0.95, probability)), 8),
            "winProbabilityPct": round(max(0.05, min(0.95, probability)) * 100.0, 4)})
        return out

    def patched_search(records, config=None, *, untouched_holdout_dates=None):
        cfg = (config or optimizer.SearchConfig()).validate()
        clean = [copy.deepcopy(dict(row)) for row in records]
        try:
            partitions = optimizer.chronological_partitions(clean, cfg, untouched_holdout_dates=untouched_holdout_dates)
            _apply_similarity(clean, partitions["train"])
        except Exception:
            pass
        return original_search(clean, cfg, untouched_holdout_dates=untouched_holdout_dates)

    optimizer._signal = patched_signal
    optimizer._candidate_policy = patched_candidate_policy
    optimizer._compile_signal_for_search = patched_compile_signal
    optimizer._compile_policy_for_search = patched_compile_policy
    optimizer._score_compiled_signal = patched_score_compiled
    optimizer.search = patched_search
    policy_runtime.production_optimized_signal = patched_production_optimized_signal
    optimizer.DERIVED_FEATURE_VERSION = VERSION
    optimizer._INQSI_DERIVED_FEATURES_V1_INSTALLED = True
    optimizer._INQSI_ODDS_MARKET_INTELLIGENCE_V2_INSTALLED = True
