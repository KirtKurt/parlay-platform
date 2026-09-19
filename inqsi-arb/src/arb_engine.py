"""Deterministic N-way arbitrage engine for Inqsi.

The engine has no network or AWS dependencies. It evaluates normalized quote
sets and explicitly separates mathematical detection from settlement-verified
arbitrage qualification. Mathematical opportunity detection is never enough to
label an opportunity a verified arb: settlement rules must be COMPATIBLE.
"""
from __future__ import annotations

from itertools import islice, product
from math import ceil, floor, isfinite
from typing import Any, Dict, Iterable, List, Mapping, Optional

from stake_rounding import StakeRoundingError, optimize_rounding_neighborhood


class ArbValidationError(ValueError):
    pass


def american_to_decimal(value: float) -> float:
    a = float(value)
    if not isfinite(a) or a == 0:
        raise ArbValidationError("invalid American odds")
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def decimal_to_american(value: float) -> float:
    d = float(value)
    if not isfinite(d) or d <= 1:
        raise ArbValidationError("invalid decimal odds")
    return (d - 1.0) * 100.0 if d >= 2.0 else -100.0 / (d - 1.0)


def _quote_decimal(raw: Mapping[str, Any]) -> float:
    if raw.get("decimal") is not None:
        d = float(raw["decimal"])
        if not isfinite(d) or d <= 1:
            raise ArbValidationError("invalid decimal odds")
        return d
    return american_to_decimal(float(raw["american"]))


def _net_decimal(decimal_odds: float, commission_rate: float) -> float:
    c = float(commission_rate or 0.0)
    if c < 0 or c >= 1:
        raise ArbValidationError("commission must be in [0, 1)")
    return 1.0 + (decimal_odds - 1.0) * (1.0 - c)


def _parse_books(raw: Any) -> Optional[set[str]]:
    if not raw:
        return None
    if isinstance(raw, str):
        values = [part.strip().lower() for part in raw.split(",")]
    else:
        values = [str(part).strip().lower() for part in raw]
    allowed = {part for part in values if part}
    return allowed or None


def _candidate_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Identify the selected prices independently of settlement-profile rows."""
    return (
        str(row.get("market_id") or "").split("|rules:", 1)[0],
        str(row.get("event") or ""),
        str(row.get("market") or ""),
        str(row.get("commence_time") or ""),
        tuple(sorted(
            (str(leg.get("outcome") or ""), str(leg.get("book") or ""), leg.get("net_decimal"))
            for leg in row.get("legs") or []
        )),
    )


def _cap(value: Any) -> Optional[float]:
    try:
        parsed = float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and isfinite(parsed) and parsed >= 0 else None


def _constraint(value: Any, default: float, *, positive: bool = False) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(parsed) or (parsed <= 0 if positive else parsed < 0):
        return None
    return parsed


def _quote_constraints_usable(raw: Mapping[str, Any], bankroll: float) -> bool:
    cap = _constraint(raw.get("limit"), bankroll)
    minimum = _constraint(raw.get("min_stake"), 0.0)
    increment = _constraint(raw.get("stake_increment"), 0.01, positive=True)
    if cap is None or minimum is None or increment is None or increment < 0.01 or minimum > cap:
        return False
    maximum = min(cap, bankroll)
    first_stake = max(increment, ceil(minimum / increment - 1e-10) * increment)
    return first_stake <= maximum + 1e-9


def _two_way_exact_plan(legs: List[Dict[str, Any]], bankroll: float) -> Optional[Dict[str, Any]]:
    """Solve a bounded two-way discrete plan when local neighborhoods fail."""
    if len(legs) != 2:
        return None
    bounds = []
    for leg in legs:
        increment = float(leg.get("stake_increment") or 0.01)
        minimum = float(leg.get("min_stake") or 0)
        cap_raw = leg.get("constraint_cap")
        cap = bankroll if cap_raw is None else min(float(cap_raw), bankroll)
        first = max(increment, ceil(minimum / increment - 1e-10) * increment)
        last = floor(cap / increment + 1e-10) * increment
        count = max(0, int(floor((last - first) / increment + 1e-9)) + 1)
        bounds.append((first, last, increment, count))
    anchor_index = 0 if bounds[0][3] <= bounds[1][3] else 1
    if bounds[anchor_index][3] > 20000:
        return None
    other_index = 1 - anchor_index
    first, _, increment, count = bounds[anchor_index]
    other_first, other_last, other_increment, _ = bounds[other_index]
    anchor_odds = float(legs[anchor_index]["net_decimal"])
    other_odds = float(legs[other_index]["net_decimal"])
    best = None
    for offset in range(count):
        anchor = first + offset * increment
        lower = max(other_first, anchor / (other_odds - 1.0))
        upper = min(other_last, bankroll - anchor, anchor * (anchor_odds - 1.0))
        low_multiple = floor(lower / other_increment + 1e-10) + 1
        high_multiple = ceil(upper / other_increment - 1e-10) - 1
        if low_multiple > high_multiple:
            continue
        equal = anchor * anchor_odds / other_odds
        equal_multiple = round(equal / other_increment)
        for multiple in {low_multiple, high_multiple, max(low_multiple, min(high_multiple, equal_multiple))}:
            other = multiple * other_increment
            adjusted = [dict(leg) for leg in legs]
            adjusted[anchor_index]["stake"] = anchor
            adjusted[other_index]["stake"] = other
            plan = _direct_discrete_plan(adjusted, bankroll)
            if not plan or not plan.get("strict_arbitrage_after_rounding"):
                continue
            if best is None or float(plan["minimum_profit"]) > float(best["minimum_profit"]):
                best = plan
    return best


def _direct_discrete_plan(legs: List[Dict[str, Any]], bankroll: float) -> Optional[Dict[str, Any]]:
    """Verify an already-discrete plan without exponential enumeration."""
    if len(legs) < 2:
        return None
    normalized = []
    total = 0.0
    for leg in legs:
        stake = float(leg["stake"])
        odds = float(leg["net_decimal"])
        increment = _constraint(leg.get("stake_increment"), 0.01, positive=True)
        minimum = _constraint(leg.get("min_stake"), 0.0)
        cap = _constraint(leg.get("constraint_cap"), bankroll)
        if increment is None or minimum is None or cap is None:
            return None
        money_stake = round(stake, 2)
        if abs(stake - money_stake) > 1e-7:
            return None
        if abs(money_stake / increment - round(money_stake / increment)) > 1e-7:
            return None
        if money_stake <= 0 or money_stake < minimum - 1e-9 or money_stake > cap + 1e-9:
            return None
        total += money_stake
        normalized.append((leg, money_stake, odds, increment, minimum, cap))
    if total > bankroll + 1e-9:
        return None
    rows = []
    profits = []
    for leg, stake, odds, increment, minimum, cap in normalized:
        payout = round(stake * odds, 2)
        profit = round(payout - total, 2)
        profits.append(profit)
        rows.append({
            "outcome": leg["outcome"], "book": leg["book"], "decimal": odds,
            "stake": stake, "stake_increment": increment, "min_stake": minimum,
            "constraint_cap": cap, "payout_if_wins": payout, "profit_if_wins": profit,
        })
    minimum_profit = min(profits)
    return {
        "ok": True, "feasible": True, "places_bets": False,
        "optimization_scope": "direct discrete plan verification",
        "global_optimum_claimed": False, "combinations_checked": 1,
        "bankroll": round(bankroll, 2), "allocated_stake": round(total, 2),
        "unused_bankroll": round(bankroll - total, 2),
        "minimum_profit": minimum_profit,
        "strict_arbitrage_after_rounding": minimum_profit > 0,
        "legs": rows,
    }


def _optimized_plan(legs: List[Dict[str, Any]], bankroll: float) -> Dict[str, Any]:
    plans: List[Dict[str, Any]] = []
    direct = _direct_discrete_plan(legs, bankroll)
    if direct:
        plans.append(direct)
    try:
        neighborhood = optimize_rounding_neighborhood(legs, bankroll=bankroll)
        if neighborhood.get("feasible"):
            plans.append(neighborhood)
    except StakeRoundingError:
        neighborhood = {"feasible": False, "reason": "ROUNDING_ERROR"}
    if not any(plan.get("strict_arbitrage_after_rounding") for plan in plans):
        exact_two_way = _two_way_exact_plan(legs, bankroll)
        if exact_two_way:
            plans.append(exact_two_way)

    # For two-way markets, re-anchor on each leg's nearby discrete stakes and
    # equalize payout from that anchor. This catches executable plans that sit
    # outside the floor/ceil neighborhood of a cap-scaled proportional target.
    if len(legs) == 2:
        for anchor_index in (0, 1):
            anchor_leg = legs[anchor_index]
            other_index = 1 - anchor_index
            increment = float(anchor_leg.get("stake_increment") or 0.01)
            target = float(anchor_leg["stake"])
            minimum = float(anchor_leg.get("min_stake") or 0)
            cap = anchor_leg.get("constraint_cap")
            cap_value = bankroll if cap is None else float(cap)
            anchors = {
                floor(target / increment + 1e-10) * increment,
                ceil(target / increment - 1e-10) * increment,
                ceil(minimum / increment - 1e-10) * increment,
                floor(cap_value / increment + 1e-10) * increment,
            }
            for anchor in anchors:
                if anchor <= 0 or anchor < minimum - 1e-9 or anchor > cap_value + 1e-9:
                    continue
                adjusted = [dict(leg) for leg in legs]
                adjusted[anchor_index]["stake"] = anchor
                adjusted[other_index]["stake"] = (
                    anchor * float(anchor_leg["net_decimal"])
                    / float(adjusted[other_index]["net_decimal"])
                )
                try:
                    candidate = optimize_rounding_neighborhood(adjusted, bankroll=bankroll)
                except StakeRoundingError:
                    continue
                if candidate.get("feasible"):
                    plans.append(candidate)
    if plans:
        return max(plans, key=lambda plan: (
            bool(plan.get("strict_arbitrage_after_rounding")),
            float(plan.get("minimum_profit") or float("-inf")),
        ))
    return neighborhood


def _rounded_quote_plan(
    selected: Mapping[str, Mapping[str, Any]], outcomes: Iterable[str], bankroll: float,
) -> tuple[float, Dict[str, float], Optional[Dict[str, Any]], Optional[str]]:
    ordered = sorted(outcomes)
    implied = sum(1.0 / selected[outcome]["net_decimal"] for outcome in ordered)
    if implied <= 0:
        return implied, {}, None, "INVALID_IMPLIED_SUM"
    unrounded = {
        outcome: bankroll * (1.0 / selected[outcome]["net_decimal"]) / implied
        for outcome in ordered
    }
    cap_scales = [1.0]
    for outcome in ordered:
        cap = _cap(selected[outcome].get("limit"))
        if cap is not None:
            cap_scales.append(cap / unrounded[outcome])
    scale = min(cap_scales)
    target_stakes = {outcome: stake * scale for outcome, stake in unrounded.items()}
    legs = [{
        "outcome": outcome,
        "book": selected[outcome]["book"],
        "stake": target_stakes[outcome],
        "net_decimal": selected[outcome]["net_decimal"],
        "constraint_cap": _cap(selected[outcome].get("limit")),
        "min_stake": selected[outcome].get("min_stake") or 0,
        "stake_increment": selected[outcome].get("stake_increment") or 0.01,
    } for outcome in ordered]
    try:
        return implied, unrounded, _optimized_plan(legs, bankroll), None
    except (StakeRoundingError, ArithmeticError, ValueError):
        return implied, unrounded, None, "ROUNDING_ERROR"


def scan_market(*, market_id: str, event: str, market: str, quotes: Iterable[Mapping[str, Any]],
                bankroll: float = 1000.0, expected_outcomes: Optional[Iterable[str]] = None,
                rules_status: str = "unknown", context: Optional[Mapping[str, Any]] = None,
                commence_time: Optional[str] = None, books: Any = None) -> Optional[Dict[str, Any]]:
    bankroll = float(bankroll)
    if not isfinite(bankroll) or bankroll <= 0:
        raise ArbValidationError("bankroll must be positive")
    allowed = _parse_books(books)
    expected = [str(x).strip() for x in (expected_outcomes or []) if str(x).strip()]
    expected_set = set(expected)
    best: Dict[str, Dict[str, Any]] = {}
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    seen_books = set()
    valid_quotes = 0
    for raw in quotes or []:
        outcome = str(raw.get("outcome") or "").strip(); book = str(raw.get("book") or "").strip()
        if not outcome or not book: continue
        if allowed and book.lower() not in allowed: continue
        try:
            d = _quote_decimal(raw); net_d = _net_decimal(d, float(raw.get("commission_rate") or 0.0))
        except (TypeError, ValueError, ArbValidationError):
            continue
        if not _quote_constraints_usable(raw, bankroll):
            continue
        valid_quotes += 1; seen_books.add(book)
        candidate = {"outcome": outcome, "book": book,
                     "american": raw.get("american") if raw.get("american") is not None else round(decimal_to_american(d), 2),
                     "decimal": d, "net_decimal": net_d, "commission_rate": float(raw.get("commission_rate") or 0.0),
                     "provider": raw.get("provider"), "last_update": raw.get("last_update"), "link": raw.get("link"),
                     "limit": raw.get("limit"), "min_stake": raw.get("min_stake"), "stake_increment": raw.get("stake_increment")}
        candidates.setdefault(outcome, []).append(candidate)
        if best.get(outcome) is None or net_d > best[outcome]["net_decimal"]: best[outcome] = candidate
    if expected_set:
        missing = sorted(expected_set - set(best)); extra = sorted(set(best) - expected_set); complete = not missing and not extra
    else:
        missing, extra = [], []; complete = len(best) >= 2; expected_set = set(best)
    if len(best) < 2: return None

    # The best headline price is not always the best executable price. Search a
    # bounded set of quote combinations so a capped or discretely unusable top
    # quote cannot hide a safe executable plan at the same outcome.
    if complete:
        ordered_outcomes = sorted(expected_set)
        pools = [
            sorted(candidates[outcome], key=lambda quote: (
                float(quote.get("min_stake") or 0), -quote["net_decimal"],
            ))
            for outcome in ordered_outcomes
        ]
        executable_choice = None
        combinations = product(*pools)
        if len(ordered_outcomes) > 2:
            combinations = islice(combinations, 4096)
        for combination in combinations:
            selected = dict(zip(ordered_outcomes, combination))
            combo_implied, _, combo_plan, _ = _rounded_quote_plan(selected, ordered_outcomes, bankroll)
            if combo_implied >= 1.0 or not combo_plan or not combo_plan.get("feasible"):
                continue
            if not combo_plan.get("strict_arbitrage_after_rounding"):
                continue
            score = (float(combo_plan.get("minimum_profit") or 0), -combo_implied)
            if executable_choice is None or score > executable_choice[0]:
                executable_choice = (score, selected)
        if executable_choice is not None:
            best = executable_choice[1]

    implied_sum = sum(1.0 / best[o]["net_decimal"] for o in sorted(expected_set) if o in best)
    math_arb = bool(complete and implied_sum < 1.0)
    normalized_rules_status = str(rules_status or "unknown").strip().lower()
    rules_compatible = normalized_rules_status == "compatible"
    theoretical_return = (1.0 / implied_sum - 1.0) if implied_sum > 0 else -1.0
    stake_rows: List[Dict[str, Any]] = []
    executable = False
    unused_bankroll = round(bankroll, 2)
    allocated_stake = 0.0
    rounding_reason = None
    if complete and implied_sum > 0:
        _, unrounded, rounded_plan, rounding_reason = _rounded_quote_plan(best, expected_set, bankroll)
        if rounded_plan and rounded_plan.get("feasible"):
            executable = bool(math_arb and rounded_plan.get("strict_arbitrage_after_rounding"))
            allocated_stake = float(rounded_plan.get("allocated_stake") or 0)
            unused_bankroll = float(rounded_plan.get("unused_bankroll") or 0)
            by_outcome = {leg["outcome"]: leg for leg in rounded_plan.get("legs") or []}
            for outcome in sorted(expected_set):
                q = best[outcome]
                plan = by_outcome.get(outcome) or {}
                stake = float(plan.get("stake") or round(unrounded[outcome], 2))
                payout = float(plan.get("payout_if_wins") if plan.get("payout_if_wins") is not None else stake * q["net_decimal"])
                profit = float(plan.get("profit_if_wins") if plan.get("profit_if_wins") is not None else payout - allocated_stake)
                stake_rows.append({"outcome": outcome, "book": q["book"], "american": q["american"],
                                   "decimal": round(q["decimal"], 6), "net_decimal": round(q["net_decimal"], 6),
                                   "stake": stake, "payout_if_wins": round(payout, 2), "profit_if_wins": round(profit, 2),
                                   "last_update": q.get("last_update"), "provider": q.get("provider"), "link": q.get("link"),
                                   "limit": q.get("limit")})
            if math_arb and not executable:
                rounding_reason = "NOT_EXECUTABLE_AFTER_ROUNDING"
        else:
            rounding_reason = (rounded_plan or {}).get("reason") or rounding_reason or "ROUNDING_INFEASIBLE"
            simple = {o: round(v, 2) for o, v in unrounded.items()}
            allocated_stake = round(sum(simple.values()), 2)
            unused_bankroll = round(max(0.0, bankroll - allocated_stake), 2)
            for outcome in sorted(expected_set):
                q = best[outcome]; stake = simple[outcome]; payout = stake * q["net_decimal"]
                stake_rows.append({"outcome": outcome, "book": q["book"], "american": q["american"],
                                   "decimal": round(q["decimal"], 6), "net_decimal": round(q["net_decimal"], 6),
                                   "stake": stake, "payout_if_wins": round(payout, 2),
                                   "profit_if_wins": round(payout - allocated_stake, 2),
                                   "last_update": q.get("last_update"), "provider": q.get("provider"),
                                   "link": q.get("link"), "limit": q.get("limit")})
    is_arb = bool(math_arb and rules_compatible and executable)
    min_profit = min((r["profit_if_wins"] for r in stake_rows), default=None)
    min_payout = min((r["payout_if_wins"] for r in stake_rows), default=None)
    validation = {
        "outcome_coverage": "complete" if complete else "incomplete",
        "rules_status": normalized_rules_status,
        "rules_compatible": rules_compatible,
        "missing_outcomes": missing,
        "extra_outcomes": extra,
        "executable": executable,
    }
    settlement_validation = (context or {}).get("settlement_validation")
    if isinstance(settlement_validation, Mapping):
        if settlement_validation.get("reason"):
            validation["settlement_reason"] = str(settlement_validation["reason"])
        if settlement_validation.get("missing_books"):
            validation["missing_books"] = sorted({
                str(book) for book in settlement_validation["missing_books"] if str(book)
            })
    if math_arb and not rules_compatible:
        validation["qualification_reason"] = "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"
    elif math_arb and not executable:
        validation["qualification_reason"] = rounding_reason or "NOT_EXECUTABLE_AFTER_ROUNDING"
    return {"market_id": market_id, "event": event, "market": market, "commence_time": commence_time,
            "math_arb": math_arb, "arb": is_arb, "executable": executable,
            "opportunity_type": "surebet",
            "validation": validation,
            "sum_implied": round(implied_sum, 8), "margin_pct": round(theoretical_return * 100.0, 4),
            "hold_pct": round((implied_sum - 1.0) * 100.0, 4), "bankroll": round(bankroll, 2), "legs": stake_rows,
            "allocated_stake": round(allocated_stake, 2), "unused_bankroll": unused_bankroll,
            "minimum_payout": min_payout if math_arb else None, "minimum_profit": min_profit if math_arb else None,
            "n_quotes": valid_quotes, "n_books": len(seen_books), "outcomes": sorted(expected_set), "context": dict(context or {})}


def scan_all(payload: Mapping[str, Any]) -> Dict[str, Any]:
    from middle_engine import detect_middles
    bankroll = float(payload.get("bankroll") or 1000.0)
    books = payload.get("books") if payload.get("books") is not None else payload.get("bookmakers")
    allowed = _parse_books(books)
    hits: List[Dict[str, Any]] = []; detected: List[Dict[str, Any]] = []; near: List[Dict[str, Any]] = []; rejected: List[Dict[str, Any]] = []
    exchange_pending: List[Dict[str, Any]] = []
    events = list(payload.get("events") or [])
    for item in events:
        market = str(item.get("market") or "unknown")
        if market.endswith("_lay"):
            quotes = [
                quote for quote in (item.get("quotes") or [])
                if not allowed or str(quote.get("book") or "").strip().lower() in allowed
            ]
            if not quotes:
                continue
            exchange_pending.append({
                "market_id": str(item.get("id") or item.get("market_id") or ""),
                "event": str(item.get("event") or ""),
                "market": market,
                "commence_time": item.get("commence_time"),
                "n_quotes": len(quotes),
                "books": sorted({str(q.get("book") or "") for q in quotes if q.get("book")}),
                "validation": {
                    "rules_status": "not_evaluated",
                    "rules_compatible": False,
                    "qualification_reason": "EXCHANGE_LAY_REQUIRES_BACK_LAY_ENGINE",
                },
            })
            continue
        row = scan_market(market_id=str(item.get("id") or item.get("market_id") or ""), event=str(item.get("event") or ""),
                          market=market, quotes=item.get("quotes") or [], bankroll=bankroll,
                          expected_outcomes=item.get("expected_outcomes"), rules_status=str(item.get("rules_status") or "unknown"),
                          context=item.get("context") or {}, commence_time=item.get("commence_time"), books=books)
        if row is None: continue
        rules_status = str(row["validation"]["rules_status"]).lower()
        invalid = row["validation"]["outcome_coverage"] != "complete" or rules_status == "incompatible"
        if invalid: rejected.append(row)
        elif row["arb"]: hits.append(row)
        elif row["math_arb"]: detected.append(row)
        else: near.append(row)
    middles = detect_middles(events, bankroll=bankroll, books=books)
    verified_signatures = {_candidate_signature(row) for row in hits}
    detected = [row for row in detected if _candidate_signature(row) not in verified_signatures]
    rejected = [
        row for row in rejected
        if not row.get("math_arb") or _candidate_signature(row) not in verified_signatures
    ]
    n_held_unverified = len(detected) + sum(bool(row.get("math_arb")) for row in rejected)
    key = lambda r: (r["minimum_profit"] or -10**9, r["margin_pct"])
    hits.sort(key=key, reverse=True); detected.sort(key=key, reverse=True); near.sort(key=lambda r: r["sum_implied"])
    return {"ok": True, "places_bets": False, "bankroll": bankroll,
            "n_markets": len(hits)+len(detected)+len(near)+len(rejected)+len(exchange_pending), "n_arbs": len(hits),
            "n_detected_unverified": len(detected), "n_rejected": len(rejected),
            "n_held_unverified": n_held_unverified,
            "n_exchange_pending": len(exchange_pending),
            "n_middles": len(middles),
            "hits": hits, "detected_unverified": detected[:100], "near": near[:100],
            "rejected": rejected[:100], "exchange_pending": exchange_pending[:100],
            "middles": middles}
