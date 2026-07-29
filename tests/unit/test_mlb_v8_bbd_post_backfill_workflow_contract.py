from pathlib import Path


def test_supervised_workflow_retrains_after_successful_bbd_backfill():
    text = Path('.github/workflows/mlb-supervised-shadow-v2-recurring.yml').read_text()

    assert 'workflow_run:' in text
    assert 'MLB V8 Historical BBD Prior-Game Backfill' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert 'github.event.workflow_run.head_sha' in text
    assert "cancel-in-progress: false" in text
