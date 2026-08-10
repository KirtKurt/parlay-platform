from __future__ import annotations

import copy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_fundamentals_scoring_bridge_v1 as bridge
import mlb_fundamentals_snapshot_v1 as snapshot_v1
import mlb_fundamentals_snapshot_v2 as snapshot_v2


def _row():
    return {
        "gameId": "official:deterministic-1",
        "slateDateEt": "2026-08-10",
        "predictionSourcePullAt": "2026-08-10T17:00:00+00:00",
        "predictionSourcePullId": "pull-deterministic-1",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "advanced_context": {},
    }


def test_v2_rebuild_uses_immutable_source_pull_time_and_fingerprint():
    bridge.install_snapshot_determinism(snapshot_v2)
    first = snapshot_v2.build(copy.deepcopy(_row()))
    second = snapshot_v2.build(copy.deepcopy(_row()))

    assert first["createdAtUtc"] == "2026-08-10T17:00:00+00:00"
    assert second["createdAtUtc"] == first["createdAtUtc"]
    assert second["fingerprint"] == first["fingerprint"]
    assert snapshot_v2.validate(first) == []
    assert snapshot_v2.validate(second) == []


def test_v2_missing_source_time_uses_stable_ineligible_sentinel():
    bridge.install_snapshot_determinism(snapshot_v2)
    row = _row()
    row.pop("predictionSourcePullAt")

    first = snapshot_v2.build(copy.deepcopy(row))
    second = snapshot_v2.build(copy.deepcopy(row))

    assert first["createdAtUtc"] == bridge.DETERMINISTIC_MISSING_CAPTURE_TIME
    assert second["fingerprint"] == first["fingerprint"]
    assert first["trainingEligibleAtCapture"] is False
    assert "fundamentals_v2_source_pull_timestamp_missing" in first[
        "trainingExclusionReasons"
    ]


def test_legacy_snapshot_rebuild_is_deterministic_too():
    first = snapshot_v1.build(copy.deepcopy(_row()))
    second = snapshot_v1.build(copy.deepcopy(_row()))

    assert first["createdAtUtc"] == "2026-08-10T17:00:00+00:00"
    assert second == first
