from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "MLB-TEMPORAL-FEATURES-v2-lock-bounded-leg-time-size"
HORIZONS: Tuple[Tuple[str, Optional[int]], ...] = (
    ("15m", 15),
    ("60m", 60),
    ("180m", 180),
    ("full", None),
)
PULL_INTERVAL_MINUTES = 15
REVERSAL_EPSILON = 0.0005
MARKET_FLIP_LOW_PP = 2.0
MARKET_FLIP_HIGH_PP = 3.0


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _probability(row: Dict[str, Any], side: str) -> Optional[float]:
    values = row.get("fair") or row.get("probs") or {}
    try:
        probability = float(values.get(side))
    except Exception:
        return None
    return probability if 0.0 < probability < 1.0 else None


def _observations(
    series: Iterable[Dict[str, Any]], side: str, cutoff_at: Any
) -> Tuple[List[Tuple[datetime, float]], int, int]:
    cutoff = parse_dt(cutoff_at)
    by_time: Dict[datetime, float] = {}
    input_count = 0
    excluded_after_cutoff = 0
    for row in series or []:
        if not isinstance(row, dict):
            continue
        input_count += 1
        at = parse_dt(row.get("pulled_at") or row.get("pulledAt") or row.get("asof"))
        probability = _probability(row, side)
        if at is None or probability is None:
            continue
        if cutoff is not None and at > cutoff:
            excluded_after_cutoff += 1
            continue
        by_time[at] = probability
    return sorted(by_time.items()), input_count, excluded_after_cutoff


def _window(
    observations: Sequence[Tuple[datetime, float]], as_of: datetime, minutes: Optional[int]
) -> List[Tuple[datetime, float]]:
    if minutes is None:
        return list(observations)
    threshold = as_of - timedelta(minutes=minutes)
    points = [point for point in observations if point[0] >= threshold]
    anchors = [point for point in observations if point[0] <= threshold]
    if anchors and (not points or anchors[-1][0] < points[0][0]):
        points.insert(0, anchors[-1])
    return points


def _intervals(points: Sequence[Tuple[datetime, float]]) -> List[Dict[str, Any]]:
    intervals: List[Dict[str, Any]] = []
    if len(points) < 2:
        return intervals
    origin = points[0][0]
    for previous, current in zip(points, points[1:]):
        hours = (current[0] - previous[0]).total_seconds() / 3600.0
        if hours <= 0:
            continue
        change_pp = (current[1] - previous[1]) * 100.0
        midpoint = previous[0] + (current[0] - previous[0]) / 2
        intervals.append(
            {
                "startAt": previous[0],
                "endAt": current[0],
                "startProbability": previous[1],
                "endProbability": current[1],
                "changePp": change_pp,
                "velocityPpHr": change_pp / hours,
                "midpointHours": (midpoint - origin).total_seconds() / 3600.0,
                "gapMinutes": hours * 60.0,
            }
        )
    return intervals


def _sign(change_pp: float) -> int:
    epsilon_pp = REVERSAL_EPSILON * 100.0
    return 1 if change_pp > epsilon_pp else -1 if change_pp < -epsilon_pp else 0


def _directional_legs(points: Sequence[Tuple[datetime, float]]) -> List[Dict[str, Any]]:
    """Collapse consecutive interval moves into time-aware directional legs."""
    legs: List[Dict[str, Any]] = []
    for interval in _intervals(points):
        sign = _sign(float(interval["changePp"]))
        if sign == 0:
            if legs:
                legs[-1]["endAt"] = interval["endAt"]
                legs[-1]["endProbability"] = interval["endProbability"]
                legs[-1]["durationMinutes"] = (
                    (legs[-1]["endAt"] - legs[-1]["startAt"]).total_seconds() / 60.0
                )
            continue
        if legs and int(legs[-1]["sign"]) == sign:
            leg = legs[-1]
            leg["endAt"] = interval["endAt"]
            leg["endProbability"] = interval["endProbability"]
            leg["signedMovePp"] += float(interval["changePp"])
            leg["grossMovePp"] += abs(float(interval["changePp"]))
            leg["intervalCount"] += 1
            leg["durationMinutes"] = (
                (leg["endAt"] - leg["startAt"]).total_seconds() / 60.0
            )
        else:
            legs.append(
                {
                    "sign": sign,
                    "direction": "up" if sign > 0 else "down",
                    "startAt": interval["startAt"],
                    "endAt": interval["endAt"],
                    "startProbability": interval["startProbability"],
                    "endProbability": interval["endProbability"],
                    "signedMovePp": float(interval["changePp"]),
                    "grossMovePp": abs(float(interval["changePp"])),
                    "intervalCount": 1,
                    "durationMinutes": float(interval["gapMinutes"]),
                }
            )
    for leg in legs:
        duration_hours = float(leg["durationMinutes"]) / 60.0
        leg["movePp"] = abs(float(leg["signedMovePp"]))
        leg["velocityPpHr"] = (
            float(leg["signedMovePp"]) / duration_hours if duration_hours > 0 else 0.0
        )
        start = float(leg["startProbability"])
        end = float(leg["endProbability"])
        leg["marketFlip"] = bool((start < 0.5 <= end) or (start > 0.5 >= end))
    return legs


def _slope(x_values: Sequence[float], y_values: Sequence[float]) -> float:
    if len(x_values) < 2 or len(x_values) != len(y_values):
        return 0.0
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator <= 0:
        return 0.0
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values)) / denominator


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    return sqrt(sum((value - average) ** 2 for value in values) / len(values))


def _reversals(values: Sequence[float]) -> int:
    signs = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        signs.append(1 if change > REVERSAL_EPSILON else -1 if change < -REVERSAL_EPSILON else 0)
    nonzero = [sign for sign in signs if sign]
    return sum(previous != current for previous, current in zip(nonzero, nonzero[1:]))


def _round(value: float) -> float:
    return round(float(value), 6)


def _path_metrics(points: Sequence[Tuple[datetime, float]], as_of: Optional[datetime]) -> Dict[str, Any]:
    intervals = _intervals(points)
    legs = _directional_legs(points)
    changes = [float(item["changePp"]) for item in intervals]
    gross_move = sum(abs(value) for value in changes)
    net_move = (points[-1][1] - points[0][1]) * 100.0 if len(points) >= 2 else 0.0
    latest = legs[-1] if legs else None
    prior = legs[-2] if len(legs) >= 2 else None
    reversal_legs = legs[1:] if len(legs) >= 2 else []
    reversal_spacings = [
        (current["startAt"] - previous["startAt"]).total_seconds() / 60.0
        for previous, current in zip(legs, legs[1:])
    ]
    market_flips = [leg for leg in legs if leg.get("marketFlip")]
    toward_flips = [leg for leg in market_flips if int(leg.get("sign") or 0) > 0]
    against_flips = [leg for leg in market_flips if int(leg.get("sign") or 0) < 0]
    largest_flip = max(market_flips, key=lambda leg: float(leg["movePp"]), default=None)
    largest_toward = max(toward_flips, key=lambda leg: float(leg["movePp"]), default=None)
    largest_against = max(against_flips, key=lambda leg: float(leg["movePp"]), default=None)
    latest_move = float(latest["movePp"]) if latest else 0.0
    prior_move = float(prior["movePp"]) if prior else 0.0
    recovery_ratio = latest_move / prior_move if prior_move > 0 else 0.0
    minutes_since_reversal = (
        (as_of - latest["startAt"]).total_seconds() / 60.0 if as_of and latest and len(legs) >= 2 else 0.0
    )
    largest_flip_age = (
        (as_of - largest_flip["endAt"]).total_seconds() / 60.0
        if as_of and largest_flip
        else 0.0
    )
    largest_toward_pp = float(largest_toward["movePp"]) if largest_toward else 0.0
    return {
        "netMovePp": _round(net_move),
        "grossMovePp": _round(gross_move),
        "pathEfficiency": _round(abs(net_move) / gross_move) if gross_move > 0 else 0.0,
        "meanAbsIntervalMovePp": _round(sum(abs(value) for value in changes) / len(changes)) if changes else 0.0,
        "maxIntervalMovePp": _round(max((abs(value) for value in changes), default=0.0)),
        "directionalLegCount": len(legs),
        "meanDirectionalLegMovePp": _round(sum(float(leg["movePp"]) for leg in legs) / len(legs)) if legs else 0.0,
        "maxDirectionalLegMovePp": _round(max((float(leg["movePp"]) for leg in legs), default=0.0)),
        "meanReversalSwingPp": _round(sum(float(leg["movePp"]) for leg in reversal_legs) / len(reversal_legs)) if reversal_legs else 0.0,
        "maxReversalSwingPp": _round(max((float(leg["movePp"]) for leg in reversal_legs), default=0.0)),
        "latestLegMovePp": _round(latest_move),
        "latestLegSignedMovePp": _round(float(latest["signedMovePp"])) if latest else 0.0,
        "latestLegDirection": str(latest["direction"]) if latest else "flat",
        "latestLegDurationMinutes": _round(float(latest["durationMinutes"])) if latest else 0.0,
        "latestLegVelocityPpHr": _round(float(latest["velocityPpHr"])) if latest else 0.0,
        "priorLegMovePp": _round(prior_move),
        "priorLegDurationMinutes": _round(float(prior["durationMinutes"])) if prior else 0.0,
        "reversalRecoveryRatio": _round(recovery_ratio),
        "minutesSinceLastReversal": _round(max(0.0, minutes_since_reversal)),
        "meanReversalSpacingMinutes": _round(sum(reversal_spacings) / len(reversal_spacings)) if reversal_spacings else 0.0,
        "marketFlipCount": len(market_flips),
        "largestMarketFlipPp": _round(float(largest_flip["movePp"])) if largest_flip else 0.0,
        "largestMarketFlipDirection": str(largest_flip["direction"]) if largest_flip else "none",
        "largestMarketFlipAgeMinutes": _round(max(0.0, largest_flip_age)),
        "largestMarketFlipTowardSidePp": _round(largest_toward_pp),
        "largestMarketFlipAgainstSidePp": _round(float(largest_against["movePp"])) if largest_against else 0.0,
        "marketFlip2To3PpCandidate": bool(MARKET_FLIP_LOW_PP <= largest_toward_pp < MARKET_FLIP_HIGH_PP),
    }


def _summarize(points: Sequence[Tuple[datetime, float]]) -> Dict[str, Any]:
    intervals = _intervals(points)
    duration_minutes = (
        (points[-1][0] - points[0][0]).total_seconds() / 60.0 if len(points) >= 2 else 0.0
    )
    expected = int(round(duration_minutes / PULL_INTERVAL_MINUTES)) + 1 if points else 0
    expected = max(expected, 1) if points else 0
    changes = [float(item["changePp"]) for item in intervals]
    velocities = [float(item["velocityPpHr"]) for item in intervals]
    midpoints = [float(item["midpointHours"]) for item in intervals]
    gaps = [float(item["gapMinutes"]) for item in intervals]
    summary = {
        "pullCount": len(points),
        "durationMinutes": round(duration_minutes, 3),
        "coverageRatio": round(min(1.0, len(points) / expected), 6) if expected else 0.0,
        "maxGapMinutes": round(max(gaps), 3) if gaps else 0.0,
        "velocityPpHr": round(
            ((points[-1][1] - points[0][1]) * 100.0) / (duration_minutes / 60.0), 6
        )
        if duration_minutes > 0
        else 0.0,
        "accelerationPpHr2": round(_slope(midpoints, velocities), 6),
        "volatilityPpPerPull": round(_std(changes), 6),
        "reversalCount": _reversals([point[1] for point in points]),
    }
    summary.update(_path_metrics(points, points[-1][0] if points else None))
    return summary


def summarize_side(series: Iterable[Dict[str, Any]], side: str, cutoff_at: Any) -> Dict[str, Any]:
    """Summarize one side from timestamped pulls, excluding every point after cutoff."""
    if side not in {"home", "away"}:
        raise ValueError("side must be home or away")
    observations, input_count, excluded = _observations(series, side, cutoff_at)
    as_of = observations[-1][0] if observations else None
    cutoff = parse_dt(cutoff_at)
    return {
        "version": VERSION,
        "side": side,
        "available": bool(observations),
        "asOfUtc": as_of.isoformat() if as_of else None,
        "cutoffAtUtc": cutoff.isoformat() if cutoff else None,
        "sourceWindowStartUtc": observations[0][0].isoformat() if observations else None,
        "sourcePointCount": len(observations),
        "inputPointCount": input_count,
        "excludedAfterCutoffCount": excluded,
        "horizons": {
            name: _summarize(_window(observations, as_of, minutes)) if as_of else _summarize([])
            for name, minutes in HORIZONS
        },
        "policy": "Only timestamped observations at or before cutoffAtUtc are included.",
    }


def flatten(summary: Any, prefix: str) -> Dict[str, float]:
    summary = summary if isinstance(summary, dict) else {}
    horizons = summary.get("horizons") or {}
    out = {
        f"{prefix}TemporalAvailable": 1.0 if summary.get("available") is True else 0.0,
        f"{prefix}TemporalSourcePullCount": float(summary.get("sourcePointCount") or 0.0),
    }
    fields = (
        ("pullCount", "PullCount"),
        ("durationMinutes", "DurationMinutes"),
        ("coverageRatio", "CoverageRatio"),
        ("maxGapMinutes", "MaxGapMinutes"),
        ("velocityPpHr", "VelocityPpHr"),
        ("volatilityPpPerPull", "VolatilityPpPerPull"),
        ("reversalCount", "ReversalCount"),
        ("netMovePp", "NetMovePp"),
        ("grossMovePp", "GrossMovePp"),
        ("pathEfficiency", "PathEfficiency"),
        ("meanAbsIntervalMovePp", "MeanAbsIntervalMovePp"),
        ("maxIntervalMovePp", "MaxIntervalMovePp"),
        ("directionalLegCount", "DirectionalLegCount"),
        ("meanDirectionalLegMovePp", "MeanDirectionalLegMovePp"),
        ("maxDirectionalLegMovePp", "MaxDirectionalLegMovePp"),
        ("meanReversalSwingPp", "MeanReversalSwingPp"),
        ("maxReversalSwingPp", "MaxReversalSwingPp"),
        ("latestLegMovePp", "LatestLegMovePp"),
        ("latestLegSignedMovePp", "LatestLegSignedMovePp"),
        ("latestLegDurationMinutes", "LatestLegDurationMinutes"),
        ("latestLegVelocityPpHr", "LatestLegVelocityPpHr"),
        ("priorLegMovePp", "PriorLegMovePp"),
        ("priorLegDurationMinutes", "PriorLegDurationMinutes"),
        ("reversalRecoveryRatio", "ReversalRecoveryRatio"),
        ("minutesSinceLastReversal", "MinutesSinceLastReversal"),
        ("meanReversalSpacingMinutes", "MeanReversalSpacingMinutes"),
        ("marketFlipCount", "MarketFlipCount"),
        ("largestMarketFlipPp", "LargestMarketFlipPp"),
        ("largestMarketFlipAgeMinutes", "LargestMarketFlipAgeMinutes"),
        ("largestMarketFlipTowardSidePp", "LargestMarketFlipTowardSidePp"),
        ("largestMarketFlipAgainstSidePp", "LargestMarketFlipAgainstSidePp"),
        ("marketFlip2To3PpCandidate", "MarketFlip2To3PpCandidate"),
    )
    for label, _ in HORIZONS:
        values = horizons.get(label) or {}
        suffix = label[0].upper() + label[1:]
        for source, target in fields:
            value = values.get(source)
            try:
                out[f"{prefix}{target}{suffix}"] = 1.0 if value is True else float(value or 0.0)
            except Exception:
                out[f"{prefix}{target}{suffix}"] = 0.0
        try:
            out[f"{prefix}Acceleration{suffix}PpHr2"] = float(values.get("accelerationPpHr2") or 0.0)
        except Exception:
            out[f"{prefix}Acceleration{suffix}PpHr2"] = 0.0
    return out


def provenance_is_lock_safe(summary: Any, source_at: Any, lock_at: Any) -> bool:
    if not isinstance(summary, dict) or summary.get("version") != VERSION or summary.get("available") is not True:
        return False
    as_of = parse_dt(summary.get("asOfUtc"))
    source = parse_dt(source_at)
    lock = parse_dt(lock_at)
    return bool(as_of and source and lock and as_of <= source <= lock)
