from backend.src.slip_scanner_service import scan_selection

ROW={"game_id":"g1","home_team":"Yankees","away_team":"Red Sox","p_fundamental_home":.54,
     "p_home":.56,"market_home_prob":.59}

def test_structured_mlb_moneyline_scan():
    out=scan_selection({"sport":"MLB","market_type":"moneyline","selection":"Yankees"},ROW)
    assert out["ok"] is True and out["status"]==200

def test_not_ready_is_explicit():
    row=dict(ROW); del row["p_fundamental_home"]
    out=scan_selection({"sport":"MLB","market_type":"moneyline","selection":"Yankees"},row)
    assert out["status"]==503
    assert out["error"]=="scanner_intelligence_not_ready"

def test_unknown_market_does_not_fake_support():
    out=scan_selection({"sport":"MLB","market_type":"player_prop","selection":"x"},ROW)
    assert out["status"]==400
