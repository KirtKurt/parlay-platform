from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import historical_backfill as _history
from .storage import Store

ET = ZoneInfo('America/New_York')


# MLB Stats occasionally reports a postponed/cancelled game with
# abstractGameState="Final" but no usable score.  The v1 scheduler tests the
# abstract state before the detailed terminal state, which makes that harmless
# terminal non-game look like a corrupt final and parks the historical cursor
# forever.  Normalize only known terminal-nonfinal detailed states before the
# v1 schedule parser sees them.  No completed game or score is changed.
_ORIGINAL_STATS_GET = _history._stats_get


def _terminal_safe_stats_get(path: str, **params):
    payload = _ORIGINAL_STATS_GET(path, **params)
    if path != '/schedule' or not isinstance(payload, dict):
        return payload
    for bucket in payload.get('dates') or []:
        for game in bucket.get('games') or []:
            status = game.get('status') or {}
            if _history._terminal_nonfinal(status):
                status['abstractGameState'] = 'Cancelled'
    return payload


_history._stats_get = _terminal_safe_stats_get
_run = _history.run_historical_backfill


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
        store.put_state('controller', {
            'known_market_keys': sorted(keys),
            'known_market_key_count': len(keys),
        })
    return sorted(keys)


def run_historical_backfill(days_per_run: int | None = None, max_games_per_run: int | None = None):
    store = Store()
    _learn_live_market_catalog(store)
    return _run(days_per_run=days_per_run, max_games_per_run=max_games_per_run)
