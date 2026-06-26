from __future__ import annotations

from typing import Any, Dict, List, Set

import inqsi_pull_history as history

MIN_PULLS = 12
DEADLINE_REASONS = {"MISSED_2_HOUR_BUILD_DEADLINE"}


def _unique(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        game_id = str(row.get("gameId") or row.get("game_id") or row.get("gameKey") or "")
        if not game_id or game_id in seen:
            continue
        seen.add(game_id)
        out.append(row)
    return out


def _leg_from_signal(signal: Dict[str, Any], force_side: str | None = None) -> Dict[str, Any]:
    side = force_side or signal.get("selectedSide") or "home"
    side_signal = signal.get("homeSignal") if side == "home" else signal.get("awaySignal")
    if not isinstance(side_signal, dict):
        side_signal = {}
    selection = signal.get("homeTeam") if side == "home" else signal.get("awayTeam")
    return {
        "gameId": signal.get("gameId"),
        "gameKey": signal.get("gameKey"),
        "selection": selection,
        "side": side,
        "grade": side_signal.get("grade") or signal.get("grade") or "BASELINE",
        "score": side_signal.get("score") or signal.get("score") or 0,
        "tags": side_signal.get("tags") or signal.get("tags") or [],
        "commenceTime": signal.get("commenceTime"),
        "homeTeam": signal.get("homeTeam"),
        "awayTeam": signal.get("awayTeam"),
    }


def build_baseline(sport: str, slate_date: str | None = None, previous: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if previous and previous.get("reason") in DEADLINE_REASONS:
        return previous
    signal_report = history.signals({"sport": sport, "slate_date": slate_date})
    pull_count = int(signal_report.get("pullCount") or 0)
    slate = signal_report.get("slate_date") or slate_date or history.today()
    if pull_count < MIN_PULLS:
        return previous or {
            "ok": True,
            "sport": sport,
            "slate_date": slate,
            "pullCount": pull_count,
            "buildStatus": "NO_BUILD",
            "reason": "WAITING_FOR_12TH_PULL",
            "minimumParlayPulls": MIN_PULLS,
        }

    signals = _unique(signal_report.get("signals") or [])
    signals.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
    if len(signals) < 3:
        result = previous or {}
        result.update({
            "ok": True,
            "sport": sport,
            "slate_date": slate,
            "pullCount": pull_count,
            "buildStatus": "NO_BUILD",
            "reason": "NOT_ENOUGH_UNIQUE_GAMES_FOR_3_LEG",
            "uniqueSignalCount": len(signals),
            "minimumParlayPulls": MIN_PULLS,
            "message": "Cannot build a three-leg parlay without three unique games or matches.",
        })
        return result

    selected = signals[:3]
    combos = []
    for mask in range(8):
        legs = []
        score = 0.0
        for index, signal in enumerate(selected):
            side = "home" if (mask & (1 << index)) else "away"
            leg = _leg_from_signal(signal, side)
            legs.append(leg)
            score += float(leg.get("score") or 0)
        combos.append({"rank": 0, "score": round(score / 3.0, 2), "legs": legs})
    combos.sort(key=lambda row: row.get("score", 0), reverse=True)
    for rank, combo in enumerate(combos, 1):
        combo["rank"] = rank
        combo["top3"] = rank <= 3

    strong_count = sum(1 for row in selected if row.get("grade") in {"STRONG_SOLID", "SOLID", "MLB_STRONG", "MLB_LEAN"})
    quality = "QUALIFIED" if strong_count >= 2 else "BASELINE_12_SNAPSHOT"
    return {
        "ok": True,
        "sport": signal_report.get("sport") or sport,
        "slate_date": slate,
        "pullCount": pull_count,
        "minimumParlayPulls": MIN_PULLS,
        "buildStatus": "BUILT",
        "buildQuality": quality,
        "reason": "BUILT_FROM_TOP_AVAILABLE_SIGNALS_AFTER_12_SNAPSHOTS",
        "structure": "THREE_UNIQUE_GAMES_AFTER_12_SNAPSHOTS",
        "officialStrength": "STRONG" if quality == "QUALIFIED" else "BASELINE_NOT_STRONG",
        "message": "Built after 12 snapshots from top available ranked signals. Leg grades remain honest and are not promoted to strong.",
        "selectedStrongCount": strong_count,
        "legs": selected,
        "rankedCombos": combos,
        "previousNoBuildReason": (previous or {}).get("reason"),
        "fallbackApplied": True,
    }


def apply_if_needed(result: Dict[str, Any], sport: str, slate_date: str | None = None) -> Dict[str, Any]:
    if result.get("buildStatus") == "BUILT":
        return result
    if result.get("reason") in DEADLINE_REASONS:
        return result
    return build_baseline(sport, slate_date or result.get("slate_date"), previous=result)
