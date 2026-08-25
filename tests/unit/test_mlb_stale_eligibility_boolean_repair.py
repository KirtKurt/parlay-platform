from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_prospective_row_repair as row_repair
import mlb_prospective_trainer_read_repair as read_repair


REAL_EXCLUSION = "lock_reliability:stale_or_missing_source_at_lock"


def _verified_lock(*reasons: str) -> dict:
    return {
        "lockedPrediction": True,
        "immutablePerGameStage": True,
        "immutableLockedStorage": True,
        "exactVectorVerified": True,
        "exactVectorValidationErrors": [],
        "frozenFeatureVector": {
            "fingerprint": "sha256:verified-vector",
            "version": "MLB-ML-CLEAN-COHORT-v1",
        },
        "trainingEligible": False,
        "trainingEligibilityStatus": "INELIGIBLE",
        "trainingExclusionReasons": list(reasons),
        "mlFeatureFreeze": {
            "trainingEligible": False,
            "trainingExclusionReasons": list(reasons),
            "exactVectorValidationErrors": [],
        },
    }


def test_verified_lock_with_empty_exclusions_clears_only_stale_false_boolean():
    source = _verified_lock()

    materialized = row_repair._cleanup_promoted_lock_training_eligibility(source)
    trainer_copy = read_repair._copy_with_stale_prelock_exclusions_cleared(source)

    for result in (materialized, trainer_copy):
        assert result["trainingEligible"] is True
        assert result["trainingEligibilityStatus"] == "ELIGIBLE"
        assert result["trainingExclusionReasons"] == []
        assert result["mlFeatureFreeze"]["trainingEligible"] is True
        assert result["mlFeatureFreeze"]["trainingExclusionReasons"] == []
    assert materialized["staleTrainingEligibleBooleanCleared"] is True
    assert trainer_copy["staleTrainingEligibleBooleanClearedAtRead"] is True
    assert source["trainingEligible"] is False
    assert source["mlFeatureFreeze"]["trainingEligible"] is False


def test_missing_immutable_or_vector_proof_never_clears_false_boolean():
    cases = []
    missing_stage = _verified_lock()
    missing_stage["immutablePerGameStage"] = False
    missing_stage["immutableLockedStorage"] = False
    cases.append(missing_stage)

    invalid_vector = _verified_lock()
    invalid_vector["exactVectorVerified"] = False
    invalid_vector["exactVectorValidationErrors"] = [
        "frozen_vector_fingerprint_mismatch"
    ]
    cases.append(invalid_vector)

    missing_fingerprint = _verified_lock()
    missing_fingerprint["frozenFeatureVector"].pop("fingerprint")
    cases.append(missing_fingerprint)

    for source in cases:
        assert row_repair._cleanup_promoted_lock_training_eligibility(source) == source
        assert read_repair._copy_with_stale_prelock_exclusions_cleared(source) == source


def test_substantive_exclusion_remains_authoritative():
    source = _verified_lock(REAL_EXCLUSION)

    materialized = row_repair._cleanup_promoted_lock_training_eligibility(source)
    trainer_copy = read_repair._copy_with_stale_prelock_exclusions_cleared(source)

    assert materialized["trainingEligible"] is False
    assert trainer_copy["trainingEligible"] is False
    assert materialized["trainingExclusionReasons"] == [REAL_EXCLUSION]
    assert trainer_copy["trainingExclusionReasons"] == [REAL_EXCLUSION]
    assert source == _verified_lock(REAL_EXCLUSION)


def test_already_eligible_verified_lock_is_unchanged():
    source = _verified_lock()
    source["trainingEligible"] = True
    source["trainingEligibilityStatus"] = "ELIGIBLE"
    source["mlFeatureFreeze"]["trainingEligible"] = True

    assert row_repair._cleanup_promoted_lock_training_eligibility(source) == source
    assert read_repair._copy_with_stale_prelock_exclusions_cleared(source) == source
