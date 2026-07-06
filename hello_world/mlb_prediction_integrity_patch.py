from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

MAX_STALE_MINUTES = int(os.environ.get("INQSI_MLB_MAX_LOCK_STALENESS_MINUTES", "75"))
VERSION = "MLB-PREDICTION-INTEGRITY-v1-stale-pass-reversal-optimizer-guard"


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _sig(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    sig = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return sig if isinstance(sig, dict) else {}


def _selected(row: Dict[str, Any]) -> Dict[str, Any]:
    side = str(row.get("predictedSide") or "home").lower()
    if side not in {"home", "away"}:
        side = "home"
    return _sig(row, side)


def _market_edge(row: Dict[str, Any]) -> float:
    side = str(row.get("predictedSide") or "home").lower()
    if side not in {"home", "away"}:
        side = "home"
    sel = _sig(row, side)
    opp = _sig(row, "away" if side == "home" else "home")
    sp = _f(sel.get("marketConsensusProbability"), _f(sel.get("probLatest"), 0.5))
    op = _f(opp.get("marketConsensusProbability"), _f(opp.get("probLatest"), 1.0 - sp))
    return sp - op


def _lock_stale(lock: Dict[str, Any]) -> bool:
    if not isinstance(lock, dict) or not lock.get("locked"):
        return False
    if lock.get("staleLock") is True:
        return True
    lock_at = _dt(lock.get("lockAtUtc"))
    latest = _dt(lock.get("latestScoringPullAt"))
    if not lock_at or not latest:
        return True
    age = (lock_at - latest).total_seconds() / 60.0
    return age > MAX_STALE_MINUTES


def _tag(row: Dict[str, Any], *tags: str) -> None:
    row["tags"] = sorted(set([str(x) for x in (row.get("tags") or [])] + list(tags)))


def _suppress(row: Dict[str, Any], reason: str, bucket: str) -> Dict[str, Any]:
    row["officialPick"] = False
    row["officialPrediction"] = False
    row["accuracyTargetEligible"] = False
    row["actionablePick"] = False
    row["publicPick"] = None
    row["displayWinner"] = None
    row["displayBucket"] = bucket
    row["doNotUseAsWinnerPick"] = True
    row["predictionVisibility"] = "DIAGNOSTIC_ONLY" if bucket != "WATCHLIST_LEAN" else "WATCHLIST_ONLY"
    if bucket != "WATCHLIST_LEAN":
        row["actionability"] = bucket
        row["actionabilityReason"] = reason
    risks = list(row.get("actionabilityRiskReasons") or [])
    if reason not in risks:
        risks.append(reason)
    row["actionabilityRiskReasons"] = risks
    _tag(row, "NO_PICK", "NO_PICK_DISCIPLINE")
    return row


def _allow(row: Dict[str, Any]) -> Dict[str, Any]:
    row["publicPick"] = row.get("predictedWinner")
    row["displayWinner"] = row.get("predictedWinner")
    row["displayBucket"] = "OFFICIAL_PICK"
    row["doNotUseAsWinnerPick"] = False
    row["predictionVisibility"] = "OFFICIAL_ACTIONABLE"
    return row


def _protect_row(row: Dict[str, Any], lock_stale: bool) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return row
    sel = _selected(row)
    tags = set([str(x) for x in (row.get("tags") or [])] + [str(x) for x in (sel.get("tags") or [])])
    rev = int(_f(sel.get("reversalCount"), 0.0))
    edge = _market_edge(row)
    action = str(row.get("actionability") or "")
    reasons: List[str] = []

    if lock_stale:
        reasons.append("stale_pre_lock_data")
        row = _suppress(row, "stale_pre_lock_data", "NO_PICK_STALE_LOCK")
    elif action in {"PASS_NO_PICK", "NO_PICK_STALE_LOCK"} or str(row.get("confidenceTier") or "").lower() == "pass":
        reasons.append("pass_no_pick_not_user_facing_winner")
        row = _suppress(row, "pass_no_pick_not_user_facing_winner", "PASS_NO_PICK")
    elif action == "WATCHLIST_LEAN" or (row.get("actionablePick") is not True and row.get("officialPick") is not True):
        reasons.append("watchlist_not_official_pick")
        row = _suppress(row, "watchlist_not_official_pick", "WATCHLIST_LEAN")
    elif "REVERSAL" in tags and (rev >= 2 or edge < 0.08 or "COMPRESSED_MARKET" in tags or "UNCONFIRMED_RUN_LINE_MOVE" in tags):
        reasons.append("reversal_integrity_block")
        row = _suppress(row, "reversal_integrity_block", "NO_PICK_REVERSAL_RISK")
        _tag(row, "REVERSAL_RISK_BLOCK")
    else:
        row = _allow(row)

    row["winnerOptimizerProtection"] = {
        "applied": True,
        "version": VERSION,
        "action": "allow_official" if row.get("actionablePick") else "suppress_or_watchlist",
        "reasons": reasons,
        "selectedReversalCount": rev,
        "selectedMarketEdge": round(edge, 5),
    }
    if lock_stale:
        _tag(row, "STALE_LOCK", "DIAGNOSTIC_ONLY")
    return row


def enforce_result(result: Dict[str, Any], module: Any = None, store: bool = False) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    lock = result.get("slatePredictionLock") or {}
    stale = _lock_stale(lock)
    if isinstance(lock, dict):
        lock["maxLockStalenessMinutes"] = MAX_STALE_MINUTES
        lock["freshLockRequired"] = True
        lock["staleLock"] = stale
        lock["officialPicksAllowed"] = not stale
        if stale:
            lock["lockStatus"] = "STALE_LOCK_NO_ACTIONABLE_PICKS"
            lock["source"] = "stale_pre_lock_pull_history_no_official_picks"
        result["slatePredictionLock"] = lock

    rows = [_protect_row(dict(r), stale) for r in (result.get("predictions") or []) if isinstance(r, dict)]
    rows.sort(key=lambda r: (float(r.get("actionablePick") is True), float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
    for idx, row in enumerate(rows, 1):
        row["rank"] = idx
    result["predictions"] = rows
    result["actionablePickCount"] = len([r for r in rows if r.get("actionablePick")])
    result["watchlistCount"] = len([r for r in rows if r.get("displayBucket") == "WATCHLIST_LEAN"])
    result["noPickCount"] = len([r for r in rows if not r.get("actionablePick")])

    stack = result.get("winnerStackV2") or {}
    if isinstance(stack, dict):
        stack["integrityGuard"] = {"applied": True, "version": VERSION, "staleLockSuppressed": stale}
        stack["actionablePickCount"] = result["actionablePickCount"]
        stack["watchlistCount"] = result["watchlistCount"]
        stack["passNoPickCount"] = result["noPickCount"]
        result["winnerStackV2"] = stack

    if store and module is not None and hasattr(module, "_store_prediction"):
        stored = []
        for row in rows:
            try:
                row["stored"] = module._store_prediction(row)
                stored.append(row.get("stored"))
            except Exception as exc:
                row["storeError"] = str(exc)
        result["storedCount"] = len([s for s in stored if s and s.get("ok")])
    return result


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_PREDICTION_INTEGRITY_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        result = original(*args, **kwargs)
        return enforce_result(result, module=module, store=bool(kwargs.get("store")))

    module.predict_all = patched_predict_all
    module._INQSI_MLB_PREDICTION_INTEGRITY_APPLIED = True
    return module
