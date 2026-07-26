from datetime import datetime, timedelta, timezone
import math
import pytest

from mlb_v7_integrity_pattern_v1 import (
    candidate_rank,
    canonicalize_slots,
    interaction_features,
    strict_binary_label,
    temporal_pattern_features,
    validate_training_rows,
)


def test_missing_label_fails_closed():
    with pytest.raises(ValueError): strict_binary_label({})
    with pytest.raises(ValueError): strict_binary_label({"homeWon": None})
    assert strict_binary_label({"homeWon": 0}) == 0
    assert strict_binary_label({"homeWon": True}) == 1


def test_canonicalize_slots_deduplicates_and_excludes_post_lock():
    base = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    observations = [
        {"observedAt": (base + timedelta(minutes=1)).isoformat(), "deVigProbability": .51},
        {"observedAt": (base + timedelta(minutes=8)).isoformat(), "deVigProbability": .52},
        {"observedAt": (base + timedelta(minutes=16)).isoformat(), "deVigProbability": .54},
        {"observedAt": (base + timedelta(minutes=46)).isoformat(), "deVigProbability": .60},
    ]
    rows, proof = canonicalize_slots(observations, lock_at=(base + timedelta(minutes=45)).isoformat())
    assert len(rows) == 2
    assert rows[0]["deVigProbability"] == .52
    assert proof["rejected"]["duplicate_slot"] == 1
    assert proof["rejected"]["post_lock"] == 1
    assert proof["trainingEligible"] is False
    assert len(proof["fingerprint"]) == 64


def test_pattern_features_capture_reversals_and_late_movement():
    rows = [{"deVigProbability": value} for value in (.50, .53, .51, .54, .58)]
    features = temporal_pattern_features(rows)
    assert features["reversalCount"] == 2
    assert features["movementPathLength"] > abs(features["movementNet"])
    assert 0 <= features["lateMoveShare"] <= 1
    assert features["accelerationEnergy"] > 0


def test_interactions_are_finite():
    values = interaction_features({"reversalMagnitude": .02, "lateMoveShare": .7, "coverageRatio": .9, "bookDivergence": .04, "volatility": .01, "movementEfficiency": .8})
    assert all(math.isfinite(value) for value in values.values())
    assert values["reversalLateInteraction"] == pytest.approx(.014)


def test_candidate_rank_prefers_generalization_before_worst_day_gate():
    stable = {"meanDailyAccuracy": .62, "overallAccuracy": .61, "brierScore": .23, "logLoss": .66, "dailyPassRate": .1, "minimumDailyAccuracy": .4}
    gate_gamer = {"meanDailyAccuracy": .58, "overallAccuracy": .58, "brierScore": .25, "logLoss": .70, "dailyPassRate": .2, "minimumDailyAccuracy": .5}
    assert candidate_rank(stable) > candidate_rank(gate_gamer)


def test_training_rows_reject_duplicates_missing_labels_and_missing_proofs():
    good = {"slateDateEt": "2026-07-01", "officialGamePk": "1", "homeWon": 1, "postLockDataExcluded": True, "gameSpecificLockClipping": True}
    result = validate_training_rows([
        good, dict(good),
        {"slateDateEt": "2026-07-01", "officialGamePk": "2", "homeWon": None, "postLockDataExcluded": True, "gameSpecificLockClipping": True},
        {"slateDateEt": "2026-07-01", "officialGamePk": "3", "homeWon": 0, "postLockDataExcluded": False, "gameSpecificLockClipping": True},
    ])
    assert result["acceptedCount"] == 1
    assert result["rejected"]["duplicate_game"] == 1
    assert result["rejected"]["invalid_label"] == 1
    assert result["rejected"]["post_lock_proof_missing"] == 1
