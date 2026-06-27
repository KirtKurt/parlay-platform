from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SLATE_TZ = ZoneInfo("America/New_York")


def today_et() -> str:
    return datetime.now(SLATE_TZ).date().isoformat()


def slate_date_et(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(SLATE_TZ).date().isoformat()
    except Exception:
        return today_et()


def apply_to_history(history_module):
    if history_module is None:
        return
    history_module.today = today_et
    history_module.slate_date = slate_date_et


def apply_to_odds(odds_module):
    if odds_module is None:
        return
    odds_module.today = today_et
