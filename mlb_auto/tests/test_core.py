from datetime import datetime, timezone, timedelta

from mlb_auto.schedule_controller import desired_interval_minutes, decide_pull
from mlb_auto.engine import devig_pair, temporal_features
from mlb_auto.repair import diagnose, validate_self_repair


def test_adaptive_cadence():
    assert desired_interval_minutes(30) == 60
    assert desired_interval_minutes(10) == 15
    assert desired_interval_minutes(5) == 10
    assert desired_interval_minutes(1) == 5
    assert desired_interval_minutes(10, volatility=.03) == 5


def test_heartbeat_can_skip_api_spend():
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    events = [{'commence_time': (now + timedelta(hours=8)).isoformat()}]
    d = decide_pull(now=now, events=events, last_pull_at=now - timedelta(minutes=5))
    assert not d.should_pull and d.next_interval_minutes == 15


def test_devig():
    h, a = devig_pair(-110, -110)
    assert round(h, 6) == .5 and round(a, 6) == .5


def test_temporal():
    f = temporal_features([.50, .51, .505, .52])
    assert f['move'] > 0 and f['reversals'] >= 1


def test_repairs_are_self_scoped_actions():
    actions = diagnose({
        'minutes_since_last_pull': 40,
        'expected_interval_minutes': 10,
        'missing_market_fraction': .4,
        'unsettled_completed_games': 2,
    })
    names = {x.action for x in actions}
    assert {'FORCE_INGEST', 'REDISCOVER_EVENT_MARKETS', 'RUN_SETTLEMENT'} <= names
    for action in actions:
        validate_self_repair(action)
