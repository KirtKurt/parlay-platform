from pathlib import Path


def test_material_handoff_is_explicit_idempotent_and_fail_closed():
    source = Path(
        ".github/workflows/mlb-v10-material-handoff.yml"
    ).read_text()

    assert "actions: write" in source
    assert "MLB Historical Hourly Liveness V1" in source
    assert "decide_mlb_v10_dispatch.py" in source
    assert "dispatch_required == 'true'" in source
    assert "queued\" or .status == \"in_progress" in source
    assert "/actions/workflows/${V10_WORKFLOW}/dispatches" in source
    assert "assert value.get('ok') is True" in source
    assert "dispatchIssued" in source
    assert "activeV10RunCount" in source
