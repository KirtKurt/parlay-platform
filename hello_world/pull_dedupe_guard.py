from datetime import datetime, timezone
import os
from typing import Any, Dict, Optional, Tuple

SLOT_MINUTES = int(os.environ.get("INQSI_PULL_DEDUPE_SLOT_MINUTES", "15"))
VERSION = "INQSI-PULL-DEDUPE-v2-atomic-quarter-hour-slot"
RECORD_TYPE = "canonical_pull_slot_claim"


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _slot_start(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    minute = (value.minute // SLOT_MINUTES) * SLOT_MINUTES
    return value.replace(minute=minute, second=0, microsecond=0)


def _slot_key(sport: str, slate: str, pulled_at: datetime) -> Dict[str, str]:
    slot = _slot_start(pulled_at).isoformat()
    return {
        "PK": f"PULL_SLOT#{sport}#{slate}",
        "SK": f"SLOT#{slot}",
    }


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", {}) or {}
    return str((response.get("Error") or {}).get("Code") or "")


def _deduped_result(sport: str, slate: str, pulled_at: datetime, games: Any, key: Dict[str, str]) -> Dict[str, Any]:
    return {
        "ok": True,
        "deduped": True,
        "dedupeVersion": VERSION,
        "message": "Skipped duplicate canonical pull in the same quarter-hour slot.",
        "stored": {
            "pk": f"PULLS#{sport}#{slate}",
            "sk": None,
            "pull_id": None,
            "game_count": len(games or []),
        },
        "pull": {
            "sport": sport,
            "slate_date": slate,
            "pulled_at": pulled_at.isoformat(),
            "games": games or [],
        },
        "slotClaim": {**key, "claimed": False},
    }


def _latest_pull(history_module: Any, sport: str, slate: str) -> Optional[Dict[str, Any]]:
    # query_pulls is chronological. A limit of three returned the first three rows
    # of the day, so the old guard stopped deduplicating after the opening hour.
    existing = history_module.query_pulls(sport, slate, 500)
    return (existing[-1] or {}) if existing else None


def apply(history_module: Any) -> None:
    if history_module is None or getattr(history_module, "_inqsi_pull_dedupe_installed", False):
        return
    # New history runtimes atomically store the observation at the canonical
    # PULL#SLOT key and preserve raw variants there. Wrapping that writer with
    # the legacy, separately persisted PULL_SLOT marker recreates the exact
    # marker/observation crash gap this guard originally tried to avoid.
    if (
        getattr(history_module, "_INQSI_INTRINSIC_PULL_SLOT_IDEMPOTENCY", False)
        or (
            getattr(history_module, "PULL_SLOT_VERSION", None)
            and getattr(history_module, "PULL_HISTORY_INTEGRITY_VERSION", None)
            and callable(getattr(history_module, "canonicalize_pull_slots", None))
        )
    ):
        history_module._inqsi_pull_dedupe_installed = True
        history_module._inqsi_native_atomic_pull_slot_writer = True
        history_module.PULL_DEDUPE_VERSION = (
            getattr(history_module, "INTRINSIC_PULL_IDEMPOTENCY_VERSION", None)
            or getattr(history_module, "PULL_SLOT_VERSION", None)
            or VERSION
        )
        return
    original_store_pull = history_module.store_pull

    def store_pull(body: Dict[str, Any]) -> Dict[str, Any]:
        body = body or {}
        sport = history_module.sport_key(body.get("sport") or body.get("sport_key"))
        pulled_at = _parse(body.get("pulled_at") or body.get("asof")) or datetime.now(timezone.utc)
        slate = str(body.get("slate_date") or history_module.slate_date(pulled_at.isoformat()))
        games = body.get("games") or []
        key = _slot_key(sport, slate, pulled_at)
        table = getattr(history_module, "PULLS", None)
        marker_claimed = False

        if table is not None:
            marker = {
                **key,
                "record_type": RECORD_TYPE,
                "version": VERSION,
                "sport": sport,
                "slate_date": slate,
                "slot_start_utc": key["SK"].removeprefix("SLOT#"),
                "source_pull_at_utc": pulled_at.isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                table.put_item(
                    Item=marker,
                    ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
                )
                marker_claimed = True
            except Exception as exc:
                if _error_code(exc) == "ConditionalCheckFailedException":
                    return _deduped_result(sport, slate, pulled_at, games, key)
                # A slot-marker permission or transient error must not silently stop
                # collection. Fall back to a strongly consistent history comparison.

        if not marker_claimed:
            try:
                latest = _latest_pull(history_module, sport, slate)
                latest_at = _parse((latest or {}).get("pulled_at"))
                if latest_at and _slot_start(latest_at) == _slot_start(pulled_at):
                    return _deduped_result(sport, slate, pulled_at, games, key)
            except Exception:
                pass

        try:
            result = original_store_pull(body)
        except Exception:
            if marker_claimed:
                try:
                    table.delete_item(Key=key)
                except Exception:
                    pass
            raise

        if not isinstance(result, dict) or result.get("ok") is not True:
            if marker_claimed:
                try:
                    table.delete_item(Key=key)
                except Exception:
                    pass
            return result

        result = dict(result)
        result["dedupeVersion"] = VERSION
        result["slotClaim"] = {**key, "claimed": marker_claimed}
        return result

    history_module.store_pull = store_pull
    history_module._inqsi_pull_dedupe_installed = True
    history_module.PULL_DEDUPE_VERSION = VERSION
