from pathlib import Path


def test_one_time_v7_v8_test_is_read_only_and_cost_bounded():
    source = Path("scripts/run_mlb_v7_v8_today_test.py").read_text()
    assert 'MAX_ONE_TIME_ESTIMATED_CREDITS = 150' in source
    assert 'automaticWagerAllowed": False' in source
    assert 'productionAuthorityChanged": False' in source
    assert 'store=False' in source
    assert 'ConditionExpression' not in source
    assert '.put_item(' not in source
    assert '.put_object(' not in source


def test_one_time_workflow_has_no_schedule():
    source = Path(".github/workflows/mlb-v7-v8-today-test-once.yml").read_text()
    assert "schedule:" not in source
    assert "workflow_dispatch:" in source
    assert "TEST_DATE_ET: '2026-07-26'" in source
