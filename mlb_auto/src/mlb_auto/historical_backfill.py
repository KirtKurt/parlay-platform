from __future__ import annotations

import json, os, re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .engine import moneyline_consensus
from .features import build_feature_vector
from .odds_api import FEATURED, OddsApiClient, merge_event_odds
from .storage import Store

MLB_STATS_BASE = 'https://statsapi.mlb.com/api/v1'
SNAPSHOT_OFFSETS_MINUTES = (600, 360, 180, 90, 45)
EARLIEST_HISTORY = date(2020, 6, 30)
EARLIEST_ADDITIONAL = datetime(2023, 5, 3, 5, 30, tzinfo=timezone.utc)
DAYS_PER_RUN = int(os.getenv('MLB_AUTO_HISTORICAL_DAYS_PER_RUN', '1'))


def _dt(v: Any) -> datetime:
    d = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def _unwrap(v: Any) -> Any:
    return v.get('data') if isinstance(v, dict) and 'data' in v else v


def _norm(v: Any) -> str:
    s = re.sub(r'[^a-z0-9]+', '', str(v or '').lower())
    return {'oaklandathletics': 'athletics'}.get(s, s)


def _stats_get(path: str, **params) -> Any:
    url = f"{MLB_STATS_BASE}{path}?{urlencode(params)}"
    req = Request(url, headers={'User-Agent': 'inqsi-mlb-auto-history/1.0', 'Accept': 'application/json'})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def final_games(day: date) -> list[dict[str, Any]]:
    payload = _stats_get('/schedule', sportId=1, date=day.isoformat(), hydrate='linescore')
    out = []
    for bucket in payload.get('dates') or []:
        for g in bucket.get('games') or []:
            if str((g.get('status') or {}).get('abstractGameState') or '').lower() != 'final':
                continue
            h = ((g.get('teams') or {}).get('home') or {})
            a = ((g.get('teams') or {}).get('away') or {})
            hn, an = (h.get('team') or {}).get('name'), (a.get('team') or {}).get('name')
            if not hn or not an or h.get('score') is None or a.get('score') is None:
                continue
            out.append({'game_pk': str(g.get('gamePk') or ''), 'commence_time': _iso(_dt(g['gameDate'])),
                        'home_team': str(hn), 'away_team': str(an),
                        'home_score': int(h['score']), 'away_score': int(a['score'])})
    return out


def _find(rows: Any, game: dict[str, Any]) -> dict[str, Any] | None:
    target = _dt(game['commence_time'])
    cand = []
    for e in (_unwrap(rows) or []):
        if _norm(e.get('home_team')) != _norm(game['home_team']) or _norm(e.get('away_team')) != _norm(game['away_team']):
            continue
        try:
            delta = abs((_dt(e['commence_time']) - target).total_seconds())
        except Exception:
            continue
        if delta <= 6 * 3600:
            cand.append((delta, e))
    return min(cand, key=lambda x: x[0])[1] if cand else None


def _detail(client: OddsApiClient, event: dict[str, Any], at: datetime, keys: list[str]) -> tuple[dict[str, Any], list[str]]:
    detail, errors = dict(event), []
    if at < EARLIEST_ADDITIONAL:
        return detail, errors
    additional = [k for k in keys if k not in FEATURED]
    for i in range(0, len(additional), 10):
        try:
            p = _unwrap(client.historical_event_odds(str(event['id']), _iso(at), additional[i:i+10]).data)
            if isinstance(p, dict):
                detail = merge_event_odds(detail, p)
        except Exception as exc:
            errors.append(f'{type(exc).__name__}:{i}')
    return detail, errors


def build_example(client: OddsApiClient, game: dict[str, Any], market_keys: list[str]):
    start, history, chosen, errors = _dt(game['commence_time']), [], None, []
    for mins in SNAPSHOT_OFFSETS_MINUTES:
        at = start - timedelta(minutes=mins)
        try:
            e = _find(client.historical_featured_odds(_iso(at)).data, game)
            if not e:
                errors.append(f'{mins}:NO_MATCH'); continue
            history.append(float(moneyline_consensus(e).get('home') or .5))
            if mins == 45:
                chosen = (e, at)
        except Exception as exc:
            errors.append(f'{mins}:{type(exc).__name__}')
    if not chosen or not history:
        return None, {'reason': 'NO_T45', 'errors': errors}
    event, at = chosen
    detail, more = _detail(client, event, at, market_keys)
    errors += more
    features = build_feature_vector(event=event, detail=detail, home_probability_history=history,
                                    pulled_at=_iso(at), pull_count=len(history))
    return {
        'sport': 'mlb_auto', 'provider_sport_key': 'baseball_mlb', 'event_id': str(event['id']),
        'historical': True, 'historical_source': 'THE_ODDS_API_POINT_IN_TIME',
        'label_source': 'MLB_STATS_FINAL_SCORE', 'game_pk': game['game_pk'],
        'slate_date': start.date().isoformat(), 'commence_time': _iso(start),
        'home_team': event.get('home_team') or game['home_team'], 'away_team': event.get('away_team') or game['away_team'],
        'home_score': game['home_score'], 'away_score': game['away_score'],
        'label_home_win': 1 if game['home_score'] > game['away_score'] else 0,
        'features': features, 'source_pull_at': _iso(at), 'lock_cutoff_at': _iso(start - timedelta(minutes=45)),
        'source_before_or_at_cutoff': True, 'training_eligible': True,
        'snapshot_offsets_minutes': list(SNAPSHOT_OFFSETS_MINUTES), 'historical_market_errors': errors[:50]
    }, {'event_id': str(event['id']), 'history_points': len(history), 'errors': errors}


def run_historical_backfill(days_per_run: int | None = None) -> dict[str, Any]:
    store, client = Store(), OddsApiClient()
    ctl, state = store.get_state('controller'), store.get_state('historical_backfill')
    market_keys = sorted({str(x) for x in (ctl.get('known_market_keys') or []) if str(x)})
    cursor = date.fromisoformat(state['cursor_date']) if state.get('cursor_date') else datetime.now(timezone.utc).date() - timedelta(days=1)
    days = max(1, min(7, int(days_per_run or DAYS_PER_RUN)))
    added = attempted = 0; reports = []
    prior_days = int(state.get('total_days_processed') or 0)
    for _ in range(days):
        if cursor < EARLIEST_HISTORY: break
        games, day_added, audits = final_games(cursor), 0, []
        for game in games:
            attempted += 1
            row, audit = build_example(client, game, market_keys); audits.append(audit)
            if not row: continue
            store.put_training_example(row['slate_date'], row['event_id'], row)
            day_added += 1; added += 1
        store.archive_json(f'mlb_auto/historical/{cursor.isoformat()}.json', {'date': cursor.isoformat(), 'games': len(games), 'added': day_added, 'audits': audits})
        reports.append({'date': cursor.isoformat(), 'games': len(games), 'added': day_added})
        cursor -= timedelta(days=1)
        store.put_state('historical_backfill', {'cursor_date': cursor.isoformat(), 'last_run_at': datetime.now(timezone.utc).isoformat(),
                        'last_run_added': added, 'total_days_processed': prior_days + len(reports),
                        'autonomous': True, 'point_in_time_only': True})
    total = len(store.query_training_examples(limit=5000))
    return {'ok': True, 'action': 'HISTORICAL_BACKFILL', 'added': added, 'attempted': attempted,
            'training_examples': total, 'next_cursor_date': cursor.isoformat(), 'days': reports,
            'known_market_key_count': len(market_keys)}
