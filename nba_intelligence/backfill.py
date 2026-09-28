"""Chronological NBA shadow backfill. No serving authority.

Replays frozen rows in tip-off order. Market probabilities come from
de-vigged moneylines already stored on the row. Fundamentals probabilities
are accepted only when basketball fields are present. Odds never fill
p_fundamental.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .contract import filter_fundamental_features
from .layer import evaluate_game
from .audit import ablation_compare, rolling_audit

AUTHORITY_CHANGED = False


def _parse_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _p(value: Any) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("NBA backfill probability must be in [0,1]")
    return number


def american_to_raw(american: int) -> float:
    if int(american) == 0:
        raise ValueError("American odds cannot be 0")
    if american < 0:
        a = abs(int(american))
        return a / (a + 100.0)
    return 100.0 / (int(american) + 100.0)


def devig_home(home_american: int, away_american: int) -> float:
    home_raw = american_to_raw(home_american)
    away_raw = american_to_raw(away_american)
    total = home_raw + away_raw
    if total <= 0:
        return 0.5
    return home_raw / total


def sort_chronological(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = [dict(row) for row in rows]
    ordered.sort(key=lambda row: (_parse_utc(str(row["tip_utc"])), str(row.get("event_id") or "")))
    times = [_parse_utc(str(row["tip_utc"])) for row in ordered]
    if times != sorted(times):
        raise ValueError("NBA backfill rows must stay chronological")
    return ordered


def replay_row(row: Mapping[str, Any]) -> dict[str, Any]:
    feature_names = list(row.get("fundamental_features") or [])
    kept = filter_fundamental_features(feature_names) if feature_names else ()
    fundamentals_available = bool(kept) and row.get("p_fundamental_home") is not None
    if not fundamentals_available and row.get("p_fundamental_home") is not None:
        raise ValueError("p_fundamental cannot be used without basketball features")
    if row.get("p_market_home") is not None:
        p_market = _p(row["p_market_home"])
    else:
        p_market = devig_home(int(row["home_american"]), int(row["away_american"]))
    if fundamentals_available:
        p_fundamental = _p(row["p_fundamental_home"])
    else:
        p_fundamental = 0.5
    p_market_aware = _p(row["p_market_aware_home"]) if row.get("p_market_aware_home") is not None else p_market
    scored = evaluate_game(
        event_id=str(row["event_id"]),
        home=str(row["home"]),
        away=str(row["away"]),
        p_fundamental_home=p_fundamental,
        p_market_aware_home=p_market_aware,
        p_market_home=p_market,
        market=str(row.get("market") or "moneyline"),
        active_fundamental_signals=kept,
        active_market_signals=tuple(row.get("market_features") or ("devig_moneyline",)),
    )
    scored.update(
        {
            "tip_utc": str(row["tip_utc"]),
            "fundamentals_available": fundamentals_available,
            "label": row.get("label"),
            "source": str(row.get("source") or "fixture"),
            "authority_changed": AUTHORITY_CHANGED,
        }
    )
    if not fundamentals_available:
        scored["p_fundamental_status"] = "UNAVAILABLE"
        scored["note"] = "No basketball features on the row. Neutral 0.5 is diagnostic only."
    return scored


def replay_slate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sort_chronological(rows)
    scored = [replay_row(row) for row in ordered]
    graded = []
    for item in scored:
        if item.get("label") is None:
            continue
        pick_home = item["final_selected_side"] == item["home"]
        hit = int(pick_home) == int(item["label"])
        p_pred = item["p_market_aware"] if pick_home else 1.0 - item["p_market_aware"]
        graded.append(
            {
                "favorite_selected": item["favorite_selected_market_aware"],
                "hit": bool(hit),
                "brier": (p_pred - hit) ** 2,
                "log_loss": None,
                "p_pred": p_pred,
                "p_market": item["p_market"] if item["favorite"] == item["home"] else 1.0 - item["p_market"],
                "market_flip": item["market_flip"],
                "fundamentals_would_have_won": (
                    (item["fundamental_selected_side"] == item["home"]) == bool(item["label"])
                    and not hit
                ),
            }
        )
    return {
        "events": scored,
        "order": [item["event_id"] for item in scored],
        "fundamentals_available_count": sum(1 for item in scored if item["fundamentals_available"]),
        "audit": rolling_audit(graded, windows=(7, 30, 100)) if graded else {"sample": 0},
        "ablation": ablation_compare(graded, graded) if graded else {"assumed_market_helps": False},
        "authority_changed": AUTHORITY_CHANGED,
        "live_odds_called": False,
    }
