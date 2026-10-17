"""2stackMLB serving gate.

Official KS1 p_home is never rewritten.
pick_status is the only actionable field this package is allowed to emit.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from stack2mlb import DISAGREE_PP, SYSTEM, VERSION
from stack2mlb.bayes_market import blend_signals, update as bayes_update
from stack2mlb.market import clip01


def side(p: float) -> str:
    return "home" if float(p) >= 0.5 else "away"


@dataclass
class StackDecision:
    system: str
    version: str
    p_lgb: float
    p_poisson: float
    p_elo: float
    p_market: float
    p_markov: float | None
    p_glm: float | None
    p_home_official: float
    p_stack: float
    pick_status: str
    selection_reason: str
    lgb_is_outlier: bool
    max_engine_gap: float
    agreed_side: str | None

    def as_dict(self) -> dict:
        return asdict(self)


def _gap(*probs: float) -> float:
    vals = [float(p) for p in probs]
    return max(vals) - min(vals)


def decide(
    *,
    p_lgb: float,
    p_poisson: float,
    p_market: float,
    p_elo: float,
    p_home_official: float,
    p_markov: float | None = None,
    p_glm: float | None = None,
    starter_unverified: bool = False,
    disagree: float = DISAGREE_PP,
) -> StackDecision:
    official = clip01(p_home_official)
    lgb, poi, mkt, elo = map(clip01, (p_lgb, p_poisson, p_market, p_elo))
    markov = None if p_markov is None else clip01(p_markov)
    glm = None if p_glm is None else clip01(p_glm)

    if starter_unverified:
        return StackDecision(
            SYSTEM, VERSION, lgb, poi, elo, mkt, markov, glm, official, official,
            "pass", "starter_unverified", False, _gap(lgb, poi, mkt), None,
        )

    sides = {"lgb": side(lgb), "poisson": side(poi), "elo": side(elo), "market": side(mkt)}
    lgb_outlier = (
        sides["poisson"] == sides["elo"] == sides["market"]
        and sides["lgb"] != sides["poisson"]
    )
    structural = [sides["poisson"], sides["elo"], sides["market"]]
    structural_agree = len(set(structural)) == 1
    all_agree = len(set(sides.values())) == 1
    max_gap = _gap(lgb, poi, mkt)

    if not structural_agree:
        return StackDecision(
            SYSTEM, VERSION, lgb, poi, elo, mkt, markov, glm, official, official,
            "pass",
            f"side_split:l={sides['lgb']},p={sides['poisson']},e={sides['elo']},m={sides['market']}",
            lgb_outlier, max_gap, None,
        )

    agreed = structural[0]
    signal = blend_signals({"poisson": poi, "elo": elo, "market": mkt}, {"poisson": 1.0, "elo": 1.0, "market": 1.0})
    if all_agree and not lgb_outlier:
        signal = blend_signals(
            {"lgb": lgb, "poisson": poi, "elo": elo, "market": mkt},
            {"lgb": 0.8, "poisson": 1.0, "elo": 1.2, "market": 1.2},
        )

    stacked = bayes_update(mkt, signal, kappa=0.35 if all_agree else 0.20)

    if max_gap > disagree and not lgb_outlier:
        stacked = bayes_update(mkt, signal, kappa=0.15)
        status, reason = "shrink", f"engine_gap={max_gap:.3f}>{disagree:.3f}"
    elif lgb_outlier:
        status, reason = "shrink", f"lgb_outlier side={sides['lgb']} vs structural={agreed}"
    elif max_gap > disagree:
        status, reason = "shrink", f"engine_gap={max_gap:.3f}>{disagree:.3f}"
    else:
        status, reason = "bet", f"agree_{agreed} gap={max_gap:.3f}"

    if status == "bet" and markov is not None and side(markov) != agreed:
        status, reason = "shrink", reason + "|markov_side_conflict"
        stacked = bayes_update(mkt, markov, kappa=0.10)
    if status == "bet" and glm is not None and side(glm) != agreed:
        status, reason = "shrink", reason + "|glm_side_conflict"

    return StackDecision(
        SYSTEM, VERSION, lgb, poi, elo, mkt, markov, glm, official, stacked,
        status, reason, lgb_outlier, max_gap, agreed,
    )
