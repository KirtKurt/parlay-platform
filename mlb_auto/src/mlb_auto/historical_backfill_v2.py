from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .historical_backfill import run_historical_backfill as _run
from .storage import Store

ET = ZoneInfo('America/New_York')


def _learn_live_market_catalog(store: Store) -> list[str]:
    now = datetime.now(timezone.utc).astimezone(ET).date()
    keys: set[str] = set()
    for offset in (-1, 0, 1, 2):
        slate = (now + timedelta(days=offset)).isoformat()
        for row in store.query_predictions(slate):
            for key in row.get('discovered_market_keys') or []:
                if key:
                    keys.add(str(key))
    state = store.get_state('controller')
    keys.update(str(x) for x in (state.get('known_market_keys') or []) if x)
    if keys:
        store.put_state('controller', {**state, 'known_market_keys': sorted(keys), 'known_market_key_count': len(keys)})
    return sorted(keys)


def run_historical_backfill(days_per_run: int | None = None, max_games_per_run: int | None = None):
    store = Store()
    _learn_live_market_catalog(store)
    return _run(days_per_run=days_per_run, max_games_per_run=max_games_per_run)
