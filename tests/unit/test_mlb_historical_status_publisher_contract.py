from pathlib import Path


def test_historical_status_publisher_is_race_safe():
    source = Path('.github/workflows/mlb-historical-status-snapshot.yml').read_text()
    assert 'git checkout -B historical-live-status origin/main' in source
    assert 'cp /tmp/status-summary-copy.json docs/runtime/mlb-historical-live-status.json' in source
    assert 'git pull --rebase origin main' not in source
    assert 'for attempt in 1 2 3 4 5' in source
