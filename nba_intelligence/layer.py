"""Two-path NBA diagnostics. Never changes serving authority."""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from inqsi_intelligence.favorite_bias import compare_binary, slate_audit

AUTHORITY_CHANGED = False
VULNERABLE_GAP = 0.05
CREDIBLE_GAP = 0.05


def evaluate_game(
    *,
    event_id: str,
    home: str,
    away: str,
    p_fundamental_home: float,
    p_market_aware_home: float,
    p_market_home: float,
    market: str = "moneyline",
    active_fundamental_signals: Sequence[str] = (),
    active_market_signals: Sequence[str] = (),
    collected_signals: Sequence[str] = (),
    shap: Mapping[str, float] | None = None,
) -> dict:
    row = compare_binary(
        event_id=str(event_id),
        p_fundamental_home=float(p_fundamental_home),
        p_market_aware_home=float(p_market_aware_home),
        p_market_home=float(p_market_home),
        home=str(home),
        away=str(away),
    )
    final_side = row.market_aware_pick
    favorite = row.market_favorite
    dog = home if favorite == away else away
    payload = {
        "event_id": row.event_id,
        "market": str(market),
        "home": home,
        "away": away,
        "p_fundamental": row.p_fundamental,
        "p_market_aware": row.p_market_aware,
        "p_market": float(p_market_home),
        "market_delta": row.market_influence,
        "fundamental_selected_side": row.fundamental_pick,
        "market_aware_selected_side": row.market_aware_pick,
        "final_selected_side": final_side,
        "favorite": favorite,
        "underdog": dog,
        "favorite_underdog": "FAVORITE" if final_side == favorite else "UNDERDOG",
        "market_flip": row.market_flip,
        "market_influence_magnitude": abs(row.market_influence),
        "favorite_selected_fundamental": row.favorite_selected_fundamental,
        "favorite_selected_market_aware": row.favorite_selected_market_aware,
        "upset_probability_fundamental": row.upset_probability_fundamental,
        "upset_probability_market_aware": row.upset_probability_market_aware,
        "active_fundamental_signals": list(active_fundamental_signals),
        "active_market_signals": list(active_market_signals),
        "diagnostic_only_signals": sorted(set(map(str, collected_signals)) - set(map(str, active_fundamental_signals)) - set(map(str, active_market_signals))),
        "shap": dict(shap) if shap else {},
        "shap_available": bool(shap),
        "authority_changed": AUTHORITY_CHANGED,
        "quota_applied": False,
    }
    payload["vulnerable_favorite"] = vulnerable_favorite(payload)
    payload["credible_underdog"] = credible_underdog(payload)
    return payload


def vulnerable_favorite(row: Mapping) -> dict:
    """Market values the favorite materially above independent basketball."""
    favorite = str(row["favorite"])
    home = str(row["home"])
    p_f_fav = float(row["p_fundamental"]) if favorite == home else 1.0 - float(row["p_fundamental"])
    p_m_fav = float(row["p_market"]) if favorite == home else 1.0 - float(row["p_market"])
    gap = p_m_fav - p_f_fav
    flagged = gap >= VULNERABLE_GAP and bool(row.get("favorite_selected_market_aware"))
    return {
        "flagged": flagged,
        "favorite": favorite,
        "favorite_fundamental_prob": p_f_fav,
        "favorite_market_prob": p_m_fav,
        "gap": gap,
        "auto_flipped": False,
        "evidence": list(row.get("active_fundamental_signals") or []) if flagged else [],
    }


def credible_underdog(row: Mapping) -> dict:
    """Underdog is independently supported by basketball, not by being the dog."""
    dog = str(row["underdog"])
    home = str(row["home"])
    p_f_dog = float(row["p_fundamental"]) if dog == home else 1.0 - float(row["p_fundamental"])
    p_m_dog = float(row["p_market"]) if dog == home else 1.0 - float(row["p_market"])
    gap = p_f_dog - p_m_dog
    supported = p_f_dog >= 0.5 and gap >= CREDIBLE_GAP
    return {
        "flagged": supported,
        "underdog": dog,
        "underdog_fundamental_prob": p_f_dog,
        "underdog_market_prob": p_m_dog,
        "gap": gap,
        "auto_selected": False,
        "evidence": list(row.get("active_fundamental_signals") or []) if supported else [],
    }


def explain_pick(row: Mapping) -> dict:
    return {
        "fundamental_probability": row["p_fundamental"],
        "market_aware_probability": row["p_market_aware"],
        "market_delta": row["market_delta"],
        "final_probability": row["p_market_aware"],
        "selected_team": row["final_selected_side"],
        "favorite_underdog": row["favorite_underdog"],
        "market_flip": "YES" if row["market_flip"] else "NO",
        "strongest_team_signals": list(row.get("active_fundamental_signals") or []),
        "strongest_market_signals": list(row.get("active_market_signals") or []),
        "shap": row.get("shap") or {},
        "shap_available": bool(row.get("shap_available")),
        "vulnerable_favorite": row.get("vulnerable_favorite"),
        "credible_underdog": row.get("credible_underdog"),
        "authority_changed": AUTHORITY_CHANGED,
        "lock_status": "UNCHANGED",
    }


def slate_report(rows: Sequence[Mapping]) -> dict:
    comparisons = []
    for raw in rows:
        comparisons.append(
            compare_binary(
                event_id=str(raw["event_id"]),
                p_fundamental_home=float(raw["p_fundamental_home"]),
                p_market_aware_home=float(raw["p_market_aware_home"]),
                p_market_home=float(raw["p_market_home"]),
                home=str(raw.get("home", "home")),
                away=str(raw.get("away", "away")),
            )
        )
    summary = slate_audit(comparisons)
    summary["quota_applied"] = False
    summary["authority_changed"] = AUTHORITY_CHANGED
    summary["vulnerable_favorites"] = sum(
        1
        for raw in rows
        if vulnerable_favorite(
            evaluate_game(
                event_id=str(raw["event_id"]),
                home=str(raw.get("home", "home")),
                away=str(raw.get("away", "away")),
                p_fundamental_home=float(raw["p_fundamental_home"]),
                p_market_aware_home=float(raw["p_market_aware_home"]),
                p_market_home=float(raw["p_market_home"]),
            )
        )["flagged"]
    )
    return summary
