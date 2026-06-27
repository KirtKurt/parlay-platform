from datetime import datetime, timezone
import os
from typing import Any, Dict, Optional

WINDOW_MINUTES = int(os.environ.get("INQSI_PULL_DEDUPE_WINDOW_MINUTES", "10"))


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


def apply(history_module: Any) -> None:
    if history_module is None or getattr(history_module, "_inqsi_pull_dedupe_installed", False):
        return
    original_store_pull = history_module.store_pull

    def store_pull(body: Dict[str, Any]) -> Dict[str, Any]:
        sport = history_module.sport_key((body or {}).get("sport") or (body or {}).get("sport_key"))
        pulled_at = _parse((body or {}).get("pulled_at") or (body or {}).get("asof")) or datetime.now(timezone.utc)
        slate = (body or {}).get("slate_date") or history_module.slate_date(pulled_at.isoformat())
        try:
            existing = history_module.query_pulls(sport, slate, 3)
            latest = _parse((existing[-1] or {}).get("pulled_at")) if existing else None
            if latest and abs((pulled_at - latest).total_seconds()) < WINDOW_MINUTES * 60:
                return {
                    "ok": True,
                    "deduped": True,
                    "message": "Skipped duplicate pull inside dedupe window.",
                    "stored": {
                        "pk": f"PULLS#{sport}#{slate}",
                        "sk": None,
                        "pull_id": None,
                        "game_count": len((body or {}).get("games") or []),
                    },
                    "pull": {
                        "sport": sport,
                        "slate_date": slate,
                        "pulled_at": pulled_at.isoformat(),
                        "games": (body or {}).get("games") or [],
                    },
                }
        except Exception:
            pass
        return original_store_pull(body)

    history_module.store_pull = store_pull
    history_module._inqsi_pull_dedupe_installed = True
