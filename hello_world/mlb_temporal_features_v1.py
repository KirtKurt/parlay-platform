from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "MLB-TEMPORAL-FEATURES-v2-reversal-path-market-coherence"
HORIZONS: Tuple[Tuple[str, Optional[int]], ...] = (
    ("15m", 15), ("60m", 60), ("120m", 120), ("180m", 180),
    ("240m", 240), ("480m", 480), ("full", None),
)
PULL_INTERVAL_MINUTES = 15
REVERSAL_EPSILON = 0.0005


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (out if out.tzinfo else out.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        return None


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return default if value in (None, "") else float(value)
    except Exception:
        return default


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _sign(change: float) -> int:
    return 1 if change > REVERSAL_EPSILON else -1 if change < -REVERSAL_EPSILON else 0


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    return sqrt(sum((value - avg) ** 2 for value in values) / len(values))


def _slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 2 or len(xs) != len(ys):
        return 0.0
    xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((value - xm) ** 2 for value in xs)
    return (
        sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / denominator
        if denominator > 0 else 0.0
    )


def _robust_z(value: float, values: Sequence[float]) -> float:
    sample = [float(item) for item in values]
    if len(sample) < 3:
        return 0.0
    center = median(sample)
    scale = 1.4826 * median(abs(item - center) for item in sample)
    if scale <= 1e-9:
        scale = _std(sample)
    return (value - center) / scale if scale > 1e-9 else 0.0


def _event_start(row: Dict[str, Any]) -> Optional[datetime]:
    game = row.get("game") if isinstance(row.get("game"), dict) else {}
    return parse_dt(
        game.get("commence_time") or game.get("commenceTime")
        or row.get("commence_time") or row.get("commenceTime")
    )


def _observations(series: Iterable[Dict[str, Any]], side: str, cutoff_at: Any):
    cutoff = parse_dt(cutoff_at)
    by_time: Dict[datetime, Dict[str, Any]] = {}
    input_count = excluded = 0
    event_start = None
    for row in series or []:
        if not isinstance(row, dict):
            continue
        input_count += 1
        at = parse_dt(row.get("pulled_at") or row.get("pulledAt") or row.get("asof"))
        fair = row.get("fair") or row.get("probs") or {}
        probability = _f(fair.get(side))
        event_start = event_start or _event_start(row)
        if not at or probability is None or not 0.0 < probability < 1.0:
            continue
        if cutoff and at > cutoff:
            excluded += 1
            continue
        books = {}
        for book, payload in (fair.get("book_probs") or fair.get("bookProbabilities") or {}).items():
            if isinstance(payload, dict):
                value = _f(payload.get(side))
                if value is not None and 0.0 < value < 1.0:
                    books[str(book).lower()] = value
        by_time[at] = {"at": at, "probability": probability, "books": books}
    return [by_time[key] for key in sorted(by_time)], input_count, excluded, event_start


def _window(points: Sequence[Dict[str, Any]], as_of: datetime, minutes: Optional[int]):
    if minutes is None:
        return list(points)
    threshold = as_of - timedelta(minutes=minutes)
    out = [point for point in points if point["at"] >= threshold]
    anchors = [point for point in points if point["at"] <= threshold]
    if anchors and (not out or anchors[-1]["at"] < out[0]["at"]):
        out.insert(0, anchors[-1])
    return out


def _intervals(points: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    if len(points) < 2:
        return out
    origin = points[0]["at"]
    for index, (previous, current) in enumerate(zip(points, points[1:]), 1):
        hours = (current["at"] - previous["at"]).total_seconds() / 3600.0
        if hours <= 0:
            continue
        change = current["probability"] - previous["probability"]
        midpoint = previous["at"] + (current["at"] - previous["at"]) / 2
        out.append({
            "index": index,
            "startAt": previous["at"],
            "endAt": current["at"],
            "changePp": change * 100.0,
            "absChangePp": abs(change) * 100.0,
            "direction": _sign(change),
            "velocityPpHr": change * 100.0 / hours,
            "midpointHours": (midpoint - origin).total_seconds() / 3600.0,
            "gapMinutes": hours * 60.0,
        })
    return out


def _leg(points: Sequence[Dict[str, Any]], start: int, end: int, direction: int):
    subset = points[start:end + 1]
    start_probability, end_probability = points[start]["probability"], points[end]["probability"]
    signed = (end_probability - start_probability) * 100.0
    gross = sum(
        abs(current["probability"] - previous["probability"]) * 100.0
        for previous, current in zip(subset, subset[1:])
    )
    duration = max(0.0, (points[end]["at"] - points[start]["at"]).total_seconds() / 60.0)
    amplitude = abs(signed)
    return {
        "startIndex": start, "endIndex": end,
        "startAtUtc": points[start]["at"].isoformat(), "endAtUtc": points[end]["at"].isoformat(),
        "startProbability": round(start_probability, 8), "endProbability": round(end_probability, 8),
        "direction": direction, "directionLabel": "toward_side" if direction > 0 else "away_from_side",
        "amplitudePp": round(amplitude, 6), "signedMovementPp": round(signed, 6),
        "grossMovementPp": round(gross, 6),
        "pathEfficiency": round(amplitude / gross, 6) if gross else 0.0,
        "durationMinutes": round(duration, 3),
        "velocityPpHr": round(amplitude / (duration / 60.0), 6) if duration else 0.0,
        "pullSpan": max(0, end - start),
        "marketFlip": min(start_probability, end_probability) < 0.5 < max(start_probability, end_probability),
    }


def _legs(points: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    moves = [
        (index, _sign(points[index]["probability"] - points[index - 1]["probability"]))
        for index in range(1, len(points))
    ]
    moves = [(index, direction) for index, direction in moves if direction]
    if not moves:
        return []
    out, start, direction = [], moves[0][0] - 1, moves[0][1]
    for position, current_direction in moves[1:]:
        if current_direction != direction:
            end = position - 1
            out.append(_leg(points, start, end, direction))
            start, direction = end, current_direction
    out.append(_leg(points, start, moves[-1][0], direction))
    return out


def _book_value(points, book: str, at: datetime):
    value = None
    for point in points:
        if point["at"] > at:
            break
        if book in point["books"]:
            value = point["books"][book]
    return value


def _book_market(points: Sequence[Dict[str, Any]], legs: Sequence[Dict[str, Any]]):
    if not points:
        return {"available": False, "latestBookCount": 0, "bookDirectionAgreement": 0.0,
                "weightedBookDirectionAgreement": 0.0, "synchronizedLatestLegBreadth": 0.0}
    books = sorted({book for point in points for book in point["books"]})
    opening, latest = list(points[0]["books"].values()), list(points[-1]["books"].values())
    opening_range = (max(opening) - min(opening)) * 100.0 if len(opening) > 1 else 0.0
    latest_range = (max(latest) - min(latest)) * 100.0 if len(latest) > 1 else 0.0
    direction = _sign(points[-1]["probability"] - points[0]["probability"])
    eligible, weighted_hits, total_weight, lags = [], 0.0, 0.0, []
    consensus_move_at = next((
        point["at"] for point in points[1:]
        if direction and _sign(point["probability"] - points[0]["probability"]) == direction
    ), None)
    coverages = []
    for book in books:
        values = [(point["at"], point["books"][book]) for point in points if book in point["books"]]
        coverages.append(len(values) / len(points))
        if len(values) < 2:
            continue
        change = values[-1][1] - values[0][1]
        changes = [(current[1] - previous[1]) * 100.0 for previous, current in zip(values, values[1:])]
        weight = (len(values) / len(points)) / (1.0 + _std(changes))
        eligible.append((change, weight))
        total_weight += weight
        if direction and _sign(change) == direction:
            weighted_hits += weight
        if consensus_move_at and direction:
            moved_at = next((at for at, value in values[1:] if _sign(value - values[0][1]) == direction), None)
            if moved_at:
                lags.append((moved_at - consensus_move_at).total_seconds() / 60.0)
    matches = sum(1 for change, _ in eligible if direction and _sign(change) == direction)
    agreement = matches / len(eligible) if direction and eligible else 0.0
    weighted = weighted_hits / total_weight if direction and total_weight else 0.0
    synchronized = movers = 0
    if legs:
        start, end = parse_dt(legs[-1]["startAtUtc"]), parse_dt(legs[-1]["endAtUtc"])
        if start and end:
            for book in books:
                before, after = _book_value(points, book, start), _book_value(points, book, end)
                if before is None or after is None or not _sign(after - before):
                    continue
                movers += 1
                synchronized += int(_sign(after - before) == legs[-1]["direction"])
    return {
        "available": bool(books), "eligibleBookCount": len(eligible),
        "openingBookCount": len(opening), "latestBookCount": len(latest),
        "meanBookCoverageRatio": round(sum(coverages) / len(coverages), 6) if coverages else 0.0,
        "bookDirectionAgreement": round(agreement, 6),
        "weightedBookDirectionAgreement": round(weighted, 6),
        "synchronizedLatestLegBookCount": movers,
        "synchronizedLatestLegBreadth": round(synchronized / movers, 6) if movers else 0.0,
        "openingBookRangePp": round(opening_range, 6), "latestBookRangePp": round(latest_range, 6),
        "openingBookStdPp": round(_std(opening) * 100.0, 6),
        "latestBookStdPp": round(_std(latest) * 100.0, 6),
        "marketCompressionPp": round(opening_range - latest_range, 6),
        "marketExpansionPp": round(latest_range - opening_range, 6),
        "averageFirstMoveLagMinutes": round(sum(lags) / len(lags), 3) if lags else None,
        "medianFirstMoveLagMinutes": round(median(lags), 3) if lags else None,
        "bookWeightsAreCoverageAndNoiseBased": True, "sharpBookPriorUsed": False,
    }


def _quality(summary: Dict[str, Any], market: Dict[str, Any]):
    coverage = _clip(_f(summary.get("coverageRatio"), 0.0) or 0.0)
    path = _clip(_f(summary.get("pathEfficiency"), 0.0) or 0.0)
    persistence = _clip(_f(summary.get("directionPersistence"), 0.0) or 0.0)
    stability = _clip((_f(summary.get("stabilityScore"), 0.0) or 0.0) / 100.0)
    agreement = _clip(_f(market.get("weightedBookDirectionAgreement"), 0.0) or 0.0)
    book_coverage = _clip(_f(market.get("meanBookCoverageRatio"), 0.0) or 0.0)
    depth = _clip((_f(market.get("latestBookCount"), 0.0) or 0.0) / 6.0)
    convergence = _clip(0.5 + (_f(market.get("marketCompressionPp"), 0.0) or 0.0) / 4.0)
    dispersion = _clip((_f(market.get("latestBookRangePp"), 0.0) or 0.0) / 5.0)
    reversal_penalty = _clip((_f(summary.get("reversalCount"), 0.0) or 0.0) / 4.0)
    score = 100.0 * (
        .16 * coverage + .16 * path + .14 * persistence + .12 * stability + .20 * agreement
        + .08 * book_coverage + .06 * depth + .08 * convergence - .06 * dispersion - .08 * reversal_penalty
    )
    score = max(0.0, min(100.0, score))
    return {
        "signalQualityIndex": round(score, 3),
        "band": "high" if score >= 75 else "medium" if score >= 55 else "low",
        "researchOnly": True, "notWinProbability": True, "positiveScoreAuthority": False,
    }


def _summarize(points: Sequence[Dict[str, Any]], event_start: Optional[datetime]):
    intervals, legs = _intervals(points), _legs(points)
    duration = (points[-1]["at"] - points[0]["at"]).total_seconds() / 60.0 if len(points) > 1 else 0.0
    expected = max(1, int(round(duration / PULL_INTERVAL_MINUTES)) + 1) if points else 0
    changes = [item["changePp"] for item in intervals]
    absolute = [item["absChangePp"] for item in intervals]
    velocities = [item["velocityPpHr"] for item in intervals]
    directions = [item["direction"] for item in intervals if item["direction"]]
    net = (points[-1]["probability"] - points[0]["probability"]) * 100.0 if len(points) > 1 else 0.0
    gross = sum(absolute)
    positive = sum(item["absChangePp"] for item in intervals if item["direction"] > 0)
    negative = sum(item["absChangePp"] for item in intervals if item["direction"] < 0)
    directional = positive + negative
    dominant = max(positive, negative) / directional if directional else 0.0
    terminal_direction = directions[-1] if directions else 0
    terminal = 0.0
    for item in reversed(intervals):
        if not item["direction"]:
            continue
        if item["direction"] != terminal_direction:
            break
        terminal += item["absChangePp"]
    terminal_share = terminal / directional if directional else 0.0
    persistence = .65 * dominant + .35 * terminal_share
    reversals = max(0, len(legs) - 1)
    density = reversals / max(1, len(directions) - 1) if directions else 0.0
    efficiency = abs(net) / gross if gross else 0.0
    stability = 100.0 * (.45 * _clip(efficiency) + .35 * _clip(persistence) + .20 * (1.0 - _clip(density)))
    latest, previous = (dict(legs[-1]) if legs else None), (dict(legs[-2]) if len(legs) > 1 else None)
    reversal_age = before_event = swing = recovery = decay = velocity_change = None
    if latest and previous:
        reversal_at = parse_dt(latest["startAtUtc"])
        if reversal_at:
            reversal_age = (points[-1]["at"] - reversal_at).total_seconds() / 60.0
            before_event = (event_start - reversal_at).total_seconds() / 60.0 if event_start else None
        swing = latest["amplitudePp"] + previous["amplitudePp"]
        recovery = latest["amplitudePp"] / previous["amplitudePp"] if previous["amplitudePp"] else None
        previous_velocity, latest_velocity = abs(previous["velocityPpHr"]), abs(latest["velocityPpHr"])
        decay = latest_velocity / previous_velocity if previous_velocity else None
        velocity_change = latest_velocity - previous_velocity
    all_flips = [leg for leg in legs if leg.get("marketFlip")]
    reversal_flips = [leg for leg in legs[1:] if leg.get("marketFlip")]
    recent60 = [item["absChangePp"] for item in intervals if points and item["endAt"] >= points[-1]["at"] - timedelta(minutes=60)]
    late_shock = max(recent60) if recent60 else 0.0
    market = _book_market(points, legs)
    summary = {
        "pullCount": len(points), "durationMinutes": round(duration, 3),
        "coverageRatio": round(min(1.0, len(points) / expected), 6) if expected else 0.0,
        "maxGapMinutes": round(max((item["gapMinutes"] for item in intervals), default=0.0), 3),
        "velocityPpHr": round(net / (duration / 60.0), 6) if duration else 0.0,
        "accelerationPpHr2": round(_slope([item["midpointHours"] for item in intervals], velocities), 6),
        "volatilityPpPerPull": round(_std(changes), 6), "reversalCount": reversals,
        "netMovementPp": round(net, 6), "grossMovementPp": round(gross, 6),
        "pathEfficiency": round(efficiency, 6), "directionPersistence": round(persistence, 6),
        "dominantDirectionShare": round(dominant, 6), "terminalRunShare": round(terminal_share, 6),
        "stabilityScore": round(stability, 3), "directionalLegCount": len(legs),
        "directionChangeDensity": round(density, 6),
        "maxIntervalMovePp": round(max(absolute, default=0.0), 6),
        "medianIntervalMovePp": round(median(absolute), 6) if absolute else 0.0,
        "latestIntervalMovePp": round(absolute[-1], 6) if absolute else 0.0,
        "latestIntervalVelocityPpHr": round(abs(velocities[-1]), 6) if velocities else 0.0,
        "latestIntervalMoveRobustZ": round(_robust_z(absolute[-1], absolute[:-1] or absolute), 6) if absolute else 0.0,
        "lateShock60mPp": round(late_shock, 6),
        "lateShock60mRobustZ": round(_robust_z(late_shock, absolute), 6) if absolute else 0.0,
        "latestLeg": latest, "previousLeg": previous,
        "latestReversalSwingPp": round(swing, 6) if swing is not None else None,
        "latestReversalRecoveryRatio": round(recovery, 6) if recovery is not None else None,
        "latestReversalAgeMinutes": round(reversal_age, 3) if reversal_age is not None else None,
        "latestReversalMinutesBeforeEvent": round(before_event, 3) if before_event is not None else None,
        "velocityDecayRatio": round(decay, 6) if decay is not None else None,
        "velocityChangePpHr": round(velocity_change, 6) if velocity_change is not None else None,
        "marketFlipCount": len(all_flips), "reversalMarketFlipCount": len(reversal_flips),
        "latestMarketFlip": dict(all_flips[-1]) if all_flips else None,
        "latestReversalMarketFlip": dict(reversal_flips[-1]) if reversal_flips else None,
        "directionalLegs": legs, "market": market,
    }
    summary["signalQuality"] = _quality(summary, market)
    return summary


def summarize_side(series: Iterable[Dict[str, Any]], side: str, cutoff_at: Any) -> Dict[str, Any]:
    if side not in {"home", "away"}:
        raise ValueError("side must be home or away")
    points, input_count, excluded, event_start = _observations(series, side, cutoff_at)
    as_of = points[-1]["at"] if points else None
    horizons = {
        label: _summarize(_window(points, as_of, minutes), event_start) if as_of else _summarize([], event_start)
        for label, minutes in HORIZONS
    }
    full = horizons["full"]
    return {
        "version": VERSION, "side": side, "available": bool(points),
        "asOfUtc": as_of.isoformat() if as_of else None,
        "cutoffAtUtc": parse_dt(cutoff_at).isoformat() if parse_dt(cutoff_at) else None,
        "eventStartAtUtc": event_start.isoformat() if event_start else None,
        "sourceWindowStartUtc": points[0]["at"].isoformat() if points else None,
        "sourcePointCount": len(points), "inputPointCount": input_count,
        "excludedAfterCutoffCount": excluded, "horizons": horizons,
        "signalQualityIndex": full["signalQuality"]["signalQualityIndex"],
        "signalQualityIsWinProbability": False,
        "policy": "All features use observations at or before cutoff. SQI is descriptive, not predictive authority.",
    }


def flatten(summary: Any, prefix: str) -> Dict[str, float]:
    summary = summary if isinstance(summary, dict) else {}
    out = {
        f"{prefix}TemporalAvailable": float(summary.get("available") is True),
        f"{prefix}TemporalSourcePullCount": float(summary.get("sourcePointCount") or 0.0),
        f"{prefix}SignalQualityIndex": float(summary.get("signalQualityIndex") or 0.0),
    }
    scalar = (
        "pullCount", "durationMinutes", "coverageRatio", "maxGapMinutes", "velocityPpHr",
        "volatilityPpPerPull", "reversalCount", "netMovementPp", "grossMovementPp",
        "pathEfficiency", "directionPersistence", "stabilityScore", "directionalLegCount",
        "directionChangeDensity", "maxIntervalMovePp", "latestIntervalMovePp",
        "latestIntervalVelocityPpHr", "latestIntervalMoveRobustZ", "lateShock60mPp",
        "lateShock60mRobustZ", "latestReversalSwingPp", "latestReversalRecoveryRatio",
        "latestReversalAgeMinutes", "latestReversalMinutesBeforeEvent", "velocityDecayRatio",
        "velocityChangePpHr", "marketFlipCount", "reversalMarketFlipCount",
    )
    market_fields = (
        "latestBookCount", "meanBookCoverageRatio", "bookDirectionAgreement",
        "weightedBookDirectionAgreement", "synchronizedLatestLegBreadth", "latestBookRangePp",
        "latestBookStdPp", "marketCompressionPp", "marketExpansionPp", "averageFirstMoveLagMinutes",
    )
    for label, _ in HORIZONS:
        values = (summary.get("horizons") or {}).get(label) or {}
        suffix = label[0].upper() + label[1:]
        for field in scalar:
            out[f"{prefix}{field[0].upper() + field[1:]}{suffix}"] = float(_f(values.get(field), 0.0) or 0.0)
        out[f"{prefix}Acceleration{suffix}PpHr2"] = float(_f(values.get("accelerationPpHr2"), 0.0) or 0.0)
        market = values.get("market") or {}
        for field in market_fields:
            out[f"{prefix}{field[0].upper() + field[1:]}{suffix}"] = float(_f(market.get(field), 0.0) or 0.0)
        out[f"{prefix}SignalQualityIndex{suffix}"] = float(_f((values.get("signalQuality") or {}).get("signalQualityIndex"), 0.0) or 0.0)
        for leg_name, leg in (("LatestLeg", values.get("latestLeg") or {}), ("PreviousLeg", values.get("previousLeg") or {})):
            for field in ("amplitudePp", "durationMinutes", "velocityPpHr", "direction"):
                out[f"{prefix}{leg_name}{field[0].upper() + field[1:]}{suffix}"] = float(_f(leg.get(field), 0.0) or 0.0)
    return out


def provenance_is_lock_safe(summary: Any, source_at: Any, lock_at: Any) -> bool:
    if not isinstance(summary, dict) or summary.get("version") != VERSION or summary.get("available") is not True:
        return False
    as_of, source, lock = parse_dt(summary.get("asOfUtc")), parse_dt(source_at), parse_dt(lock_at)
    return bool(as_of and source and lock and as_of <= source <= lock)
