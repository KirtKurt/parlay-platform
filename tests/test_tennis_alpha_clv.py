from clv import clv_report, fair_pair
from live_signals import live_signals
from ratings import RatingStore


def test_favorite_has_fair_p_above_half():
    assert fair_pair(-200, 170) > 0.6


def test_model_beats_close_when_closer_to_result():
    report = clv_report(0.72, -150, 130, -180, 150, True)
    assert report["beat_close"] is True
    assert report["clv"] != 0


def test_live_signals_use_stored_elo():
    store = RatingStore()
    for i in range(15):
        store.observe("Ace Player", "Pushed Player", "hard", 20240101 + i)
    sig = live_signals(store, "A Player", "Pushed Player", -140, 120, "hard", False, 20250101)
    assert sig["elo_diff"] > 0
    assert sig["matched_player"] == "Ace Player"
