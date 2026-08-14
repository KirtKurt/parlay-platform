from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_ml_training_envelope_bridge_v1 as bridge


def _row():
    commence = datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
    lock_at = commence - timedelta(minutes=45)
    source_at = lock_at - timedelta(seconds=5)
    return {
        "officialGamePk": "1",
        "slateDateEt": "2026-08-05",
        "commenceTime": commence.isoformat(),
        "lockedAtUtc": lock_at.isoformat(),
        "predictionSourcePullAt": source_at.isoformat(),
        "lockedPrediction": True,
        "officialPrediction": True,
        "lockedAmericanOdds": -110,
        "settled": True,
        "actualWinner": "Home",
        "frozenFeatureVector": {
            "officialGamePk": "1",
            "lockAtUtc": lock_at.isoformat(),
            "sourcePullAtUtc": source_at.isoformat(),
            "fingerprint": "a" * 64,
            "features": {"selected_probability": 0.58},
        },
    }


def test_bridge_removes_only_strict_envelope_proven_alias_reasons(monkeypatch):
    module = SimpleNamespace()

    def validate_training_row(row):
        # current_canonical_row_not_training_eligible
        return [
            "current_canonical_row_not_training_eligible",
            "missing_selected_side_locked_odds",
            "incomplete_or_unproven_slate_coverage",
        ]

    module.validate_training_row = validate_training_row
    monkeypatch.setattr(bridge, "_INSTALLED", False)
    monkeypatch.setattr(bridge, "_WRAPPED", [])
    status = bridge.install(module)
    assert status["installed"] is True
    row = _row()
    result = module.validate_training_row(row)
    assert result == ["incomplete_or_unproven_slate_coverage"]
    assert row["mlbT45TrainingEnvelope"]["eligible"] is True


def test_bridge_does_not_override_invalid_or_reconstructed_row(monkeypatch):
    module = SimpleNamespace()

    def validate_training_row(row):
        # current_canonical_row_not_training_eligible
        return ["current_canonical_row_not_training_eligible"]

    module.validate_training_row = validate_training_row
    monkeypatch.setattr(bridge, "_INSTALLED", False)
    monkeypatch.setattr(bridge, "_WRAPPED", [])
    bridge.install(module)
    row = _row()
    row.pop("lockedAtUtc")
    assert module.validate_training_row(row) == ["current_canonical_row_not_training_eligible"]
    assert row["mlbT45TrainingEnvelope"]["eligible"] is False
