from __future__ import annotations

from typing import Any, Dict, List, Optional

VERSION = "INQSI-PULL-HISTORY-ROUTES-v1-restored-import-contract"


def _history():
    # Lazy import avoids a circular import when inqsi_pull_history re-exports
    # handle_pull_history_route from this module at the end of its definition.
    import inqsi_pull_history

    return inqsi_pull_history


def _params(query: Optional[Dict[str, Any]], body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(query, dict):
        out.update(query)
    if isinstance(body, dict):
        out.update(body)
    return out


def _required_sport(params: Dict[str, Any]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    history = _history()
    sport = history.sport_key(params.get("sport") or params.get("sport_key"))
    if not sport:
        return None, {"ok": False, "error": "sport_required", "message": "sport or sport_key is required"}
    if sport not in history.SUPPORTED:
        return None, {
            "ok": False,
            "error": "unsupported_sport",
            "sport": sport,
            "supportedSports": sorted(history.SUPPORTED),
        }
    return sport, None


def _latest(params: Dict[str, Any]) -> Dict[str, Any]:
    history = _history()
    sport, error = _required_sport(params)
    if error:
        return error
    pulls = history.query_pulls(sport, params.get("slate_date") or params.get("date"), params.get("limit") or 500)
    latest = pulls[-1] if pulls else None
    return {
        "ok": True,
        "version": VERSION,
        "architecture": "15_min_pull_history",
        "sport": sport,
        "slate_date": (latest or {}).get("slate_date") or params.get("slate_date") or params.get("date") or history.today(),
        "pullCount": len(pulls),
        "latest": latest,
        "latestPull": latest,
        "message": None if latest else "No stored pull history found for this sport and slate.",
    }


def _data_quality(params: Dict[str, Any]) -> Dict[str, Any]:
    history = _history()
    requested = params.get("sport") or params.get("sport_key")
    sports: List[str] = [history.sport_key(requested)] if requested else sorted(history.SUPPORTED)
    rows = []
    for sport in sports:
        if sport not in history.SUPPORTED:
            continue
        try:
            pulls = history.query_pulls(sport, params.get("slate_date") or params.get("date"), params.get("limit") or 500)
            latest = pulls[-1] if pulls else {}
            intervals = [int(p.get("interval_minutes") or 15) for p in pulls if isinstance(p, dict)]
            game_counts = [len(p.get("games") or []) for p in pulls if isinstance(p, dict)]
            rows.append(
                {
                    "sport": sport,
                    "pullCount": len(pulls),
                    "latestPullAt": latest.get("pulled_at"),
                    "latestGameCount": len(latest.get("games") or []),
                    "minimumGameCount": min(game_counts) if game_counts else 0,
                    "maximumGameCount": max(game_counts) if game_counts else 0,
                    "intervalMinutes": intervals[-1] if intervals else 15,
                    "signalReady": len(pulls) >= 2,
                    "parlayReadyByDepth": len(pulls) >= history.MIN_PARLAY_PULLS,
                }
            )
        except Exception as exc:
            rows.append({"sport": sport, "ok": False, "error": str(exc)})
    return {
        "ok": True,
        "version": VERSION,
        "architecture": "15_min_pull_history",
        "slate_date": params.get("slate_date") or params.get("date") or history.today(),
        "sports": rows,
    }


def _build_pull_history_parlay(params: Dict[str, Any]) -> Dict[str, Any]:
    history = _history()
    sport, error = _required_sport(params)
    if error:
        return error
    signal_report = history.signals({**params, "sport": sport})
    readiness = history.readiness({**params, "sport": sport})
    if readiness.get("status") != "READY":
        return {
            "ok": True,
            "version": VERSION,
            "sport": sport,
            "buildStatus": "REFUSED",
            "reason": readiness.get("status") or "NOT_READY",
            "message": "InQsi refused to force a parlay because pull-history readiness requirements were not met.",
            "readiness": readiness,
            "legs": [],
            "rankedCombos": [],
        }

    eligible = [
        row
        for row in signal_report.get("signals") or []
        if row.get("grade") in {"STRONG_SOLID", "SOLID", "COIN_FLIP"}
        and "CHAOS" not in set(row.get("tags") or [])
    ]
    unique = []
    seen = set()
    for row in eligible:
        key = row.get("gameId") or row.get("gameKey")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)
        if len(unique) == 3:
            break

    if len(unique) != 3 or not any(row.get("grade") == "STRONG_SOLID" for row in unique):
        return {
            "ok": True,
            "version": VERSION,
            "sport": sport,
            "buildStatus": "REFUSED",
            "reason": "INSUFFICIENT_UNIQUE_QUALIFIED_LEGS",
            "message": "InQsi refused to force a three-leg parlay.",
            "readiness": readiness,
            "legs": [],
            "rankedCombos": [],
        }

    legs = [
        {
            "gameId": row.get("gameId"),
            "gameKey": row.get("gameKey"),
            "selection": row.get("selection"),
            "selectedSide": row.get("selectedSide"),
            "grade": row.get("grade"),
            "score": row.get("score"),
            "tags": row.get("tags") or [],
        }
        for row in unique
    ]
    return {
        "ok": True,
        "version": VERSION,
        "sport": sport,
        "buildStatus": "BUILT",
        "structure": "PULL_HISTORY_THREE_LEG_NO_FORCE",
        "readiness": readiness,
        "legs": legs,
        "rankedCombos": [{"rank": 1, "legs": legs}],
    }


def _scan_pull_history_slip(params: Dict[str, Any]) -> Dict[str, Any]:
    history = _history()
    sport, error = _required_sport(params)
    if error:
        return error
    legs = params.get("legs") or []
    if not isinstance(legs, list) or not legs:
        return {"ok": False, "error": "legs_required", "message": "Provide at least one leg to scan."}
    report = history.signals({**params, "sport": sport})
    by_game: Dict[str, Dict[str, Any]] = {}
    for row in report.get("signals") or []:
        for key in (row.get("gameId"), row.get("gameKey")):
            if key:
                by_game[str(key)] = row
    scanned = []
    for leg in legs:
        leg = leg if isinstance(leg, dict) else {"selection": str(leg)}
        key = str(leg.get("gameId") or leg.get("game_id") or leg.get("gameKey") or leg.get("game_key") or "")
        signal = by_game.get(key)
        tags = set((signal or {}).get("tags") or [])
        grade = (signal or {}).get("grade") or "UNMATCHED"
        risk = "HIGH" if not signal or grade == "FRAGILE" or "CHAOS" in tags else "MEDIUM" if grade == "COIN_FLIP" else "LOW"
        scanned.append({"leg": leg, "matchedSignal": signal, "risk": risk})
    overall = "HIGH" if any(row["risk"] == "HIGH" for row in scanned) else "MEDIUM" if any(row["risk"] == "MEDIUM" for row in scanned) else "LOW"
    return {
        "ok": True,
        "version": VERSION,
        "sport": sport,
        "overallRisk": overall,
        "legs": scanned,
        "pullCount": report.get("pullCount"),
    }


def handle_pull_history_route(
    path: str,
    method: str,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Dispatch documented pull-history routes or return None for unrelated APIs."""
    history = _history()
    path = (path or "/").rstrip("/") or "/"
    method = (method or "GET").upper()
    params = _params(query, body)

    if path in {"/v1/inqsi/algorithm/sports", "/v1/algorithm/sports"} and method == "GET":
        return {**history.supported_sports(), "version": VERSION}
    if path in {"/v1/inqsi/markets/normalize-pull", "/v1/markets/normalize-pull"} and method == "POST":
        return {**history.normalize_pull(params), "version": VERSION}
    if path in {"/v1/inqsi/pulls", "/v1/pulls"} and method == "POST":
        return {**history.store_pull(params), "version": VERSION}
    if path in {"/v1/inqsi/pulls/latest", "/v1/pulls/latest"} and method == "GET":
        return _latest(params)
    if path in {"/v1/inqsi/algorithm/signals", "/v1/algorithm/signals"} and method == "GET":
        return {**history.signals(params), "version": VERSION}
    if path in {"/v1/inqsi/algorithm/readiness", "/v1/algorithm/readiness"} and method in {"GET", "POST"}:
        return {**history.readiness(params), "version": VERSION}
    if path in {"/v1/inqsi/parlays/build-pull-history", "/v1/parlays/build-pull-history"} and method == "POST":
        return _build_pull_history_parlay(params)
    if path in {"/v1/inqsi/slips/scan-pull-history", "/v1/slips/scan-pull-history"} and method == "POST":
        return _scan_pull_history_slip(params)
    if path in {"/v1/inqsi/monitoring/pull-data-quality", "/v1/monitoring/pull-data-quality"} and method == "GET":
        return _data_quality(params)
    return None
