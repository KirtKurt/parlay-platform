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
                    return ApiResponse(payload, _int_header(resp.headers, 'x-requests-remaining'), _int_header(resp.headers, 'x-requests-used'), _int_header(resp.headers, 'x-requests-last'))
            except Exception as exc:
                last = exc
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        raise last

    def events(self) -> ApiResponse:
        return self._get(f'/sports/{SPORT}/events', dateFormat='iso')

    def participants(self) -> ApiResponse:
        return self._get(f'/sports/{SPORT}/participants')

    def scores(self, days_from: int = 3) -> ApiResponse:
        return self._get(f'/sports/{SPORT}/scores', daysFrom=days_from, dateFormat='iso')

    def featured_odds(self) -> ApiResponse:
        return self._get(f'/sports/{SPORT}/odds', regions=self.regions, markets=','.join(FEATURED), oddsFormat='american', dateFormat='iso')

    def event_markets(self, event_id: str) -> ApiResponse:
        return self._get(f'/sports/{SPORT}/events/{event_id}/markets', regions=self.regions, dateFormat='iso')

    def event_odds(self, event_id: str, markets: Iterable[str]) -> ApiResponse:
        markets = [m for m in markets if m]
        if not markets:
            return ApiResponse({}, None, None, 0)
        return self._get(f'/sports/{SPORT}/events/{event_id}/odds', regions=self.regions, markets=','.join(sorted(set(markets))), oddsFormat='american', dateFormat='iso')

    def historical_featured_odds(self, snapshot_at_iso: str) -> ApiResponse:
        return self._get(f'/historical/sports/{SPORT}/odds', regions=self.regions, markets=','.join(FEATURED), oddsFormat='american', dateFormat='iso', date=snapshot_at_iso)

    def historical_event_odds(self, event_id: str, snapshot_at_iso: str, markets: Iterable[str]) -> ApiResponse:
        markets = [m for m in markets if m]
        if not markets:
            return ApiResponse({}, None, None, 0)
        return self._get(f'/historical/sports/{SPORT}/events/{event_id}/odds', regions=self.regions, markets=','.join(sorted(set(markets))), oddsFormat='american', dateFormat='iso', date=snapshot_at_iso)

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
            markets = book.get('markets') or [] if isinstance(book, dict) else []
            for market in markets:
                value = str((market.get('key') if isinstance(market, dict) else market) or '')
                if value and (value in FEATURED or value.startswith(USEFUL_PREFIXES)):
                    keys.add(value)
        return sorted(keys)


def merge_event_odds(featured: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(featured or {})
    merged: dict[str, dict[str, Any]] = {str(b.get('key')): dict(b) for b in out.get('bookmakers') or [] if b.get('key')}
    for book in (detail or {}).get('bookmakers') or []:
        key = str(book.get('key') or '')
        if not key:
            continue
        target = merged.setdefault(key, {'key': key, 'title': book.get('title'), 'markets': []})
        by_market = {str(m.get('key')): m for m in target.get('markets') or [] if m.get('key')}
        for market in book.get('markets') or []:
            if market.get('key'):
                by_market[str(market['key'])] = market
        target['markets'] = list(by_market.values())
    out['bookmakers'] = list(merged.values())
    return out
