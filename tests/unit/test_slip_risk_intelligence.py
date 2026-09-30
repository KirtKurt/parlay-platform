from inqsi_intelligence.slip_risk import build_binary_risk_assessment, rank_assessments

def sample(**overrides):
    row=dict(event_id="g1",sport="MLB",home="Yankees",away="Red Sox",selection="Yankees",
      market_type="moneyline",p_fundamental_home=.54,p_market_aware_home=.56,p_market_home=.59,
      books_moved_against=5,books_tracked=7,movement_minutes=40,selected_price_american=-145,best_price_american=-137,
      signals=[{"code":"OPP_LINEUP_IMPROVED","label":"Confirmed lineup","severity":"ELEVATED",
        "summary":"Boston's confirmed lineup improved the opposing matchup.","source":"BBD","confirmed":True}])
    row.update(overrides); return row

def test_builds_three_probability_views_and_plain_english_message():
    out=build_binary_risk_assessment(**sample())
    assert out["authority_changed"] is False
    assert out["probabilities"]["fundamentals"]==.54
    assert out["probabilities"]["market_aware"]==.56
    assert out["probabilities"]["sportsbook_implied"]==.59
    assert out["probabilities"]["market_influence"]==.02
    assert "5 of 7 tracked sportsbooks" in out["consumer_message"]
    assert "independent fundamentals" in out["consumer_message"]
    assert "Boston's confirmed lineup" in out["consumer_message"]

def test_better_price_is_exposed_without_requiring_book_selection():
    out=build_binary_risk_assessment(**sample())
    assert out["market"]["better_price"]["best_price_american"]==-137

def test_away_selection_probabilities_are_inverted():
    out=build_binary_risk_assessment(**sample(selection="Red Sox"))
    assert out["probabilities"]["fundamentals"]==.46
    assert out["probabilities"]["market_aware"]==.44
    assert out["probabilities"]["sportsbook_implied"]==.41

def test_uncertain_signal_is_not_presented_as_confirmed():
    s={"code":"WEATHER","summary":"Weather may deteriorate.","severity":"MODERATE","source":"weather","confirmed":False}
    out=build_binary_risk_assessment(**sample(signals=[s]))
    assert out["confirmed_signals"]==[]
    assert out["monitoring_signals"][0]["code"]=="WEATHER"

def test_rank_highest_risk_first():
    a=build_binary_risk_assessment(**sample(event_id="a"))
    b=build_binary_risk_assessment(**sample(event_id="b",books_moved_against=0,signals=[],
      p_fundamental_home=.58,p_market_aware_home=.58,p_market_home=.59))
    ranked=rank_assessments([b,a])
    assert ranked[0]["event_id"]=="a"
