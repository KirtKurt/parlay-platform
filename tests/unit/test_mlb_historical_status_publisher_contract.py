from pathlib import Path


def test_historical_status_publisher_is_race_safe_and_evidence_honest():
    source = Path(
        ".github/workflows/mlb-historical-status-snapshot.yml"
    ).read_text()
    assert (
        "git checkout -B historical-live-status origin/main"
        in source
    )
    assert (
        "cp /tmp/status-summary-copy.json "
        "docs/runtime/mlb-historical-live-status.json"
        in source
    )
    assert "git pull --rebase origin main" not in source
    assert "for attempt in 1 2 3 4 5" in source
    assert "scripts/build_mlb_historical_status.py" in source
    assert "aws lambda get-function-configuration" in source


def test_next_round_workflow_is_readiness_aware():
    source = Path(
        ".github/workflows/mlb-historical-next-round-catchup.yml"
    ).read_text()
    assert "EXPECTED_END_DATE='2026-07-24'" not in source
    assert (
        "scripts/run_mlb_historical_next_round_catchup.py"
        in source
    )
    assert "--template mlb_historical_optimizer/template.yaml" in source
    assert "Advance only when the canonical next round is ready" in source
