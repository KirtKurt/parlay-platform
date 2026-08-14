from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

MLB_STATS_BASE = 'https://statsapi.mlb.com/api/v1'
TEAM_FORM_VERSION = 'MLB_TEAM_FORM_V1_POINT_IN_TIME'
TEAM_FORM_SOURCE = 'MLB_STATS_SCHEDULE_RECONSTRUCTED'
COMPETITIVE_GAME_TYPES = frozenset({'R', 'F', 'D', 'L', 'W'})
REGULAR_GAME_TYPE = 'R'
ET = ZoneInfo('America/New_York')
REMATERIALIZE_LIMIT = max(
    1, min(1000, int(os.getenv('MLB_AUTO_TEAM_FORM_REMATERIALIZE_PER_RUN', '500')))
)
LLM_READY_COVERAGE = max(
    0.0, min(1.0, float(os.getenv('MLB_AUTO_TEAM_FORM_MIN_LLM_COVERAGE', '0.65')))
)

_NAME_ALIASES = {
    'oaklandathletics': 'athletics',
    'theathletics': 'athletics',
    'losangelesangelsofanaheim': 'losangelesangels',
    'clevelandindians': 'clevelandclub',
    'clevelandguardians': 'clevelandclub',
    'tampabaydevilrays': 'tampabayclub',
    'tampabayrays': 'tampabayclub',
    'floridamarlins': 'marlinsclub',
    'miamimarlins': 'marlinsclub',
}


class TeamFormUnavailable(RuntimeError):
    pass


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _norm(value: Any) -> str:
    normalized = re.sub(r'[^a-z0-9]+', '', str(value or '').lower())
    return _NAME_ALIASES.get(normalized, normalized)


def _stats_get(path: str, **params) -> Any:
    url = f"{MLB_STATS_BASE}{path}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            'User-Agent': 'inqsi-mlb-auto-team-form/1.0',
            'Accept': 'application/json',
        },
    )
    with urlopen(request, timeout=35) as response:
        return json.loads(response.read().decode())


def _season_through_date(season: int) -> date:
    today = datetime.now(timezone.utc).astimezone(ET).date()
    return min(date(int(season), 12, 31), today)


def _schedule_cache_bucket() -> int:
    return int(datetime.now(timezone.utc).timestamp() // 300)


@lru_cache(maxsize=48)
def _season_schedule_cached(season: int, through_date: str, cache_bucket: int) -> dict[str, Any]:
    del cache_bucket  # The bucket is part of the cache key and intentionally unused otherwise.
    return _stats_get(
        '/schedule',
        sportId=1,
        startDate=f'{int(season):04d}-01-01',
        endDate=str(through_date),
        hydrate='linescore',
    )


def _season_schedule(season: int) -> dict[str, Any]:
    through = _season_through_date(int(season)).isoformat()
    return _season_schedule_cached(int(season), through, _schedule_cache_bucket())


def clear_schedule_cache() -> None:
    _season_schedule_cached.cache_clear()


def _parse_schedule(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    for bucket in payload.get('dates') or []:
        for game in bucket.get('games') or []:
            teams = game.get('teams') or {}
            home = teams.get('home') or {}
            away = teams.get('away') or {}
            home_team = home.get('team') or {}
            away_team = away.get('team') or {}
            if not home_team.get('id') or not away_team.get('id'):
                continue
            try:
                start = _dt(game.get('gameDate'))
            except Exception:
                continue
            official_date = str(game.get('officialDate') or start.astimezone(ET).date().isoformat())
            status = game.get('status') or {}
            final = str(status.get('abstractGameState') or '').lower() == 'final'
            home_score = home.get('score')
            away_score = away.get('score')
            games.append({
                'game_pk': str(game.get('gamePk') or ''),
                'game_type': str(game.get('gameType') or ''),
                'start': start,
                'official_date': official_date,
                'final': final,
                'home_team_id': int(home_team['id']),
                'away_team_id': int(away_team['id']),
                'home_team': str(home_team.get('name') or ''),
                'away_team': str(away_team.get('name') or ''),
                'home_score': int(home_score) if home_score is not None else None,
                'away_score': int(away_score) if away_score is not None else None,
            })
    games.sort(key=lambda item: (item['start'], item['game_pk']))
    return games


def _team_name_map(games: list[dict[str, Any]]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for game in games:
        mapping[_norm(game.get('home_team'))] = int(game['home_team_id'])
        mapping[_norm(game.get('away_team'))] = int(game['away_team_id'])
    return mapping


def _eligible_results(
    games: list[dict[str, Any]],
    *,
    as_of: datetime,
    historical: bool,
) -> list[dict[str, Any]]:
    cutoff_date = as_of.astimezone(ET).date().isoformat()
    results: list[dict[str, Any]] = []
    for game in games:
        if game.get('game_type') not in COMPETITIVE_GAME_TYPES:
            continue
        if not game.get('final'):
            continue
        if game.get('home_score') is None or game.get('away_score') is None:
            continue
        if historical:
            # Historical final-state APIs do not prove the exact same-day final timestamp.
            # Excluding all same-day games is conservative and prevents Game 1 -> Game 2 leakage.
            if str(game.get('official_date') or '') >= cutoff_date:
                continue
        elif game['start'] >= as_of:
            continue
        results.append(game)
    return results


def _pct(wins: int, games: int, default: float = 0.5) -> float:
    return float(wins) / float(games) if games > 0 else float(default)


def _window(history: list[dict[str, Any]], size: int) -> dict[str, float]:
    window = history[-int(size):]
    games = len(window)
    wins = sum(1 for item in window if int(item['result']) > 0)
    losses = games - wins
    run_diff = sum(float(item['run_diff']) for item in window)
    return {
        'games': float(games),
        'wins': float(wins),
        'losses': float(losses),
        'win_pct': _pct(wins, games),
        'run_diff_per_game': run_diff / games if games else 0.0,
    }


def _streak(history: list[dict[str, Any]]) -> tuple[float, float, float]:
    if not history:
        return 0.0, 0.0, 0.0
    latest = 1 if int(history[-1]['result']) > 0 else -1
    length = 0
    for item in reversed(history):
        result = 1 if int(item['result']) > 0 else -1
        if result != latest:
            break
        length += 1
    signed = float(length * latest)
    return signed, float(length if latest > 0 else 0), float(length if latest < 0 else 0)


def _team_summary(
    results: list[dict[str, Any]],
    *,
    team_id: int,
    as_of: datetime,
) -> dict[str, float]:
    regular_wins = regular_losses = 0
    home_wins = home_losses = 0
    road_wins = road_losses = 0
    run_diff_total = 0.0
    history: list[dict[str, Any]] = []

    for game in results:
        if int(team_id) not in (int(game['home_team_id']), int(game['away_team_id'])):
            continue
        is_home = int(game['home_team_id']) == int(team_id)
        team_score = int(game['home_score'] if is_home else game['away_score'])
        opponent_score = int(game['away_score'] if is_home else game['home_score'])
        won = team_score > opponent_score
        result = 1 if won else -1
        run_diff = float(team_score - opponent_score)
        history.append({
            'result': result,
            'run_diff': run_diff,
            'start': game['start'],
            'is_home': is_home,
            'game_type': game.get('game_type'),
        })
        run_diff_total += run_diff
        if game.get('game_type') == REGULAR_GAME_TYPE:
            if won:
                regular_wins += 1
            else:
                regular_losses += 1
            if is_home:
                if won:
                    home_wins += 1
                else:
                    home_losses += 1
            else:
                if won:
                    road_wins += 1
                else:
                    road_losses += 1

    regular_games = regular_wins + regular_losses
    competitive_games = len(history)
    home_games = home_wins + home_losses
    road_games = road_wins + road_losses
    last3 = _window(history, 3)
    last7 = _window(history, 7)
    signed_streak, win_streak, loss_streak = _streak(history)

    if history:
        days_since_last = max(0.0, (as_of - history[-1]['start']).total_seconds() / 86400.0)
        days_since_last = min(14.0, days_since_last)
    else:
        days_since_last = 14.0
    games_last7_days = sum(1 for item in history if item['start'] >= as_of - timedelta(days=7))
    games_last14_days = sum(1 for item in history if item['start'] >= as_of - timedelta(days=14))

    summary: dict[str, float] = {
        'games_played': float(regular_games),
        'competitive_games_played': float(competitive_games),
        'wins': float(regular_wins),
        'losses': float(regular_losses),
        'win_pct': _pct(regular_wins, regular_games),
        'win_pct_shrunk': (regular_wins + 5.0) / (regular_games + 10.0),
        'home_games': float(home_games),
        'home_win_pct': _pct(home_wins, home_games),
        'road_games': float(road_games),
        'road_win_pct': _pct(road_wins, road_games),
        'run_diff_per_game': run_diff_total / competitive_games if competitive_games else 0.0,
        'last3_games': last3['games'],
        'last3_wins': last3['wins'],
        'last3_losses': last3['losses'],
        'last3_win_pct': last3['win_pct'],
        'last3_run_diff_per_game': last3['run_diff_per_game'],
        'last7_games': last7['games'],
        'last7_wins': last7['wins'],
        'last7_losses': last7['losses'],
        'last7_win_pct': last7['win_pct'],
        'last7_run_diff_per_game': last7['run_diff_per_game'],
        'streak_signed': signed_streak,
        'win_streak': win_streak,
        'loss_streak': loss_streak,
        'days_since_last_game': days_since_last,
        'games_last7_days': float(games_last7_days),
        'games_last14_days': float(games_last14_days),
    }
    for lag in range(1, 8):
        summary[f'wl_lag{lag}'] = float(history[-lag]['result']) if len(history) >= lag else 0.0
    return summary


def _neutral_summary() -> dict[str, float]:
    summary = {
        'games_played': 0.0,
        'competitive_games_played': 0.0,
        'wins': 0.0,
        'losses': 0.0,
        'win_pct': 0.5,
        'win_pct_shrunk': 0.5,
        'home_games': 0.0,
        'home_win_pct': 0.5,
        'road_games': 0.0,
        'road_win_pct': 0.5,
        'run_diff_per_game': 0.0,
        'last3_games': 0.0,
        'last3_wins': 0.0,
        'last3_losses': 0.0,
        'last3_win_pct': 0.5,
        'last3_run_diff_per_game': 0.0,
        'last7_games': 0.0,
        'last7_wins': 0.0,
        'last7_losses': 0.0,
        'last7_win_pct': 0.5,
        'last7_run_diff_per_game': 0.0,
        'streak_signed': 0.0,
        'win_streak': 0.0,
        'loss_streak': 0.0,
        'days_since_last_game': 14.0,
        'games_last7_days': 0.0,
        'games_last14_days': 0.0,
    }
    for lag in range(1, 8):
        summary[f'wl_lag{lag}'] = 0.0
    return summary


def _compose_features(
    home: Mapping[str, float],
    away: Mapping[str, float],
    *,
    available: bool,
    same_day_results_included: bool,
) -> dict[str, float]:
    features: dict[str, float] = {
        'form_available': 1.0 if available else 0.0,
        'form_same_day_results_included': 1.0 if same_day_results_included else 0.0,
    }
    for side, values in (('home', home), ('away', away)):
        for name, value in values.items():
            number = float(value)
            features[f'form_{side}_{name}'] = number if math.isfinite(number) else 0.0

    delta_names = (
        'games_played',
        'competitive_games_played',
        'win_pct',
        'win_pct_shrunk',
        'run_diff_per_game',
        'last3_win_pct',
        'last3_run_diff_per_game',
        'last7_win_pct',
        'last7_run_diff_per_game',
        'streak_signed',
        'win_streak',
        'loss_streak',
        'days_since_last_game',
        'games_last7_days',
        'games_last14_days',
    )
    for name in delta_names:
        features[f'form_{name}_delta'] = float(home[name]) - float(away[name])
    for lag in range(1, 8):
        features[f'form_wl_lag{lag}_delta'] = (
            float(home[f'wl_lag{lag}']) - float(away[f'wl_lag{lag}'])
        )
    features['form_home_split_vs_away_split_delta'] = (
        float(home['home_win_pct']) - float(away['road_win_pct'])
    )
    features['form_min_games_played'] = min(
        float(home['games_played']), float(away['games_played'])
    )
    return features


def neutral_team_form_features(*, historical: bool) -> dict[str, float]:
    neutral = _neutral_summary()
    return _compose_features(
        neutral,
        neutral,
        available=False,
        same_day_results_included=not historical,
    )


def build_team_form_context(
    home_team: str,
    away_team: str,
    as_of: datetime | str,
    *,
    historical: bool,
    schedule_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    at = _dt(as_of)
    try:
        payload = dict(schedule_payload) if schedule_payload is not None else _season_schedule(at.astimezone(ET).year)
        games = _parse_schedule(payload)
        mapping = _team_name_map(games)
        home_id = mapping.get(_norm(home_team))
        away_id = mapping.get(_norm(away_team))
        if home_id is None or away_id is None:
            raise TeamFormUnavailable(
                f'TEAM_ID_NOT_RESOLVED:{home_team}:{away_team}'
            )
        eligible = _eligible_results(games, as_of=at, historical=historical)
        home = _team_summary(eligible, team_id=home_id, as_of=at)
        away = _team_summary(eligible, team_id=away_id, as_of=at)
        features = _compose_features(
            home,
            away,
            available=True,
            same_day_results_included=not historical,
        )
        return {
            'ok': True,
            'version': TEAM_FORM_VERSION,
            'source': TEAM_FORM_SOURCE,
            'features': features,
            'metadata': {
                'as_of': _iso(at),
                'historical': bool(historical),
                'same_day_policy': (
                    'EXCLUDE_ALL_SAME_DAY_RESULTS_FOR_HISTORICAL_LEAKAGE_SAFETY'
                    if historical else 'INCLUDE_ONLY_RESULTS_ALREADY_FINAL_AT_PULL_TIME'
                ),
                'home_team_id': int(home_id),
                'away_team_id': int(away_id),
                'eligible_results_considered': len(eligible),
                'regular_season_record_only': True,
                'recent_form_game_types': sorted(COMPETITIVE_GAME_TYPES),
            },
            'error': '',
        }
    except Exception as exc:
        return {
            'ok': False,
            'version': TEAM_FORM_VERSION,
            'source': TEAM_FORM_SOURCE,
            'features': neutral_team_form_features(historical=historical),
            'metadata': {
                'as_of': _iso(at),
                'historical': bool(historical),
                'same_day_policy': (
                    'EXCLUDE_ALL_SAME_DAY_RESULTS_FOR_HISTORICAL_LEAKAGE_SAFETY'
                    if historical else 'INCLUDE_ONLY_RESULTS_ALREADY_FINAL_AT_PULL_TIME'
                ),
            },
            'error': f'{type(exc).__name__}:{str(exc)[:500]}',
        }


def _row_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    slate = str(row.get('slate_date') or '')
    event_id = str(row.get('event_id') or '')
    sk = str(row.get('SK') or '')
    if not slate and '#' in sk:
        slate = sk.split('#', 1)[0]
    if not event_id and '#' in sk:
        event_id = sk.split('#', 1)[1]
    return slate, event_id


def _needs_rematerialization(row: Mapping[str, Any]) -> bool:
    return not (
        row.get('team_form_version') == TEAM_FORM_VERSION
        and row.get('team_form_available') is True
        and float((row.get('features') or {}).get('form_available') or 0.0) == 1.0
    )


def rematerialize_training_examples(*, Store, limit: int | None = None) -> dict[str, Any]:
    store = Store()
    rows = store.query_training_examples(limit=5000)
    rows.sort(key=lambda row: (str(row.get('commence_time') or ''), str(row.get('SK') or '')))
    missing = [row for row in rows if _needs_rematerialization(row)]
    budget = max(1, min(1000, int(limit or REMATERIALIZE_LIMIT)))
    state_before = dict(store.get_state('team_form_backfill') or {})
    previous_coverage = float(state_before.get('coverage_fraction') or 0.0)
    enriched = attempted = failed = 0
    failure_reasons: Counter[str] = Counter()

    for row in missing[:budget]:
        attempted += 1
        slate, event_id = _row_identity(row)
        home = str(row.get('home_team') or '')
        away = str(row.get('away_team') or '')
        try:
            start = _dt(row.get('commence_time'))
            source = _dt(row.get('source_pull_at')) if row.get('source_pull_at') else start - timedelta(minutes=45)
            as_of = source if source <= start else start - timedelta(minutes=45)
        except Exception as exc:
            failed += 1
            failure_reasons[f'INVALID_TIME:{type(exc).__name__}'] += 1
            continue
        if not slate or not event_id or not home or not away:
            failed += 1
            failure_reasons['MISSING_ROW_IDENTITY'] += 1
            continue
        context = build_team_form_context(home, away, as_of, historical=True)
        if not context.get('ok'):
            failed += 1
            failure_reasons[str(context.get('error') or 'TEAM_FORM_UNAVAILABLE').split(':', 1)[0]] += 1
            continue
        features = dict(row.get('features') or {})
        features.update(context['features'])
        fields = {
            'features': features,
            'team_form_version': TEAM_FORM_VERSION,
            'team_form_available': True,
            'team_form_source': TEAM_FORM_SOURCE,
            'team_form_as_of': context['metadata'].get('as_of'),
            'team_form_metadata': context['metadata'],
        }
        updater = getattr(store, 'update_training_example_features', None)
        if callable(updater):
            updater(slate, event_id, features, fields)
        else:
            replacement = dict(row)
            replacement.update(fields)
            store.put_training_example(slate, event_id, replacement)
        enriched += 1

    total = len(rows)
    already_ready = total - len(missing)
    ready_after = min(total, already_ready + enriched)
    remaining = max(0, total - ready_after)
    coverage = (ready_after / total) if total else 0.0
    feature_family_became_ready = (
        previous_coverage < LLM_READY_COVERAGE <= coverage
    )
    now = datetime.now(timezone.utc).isoformat()
    revision = int(state_before.get('feature_revision') or 0) + enriched
    state = {
        'last_run_at': now,
        'last_run_ok': failed == 0,
        'version': TEAM_FORM_VERSION,
        'source': TEAM_FORM_SOURCE,
        'attempted': attempted,
        'enriched': enriched,
        'failed': failed,
        'failure_reasons': dict(failure_reasons),
        'total_training_examples': total,
        'ready_training_examples': ready_after,
        'remaining_training_examples': remaining,
        'coverage_fraction': coverage,
        'llm_ready_coverage': LLM_READY_COVERAGE,
        'feature_family_ready_for_llm': coverage >= LLM_READY_COVERAGE,
        'feature_family_became_ready': feature_family_became_ready,
        'feature_revision': revision,
        'point_in_time_only': True,
        'historical_same_day_policy': 'EXCLUDE_ALL_SAME_DAY_RESULTS_FOR_HISTORICAL_LEAKAGE_SAFETY',
    }
    store.put_state('team_form_backfill', state)
    return {
        'ok': True,
        'action': 'TEAM_FORM_REMATERIALIZATION',
        **state,
    }


def status_payload(*, Store) -> dict[str, Any]:
    state = dict(Store().get_state('team_form_backfill') or {})
    return {
        'enabled': True,
        'version': state.get('version') or TEAM_FORM_VERSION,
        'source': state.get('source') or TEAM_FORM_SOURCE,
        'point_in_time_only': True,
        'record_features': True,
        'last3_features': True,
        'last7_features': True,
        'win_loss_streak_features': True,
        'result_sequence_lags': 7,
        'run_differential_features': True,
        'rest_and_congestion_features': True,
        'llm_may_select_or_transform': True,
        'last_run_at': state.get('last_run_at'),
        'last_run_ok': state.get('last_run_ok'),
        'attempted': state.get('attempted'),
        'enriched': state.get('enriched'),
        'failed': state.get('failed'),
        'failure_reasons': state.get('failure_reasons') or {},
        'total_training_examples': state.get('total_training_examples'),
        'ready_training_examples': state.get('ready_training_examples'),
        'remaining_training_examples': state.get('remaining_training_examples'),
        'coverage_fraction': state.get('coverage_fraction'),
        'feature_family_ready_for_llm': bool(state.get('feature_family_ready_for_llm')),
        'feature_revision': state.get('feature_revision'),
        'historical_same_day_policy': state.get('historical_same_day_policy') or (
            'EXCLUDE_ALL_SAME_DAY_RESULTS_FOR_HISTORICAL_LEAKAGE_SAFETY'
        ),
    }
