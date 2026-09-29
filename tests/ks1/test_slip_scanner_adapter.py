import pytest
from ks1.slip_scanner_adapter import ScannerInputUnavailable, build_ks1_moneyline_scan

def row():
    return {"game_id":"g1","home_team":"Yankees","away_team":"Red Sox","p_fundamental_home":.54,
      "p_home":.56,"market_home_prob":.59,"as_of":"2026-09-29T20:00:00Z",
      "books_moved_against":5,"books_tracked":7,"movement_minutes":40,
      "scanner_signals":[{"code":"LINEUP","summary":"Boston's confirmed lineup improved the opposing matchup.",
       "severity":"ELEVATED","source":"BBD","confirmed":True,"active_model_signal":True}]}

def test_ks1_adapter_preserves_three_distinct_views():
    out=build_ks1_moneyline_scan(row(),selection="Yankees")
    assert out["probabilities"]["fundamentals"]==.54
    assert out["probabilities"]["market_aware"]==.56
    assert out["probabilities"]["sportsbook_implied"]==.59
    assert out["authority_changed"] is False

def test_missing_fundamentals_fails_closed_instead_of_reusing_active_or_market():
    r=row(); del r["p_fundamental_home"]
    with pytest.raises(ScannerInputUnavailable,match="p_fundamental_home"):
        build_ks1_moneyline_scan(r,selection="Yankees")

def test_missing_market_fails_closed():
    r=row(); r["market_home_prob"]=None
    with pytest.raises(ScannerInputUnavailable,match="market_home_prob"):
        build_ks1_moneyline_scan(r,selection="Yankees")
