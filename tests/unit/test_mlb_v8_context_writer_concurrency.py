from __future__ import annotations

import re
from pathlib import Path


LIVE_CONTEXT_WRITERS = (
    Path(".github/workflows/mlb-v8-autonomous-controller.yml"),
    Path(".github/workflows/mlb-v8-context-acceleration.yml"),
    Path(".github/workflows/mlb-v8-weather-runtime-repair.yml"),
)
EXPECTED_GROUP = "mlb-v8-autonomous-control-plane"


def _concurrency_group(path: Path) -> str:
    source = path.read_text()
    match = re.search(r"(?m)^\s*group:\s*([^\s#]+)\s*$", source)
    assert match, f"missing concurrency group in {path}"
    return match.group(1)


def test_all_live_context_writers_share_one_serialized_control_plane():
    groups = {path: _concurrency_group(path) for path in LIVE_CONTEXT_WRITERS}

    assert set(groups.values()) == {EXPECTED_GROUP}, groups
    for path in LIVE_CONTEXT_WRITERS:
        source = path.read_text()
        assert "cancel-in-progress: false" in source
        assert "run_mlb_v8_historical_context_backfill" in source
