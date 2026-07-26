from pathlib import Path


def test_hourly_liveness_publisher_is_race_safe():
    source = Path(
        ".github/workflows/mlb-historical-hourly-liveness-v1.yml"
    ).read_text()

    assert "git checkout -B historical-hourly-liveness-proof origin/main" in source
    assert "Unable to publish MLB historical hourly liveness proof after bounded retries" in source
    assert "git pull --rebase origin main" not in source
    assert "cp \"$PROOF_PATH\" /tmp/mlb-historical-hourly-liveness-proof.json" in source
    assert "runId" in source
    assert "runUrl" in source
