from __future__ import annotations

from typing import Any

from .odds_api import OddsApiClient


class OpenEndedOddsApiClient(OddsApiClient):
    """Collect every market key exposed for a baseball_mlb event.

    The immutable audit layer stores raw payloads. Training eligibility is decided later
    by pregame/leakage-safe feature engineering, not by a hard-coded market allowlist.
    """

    @staticmethod
    def useful_markets(market_payload: Any) -> list[str]:
        keys: set[str] = set()
        if isinstance(market_payload, dict):
            rows = market_payload.get('bookmakers') or market_payload.get('data') or []
        elif isinstance(market_payload, list):
            rows = market_payload
        else:
            rows = []
        for book in rows:
            if not isinstance(book, dict):
                continue
            for market in book.get('markets') or []:
                if isinstance(market, dict):
                    value = str(market.get('key') or '').strip()
                else:
                    value = str(market or '').strip()
                if value:
                    keys.add(value)
        return sorted(keys)
