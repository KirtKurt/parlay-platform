from pathlib import Path

import yaml


def test_material_handoff_is_explicit_idempotent_and_fail_closed():
    source = Path(
        ".github/workflows/mlb-v10-material-handoff.yml"
    ).read_text()
    document = yaml.load(source, Loader=yaml.BaseLoader)

    assert "actions: write" in source
    assert set(document["on"]) == {"workflow_dispatch"}
    assert "decide_mlb_v10_dispatch.py" in source
    assert "dispatch_required == 'true'" in source
    assert "queued\" or .status == \"in_progress" in source
    assert "/actions/workflows/${V10_WORKFLOW}/dispatches" in source
    assert "assert value.get('ok') is True" in source
    assert "dispatchIssued" in source
    assert "activeV10RunCount" in source
