from types import SimpleNamespace
from datetime import date, datetime, timezone

from mlb_auto import historical_backfill as history
from mlb_auto.historical_backfill import build_example
from mlb_auto.team_form import TEAM_FORM_VERSION


def _event():
    return {
        'id': 'hist-event-1',
        'sport_key': 'baseball_mlb',
        # UTC day is Aug 13, but the authoritative MLB slate is Aug 12 ET.
        'commence_time': '2026-08-13T01:10:00+00:00',
        'home_team': 'Boston Red Sox',
        'away_team': 'New York Yankees',
        'bookmakers': [
            {'key': 'fanduel', 'markets': [
                {'key': 'h2h', 'outcomes': [
                    {'name': 'Boston Red Sox', 'price': -120},
                    {'name': 'New York Yankees', 'price': 110},
                ]},
                {'key': 'spreads', 'outcomes': [
                    {'name': 'Boston Red Sox', 'price': 105, 'point': -1.5},
                    {'name': 'New York Yankees', 'price': -125, 'point': 1.5},
                ]},
                {'key': 'totals', 'outcomes': [
                    {'name': 'Over', 'price': -110, 'point': 8.5},
                    {'name': 'Under', 'price': -110, 'point': 8.5},
                ]},
            ]},
            {'key': 'draftkings', 'markets': [
                {'key': 'h2h', 'outcomes': [
                    {'name': 'Boston Red Sox', 'price': -118},
                    {'name': 'New York Yankees', 'price': 108},
                ]},
            ]},
        ],
    }


class FakeClient:
    def historical_featured_odds(self, snapshot_at):
        return SimpleNamespace(data={'data': [_event()]})

    def historical_event_odds(self, event_id, snapshot_at, markets):
        return SimpleNamespace(data={'data': _event()})


def _team_form(home, away, as_of, *, historical):
    assert historical is True
    return {
        'ok': True,
        'features': {
            'form_available': 1.0,
            'form_home_wins': 70.0,
            'form_away_wins': 68.0,
            'form_home_last3_wins': 2.0,
            'form_away_last7_wins': 4.0,
            'form_home_streak_signed': 2.0,
            'form_away_streak_signed': -1.0,
        },
        'metadata': {
            'as_of': datetime.fromisoformat(str(as_of)).astimezone(timezone.utc).isoformat(),
            'historical': True,
        },
        'error': '',
    }


def test_historical_example_is_point_in_time_t10_locked_and_et_dated():
    game = {
        'game_pk': '123',
        'commence_time': '2026-08-13T01:10:00+00:00',
        'home_team': 'Boston Red Sox',
        'away_team': 'New York Yankees',
        'home_score': 6,
        'away_score': 4,
    }
    row, audit = build_example(
        FakeClient(),
        game,
        ['h2h_1st_5_innings', 'pitcher_strikeouts'],
        team_form_builder=_team_form,
    )
    assert row is not None
    assert row['historical'] is True
    assert row['historical_source'] == 'THE_ODDS_API_POINT_IN_TIME'
    assert row['label_source'] == 'MLB_STATS_FINAL_SCORE'
    assert row['label_home_win'] == 1
    assert row['slate_date'] == '2026-08-12'
    assert row['source_before_or_at_cutoff'] is True
    assert row['lock_minutes'] == 10
    assert row['team_form_available'] is True
    assert row['team_form_version'] == TEAM_FORM_VERSION
    assert row['features']['form_home_last3_wins'] == 2
    assert row['features']['form_away_last7_wins'] == 4
    start = datetime.fromisoformat(row['commence_time'])
    source = datetime.fromisoformat(row['source_pull_at'])
    assert (start - source).total_seconds() == 10 * 60
    assert row['lock_cutoff_at'] == source.isoformat()
    assert audit['history_points'] == 5
    assert audit['team_form_available'] is True


def test_historical_example_requires_t10_authority_snapshot():
    class MissingT10Client(FakeClient):
        def historical_featured_odds(self, snapshot_at):
            if str(snapshot_at).startswith('2026-08-13T01:00:00'):
                return SimpleNamespace(data={'data': []})
            return super().historical_featured_odds(snapshot_at)

    game = {
        'game_pk': '123',
        'commence_time': '2026-08-13T01:10:00+00:00',
        'home_team': 'Boston Red Sox',
        'away_team': 'New York Yankees',
        'home_score': 6,
        'away_score': 4,
    }

    row, audit = build_example(MissingT10Client(), game, [])

    assert row is None
    assert audit['reason'] == 'NO_T10'
    assert audit['history_points'] == 4


def _stats_game(
    game_pk,
    *,
    abstract_state,
    detailed_state,
    game_date='2026-08-13T23:10:00Z',
    home_score=5,
    away_score=3,
):
    return {
        'gamePk': game_pk,
        'gameDate': game_date,
        'status': {
            'abstractGameState': abstract_state,
            'detailedState': detailed_state,
        },
        'teams': {
            'home': {'team': {'name': f'Home {game_pk}'}, 'score': home_score},
            'away': {'team': {'name': f'Away {game_pk}'}, 'score': away_score},
        },
    }


class BackfillStore:
    def __init__(self, historical_state=None):
        self.states = {
            'controller': {},
            'historical_backfill': dict(historical_state or {}),
        }
        self.training = []
        self.archives = []

    def get_state(self, name='controller'):
        return dict(self.states.get(name) or {})

    def put_state(self, name, item, **_kwargs):
        self.states.setdefault(name, {}).update(dict(item))

    def archive_json(self, key, payload):
        self.archives.append((key, dict(payload)))

    def put_training_example(self, slate, event_id, item):
        self.training.append((slate, event_id, dict(item)))

    def query_training_examples(self, limit=5000):
        return [item for _, _, item in self.training[:limit]]


def _complete_schedule(day, games):
    return {
        'date': day.isoformat(),
        'status': 'COMPLETE',
        'complete': True,
        'scheduled_game_count': len(games),
        'terminal_game_count': len(games),
        'final_game_count': len(games),
        'terminal_nonfinal_count': 0,
        'nonterminal_game_count': 0,
        'nonterminal_game_pks': [],
        'unusable_final_game_count': 0,
        'games': list(games),
    }


def test_mixed_final_and_in_progress_schedule_waits_without_advancing(monkeypatch):
    payload = {
        'dates': [{
            'games': [
                _stats_game('final-1', abstract_state='Final', detailed_state='Final'),
                _stats_game('live-2', abstract_state='Live', detailed_state='In Progress'),
            ],
        }],
    }
    monkeypatch.setattr(history, '_stats_get', lambda *_args, **_kwargs: payload)
    schedule = history.daily_schedule(date(2026, 8, 13))
    assert schedule['complete'] is False
    assert schedule['status'] == 'WAITING_FOR_FINALS'
    assert schedule['scheduled_game_count'] == 2
    assert schedule['final_game_count'] == 1
    assert schedule['nonterminal_game_pks'] == ['live-2']

    store = BackfillStore({'cursor_date': '2026-08-13', 'game_index': 0})
    monkeypatch.setattr(history, 'Store', lambda: store)
    monkeypatch.setattr(history, 'OddsApiClient', lambda: object())
    monkeypatch.setattr(history, 'daily_schedule', lambda _day: schedule)

    result = history.run_historical_backfill(days_per_run=1, max_games_per_run=4)

    assert result['attempted'] == 0
    assert result['next_cursor_date'] == '2026-08-13'
    assert result['next_game_index'] == 0
    assert result['waiting_for_finals'] is True
    assert result['lock_minutes'] == 10
    assert store.training == []
    assert store.states['historical_backfill']['cursor_date'] == '2026-08-13'
    assert store.states['historical_backfill']['last_run_status'] == 'WAITING_FOR_FINALS'
    assert store.states['historical_backfill']['last_schedule_nonterminal_game_pks'] == ['live-2']
    assert store.states['historical_backfill']['lock_minutes'] == 10


def test_initial_cursor_at_0200_utc_uses_prior_completed_et_slate(monkeypatch):
    # 02:00 UTC Aug 14 is still 22:00 ET Aug 13; Aug 12 is the last completed ET date.
    monkeypatch.setattr(
        history,
        '_now',
        lambda: datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc),
    )
    store = BackfillStore()
    seen_dates = []
    monkeypatch.setattr(history, 'Store', lambda: store)
    monkeypatch.setattr(history, 'OddsApiClient', lambda: object())

    def schedule(day):
        seen_dates.append(day)
        return _complete_schedule(day, [])

    monkeypatch.setattr(history, 'daily_schedule', schedule)

    result = history.run_historical_backfill(days_per_run=1, max_games_per_run=1)

    assert seen_dates == [date(2026, 8, 12)]
    assert result['next_cursor_date'] == '2026-08-11'
    assert store.states['historical_backfill']['total_days_processed'] == 1


def test_completed_day_resumes_by_game_index_without_duplicates(monkeypatch):
    target = date(2026, 8, 12)
    games = [
        {'game_pk': 'g1', 'commence_time': '2026-08-12T20:00:00+00:00'},
        {'game_pk': 'g2', 'commence_time': '2026-08-12T23:00:00+00:00'},
    ]
    store = BackfillStore({'cursor_date': target.isoformat(), 'game_index': 0})
    monkeypatch.setattr(history, 'Store', lambda: store)
    monkeypatch.setattr(history, 'OddsApiClient', lambda: object())
    monkeypatch.setattr(history, 'daily_schedule', lambda day: _complete_schedule(day, games))

    def build(_client, game, _market_keys):
        row = {
            'slate_date': target.isoformat(),
            'event_id': game['game_pk'],
            'commence_time': game['commence_time'],
        }
        return row, {'reason': 'OK'}

    monkeypatch.setattr(history, 'build_example', build)

    first = history.run_historical_backfill(days_per_run=1, max_games_per_run=1)
    assert first['next_cursor_date'] == target.isoformat()
    assert first['next_game_index'] == 1
    assert [event_id for _, event_id, _ in store.training] == ['g1']

    second = history.run_historical_backfill(days_per_run=1, max_games_per_run=1)
    assert second['next_cursor_date'] == '2026-08-11'
    assert second['next_game_index'] == 0
    assert [event_id for _, event_id, _ in store.training] == ['g1', 'g2']
    assert store.states['historical_backfill']['total_games_processed'] == 2
    assert store.states['historical_backfill']['total_days_processed'] == 1


def test_v2_wrapper_forwards_bounded_game_budget(monkeypatch):
    import mlb_auto.historical_backfill_v2 as v2

    class FakeStore:
        def query_predictions(self, slate): return []
        def get_state(self, name='controller'): return {}
        def put_state(self, name, item): pass

    seen = {}
    monkeypatch.setattr(v2, 'Store', FakeStore)
    monkeypatch.setattr(v2, '_run', lambda **kw: seen.update(kw) or {'ok': True})
    out = v2.run_historical_backfill(days_per_run=1, max_games_per_run=1)
    assert out == {'ok': True}
    assert seen == {'days_per_run': 1, 'max_games_per_run': 1}
