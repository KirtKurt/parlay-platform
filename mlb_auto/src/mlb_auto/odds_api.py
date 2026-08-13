from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'https://api.the-odds-api.com/v4'
SPORT = 'baseball_mlb'
FEATURED = ('h2h', 'spreads', 'totals')
USEFUL_PREFIXES = (
    'h2h', 'spreads', 'totals', 'alternate_spreads', 'alternate_totals',
    'team_totals', 'alternate_team_totals', 'innings', 'first_', 'pitcher_', 'batter_',
)


@dataclass
class ApiResponse:
    data: Any
    remaining: int | None
    used: int | None
    cost: int | None


def _int_header(headers, name: str) -> int | None:
    try:
        value = headers.get(name)
        return None if value is None else int(value)
    except Exception:
        return None


class OddsApiClient:
    def __init__(self, api_key: str | None = None, regions: str = 'us,us2', timeout: int = 30):
        self.api_key = api_key or os.getenv('MLB_AUTO_ODDS_API_KEY') or os.getenv('ODDS_API_KEY')
        if not self.api_key:
            raise RuntimeError('MLB_AUTO_ODDS_API_KEY_REQUIRED')
        self.regions = regions
        self.timeout = timeout

    def _get(self, path: str, **params) -> ApiResponse:
        params = {k: v for k, v in params.items() if v not in (None, '', [])}
        params['apiKey'] = self.api_key
        url = f'{BASE}{path}?{urlencode(params)}'
        req = Request(url, headers={'User-Agent': 'inqsi-mlb-auto/1.0', 'Accept': 'application/json'})
        last = None
        for attempt in range(4):
            try:
                with urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode('utf-8'))
                    return ApiResponse(
                        payload,
                        _int_header(resp.headers, 'x-requests-remaining'),
                        _int_header(resp.headers, 'x-requests-used'),
                        _int_header(resp.headers, 'x-requests-last'),
                    )
            except Exception as exc:
                last = exc
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        raise last  # pragma: no cover

    def events(self) -> ApiResponse:
        return self._get(f'/sports/{SPORT}/events', dateFormat='iso')

    def featured_odds(self) -> ApiResponse:
        return self._get(
            f'/sports/{SPORT}/odds',
            regions=self.regions,
            markets=','.join(FEATURED),
            oddsFormat='american',
            dateFormat='iso',
        )

    def event_markets(self, event_id: str) -> ApiResponse:
        return self._get(
            f'/sports/{SPORT}/events/{event_id}/markets',
            regions=self.regions,
            dateFormat='iso',
        )

    def event_odds(self, event_id: str, markets: Iterable[str]) -> ApiResponse:
        markets = [m for m in markets if m]
        if not markets:
            return ApiResponse({}, None, None, 0)
        return self._get(
            f'/sports/{SPORT}/events/{event_id}/odds',
            regions=self.regions,
            markets=','.join(sorted(set(markets))),
            oddsFormat='american',
            dateFormat='iso',
        )

    @staticmethod
    def useful_markets(market_payload: Any) -> list[str]:
        keys: set[str] = set()
        rows = market_payload if isinstance(market_payload, list) else []
        for book in rows:
            for key in (book.get('markets') or []):
                if isinstance(key, dict):
                    value = str(key.get('key') or '')
                else:
                    value = str(key or '')
                if value and (value in FEATURED or value.startswith(USEFUL_PREFIXES)):
                    keys.add(value)
        return sorted(keys)
