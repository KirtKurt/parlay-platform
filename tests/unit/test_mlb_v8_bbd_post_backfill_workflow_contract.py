from pathlib import Path


def _workflow(path):
    return Path(path).read_text()


def test_retired_v8_workflows_delegate_to_one_scheduled_controller():
    trainer = _workflow('.github/workflows/mlb-supervised-shadow-v2-recurring.yml')
    context = _workflow('.github/workflows/mlb-v8-historical-context-backfill.yml')
    realtime = _workflow('.github/workflows/mlb-v8-shadow-realtime-72.yml')
    controller = _workflow('.github/workflows/mlb-v8-autonomous-controller.yml')

    for source in (trainer, context, realtime):
        assert "cron:" not in source
        assert 'mlb_v8_historical_bbs_backfill_latest.json' not in source
        assert 'cancel-in-progress: false' in source
        assert 'mlb-v8-autonomous-controller.yml' in source
    assert "cron: '8/15 * * * *'" in controller
    assert 'group: mlb-v8-autonomous-control-plane' in controller
    assert 'cancel-in-progress: false' in controller
    assert "MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED: 'false'" in controller
    assert "MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED: 'false'" in controller


def test_only_controller_owns_v8_monotonic_latest_publication():
    retired = Path('.github/workflows/mlb-v8-historical-bbs-backfill.yml')
    assert not retired.exists()

    compatibility_paths = [
        '.github/workflows/mlb-supervised-shadow-v2-recurring.yml',
        '.github/workflows/mlb-v8-historical-context-backfill.yml',
        '.github/workflows/mlb-v8-shadow-realtime-72.yml',
    ]
    for path in compatibility_paths:
        text = _workflow(path)
        assert 'Publish monotonic latest state' not in text, path
        assert 'git push origin HEAD:main' not in text, path

    controller = _workflow('.github/workflows/mlb-v8-autonomous-controller.yml')
    assert 'Publish monotonic latest state' in controller
    assert 'git reset --hard refs/remotes/origin/main' in controller
    assert 'git clean -fd' in controller
    assert 'git push origin HEAD:main' in controller

    v9 = _workflow('.github/workflows/mlb-historical-supervised-v9-shadow.yml')
    assert "cron: '17 * * * *'" in v9
    assert 'group: mlb-historical-v7-v9-shadow-${{ github.ref }}' in v9


def test_realtime_compatibility_cannot_run_an_independent_shadow_cycle():
    text = _workflow('.github/workflows/mlb-v8-shadow-realtime-72.yml')
    assert "schedule:" not in text
    assert 'delegate-to-autonomous-controller' in text
    assert 'gh workflow run mlb-v8-autonomous-controller.yml' in text
    assert "value['authority'] == 'SHADOW_ONLY'" in text
    assert "value['productionAuthorityChanged'] is False" in text
    assert "value['automaticWagerAllowed'] is False" in text
