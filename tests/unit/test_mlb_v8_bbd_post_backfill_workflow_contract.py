from pathlib import Path


def test_supervised_workflow_runs_once_per_hour_after_backfills():
    trainer = Path('.github/workflows/mlb-supervised-shadow-v2-recurring.yml').read_text()
    context = Path('.github/workflows/mlb-v8-historical-context-backfill.yml').read_text()

    assert 'workflow_run:' not in trainer
    assert "cron: '7 * * * *'" in trainer
    assert 'mlb_v8_historical_bbs_backfill_latest.json' not in trainer
    assert "if: steps.evaluation.outcome == 'success'" in trainer
    assert 'cancel-in-progress: false' in trainer
    assert 'gh workflow run mlb-supervised-shadow-v2-recurring.yml' not in context
    assert 'cancel-in-progress: false' in context


def test_mlb_publishers_refresh_clean_main_before_monotonic_compare():
    retired = Path('.github/workflows/mlb-v8-historical-bbs-backfill.yml')
    assert not retired.exists()

    paths = [
        '.github/workflows/mlb-supervised-shadow-v2-recurring.yml',
        '.github/workflows/mlb-v8-historical-context-backfill.yml',
        '.github/workflows/mlb-v8-shadow-realtime-72.yml',
        '.github/workflows/mlb-historical-supervised-v9-shadow.yml',
    ]
    for path in paths:
        text = Path(path).read_text()
        assert 'git reset --hard origin/main' in text, path
        assert 'git clean -fd' in text, path


def test_realtime_shadow_is_staggered_after_trainer():
    text = Path('.github/workflows/mlb-v8-shadow-realtime-72.yml').read_text()
    assert "cron: '27 * * * *'" in text
