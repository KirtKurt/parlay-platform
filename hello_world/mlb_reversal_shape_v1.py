from __future__ import annotations

from math import sqrt
from typing import Any, Dict, Iterable, List, Optional


VERSION = "MLB-REVERSAL-SHAPE-v1-amplitude-density-persistence"
HORIZONS = ("15m", "60m", "180m", "full")
_DIRECTION_HORIZONS = ("15m", "60m", "180m", "full")
_MIN_PULLS = {"15m": 2, "60m": 3, "180m": 5, "full": 8}
_EPS = 1e-9


def _f(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _sign(value: Any, epsilon: float = 1e-6) -> int:
    number = _f(value, 0.0) or 0.0
    return 1 if number > epsilon else -1 if number < -epsilon else 0


def _upper_tags(values: Iterable[Any]) -> set[str]:
    return {str(value).upper() for value in (values or []) if value not in (None, "")}


def _temporal_horizon(signal: Dict[str, Any], name: str) -> Dict[str, Any]:
    temporal = signal.get("temporalFeatures") or {}
    horizons = temporal.get("horizons") if isinstance(temporal, dict) else {}
    value = (horizons or {}).get(name) or {}
    return value if isinstance(value, dict) else {}


def _horizon_metrics(signal: Dict[str, Any], name: str) -> Dict[str, Any]:
    payload = _temporal_horizon(signal, name)
    pull_count = _i(payload.get("pullCount"), 0)
    duration_minutes = _f(payload.get("durationMinutes"), 0.0) or 0.0
    coverage_ratio = _f(payload.get("coverageRatio"), None)
    max_gap_minutes = _f(payload.get("maxGapMinutes"), None)
    velocity = _f(payload.get("velocityPpHr"), 0.0) or 0.0
    acceleration = _f(payload.get("accelerationPpHr2"), 0.0) or 0.0
    volatility = _f(payload.get("volatilityPpPerPull"), None)
    reversals = _i(payload.get("reversalCount"), 0)
    exact_net_move_pp = _f(payload.get("netMovePp"), None)
    gross_move_pp = _f(payload.get("grossMovePp"), None)
    path_efficiency = _f(payload.get("pathEfficiency"), None)
    mean_reversal_swing_pp = _f(payload.get("meanReversalSwingPp"), None)
    max_reversal_swing_pp = _f(payload.get("maxReversalSwingPp"), None)
    latest_leg_move_pp = _f(payload.get("latestLegMovePp"), None)

    duration_hours = duration_minutes / 60.0 if duration_minutes > 0 else 0.0
    net_move_pp = (
        exact_net_move_pp
        if exact_net_move_pp is not None
        else velocity * duration_hours
        if duration_hours > 0
        else None
    )
    interval_count = max(pull_count - 1, 1)
    noise_scale_pp = (
        abs(volatility) * sqrt(interval_count)
        if volatility is not None and pull_count >= 2
        else None
    )
    signal_to_noise = (
        abs(net_move_pp) / max(noise_scale_pp, 0.05)
        if net_move_pp is not None and noise_scale_pp is not None
        else None
    )
    reversal_density = reversals / duration_hours if duration_hours > 0 else None
    move_per_reversal_pp = (
        abs(net_move_pp) / max(reversals, 1) if net_move_pp is not None else None
    )

    coverage_known = bool(
        "coverageRatio" in payload
        or "maxGapMinutes" in payload
        or "pullCount" in payload
        or "durationMinutes" in payload
    )
    weak_coverage = bool(
        coverage_known
        and (
            (coverage_ratio is not None and coverage_ratio < 0.70)
            or (max_gap_minutes is not None and max_gap_minutes > 45.0)
            or (
                name in _MIN_PULLS
                and pull_count > 0
                and pull_count < _MIN_PULLS[name]
                and duration_minutes > 0
            )
        )
    )

    density_threshold = {"15m": 4.0, "60m": 1.5, "180m": 1.0, "full": 0.75}[name]
    high_reversal_density = bool(
        reversals >= 2
        and reversal_density is not None
        and reversal_density >= density_threshold
    )
    low_efficiency_churn = bool(
        reversals >= 2
        and (
            (path_efficiency is not None and path_efficiency < 0.35)
            or (signal_to_noise is not None and signal_to_noise < 0.85)
        )
    )
    large_reversal_leg = bool(
        reversals >= 2
        and (
            (max_reversal_swing_pp is not None and max_reversal_swing_pp >= 1.50)
            or (mean_reversal_swing_pp is not None and mean_reversal_swing_pp >= 0.75)
            or (
                max_reversal_swing_pp is None
                and mean_reversal_swing_pp is None
                and move_per_reversal_pp is not None
                and move_per_reversal_pp >= 0.75
            )
        )
    )
    decelerating = bool(
        _sign(velocity) != 0
        and _sign(acceleration) != 0
        and _sign(velocity) != _sign(acceleration)
    )

    return {
        "name": name,
        "available": bool(payload),
        "pullCount": pull_count,
        "durationMinutes": round(duration_minutes, 3),
        "coverageRatio": round(coverage_ratio, 6) if coverage_ratio is not None else None,
        "maxGapMinutes": round(max_gap_minutes, 3) if max_gap_minutes is not None else None,
        "velocityPpHr": round(velocity, 6),
        "accelerationPpHr2": round(acceleration, 6),
        "volatilityPpPerPull": round(volatility, 6) if volatility is not None else None,
        "reversalCount": reversals,
        "direction": _sign(velocity),
        "netMovePp": round(net_move_pp, 6) if net_move_pp is not None else None,
        "grossMovePp": round(gross_move_pp, 6) if gross_move_pp is not None else None,
        "pathEfficiency": round(path_efficiency, 6) if path_efficiency is not None else None,
        "meanReversalSwingPp": round(mean_reversal_swing_pp, 6) if mean_reversal_swing_pp is not None else None,
        "maxReversalSwingPp": round(max_reversal_swing_pp, 6) if max_reversal_swing_pp is not None else None,
        "latestLegMovePp": round(latest_leg_move_pp, 6) if latest_leg_move_pp is not None else None,
        "noiseScalePp": round(noise_scale_pp, 6) if noise_scale_pp is not None else None,
        "signalToNoise": round(signal_to_noise, 6) if signal_to_noise is not None else None,
        "reversalDensityPerHour": round(reversal_density, 6) if reversal_density is not None else None,
        "movePerReversalPp": round(move_per_reversal_pp, 6) if move_per_reversal_pp is not None else None,
        "coverageKnown": coverage_known,
        "weakCoverage": weak_coverage,
        "highReversalDensity": high_reversal_density,
        "lowEfficiencyChurn": low_efficiency_churn,
        "largeReversalLeg": large_reversal_leg,
        "decelerating": decelerating,
    }


def _probability(signal: Dict[str, Any]) -> float:
    for key in ("marketConsensusProbability", "fairProbability", "probLatest"):
        value = _f(signal.get(key), None)
        if value is None:
            continue
        value = value / 100.0 if value > 1.0 else value
        if 0.0 < value < 1.0:
            return value
    return 0.5


def _confirmation(signal: Dict[str, Any], tags: set[str]) -> Dict[str, bool]:
    book = bool(
        "BOOK_AGREEMENT" in tags
        or signal.get("bookAgreement") is True
        or signal.get("book_agreement") is True
    ) and "BOOK_DIVERGENCE" not in tags
    steam = bool(
        "STEAM" in tags
        or "STEAM_CONFIRMED" in tags
        or signal.get("steam") is True
    )
    run_line = bool(
        "RUN_LINE_CONFIRMATION" in tags
        or "RUN_LINE_CONFIRMED" in tags
        or signal.get("runLineConfirmation") is True
        or signal.get("run_line_confirmation") is True
    )
    return {
        "bookAgreement": book,
        "steam": steam,
        "runLineConfirmation": run_line,
        "independentConfirmation": bool(book and (steam or run_line)),
    }


def _direction_summary(horizons: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    directions = {
        name: int((horizons.get(name) or {}).get("direction") or 0)
        for name in _DIRECTION_HORIZONS
    }
    nonzero = [value for value in directions.values() if value]
    positives = sum(value > 0 for value in nonzero)
    negatives = sum(value < 0 for value in nonzero)
    agreement = max(positives, negatives) / len(nonzero) if nonzero else None
    if not nonzero:
        consensus = "UNKNOWN"
    elif positives == len(nonzero):
        consensus = "UP"
    elif negatives == len(nonzero):
        consensus = "DOWN"
    elif positives > negatives:
        consensus = "MOSTLY_UP"
    elif negatives > positives:
        consensus = "MOSTLY_DOWN"
    else:
        consensus = "MIXED"
    return {
        "directions": directions,
        "nonzeroHorizonCount": len(nonzero),
        "directionAgreement": round(agreement, 6) if agreement is not None else None,
        "consensus": consensus,
    }


def _size_bucket(move_pp: float) -> str:
    amount = abs(move_pp)
    if amount < 0.25:
        return "TINY"
    if amount < 0.75:
        return "SMALL"
    if amount < 1.50:
        return "MEDIUM"
    return "LARGE"


def _reversal_bucket(reversals: int) -> str:
    if reversals <= 1:
        return "LOW"
    if reversals <= 3:
        return "MEDIUM"
    return "HIGH"


def analyze(signal: Any, extra_tags: Iterable[Any] = ()) -> Dict[str, Any]:
    """Return auditable reversal-shape diagnostics from lock-bounded temporal summaries.

    The function deliberately avoids outcome labels and future observations. Missing
    temporal fields stay unknown instead of being silently treated as good evidence.
    """

    sig = signal if isinstance(signal, dict) else {}
    tags = _upper_tags([*(sig.get("tags") or []), *(extra_tags or [])])
    confirmation = _confirmation(sig, tags)
    horizons = {name: _horizon_metrics(sig, name) for name in HORIZONS}
    direction = _direction_summary(horizons)

    top_reversals = max(
        _i(sig.get("reversalCount"), 0),
        _i(sig.get("reversals"), 0),
    )
    delta = _f(sig.get("delta"), 0.0) or 0.0
    top_move_pp = delta * 100.0
    probability = _probability(sig)

    h15, h60, h180, hfull = (
        horizons["15m"],
        horizons["60m"],
        horizons["180m"],
        horizons["full"],
    )
    rev60 = int(h60["reversalCount"])
    rev180 = int(h180["reversalCount"])
    revfull = int(hfull["reversalCount"])
    max_reversals = max(top_reversals, rev60, rev180, revfull)

    late_direction_conflict = bool(
        h15["direction"]
        and (
            (h60["direction"] and h15["direction"] != h60["direction"])
            or (h180["direction"] and h15["direction"] != h180["direction"])
        )
    )
    medium_velocity = max(abs(h60["velocityPpHr"]), abs(h180["velocityPpHr"]), 0.25)
    late_velocity_ratio = abs(h15["velocityPpHr"]) / medium_velocity
    late_opposite_shock = bool(
        late_direction_conflict
        and abs(h15["velocityPpHr"]) >= 1.5
        and late_velocity_ratio >= 2.5
    )

    multi_horizon_reversal_instability = bool(
        rev60 >= 2 or rev180 >= 4 or revfull >= 8
    )
    high_reversal_density = any(
        horizons[name]["highReversalDensity"] for name in ("60m", "180m", "full")
    )
    low_efficiency_churn = any(
        horizons[name]["lowEfficiencyChurn"] for name in ("60m", "180m", "full")
    )
    large_reversal_leg = any(
        horizons[name]["largeReversalLeg"] for name in ("60m", "180m", "full")
    ) or bool(
        top_reversals >= 2 and abs(top_move_pp) / max(top_reversals, 1) >= 0.75
    )
    direction_disagreement = bool(
        direction["nonzeroHorizonCount"] >= 3
        and direction["directionAgreement"] is not None
        and direction["directionAgreement"] < 0.75
    )
    weak_coverage_horizons = [
        name for name in ("60m", "180m", "full") if horizons[name]["weakCoverage"]
    ]
    temporal_history_unreliable = len(weak_coverage_horizons) >= 2
    decelerating_horizons = [
        name for name in ("15m", "60m", "180m") if horizons[name]["decelerating"]
    ]

    medium_snr_values = [
        horizons[name]["signalToNoise"]
        for name in ("60m", "180m")
        if horizons[name]["signalToNoise"] is not None
    ]
    medium_snr = max(medium_snr_values) if medium_snr_values else None
    stable_confirmed_recovery = bool(
        confirmation["independentConfirmation"]
        and probability >= 0.58
        and not late_opposite_shock
        and not temporal_history_unreliable
        and not direction_disagreement
        and rev60 <= 1
        and rev180 <= 3
        and (medium_snr is None or medium_snr >= 1.0)
    )
    persistent_trend = bool(
        direction["nonzeroHorizonCount"] >= 2
        and direction["directionAgreement"] is not None
        and direction["directionAgreement"] >= 0.75
        and rev60 <= 1
        and rev180 <= 2
        and not low_efficiency_churn
        and not late_opposite_shock
    )

    hard_risks: List[str] = []
    if not confirmation["independentConfirmation"]:
        if late_opposite_shock:
            hard_risks.append("late_opposite_shock_without_confirmation")
        elif late_direction_conflict:
            hard_risks.append("late_direction_conflict_without_confirmation")
        if multi_horizon_reversal_instability:
            hard_risks.append("multi_horizon_reversal_instability")
        if high_reversal_density:
            hard_risks.append("high_reversal_density_without_confirmation")
        if low_efficiency_churn:
            hard_risks.append("low_efficiency_reversal_churn_without_confirmation")
        if large_reversal_leg:
            hard_risks.append("large_reversal_leg_without_confirmation")
        if direction_disagreement:
            hard_risks.append("multi_horizon_direction_disagreement_without_confirmation")
        if temporal_history_unreliable:
            hard_risks.append("temporal_history_unreliable_without_confirmation")

    cautions: List[str] = []
    if max_reversals >= 2:
        cautions.append("elevated_reversal_burden")
    if decelerating_horizons:
        cautions.append("trend_decelerating_after_reversal")
    if weak_coverage_horizons:
        cautions.append("partial_temporal_coverage")
    if top_move_pp > 0 and max_reversals >= 3 and not confirmation["independentConfirmation"]:
        cautions.append("positive_move_reversal_trap_profile")

    pattern_tags = []
    if persistent_trend:
        pattern_tags.append("REVERSAL_SHAPE_PERSISTENT_TREND")
    if stable_confirmed_recovery:
        pattern_tags.append("REVERSAL_SHAPE_CONFIRMED_RECOVERY")
    if low_efficiency_churn:
        pattern_tags.append("REVERSAL_SHAPE_CHOPPY_LOW_EFFICIENCY")
    if high_reversal_density:
        pattern_tags.append("REVERSAL_SHAPE_HIGH_DENSITY")
    if large_reversal_leg:
        pattern_tags.append("REVERSAL_SHAPE_LARGE_LEG")
    if late_opposite_shock:
        pattern_tags.append("REVERSAL_SHAPE_LATE_OPPOSITE_SHOCK")
    elif late_direction_conflict:
        pattern_tags.append("REVERSAL_SHAPE_LATE_CONFLICT")
    if direction_disagreement:
        pattern_tags.append("REVERSAL_SHAPE_MULTI_HORIZON_DISAGREEMENT")
    if temporal_history_unreliable:
        pattern_tags.append("REVERSAL_SHAPE_WEAK_TEMPORAL_EVIDENCE")

    movement_candidates = [abs(top_move_pp)] + [
        abs(value["netMovePp"])
        for value in horizons.values()
        if value["netMovePp"] is not None
    ]
    representative_move_pp = max(movement_candidates) if movement_candidates else 0.0
    if low_efficiency_churn:
        noise_bucket = "CHOPPY"
    elif medium_snr is None:
        noise_bucket = "UNKNOWN"
    elif medium_snr >= 1.25:
        noise_bucket = "CLEAN"
    else:
        noise_bucket = "MIXED"
    if late_opposite_shock:
        late_bucket = "SHOCK"
    elif late_direction_conflict:
        late_bucket = "CONFLICT"
    elif h15["direction"]:
        late_bucket = "ALIGNED"
    else:
        late_bucket = "UNKNOWN"
    if temporal_history_unreliable:
        coverage_bucket = "WEAK"
    elif any(value["coverageKnown"] for value in horizons.values()):
        coverage_bucket = "GOOD"
    else:
        coverage_bucket = "UNKNOWN"
    confirmation_bucket = (
        "INDEPENDENT"
        if confirmation["independentConfirmation"]
        else "BOOK_ONLY"
        if confirmation["bookAgreement"]
        else "NONE"
    )
    signature = "|".join(
        [
            f"DIR_{direction['consensus']}",
            f"REV_{_reversal_bucket(max_reversals)}",
            f"SIZE_{_size_bucket(representative_move_pp)}",
            f"NOISE_{noise_bucket}",
            f"CONF_{confirmation_bucket}",
            f"LATE_{late_bucket}",
            f"COV_{coverage_bucket}",
        ]
    )

    score_components: List[Dict[str, Any]] = []

    def add(name: str, value: float) -> None:
        score_components.append({"name": name, "value": round(float(value), 2)})

    if persistent_trend:
        add("reversal_shape_persistent_trend_boost", 2.0)
    if stable_confirmed_recovery:
        add("reversal_shape_confirmed_recovery_boost", 3.0)
    if late_opposite_shock:
        add("reversal_shape_late_opposite_shock_penalty", -6.0)
    if high_reversal_density:
        add("reversal_shape_high_density_penalty", -4.0)
    if low_efficiency_churn:
        add("reversal_shape_low_efficiency_penalty", -5.0)
    if large_reversal_leg and not confirmation["independentConfirmation"]:
        add("reversal_shape_large_unconfirmed_leg_penalty", -4.0)
    if direction_disagreement:
        add("reversal_shape_direction_disagreement_penalty", -4.0)
    if temporal_history_unreliable:
        add("reversal_shape_temporal_evidence_penalty", -3.0)
    if decelerating_horizons and max_reversals >= 1:
        add("reversal_shape_deceleration_penalty", -2.0)

    return {
        "applied": True,
        "version": VERSION,
        "available": any(value["available"] for value in horizons.values()),
        "marketProbability": round(probability, 6),
        "topLevelMovePp": round(top_move_pp, 6),
        "topLevelReversalCount": top_reversals,
        "maximumReversalCount": max_reversals,
        "representativeMovePp": round(representative_move_pp, 6),
        "horizons": horizons,
        "direction": direction,
        "lateDirectionConflict": late_direction_conflict,
        "lateVelocityRatio": round(late_velocity_ratio, 6),
        "lateOppositeShock": late_opposite_shock,
        "multiHorizonReversalInstability": multi_horizon_reversal_instability,
        "highReversalDensity": high_reversal_density,
        "lowEfficiencyChurn": low_efficiency_churn,
        "largeReversalLeg": large_reversal_leg,
        "multiHorizonDirectionDisagreement": direction_disagreement,
        "weakCoverageHorizons": weak_coverage_horizons,
        "deceleratingHorizons": decelerating_horizons,
        "persistentTrend": persistent_trend,
        "stableConfirmedRecovery": stable_confirmed_recovery,
        **confirmation,
        "hardRiskReasons": sorted(set(hard_risks)),
        "cautionReasons": sorted(set(cautions)),
        "patternTags": sorted(set(pattern_tags)),
        "similaritySignature": signature,
        "scoreComponents": score_components,
        "scoreAdjustment": round(sum(item["value"] for item in score_components), 2),
        "blocked": bool(hard_risks),
        "policy": (
            "Reversal quality is evaluated by amplitude, density, volatility-adjusted efficiency, "
            "coverage, and cross-horizon persistence. Independent confirmation means book agreement "
            "plus steam or run-line confirmation; unknown fields are never treated as positive evidence."
        ),
    }
