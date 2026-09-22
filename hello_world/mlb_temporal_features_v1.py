from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "MLB-TEMPORAL-FEATURES-v1-lock-bounded-multi-horizon"
REVERSAL_PATH_METRICS_VERSION = "MLB-REVERSAL-PATH-METRICS-v1-leg-amplitude-efficiency"
HORIZONS: Tuple[Tuple[str, Optional[int]], ...] = (
    ("15m", 15),
    ("60m", 60),
    ("180m", 180),
    ("full", None),
)
PULL_INTERVAL_MINUTES = 15
REVERSAL_EPSILON = 0.0005
REVERSAL_EPSILON_PP = REVERSAL_EPSILON * 100.0


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


def _intervals(points: Sequence[Tuple[datetime, float]]) -> List[Dict[str, float]]:
    intervals: List[Dict[str, float]] = []
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
                "changePp": change_pp,
                "velocityPpHr": change_pp / hours,
                "midpointHours": (midpoint - origin).total_seconds() / 3600.0,
                "gapMinutes": hours * 60.0,
            }
        )
    return intervals


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


def _sign(value: float, epsilon: float = REVERSAL_EPSILON_PP) -> int:
    return 1 if value > epsilon else -1 if value < -epsilon else 0


def _directional_legs(changes_pp: Sequence[float]) -> List[float]:
    """Aggregate adjacent interval changes into signed directional legs.

    Tiny changes within the same epsilon used by reversalCount are ignored. That
    keeps leg amplitude and reversal count on the same mathematical definition.
    """

    legs: List[float] = []
    for change in changes_pp:
        direction = _sign(change)
        if not direction:
            continue
        if legs and _sign(legs[-1]) == direction:
            legs[-1] += change
        else:
            legs.append(change)
    return legs


def _path_metrics(changes_pp: Sequence[float]) -> Dict[str, Any]:
    gross = sum(abs(value) for value in changes_pp)
    net = sum(changes_pp)
    legs = _directional_legs(changes_pp)
    leg_sizes = [abs(value) for value in legs]
    reversal_swings = [
        abs(previous) + abs(current)
        for previous, current in zip(legs, legs[1:])
    ]
    latest_leg = legs[-1] if legs else 0.0
    return {
        "reversalPathMetricsVersion": REVERSAL_PATH_METRICS_VERSION,
        "netMovePp": round(net, 6),
        "grossMovePp": round(gross, 6),
        "pathEfficiency": round(abs(net) / gross, 6) if gross > 0 else 0.0,
        "meanAbsIntervalMovePp": round(sum(abs(value) for value in changes_pp) / len(changes_pp), 6)
        if changes_pp
        else 0.0,
        "maxIntervalMovePp": round(max((abs(value) for value in changes_pp), default=0.0), 6),
        "directionalLegCount": len(legs),
        "meanDirectionalLegMovePp": round(sum(leg_sizes) / len(leg_sizes), 6) if leg_sizes else 0.0,
        "maxDirectionalLegMovePp": round(max(leg_sizes), 6) if leg_sizes else 0.0,
        "meanReversalSwingPp": round(sum(reversal_swings) / len(reversal_swings), 6)
        if reversal_swings
        else 0.0,
        "maxReversalSwingPp": round(max(reversal_swings), 6) if reversal_swings else 0.0,
        "latestLegMovePp": round(latest_leg, 6),
        "latestLegDirection": _sign(latest_leg),
        "dominantLegShare": round(max(leg_sizes) / gross, 6) if gross > 0 and leg_sizes else 0.0,
        "latestLegShare": round(abs(latest_leg) / gross, 6) if gross > 0 else 0.0,
    }


def _reversals(values: Sequence[float]) -> int:
    signs = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        signs.append(1 if change > REVERSAL_EPSILON else -1 if change < -REVERSAL_EPSILON else 0)
    nonzero = [sign for sign in signs if sign]
    return sum(previous != current for previous, current in zip(nonzero, nonzero[1:]))


def _summarize(points: Sequence[Tuple[datetime, float]]) -> Dict[str, Any]:
    intervals = _intervals(points)
    duration_minutes = (
        (points[-1][0] - points[0][0]).total_seconds() / 60.0 if len(points) >= 2 else 0.0
    )
    expected = int(round(duration_minutes / PULL_INTERVAL_MINUTES)) + 1 if points else 0
    expected = max(expected, 1) if points else 0
    changes = [item["changePp"] for item in intervals]
    velocities = [item["velocityPpHr"] for item in intervals]
    midpoints = [item["midpointHours"] for item in intervals]
    gaps = [item["gapMinutes"] for item in intervals]
    return {
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
        **_path_metrics(changes),
    }


def summarize_side(series: Iterable[Dict[str, Any]], side: str, cutoff_at: Any) -> Dict[str, Any]:
    """Summarize one side from timestamped pulls, excluding every point after cutoff."""
    if side not in {"home", "away"}:
        raise ValueError("side must be home or away")
    observations, input_count, excluded = _observations(series, side, cutoff_at)
    as_of = observations[-1][0] if observations else None
    return {
        "version": VERSION,
        "reversalPathMetricsVersion": REVERSAL_PATH_METRICS_VERSION,
        "side": side,
        "available": bool(observations),
        "asOfUtc": as_of.isoformat() if as_of else None,
        "cutoffAtUtc": parse_dt(cutoff_at).isoformat() if parse_dt(cutoff_at) else None,
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
        ("latestLegDirection", "LatestLegDirection"),
        ("dominantLegShare", "DominantLegShare"),
        ("latestLegShare", "LatestLegShare"),
    )
    for label, _ in HORIZONS:
        values = horizons.get(label) or {}
        suffix = label[0].upper() + label[1:]
        for source, target in fields:
            try:
                out[f"{prefix}{target}{suffix}"] = float(values.get(source) or 0.0)
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
