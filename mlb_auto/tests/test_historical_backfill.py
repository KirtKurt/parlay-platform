from types import SimpleNamespace
from datetime import datetime, timezone

from mlb_auto.historical_backfill import build_example


def _event():
    return {
        'id': 'hist-event-1',
        'sport_key': 'baseball_mlb',
        'commence_time': '2026-08-12T23:10:00+00:00',
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


def test_historical_example_is_point_in_time_and_t45_locked():
    game = {
        'game_pk': '123',
        'commence_time': '2026-08-12T23:10:00+00:00',
        'home_team': 'Boston Red Sox',
        'away_team': 'New York Yankees',
        'home_score': 6,
        'away_score': 4,
    }
    row, audit = build_example(FakeClient(), game, ['h2h_1st_5_innings', 'pitcher_strikeouts'])
    assert row is not None
    assert row['historical'] is True
    assert row['historical_source'] == 'THE_ODDS_API_POINT_IN_TIME'
    assert row['label_source'] == 'MLB_STATS_FINAL_SCORE'
    assert row['label_home_win'] == 1
    assert row['source_before_or_at_cutoff'] is True
    start = datetime.fromisoformat(row['commence_time'])
    source = datetime.fromisoformat(row['source_pull_at'])
    assert (start - source).total_seconds() == 45 * 60
    assert audit['history_points'] == 5


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
