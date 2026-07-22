from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from config import TennisConfig
from contracts import (
    FEATURE_SCHEMA_VERSION,
    SIGNAL_SCHEMA_VERSION,
    parse_utc,
    utc_iso,
)


def american_implied_probability(value: Any) -> Optional[float]:
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(odds) or odds == 0:
        return None
    if odds < 0:
        return abs(odds) / (abs(odds) + 100.0)
    return 100.0 / (odds + 100.0)


def no_vig_pair(
    player_a_price: Any, player_b_price: Any
) -> Optional[Tuple[float, float]]:
    a = american_implied_probability(player_a_price)
    b = american_implied_probability(player_b_price)
    if a is None or b is None or a + b <= 0:
        return None
    total = a + b
    return a / total, b / total


def usable_book_keys(event: Dict[str, Any]) -> Set[str]:
    usable: Set[str] = set()
    for key, book in (event.get("books") or {}).items():
        if not isinstance(book, dict):
            continue
        if no_vig_pair(book.get("player_a"), book.get("player_b")) is not None:
            usable.add(str(key))
    return usable


def consensus_probabilities(
    event: Dict[str, Any], *, book_keys: Optional[Set[str]] = None
) -> Optional[Dict[str, Any]]:
    a_values: List[float] = []
    b_values: List[float] = []
    per_book: Dict[str, Any] = {}
    for key, book in sorted((event.get("books") or {}).items()):
        if book_keys is not None and key not in book_keys:
            continue
        if not isinstance(book, dict):
            continue
        pair = no_vig_pair(book.get("player_a"), book.get("player_b"))
        if pair is None:
            continue
        a, b = pair
        a_values.append(a)
        b_values.append(b)
        per_book[str(key)] = {"player_a": a, "player_b": b}
    if not a_values:
        return None
    return {
        "player_a": sum(a_values) / len(a_values),
        "player_b": sum(b_values) / len(b_values),
        "book_count": len(a_values),
        "book_divergence": max(a_values) - min(a_values) if len(a_values) > 1 else 0.0,
        "book_probabilities": per_book,
    }


def _minutes_between(start: Any, end: Any) -> float:
    a = parse_utc(start)
    b = parse_utc(end)
    if a is None or b is None:
        return 15.0
    return max((b - a).total_seconds() / 60.0, 1.0)


def _score_cap(grade: str, tags: Sequence[str]) -> float:
    tag_set = set(tags or [])
    if grade == "STRONG_SOLID":
        return 100.0
    if grade == "SOLID":
        return 84.0
    if grade == "COIN_FLIP":
        return 54.0
    if grade == "FRAGILE":
        return 34.0 if "CHAOS" in tag_set else 44.0
    if grade in {"INSUFFICIENT_HISTORY", "INSUFFICIENT_MARKET_COVERAGE"}:
        return 20.0
    return 50.0


def _guard_score(
    raw_score: float,
    grade: str,
    tags: Sequence[str],
    reversals: int,
    divergence: float,
    latest_gap: float,
) -> float:
    score = float(raw_score)
    tag_set = set(tags or [])
    if "CHAOS" in tag_set:
        score -= 25
    if "BOOK_DIVERGENCE" in tag_set:
        score -= 12
    if "REVERSAL" in tag_set:
        score -= 8 * max(1, int(reversals or 0))
    if latest_gap < 0.05:
        score -= 10
    if divergence >= 0.055:
        score -= 15
    return round(max(0.0, min(score, _score_cap(grade, tags))), 2)


def directional_signal(series: Sequence[Dict[str, Any]], side: str) -> Dict[str, Any]:
    values = [float(row["probabilities"][side]) for row in series]
    pull_count = len(values)
    start = values[0]
    latest = values[-1]
    delta = latest - start
    duration = (
        _minutes_between(series[0]["observed_at_utc"], series[-1]["observed_at_utc"])
        if pull_count > 1
        else 0.0
    )
    velocity = (delta * 100.0) / max(duration / 60.0, 0.25) if pull_count > 1 else 0.0
    midpoint = max(1, pull_count // 2)
    first_half = (
        (values[midpoint - 1] - values[0]) / max(midpoint - 1, 1)
        if pull_count > 2
        else 0.0
    )
    second_half = (
        (values[-1] - values[midpoint - 1]) / max(pull_count - midpoint, 1)
        if pull_count > 2
        else 0.0
    )
    acceleration = second_half - first_half
    latest_gap = abs(
        float(series[-1]["probabilities"]["player_a"])
        - float(series[-1]["probabilities"]["player_b"])
    )
    divergence = float(series[-1]["probabilities"].get("book_divergence") or 0.0)
    reversals = 0
    if pull_count >= 3:
        signs = [
            1
            if values[index] - values[index - 1] > 0.0005
            else -1
            if values[index] - values[index - 1] < -0.0005
            else 0
            for index in range(1, pull_count)
        ]
        reversals = sum(
            1
            for index in range(1, len(signs))
            if signs[index] and signs[index - 1] and signs[index] != signs[index - 1]
        )

    tags: List[str] = []
    if pull_count < 3:
        tags.append("LOW_PULL_DEPTH")
    if delta >= 0.018:
        tags.append("STEAM")
    if delta <= -0.018:
        tags.append("RESISTANCE")
    if velocity >= 1.75:
        tags.append("MOMENTUM")
    if acceleration >= 0.004:
        tags.append("ACCELERATION")
    if acceleration <= -0.004:
        tags.append("DECELERATION")
    if reversals:
        tags.append("REVERSAL")
    if latest_gap < 0.05:
        tags.append("COMPRESSED_MARKET")
    if divergence >= 0.035:
        tags.append("BOOK_DIVERGENCE")
    if reversals >= 2 or divergence >= 0.06:
        tags.append("CHAOS")
    if latest >= 0.56 and delta >= 0.012 and divergence < 0.035:
        tags.append("CERTAINTY_ANCHOR")
    if delta > 0 and latest < 0.50:
        tags.append("PUBLIC_FADE_CANDIDATE")
    if (start < 0.5 <= latest) or (start > 0.5 >= latest):
        tags.append("FAVORITE_FLIP")
    if (
        divergence < 0.02
        and int(series[-1]["probabilities"].get("book_count") or 0) >= 3
    ):
        tags.append("MARKET_AGREEMENT")

    if pull_count < 3:
        grade = "INSUFFICIENT_HISTORY"
    elif "CHAOS" in tags or ("REVERSAL" in tags and "BOOK_DIVERGENCE" in tags):
        grade = "FRAGILE"
    elif latest_gap < 0.05 or divergence >= 0.035:
        grade = "COIN_FLIP"
    elif latest >= 0.56 and delta >= 0.018 and divergence < 0.025:
        grade = "STRONG_SOLID"
    elif latest >= 0.525 and delta >= 0.008:
        grade = "SOLID"
    else:
        grade = "COIN_FLIP" if latest_gap < 0.08 else "FRAGILE"

    raw_score = max(
        0.0,
        min(
            100.0,
            50.0
            + delta * 700.0
            + (latest - 0.5) * 80.0
            - divergence * 300.0
            - reversals * 8.0,
        ),
    )
    guarded = _guard_score(raw_score, grade, tags, reversals, divergence, latest_gap)
    return {
        "side": side,
        "probability_start": round(start, 5),
        "probability_latest": round(latest, 5),
        "delta": round(delta, 5),
        "velocity_pp_per_hour": round(velocity, 3),
        "acceleration": round(acceleration, 5),
        "pull_count": pull_count,
        "duration_minutes": round(duration, 2),
        "latest_gap": round(latest_gap, 5),
        "book_count": int(series[-1]["probabilities"].get("book_count") or 0),
        "book_divergence": round(divergence, 5),
        "reversals": reversals,
        "tags": sorted(set(tags)),
        "grade": grade,
        "raw_score_before_guard": round(raw_score, 2),
        "market_signal_score": guarded,
        "score_guard_applied": True,
    }


def _prematch_rows(
    snapshot_rows: Iterable[Dict[str, Any]],
    event: Dict[str, Any],
    as_of_utc: datetime,
) -> List[Dict[str, Any]]:
    start = parse_utc(event.get("commence_time"))
    if start is None:
        return []
    expected_id = str(event.get("event_id") or "")
    expected_tournament = str(event.get("tournament_key") or "")
    expected_player_a = str(event.get("player_a") or "")
    expected_player_b = str(event.get("player_b") or "")
    rows = []
    for stored in snapshot_rows or []:
        data = stored.get("data") if isinstance(stored, dict) else None
        observed = parse_utc((stored or {}).get("observed_at_utc"))
        if (
            not isinstance(data, dict)
            or observed is None
            or observed > as_of_utc
            or observed >= start
        ):
            continue
        if str(data.get("event_id") or "") != expected_id:
            continue
        if str(data.get("tournament_key") or "") != expected_tournament:
            continue
        if (
            str(data.get("player_a") or "") != expected_player_a
            or str(data.get("player_b") or "") != expected_player_b
        ):
            continue
        rows.append({"observed_at_utc": utc_iso(observed), "event": data})
    return sorted(rows, key=lambda row: row["observed_at_utc"])


def _market_fingerprint(event: Dict[str, Any], book_keys: Set[str]) -> str:
    material = {
        key: {
            "player_a": (event.get("books") or {}).get(key, {}).get("player_a"),
            "player_b": (event.get("books") or {}).get(key, {}).get("player_b"),
            "last_update": (event.get("books") or {}).get(key, {}).get("last_update"),
        }
        for key in sorted(book_keys)
    }
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_feature_vector(
    snapshot_rows: Iterable[Dict[str, Any]],
    event: Dict[str, Any],
    now_utc: datetime,
    config: TennisConfig,
) -> Dict[str, Any]:
    current = now_utc.astimezone(timezone.utc)
    start = parse_utc(event.get("commence_time"))
    prematch = _prematch_rows(snapshot_rows, event, current)
    latest_book_count = len(usable_book_keys(prematch[-1]["event"])) if prematch else 0
    market_rows = [
        row
        for row in prematch
        if len(usable_book_keys(row["event"])) >= config.min_common_books
    ]
    common_books: Set[str] = set()
    compatible_rows: List[Dict[str, Any]] = []
    # Use the longest recent window that retains the required common books.
    # Empty/one-book observations remain part of the audit count but cannot
    # permanently poison every later signal for the match.
    for row in reversed(market_rows):
        row_books = usable_book_keys(row["event"])
        candidate = row_books if not compatible_rows else common_books & row_books
        if len(candidate) < config.min_common_books:
            break
        common_books = candidate
        compatible_rows.append(row)
    compatible_rows.reverse()

    series: List[Dict[str, Any]] = []
    if len(common_books) >= config.min_common_books:
        for row in compatible_rows:
            probabilities = consensus_probabilities(
                row["event"], book_keys=common_books
            )
            if probabilities is not None:
                series.append(
                    {
                        "observed_at_utc": row["observed_at_utc"],
                        "probabilities": probabilities,
                        "market_fingerprint": _market_fingerprint(
                            row["event"], common_books
                        ),
                    }
                )

    unique_market_state_count = len({row["market_fingerprint"] for row in series})
    minutes_to_start = (
        max((start - current).total_seconds() / 60.0, 0.0) if start else None
    )
    base = {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "signal_version": SIGNAL_SCHEMA_VERSION,
        "sport": "tennis",
        "event_id": str(event.get("event_id") or ""),
        "slate_date_et": event.get("slate_date_et"),
        "observed_at_utc": utc_iso(current),
        "commence_time": event.get("commence_time"),
        "minutes_to_start": round(minutes_to_start, 2)
        if minutes_to_start is not None
        else None,
        "player_a": event.get("player_a"),
        "player_b": event.get("player_b"),
        "tour": event.get("tour"),
        "discipline": event.get("discipline"),
        "tournament_key": event.get("tournament_key"),
        "tournament_title": event.get("tournament_title"),
        "model_state": config.model_state,
        "model_probability": None,
        "training_label": None,
        "prediction_eligible": False,
        "market_only": True,
        "unavailable_signals": ["TRAP", "VERIFIED_PUBLIC_BETTING_SPLITS"],
        "data_quality": {
            "prematch_snapshot_count": len(prematch),
            "market_snapshot_count": len(market_rows),
            "compatible_market_snapshot_count": len(compatible_rows),
            "low_or_no_book_snapshot_count": len(prematch) - len(market_rows),
            "valid_signal_pull_count": len(series),
            "unique_market_state_count": unique_market_state_count,
            "latest_book_count": latest_book_count,
            "common_book_count": len(common_books),
            "common_books": sorted(common_books),
            "minimum_signal_pulls": config.min_signal_pulls,
            "minimum_common_books": config.min_common_books,
            "minimum_publish_pulls": config.min_publish_pulls,
            "minimum_publish_books": config.min_publish_books,
        },
    }

    if start is None or current >= start:
        return {
            **base,
            "research_status": "MATCH_STARTED_NO_FEATURE",
            "selected_side": None,
            "selected_player": None,
            "market_signal_score": None,
            "grade": "MATCH_STARTED_NO_FEATURE",
            "tags": ["PREMATCH_CUTOFF"],
            "player_a_signal": None,
            "player_b_signal": None,
        }

    if len(series) < config.min_signal_pulls:
        reason = (
            "INSUFFICIENT_MARKET_COVERAGE"
            if len(common_books) < config.min_common_books
            else "INSUFFICIENT_HISTORY"
        )
        return {
            **base,
            "research_status": reason,
            "selected_side": None,
            "selected_player": None,
            "market_signal_score": None,
            "grade": reason,
            "tags": [
                "LOW_BOOK_COVERAGE"
                if reason == "INSUFFICIENT_MARKET_COVERAGE"
                else "LOW_PULL_DEPTH"
            ],
            "player_a_signal": None,
            "player_b_signal": None,
        }

    player_a_signal = directional_signal(series, "player_a")
    player_b_signal = directional_signal(series, "player_b")
    score_a = float(player_a_signal["market_signal_score"])
    score_b = float(player_b_signal["market_signal_score"])
    if abs(score_a - score_b) > 1e-9:
        selected = player_a_signal if score_a > score_b else player_b_signal
        selection_tiebreaker = "market_signal_score"
    else:
        raw_a = float(player_a_signal["raw_score_before_guard"])
        raw_b = float(player_b_signal["raw_score_before_guard"])
        if abs(raw_a - raw_b) > 1e-9:
            selected = player_a_signal if raw_a > raw_b else player_b_signal
            selection_tiebreaker = "raw_score_before_guard"
        else:
            probability_a = float(player_a_signal["probability_latest"])
            probability_b = float(player_b_signal["probability_latest"])
            if abs(probability_a - probability_b) > 1e-9:
                selected = (
                    player_a_signal
                    if probability_a > probability_b
                    else player_b_signal
                )
                selection_tiebreaker = "latest_consensus_probability"
            else:
                selected = None
                selection_tiebreaker = "no_selection_exact_tie"

    selected_side = selected["side"] if selected else None
    tags = (
        selected["tags"]
        if selected
        else sorted(set(player_a_signal["tags"]) | set(player_b_signal["tags"]))
    )

    if len(series) < config.min_publish_pulls:
        research_status = "SHADOW_COLLECTING"
    elif latest_book_count < config.min_publish_books:
        research_status = "WATCHLIST_LOW_BOOK_COVERAGE"
    elif "CHAOS" in tags:
        research_status = "PASS_CHAOS"
    elif selected is None:
        research_status = "PASS_TIED_SIGNAL"
    else:
        research_status = "SHADOW_FEATURE_READY"

    return {
        **base,
        "research_status": research_status,
        "selected_side": selected_side,
        "selected_player": event.get(selected_side) if selected_side else None,
        "selection_tiebreaker": selection_tiebreaker,
        "market_signal_score": selected["market_signal_score"] if selected else None,
        "grade": selected["grade"] if selected else "TIED_SIGNAL",
        "tags": tags,
        "player_a_signal": player_a_signal,
        "player_b_signal": player_b_signal,
    }
