from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .engine import moneyline_consensus
from .features import build_feature_vector
from .odds_api import FEATURED, OpenEndedOddsApiClient if False else OddsApiClient, merge_event_odds
from .storage import Store

# The Odds API supplies point-in-time historical market snapshots. MLB-owned Stats API
# supplies final scores only; no existing MLB model/outcome table is imported.
MLB_STATS_BASE = 'https://statsapi.mlb.com/api/v1'
SNAPSHOT_OFFSETS_MINUTES = (600, 360, 180, 90, 45)
DEFAULT_DAYS_PER_RUN = int(os.getenv('MLB_AUTO_HISTORICAL_DAYS_PER_RUN', '1'))
DEFAULT_START_DAYS_AGO = int(os.getenv('MLB_AUTO_HISTORICAL_START_DAYS_AGO', '1'))
EARLIEST_ADDITIONAL_MARKETS = datetime(2023, 5, 3, 5, 30, tzinfo=timezone.utc)
EARLIEST_MLB_HISTORY = date(2020, 6, 30)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _dt(value: Any) -> datetime:
    d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and 'data' in payload:
        return payload.get('data')
    return payload


def _norm_team(value: Any) -> str:
    s = re.sub(r'[^a-z0-9]+', '', str(value or '').lower())
    aliases = {
        'oaklandathletics': 'athletics',
        'athletics': 'athletics',
        'losangelesangels': 'losangelesangels',
        'laangels': 'losangelesangels',
        'arizonadiamondbacks': 'arizonadiamondbacks',
    }
    return aliases.get(s, s)


def _stats_get(path: str, **params) -> Any:
    url = f"{MLB_STATS_BASE}{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    req = Request(url, headers={'User-Agent': 'inqsi-mlb-auto-history/1.0', 'Accept': 'application/json'})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def final_games_for_date(day: date) -> list[dict[str, Any]]:
    payload = _stats_get('/schedule', sportId=1, date=day.isoformat(), hydrate='linescore')
    out: list[dict[str, Any]] = []
    for bucket in payload.get('dates') or []:
        for game in bucket.get('games') or []:
            status = game.get('status') or {}
            if str(status.get('abstractGameState') or '').lower() != 'final':
                continue
            home = ((game.get('teams') or {}).get('home') or {})
            away = ((game.get('teams') or {}).get('away') or {})
            home_team = ((home.get('team') or {}).get('name'))
            away_team = ((away.get('team') or {}).get('name'))
            if not home_team or not away_team or home.get('score') is None or away.get('score') is None:
                continue
            try:
                commence = _dt(game.get('gameDate'))
            except Exception:
                continue
            out.append({
                'game_pk': str(game.get('gamePk') or ''),
                'commence_time': _iso(commence),
                'home_team': str(home_team),
                'away_team': str(away_team),
                'home_score': int(home.get('score')),
                'away_score': int(away.get('score')),
                'game_type': str(game.get('gameType') or ''),
            })
    return out


def _find_event(rows: Any, game: dict[str, Any]) -> dict[str, Any] | None:
    rows = _unwrap(rows) or []
    home = _norm_team(game['home_team'])
    away = _norm_team(game['away_team'])
    target_start = _dt(game['commence_time'])
    candidates = []
    for event in rows if isinstance(rows, list) else []:
        if _norm_team(event.get('home_team')) != home or _norm_team(event.get('away_team')) != away:
            continue
        try:
            delta = abs((_dt(event.get('commence_time')) - target_start).total_seconds())
        except Exception:
            delta = 10**9
        if delta <= 6 * 3600:
            candidates.append((delta, event))
    return min(candidates, key=lambda x: x[0])[1] if candidates else None


def _historical_featured(client: OddsApiClient, snapshot_at: datetime, game: dict[str, Any]) -> dict[str, Any] | None:
    payload = client.historical_featured_odds(_iso(snapshot_at)).data
    return _find_event(payload, game)


def _historical_detail(client: OddsApiClient, event: dict[str, Any], snapshot_at: datetime, market_keys: list[str]) -> tuple[dict[str, Any], list[str]]:
    detail = dict(event)
    errors: list[str] = []
    if snapshot_at < EARLIEST_ADDITIONAL_MARKETS:
        return detail, errors
    additional = [k for k in market_keys if k and k not in FEATURED]
    for i in range(0, len(additional), 10):
        chunk = additional[i:i + 10]
        if not chunk:
            continue
        try:
            payload = client.historical_event_odds(str(event.get('id') or ''), _iso(snapshot_at), chunk).data
            data = _unwrap(payload)
            if isinstance(data, dict):
                detail = merge_event_odds(detail, data)
        except Exception as exc:
            # Historical market coverage legitimately varies by bookmaker and timestamp.
            errors.append(f'{type(exc).__name__}:{i}')
    return detail, errors


def build_historical_example(client: OddsApiClient, game: dict[str, Any], known_market_keys: list[str]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    start = _dt(game['commence_time'])
    history: list[float] = []
    chosen_event: dict[str, Any] | None = None
    chosen_detail: dict[str, Any] | None = None
    chosen_at: datetime | None = None
    errors: list[str] = []

    for minutes in SNAPSHOT_OFFSETS_MINUTES:
        at = start - timedelta(minutes=minutes)
        try:
            event = _historical_featured(client, at, game)
        except Exception as exc:
            errors.append(f'featured_{minutes}:{type(exc).__name__}')
            continue
        if not event:
            errors.append(f'featured_{minutes}:NO_MATCH')
            continue
        try:
            fair = moneyline_consensus(event)
            history.append(float(fair.get('home') or .5))
        except Exception as exc:
            errors.append(f'consensus_{minutes}:{type(exc).__name__}')
            continue
        if minutes == 45:
            chosen_event = event
            chosen_at = at

    if not chosen_event or not chosen_at or not history:
        return None, {'errors': errors, 'reason': 'NO_VALID_T45_FEATURE_SNAPSHOT'}

    chosen_detail, detail_errors = _historical_detail(client, chosen_event, chosen_at, known_market_keys)
    errors.extend(detail_errors)
    features = build_feature_vector(
        event=chosen_event,
        detail=chosen_detail,
        home_probability_history=history,
        pulled_at=_iso(chosen_at),
        pull_count=len(history),
    )
    label = 1 if int(game['home_score']) > int(game['away_score']) else 0
    event_id = str(chosen_event.get('id') or '')
    row = {
        'sport': 'mlb_auto',
        'provider_sport_key': 'baseball_mlb',
        'event_id': event_id,
        'historical': True,
        'historical_source': 'THE_ODDS_API_POINT_IN_TIME',
        'label_source': 'MLB_STATS_FINAL_SCORE',
        'game_pk': game.get('game_pk'),
        'slate_date': start.date().isoformat(),
        'commence_time': _iso(start),
        'home_team': chosen_event.get('home_team') or game['home_team'],
        'away_team': chosen_event.get('away_team') or game['away_team'],
        'home_score': int(game['home_score']),
        'away_score': int(game['away_score']),
        'label_home_win': label,
        'features': features,
        'source_pull_at': _iso(chosen_at),
        'lock_cutoff_at': _iso(start - timedelta(minutes=45)),
        'source_before_or_at_cutoff': True,
        'training_eligible': True,
        'snapshot_offsets_minutes': list(SNAPSHOT_OFFSETS_MINUTES),
        'historical_market_errors': errors[:50],
        'known_market_key_count': len(known_market_keys),
    }
    audit = {
        'event_id': event_id,
        'game_pk': game.get('game_pk'),
        'snapshot_at': _iso(chosen_at),
        'feature_count': len(features),
        'history_points': len(history),
        'errors': errors,
    }
    return row, audit


def _cursor_date(state: dict[str, Any]) -> date:
    raw = state.get('cursor_date')
    if raw:
        return date.fromisoformat(str(raw))
    return datetime.now(timezone.utc).date() - timedelta(days=DEFAULT_START_DAYS_AGO)


def run_historical_backfill(*, days_per_run: int | None = None) -> dict[str, Any]:
    store = Store()
    client = OddsApiClient()
    controller = store.get_state('controller')
    known = sorted({str(x) for x in (controller.get('known_market_keys') or []) if str(x)})
    state = store.get_state('historical_backfill')
    cursor = _cursor_date(state)
    days = max(1, min(7, int(days_per_run or DEFAULT_DAYS_PER_RUN)))
    added = attempted = matched = 0
    day_reports: list[dict[str, Any]] = []

    for _ in range(days):
        if cursor < EARLIEST_MLB_HISTORY:
            break
        games = final_games_for_date(cursor)
        day_added = 0
        audits = []
        for game in games:
            attempted += 1
            row, audit = build_historical_example(client, game, known)
            audits.append(audit)
            if not row:
                continue
            matched += 1
            event_id = str(row['event_id'])
            if not event_id:
                continue
            store.put_training_example(str(row['slate_date']), event_id, row)
            day_added += 1
            added += 1
        store.archive_json(f'mlb_auto/historical/{cursor.isoformat()}.json', {
            'day': cursor.isoformat(), 'games': len(games), 'added': day_added,
            'known_market_keys': known, 'audits': audits,
        })
        day_reports.append({'date': cursor.isoformat(), 'games': len(games), 'added': day_added})
        cursor -= timedelta(days=1)
        store.put_state('historical_backfill', {
            'cursor_date': cursor.isoformat(),
            'last_completed_date': (cursor + timedelta(days=1)).isoformat(),
            'last_run_at': datetime.now(timezone.utc).isoformat(),
            'last_run_added': added,
            'total_days_processed': int(state.get('total_days_processed') or 0) + len(day_reports),
            'autonomous': True,
            'point_in_time_only': True,
        })

    total_examples = len(store.query_training_examples(limit=5000))
    return {
        'ok': True,
        'action': 'HISTORICAL_BACKFILL',
        'added': added,
        'attempted': attempted,
        'matched': matched,
        'training_examples': total_examples,
        'next_cursor_date': cursor.isoformat(),
        'days': day_reports,
        'known_market_key_count': len(known),
    }
