"""Two-leg / N-leg execution-assistance state machine.

No bet placement. Records user-confirmed accepted legs and recalculates remaining
exposure deterministically. Persistence is supplied by the caller/store layer.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List

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
        if stake <= 0 or decimal <= 1:
            raise ValueError("invalid accepted leg")
        return cls(
            outcome=str(p["outcome"]), book=str(p["book"]), stake=stake, decimal=decimal,
            accepted_at=str(p.get("accepted_at") or datetime.now(timezone.utc).isoformat()),
        )


def record_leg(position: Dict[str, Any], leg_payload: Dict[str, Any]) -> Dict[str, Any]:
    pos = dict(position or {})
    legs: List[Dict[str, Any]] = list(pos.get("accepted_legs") or [])
    leg = AcceptedLeg.from_payload(leg_payload)
    if any(x.get("outcome") == leg.outcome for x in legs):
        raise ValueError("outcome already recorded")
    legs.append(asdict(leg))
    pos["accepted_legs"] = legs
    required = set(pos.get("required_outcomes") or [])
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
        pnl[outcome] = round(gross - total_staked, 2)
    return pnl
