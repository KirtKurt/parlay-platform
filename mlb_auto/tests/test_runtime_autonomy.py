from mlb_auto.ml import Model
from mlb_auto.provider_open import OpenEndedOddsApiClient
from mlb_auto.schedule_controller import desired_interval_minutes


def test_interaction_model_materializes_live_features():
    model = Model(0.0, (1.0, 1.0), ('a__sq', 'a__x__b'))
    p = model.predict({'a': 2.0, 'b': 3.0})
    assert p > 0.99


def test_open_ended_market_discovery_keeps_unknown_keys():
    payload = [
        {'key': 'book', 'markets': [
            {'key': 'h2h'},
            {'key': 'totally_new_mlb_market_2026'},
            {'key': 'pitcher_strikeouts'},
        ]}
    ]
    keys = OpenEndedOddsApiClient.useful_markets(payload)
    assert keys == ['h2h', 'pitcher_strikeouts', 'totally_new_mlb_market_2026']


def test_market_change_can_force_five_minute_cadence_far_from_first_pitch():
    assert desired_interval_minutes(30, missing_market_fraction=.40) == 5
    assert desired_interval_minutes(30, new_event_fraction=.40) == 5
    assert desired_interval_minutes(30, recent_signal_change=.03) == 5
