from sackmann import is_complete_match


def test_completed_set_score_counts():
    assert is_complete_match({"score": "6-4 6-2"}) is True
    assert is_complete_match({"score": "7-6(5) 3-6 6-3"}) is True


def test_retirements_and_walkovers_dropped():
    assert is_complete_match({"score": "6-3 2-1 RET"}) is False
    assert is_complete_match({"score": "W/O"}) is False
    assert is_complete_match({"score": "Walkover"}) is False
    assert is_complete_match({"score": "3-0 DEF"}) is False
    assert is_complete_match({"score": ""}) is False
