from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sport(row: Dict[str, Any]) -> str:
    try:
        return str(row.get("sport") or "unknown").strip().lower()
    except Exception:
        return "unknown"


def _slate(row: Dict[str, Any]) -> str:
    try:
        return str(row.get("slate_date") or row.get("slateDate") or "unknown")
    except Exception:
        return "unknown"


def apply(history: Any):
    if getattr(history, "_INQSI_PARLAY_HISTORY_PATCH_APPLIED", False):
        return history

    def store_parlay_build(row: Dict[str, Any], mode: Optional[str] = None) -> Dict[str, Any]:
        if getattr(history, "PULLS", None) is None:
            return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
        sport = _sport(row or {})
        slate = _slate(row or {})
        created = _now()
        payload = dict(row or {})
        payload["storedAt"] = created
        payload["storeMode"] = mode or "parlay_build"
        item = history.ddb_safe({
            "PK": f"PARLAY_BUILDS#{sport}#{slate}",
            "SK": f"BUILD#{created}",
            "record_type": "parlay_build",
            "sport": sport,
            "slate_date": slate,
            "mode": mode or "parlay_build",
            "created_at": created,
            "data": payload,
        })
        history.PULLS.put_item(Item=item)
        latest = dict(item)
        latest["PK"] = f"PARLAY_BUILDS#{sport}#{slate}#LATEST"
        latest["SK"] = "LATEST"
        history.PULLS.put_item(Item=latest)
        return {"ok": True, "pk": item["PK"], "sk": item["SK"], "latestPk": latest["PK"], "latestSk": latest["SK"]}

    def latest_parlay_build(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if getattr(history, "PULLS", None) is None:
            return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
        params = params or {}
        sport = _sport(params)
        slate = _slate(params)
        try:
            resp = history.PULLS.get_item(Key={"PK": f"PARLAY_BUILDS#{sport}#{slate}#LATEST", "SK": "LATEST"})
            item = resp.get("Item") or {}
            return item.get("data") or {"ok": False, "reason": "not_found", "sport": sport, "slate_date": slate}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "sport": sport, "slate_date": slate}

    history.store_parlay_build = store_parlay_build
    history.latest_parlay_build = latest_parlay_build
    history._INQSI_PARLAY_HISTORY_PATCH_APPLIED = True
    return history
