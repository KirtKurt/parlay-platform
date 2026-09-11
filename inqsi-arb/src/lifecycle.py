"""Two-leg / N-leg execution-assistance state machine.

No bet placement. Records user-confirmed accepted legs, recalculates exposure,
and can recommend a purely mathematical second-leg hedge. Persistence is supplied
by the caller/store layer.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict, Iterable, List, Mapping

from constraints import apply_book_constraints

STATES = ("REVIEWED", "LEG_RECORDED", "COMPLETE", "SETTLED")


@dataclass
class AcceptedLeg:
    outcome: str
    book: str
    stake: float
    decimal: float
    accepted_at: str

    @classmethod
    def from_payload(cls, p: Dict[str, Any]) -> "AcceptedLeg":
        stake = float(p["stake"])
        decimal = float(p["decimal"])
        if not isfinite(stake) or not isfinite(decimal) or stake <= 0 or decimal <= 1:
            raise ValueError("invalid accepted leg")
        outcome = str(p.get("outcome") or "").strip()
        book = str(p.get("book") or "").strip().lower()
        if not outcome or not book:
            raise ValueError("accepted leg requires outcome and book")
        return cls(
            outcome=outcome, book=book, stake=stake, decimal=decimal,
            accepted_at=str(p.get("accepted_at") or datetime.now(timezone.utc).isoformat()),
        )


def record_leg(position: Dict[str, Any], leg_payload: Dict[str, Any]) -> Dict[str, Any]:
    pos = dict(position or {})
    legs: List[Dict[str, Any]] = list(pos.get("accepted_legs") or [])
    leg = AcceptedLeg.from_payload(leg_payload)
    if any(x.get("outcome") == leg.outcome for x in legs):
        raise ValueError("outcome already recorded")
    required = set(str(x) for x in (pos.get("required_outcomes") or []) if str(x))
    if required and leg.outcome not in required:
        raise ValueError("accepted leg outcome is not required by this position")
    legs.append(asdict(leg))
    pos["accepted_legs"] = legs
    recorded = {x["outcome"] for x in legs}
    pos["state"] = "COMPLETE" if required and required.issubset(recorded) else "LEG_RECORDED"
    pos["updated_at"] = datetime.now(timezone.utc).isoformat()
    return pos


def outcome_pnl(position: Dict[str, Any]) -> Dict[str, float]:
    legs = position.get("accepted_legs") or []
    outcomes = list(position.get("required_outcomes") or sorted({x.get("outcome") for x in legs if x.get("outcome")}))
    total_staked = sum(float(x.get("stake") or 0) for x in legs)
    pnl: Dict[str, float] = {}
    for outcome in outcomes:
        gross = 0.0
        for leg in legs:
            if leg.get("outcome") == outcome:
                gross += float(leg["stake"]) * float(leg["decimal"])
        pnl[str(outcome)] = round(gross - total_staked, 2)
    return pnl


def recommend_two_leg_completion(
    position: Mapping[str, Any],
    candidate_quotes: Iterable[Mapping[str, Any]],
    profiles: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Recommend the second stake after one leg has already been accepted.

    The first leg is immutable. Each candidate quote for the missing outcome is
    sized to equalize gross payout between the two outcomes. Balance, user max,
    and provider quote limits are then checked without placing any transaction.
    """
    required = [str(x) for x in (position.get("required_outcomes") or []) if str(x)]
    accepted = list(position.get("accepted_legs") or [])
    if len(required) != 2:
        raise ValueError("two-leg completion requires exactly two required outcomes")
    if len(accepted) != 1:
        raise ValueError("two-leg completion requires exactly one accepted leg")

    first = AcceptedLeg.from_payload(dict(accepted[0]))
    if first.outcome not in required:
        raise ValueError("accepted outcome is not in required outcomes")
    missing = required[0] if required[1] == first.outcome else required[1]
    target_gross = first.stake * first.decimal
    recommendations: List[Dict[str, Any]] = []

    for raw in candidate_quotes or []:
        quote = dict(raw)
        if str(quote.get("outcome") or "").strip() != missing:
            continue
        book = str(quote.get("book") or "").strip().lower()
        try:
            decimal = float(quote.get("net_decimal") if quote.get("net_decimal") is not None else quote.get("decimal"))
        except (TypeError, ValueError):
            continue
        if not book or not isfinite(decimal) or decimal <= 1:
            continue
        suggested = round(target_gross / decimal, 2)
        total = round(first.stake + suggested, 2)
        pnl_first = round(target_gross - total, 2)
        pnl_second = round(suggested * decimal - total, 2)
        constrained = apply_book_constraints([
            {"book": book, "stake": suggested, "limit": quote.get("limit")}
        ], profiles)
        cap = constrained["legs"][0].get("constraint_cap") if constrained.get("legs") else None
        feasible = bool(constrained.get("feasible"))
        recommendations.append({
            "outcome": missing,
            "book": book,
            "decimal": round(decimal, 6),
            "american": quote.get("american"),
            "suggested_stake": suggested,
            "target_gross_payout": round(target_gross, 2),
            "total_stake_after_completion": total,
            "outcome_pnl": {first.outcome: pnl_first, missing: pnl_second},
            "guaranteed_profit": min(pnl_first, pnl_second),
            "constraint_cap": cap,
            "feasible": feasible,
            "last_update": quote.get("last_update"),
            "provider": quote.get("provider"),
            "link": quote.get("link"),
            "limit": quote.get("limit"),
            "constraint_reasons": constrained.get("reasons") or [],
        })

    recommendations.sort(key=lambda row: (bool(row.get("feasible")), float(row.get("guaranteed_profit") or -1e18), float(row.get("decimal") or 0)), reverse=True)
    return {
        "ok": True,
        "places_bets": False,
        "position_id": position.get("position_id"),
        "accepted_outcome": first.outcome,
        "missing_outcome": missing,
        "target_gross_payout": round(target_gross, 2),
        "recommendations": recommendations,
        "n_recommendations": len(recommendations),
        "n_feasible": sum(1 for row in recommendations if row.get("feasible")),
    }
