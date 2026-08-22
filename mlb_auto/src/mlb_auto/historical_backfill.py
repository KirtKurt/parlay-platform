from __future__ import annotations

import json, os, re, time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .engine import moneyline_consensus
from .features import build_feature_vector
from .odds_api import FEATURED, OddsApiClient, merge_event_odds
from .storage import Store
from .team_form import TEAM_FORM_SOURCE, TEAM_FORM_VERSION, build_team_form_context

MLB_STATS_BASE = 'https://statsapi.mlb.com/api/v1'
ET = ZoneInfo('America/New_York')
LOCK_MINUTES = int(os.getenv('MLB_AUTO_LOCK_MINUTES', '10'))
SNAPSHOT_OFFSETS_MINUTES = (600, 360, 180, 90, LOCK_MINUTES)
EARLIEST_HISTORY = date(2020, 6, 30)
EARLIEST_ADDITIONAL = datetime(2023, 5, 3, 5, 30, tzinfo=timezone.utc)
DAYS_PER_RUN = int(os.getenv('MLB_AUTO_HISTORICAL_DAYS_PER_RUN', '2'))
MAX_GAMES_PER_RUN = max(1, int(os.getenv('MLB_AUTO_HISTORICAL_MAX_GAMES_PER_RUN', '4')))
TIME_BUDGET_SECONDS = max(60, min(480, int(os.getenv('MLB_AUTO_HISTORICAL_TIME_BUDGET_SECONDS', '420'))))
TERMINAL_NONFINAL_STATES = frozenset({
    'cancelled', 'canceled', 'forfeit', 'forfeited', 'no contest', 'postponed',
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _initial_cursor() -> date:
    """Start only with the most recently completed Eastern Time slate."""
    return _now().astimezone(ET).date() - timedelta(days=1)


def _dt(v: Any) -> datetime:
    d = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def _unwrap(v: Any) -> Any:
    return v.get('data') if isinstance(v, dict) and 'data' in v else v


def _norm(v: Any) -> str:
    s = re.sub(r'[^a-z0-9]+', '', str(v or '').lower())
    aliases = {'oaklandathletics': 'athletics', 'theathletics': 'athletics'}
    return aliases.get(s, s)


def _err(exc: Exception) -> str:
    return f'{type(exc).__name__}:{str(exc)[:500]}'


def _stats_get(path: str, **params) -> Any:
    url = f"{MLB_STATS_BASE}{path}?{urlencode(params)}"
    req = Request(url, headers={'User-Agent': 'inqsi-mlb-auto-history/1.0', 'Accept': 'application/json'})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _terminal_nonfinal(status: dict[str, Any]) -> bool:
    detailed = str(status.get('detailedState') or '').strip().lower()
    return detailed in TERMINAL_NONFINAL_STATES


def daily_schedule(day: date) -> dict[str, Any]:
    """Return final games only after proving the whole scheduled date is terminal.

    A final-only filter is not enough for a resumable cursor: while a slate is live,
    the filtered list grows and can reorder underneath ``game_index``. Keeping the
    cursor parked until every scheduled game is terminal makes that index stable.
    """
    payload = _stats_get('/schedule', sportId=1, date=day.isoformat(), hydrate='linescore')
    scheduled: list[dict[str, Any]] = []
    finals: list[dict[str, Any]] = []
    nonterminal_game_pks: list[str] = []
    terminal_nonfinal_game_pks: list[str] = []
    unusable_final_game_pks: list[str] = []
    for bucket in payload.get('dates') or []:
        for g in bucket.get('games') or []:
            game_pk = str(g.get('gamePk') or '')
            status = dict(g.get('status') or {})
            abstract = str(status.get('abstractGameState') or '').strip().lower()
            scheduled.append({
                'game_pk': game_pk,
                'abstract_state': status.get('abstractGameState'),
                'detailed_state': status.get('detailedState'),
            })
            if abstract != 'final':
                if _terminal_nonfinal(status):
                    terminal_nonfinal_game_pks.append(game_pk)
                else:
                    nonterminal_game_pks.append(game_pk)
                continue
            h = ((g.get('teams') or {}).get('home') or {})
            a = ((g.get('teams') or {}).get('away') or {})
            hn, an = (h.get('team') or {}).get('name'), (a.get('team') or {}).get('name')
            if (
                not game_pk or not g.get('gameDate') or not hn or not an
                or h.get('score') is None or a.get('score') is None
            ):
                unusable_final_game_pks.append(game_pk)
                continue
            finals.append({'game_pk': game_pk, 'commence_time': _iso(_dt(g['gameDate'])),
                           'home_team': str(hn), 'away_team': str(an),
                           'home_score': int(h['score']), 'away_score': int(a['score'])})
    finals.sort(key=lambda x: (x['commence_time'], x['game_pk']))
    scheduled_count = len(scheduled)
    nonterminal_count = len(nonterminal_game_pks)
    unusable_final_count = len(unusable_final_game_pks)
    ready = nonterminal_count == 0 and unusable_final_count == 0
    status = (
        'COMPLETE'
        if ready
        else 'WAITING_FOR_FINALS'
        if nonterminal_count
        else 'WAITING_FOR_FINAL_DATA'
    )
    return {
        'date': day.isoformat(),
        'status': status,
        'complete': ready,
        'scheduled_game_count': scheduled_count,
        'terminal_game_count': scheduled_count - nonterminal_count,
        'final_game_count': len(finals),
        'terminal_nonfinal_count': len(terminal_nonfinal_game_pks),
        'nonterminal_game_count': nonterminal_count,
        'nonterminal_game_pks': nonterminal_game_pks,
        'terminal_nonfinal_game_pks': terminal_nonfinal_game_pks,
        'unusable_final_game_count': unusable_final_count,
        'unusable_final_game_pks': unusable_final_game_pks,
        'games': finals,
    }


def final_games(day: date) -> list[dict[str, Any]]:
    """Compatibility projection for callers that only need final game rows."""
    return list(daily_schedule(day)['games'])


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
        if delta <= 12 * 3600:
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
            errors.append(f'additional[{i}:{i+10}]:{_err(exc)}')
    return detail, errors


def build_example(
    client: OddsApiClient,
    game: dict[str, Any],
    market_keys: list[str],
    team_form_builder: Callable[..., dict[str, Any]] | None = None,
):
    start, history, chosen, errors = _dt(game['commence_time']), [], None, []
    for mins in SNAPSHOT_OFFSETS_MINUTES:
        at = start - timedelta(minutes=mins)
        try:
            e = _find(client.historical_featured_odds(_iso(at)).data, game)
            if not e:
                errors.append(f'{mins}:NO_MATCH'); continue
            history.append(float(moneyline_consensus(e).get('home') or .5))
            if mins == LOCK_MINUTES:
                chosen = (e, at)
        except Exception as exc:
            errors.append(f'{mins}:{_err(exc)}')
    if not chosen:
        return None, {'reason': 'NO_T10', 'history_points': len(history), 'errors': errors}
    event, at = chosen
    if not history:
        return None, {'reason': 'NO_HISTORY', 'history_points': 0, 'errors': errors}
    detail, more = _detail(client, event, at, market_keys)
    errors += more
    form_builder = team_form_builder or build_team_form_context
    form = form_builder(
        str(event.get('home_team') or game['home_team']),
        str(event.get('away_team') or game['away_team']),
        at,
        historical=True,
    )
    features = build_feature_vector(
        event=event,
        detail=detail,
        home_probability_history=history,
        pulled_at=_iso(at),
        pull_count=len(history),
        team_form_features=form.get('features') or {},
    )
    return {
        'sport': 'mlb_auto', 'provider_sport_key': 'baseball_mlb', 'event_id': str(event['id']),
        'historical': True, 'historical_source': 'THE_ODDS_API_POINT_IN_TIME',
        'label_source': 'MLB_STATS_FINAL_SCORE', 'game_pk': game['game_pk'],
        'slate_date': start.astimezone(ET).date().isoformat(), 'commence_time': _iso(start),
        'home_team': event.get('home_team') or game['home_team'], 'away_team': event.get('away_team') or game['away_team'],
        'home_score': game['home_score'], 'away_score': game['away_score'],
        'label_home_win': 1 if game['home_score'] > game['away_score'] else 0,
        'features': features, 'source_pull_at': _iso(at),
        'lock_cutoff_at': _iso(start - timedelta(minutes=LOCK_MINUTES)),
        'lock_minutes': LOCK_MINUTES,
        'source_before_or_at_cutoff': True, 'training_eligible': True,
        'team_form_version': TEAM_FORM_VERSION,
        'team_form_source': TEAM_FORM_SOURCE,
        'team_form_available': bool(form.get('ok')),
        'team_form_as_of': (form.get('metadata') or {}).get('as_of'),
        'team_form_metadata': form.get('metadata') or {},
        'team_form_error': form.get('error') or '',
        'snapshot_offsets_minutes': list(SNAPSHOT_OFFSETS_MINUTES), 'historical_market_errors': errors[:50]
    }, {
        'event_id': str(event['id']),
        'history_points': len(history),
        'reason': 'OK',
        'errors': errors,
        'team_form_available': bool(form.get('ok')),
        'team_form_version': TEAM_FORM_VERSION,
        'team_form_error': form.get('error') or '',
    }


def _persist_progress(store: Store, *, cursor: date, game_index: int, added: int, attempted: int,
                      prior_games: int, prior_days: int, day_complete: bool = False,
                      failure_reasons: dict[str, int] | None = None,
                      run_status: str = 'IN_PROGRESS',
                      schedule: dict[str, Any] | None = None) -> None:
    payload = {
        'cursor_date': cursor.isoformat(), 'game_index': int(game_index),
        'last_run_at': _now().isoformat(), 'last_run_added': int(added),
        'last_run_attempted': int(attempted), 'last_run_failure_reasons': dict(failure_reasons or {}),
        'total_games_processed': int(prior_games + attempted),
        'total_days_processed': int(prior_days + (1 if day_complete else 0)),
        'last_run_status': str(run_status),
        'waiting_for_terminal_date': (
            str((schedule or {}).get('date') or '')
            if str(run_status).startswith('WAITING_FOR_') else ''
        ),
        'lock_minutes': LOCK_MINUTES,
        'snapshot_offsets_minutes': list(SNAPSHOT_OFFSETS_MINUTES),
        'autonomous': True, 'point_in_time_only': True, 'resumable_per_game': True,
    }
    if schedule:
        payload.update({
            'last_schedule_date': schedule.get('date'),
            'last_schedule_status': schedule.get('status'),
            'last_schedule_game_count': int(schedule.get('scheduled_game_count') or 0),
            'last_schedule_terminal_count': int(schedule.get('terminal_game_count') or 0),
            'last_schedule_final_count': int(schedule.get('final_game_count') or 0),
            'last_schedule_nonterminal_count': int(schedule.get('nonterminal_game_count') or 0),
            'last_schedule_nonterminal_game_pks': list(schedule.get('nonterminal_game_pks') or []),
            'last_schedule_unusable_final_count': int(schedule.get('unusable_final_game_count') or 0),
        })
    store.put_state('historical_backfill', payload)


def run_historical_backfill(days_per_run: int | None = None, max_games_per_run: int | None = None) -> dict[str, Any]:
    store, client = Store(), OddsApiClient()
    ctl, state = store.get_state('controller'), store.get_state('historical_backfill')
    market_keys = sorted({str(x) for x in (ctl.get('known_market_keys') or []) if str(x)})
    cursor = date.fromisoformat(state['cursor_date']) if state.get('cursor_date') else _initial_cursor()
    game_index = max(0, int(state.get('game_index') or 0))
    day_limit = max(1, min(14, int(days_per_run or DAYS_PER_RUN)))
    game_budget = max(1, min(12, int(max_games_per_run or MAX_GAMES_PER_RUN)))
    added = attempted = completed_days = 0
    reports: list[dict[str, Any]] = []
    failures: dict[str, int] = {}
    prior_days = int(state.get('total_days_processed') or 0)
    prior_games = int(state.get('total_games_processed') or 0)
    started = time.monotonic()

    while cursor >= EARLIEST_HISTORY and attempted < game_budget and completed_days < day_limit:
        if attempted and time.monotonic() - started >= TIME_BUDGET_SECONDS:
            break
        schedule = daily_schedule(cursor)
        if not schedule.get('complete'):
            waiting = {
                key: value for key, value in schedule.items() if key != 'games'
            }
            store.archive_json(f'mlb_auto/historical/{cursor.isoformat()}.json', {
                **waiting,
                'checked_at': _now().isoformat(),
            })
            reports.append(waiting)
            _persist_progress(
                store, cursor=cursor, game_index=game_index, added=added,
                attempted=attempted, prior_games=prior_games, prior_days=prior_days,
                failure_reasons=failures, run_status=str(schedule.get('status') or 'WAITING_FOR_FINALS'),
                schedule=schedule,
            )
            break

        games = list(schedule.get('games') or [])
        if game_index >= len(games):
            store.archive_json(f'mlb_auto/historical/{cursor.isoformat()}.json', {
                'date': cursor.isoformat(), 'games': len(games), 'status': 'COMPLETE',
                'scheduled_game_count': schedule.get('scheduled_game_count'),
                'terminal_nonfinal_count': schedule.get('terminal_nonfinal_count'),
                'completed_at': _now().isoformat(),
            })
            reports.append({
                'date': cursor.isoformat(), 'games': len(games), 'complete': True,
                'scheduled_game_count': schedule.get('scheduled_game_count'),
                'terminal_nonfinal_count': schedule.get('terminal_nonfinal_count'),
            })
            cursor -= timedelta(days=1); game_index = 0; completed_days += 1
            _persist_progress(store, cursor=cursor, game_index=0, added=added, attempted=attempted,
                              prior_games=prior_games, prior_days=prior_days, day_complete=True,
                              failure_reasons=failures, run_status='DAY_COMPLETE', schedule=schedule)
            prior_days += 1
            continue

        game = games[game_index]
        try:
            row, audit = build_example(client, game, market_keys)
        except Exception as exc:
            row, audit = None, {'reason': f'BUILD_{type(exc).__name__}', 'errors': [_err(exc)]}
        attempted += 1
        reason = str((audit or {}).get('reason') or ('OK' if row else 'UNKNOWN'))
        if row:
            store.put_training_example(row['slate_date'], row['event_id'], row); added += 1
        else:
            failures[reason] = failures.get(reason, 0) + 1
        store.archive_json(
            f"mlb_auto/historical/{cursor.isoformat()}/{game_index:03d}-{game.get('game_pk') or 'unknown'}.json",
            {'date': cursor.isoformat(), 'game_index': game_index, 'game': game, 'added': bool(row), 'audit': audit},
        )
        reports.append({'date': cursor.isoformat(), 'game_index': game_index, 'game_pk': game.get('game_pk'),
                        'home_team': game.get('home_team'), 'away_team': game.get('away_team'),
                        'added': bool(row), 'reason': reason, 'audit': audit})
        game_index += 1
        if game_index >= len(games):
            cursor -= timedelta(days=1); game_index = 0; completed_days += 1; day_complete = True
        else:
            day_complete = False
        _persist_progress(store, cursor=cursor, game_index=game_index, added=added, attempted=attempted,
                          prior_games=prior_games, prior_days=prior_days, day_complete=day_complete,
                          failure_reasons=failures,
                          run_status='DAY_COMPLETE' if day_complete else 'IN_PROGRESS',
                          schedule=schedule)
        if day_complete:
            prior_days += 1

    from .training_guard import _authority_minutes

    all_examples = store.query_training_examples(limit=5000)
    compatible_total = sum(1 for row in all_examples if _authority_minutes(row) == LOCK_MINUTES)
    return {
        'ok': True, 'action': 'HISTORICAL_BACKFILL', 'added': added, 'attempted': attempted,
        'training_examples': compatible_total,
        'training_examples_all_lock_horizons': len(all_examples),
        'next_cursor_date': cursor.isoformat(), 'next_game_index': game_index,
        'batch_game_budget': game_budget, 'time_budget_seconds': TIME_BUDGET_SECONDS,
        'elapsed_seconds': round(time.monotonic() - started, 3), 'resumable_per_game': True,
        'failure_reasons': failures, 'days': reports, 'known_market_key_count': len(market_keys),
        'waiting_for_finals': bool(reports and str(reports[-1].get('status') or '').startswith('WAITING_FOR_')),
        'lock_minutes': LOCK_MINUTES,
        'snapshot_offsets_minutes': list(SNAPSHOT_OFFSETS_MINUTES),
    }
