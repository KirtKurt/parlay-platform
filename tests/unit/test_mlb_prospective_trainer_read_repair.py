from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_prospective_trainer_read_repair as repair


STALE = "immutable_tminus45_prediction_not_available"
REAL = "lock_reliability:stale_or_missing_source_at_lock"


def _locked(*reasons, exact=True):
    errors = [] if exact else ["frozen_vector_fingerprint_mismatch"]
    return {
        "lockedPrediction": True,
        "immutableLockedStorage": True,
        "exactVectorVerified": exact,
        "exactVectorValidationErrors": list(errors),
        "frozenFeatureVector": {"fingerprint": "vector-fingerprint"},
        "trainingEligible": False,
        "trainingEligibilityStatus": "INELIGIBLE",
        "trainingExclusionReasons": list(reasons),
        "mlFeatureFreeze": {
            "trainingEligible": False,
            "trainingExclusionReasons": list(reasons),
            "exactVectorVerified": exact,
            "exactVectorValidationErrors": list(errors),
        },
    }


def _labels_module():
    def authority(item, slate_date):
        del slate_date
        data = item["data"]
        freeze = data.get("mlFeatureFreeze") or {}
        reasons = {
            *(data.get("trainingExclusionReasons") or []),
            *(freeze.get("trainingExclusionReasons") or []),
        }
        vector = data.get("frozenFeatureVector") or {}
        validation_errors = {
            *(data.get("exactVectorValidationErrors") or []),
            *(freeze.get("exactVectorValidationErrors") or []),
        }
        exact = bool(vector.get("fingerprint") and not validation_errors)
        status_errors = []
        for label, container in (("row", data), ("freeze", freeze)):
            if "exactVectorVerified" in container and (
                container.get("exactVectorVerified") is not exact
            ):
                status_errors.append(f"{label}_exact_vector_verified_mismatch")
        status_valid = not status_errors
        return {
            "verified": True,
            "consistentRead": True,
            "immutableLocked": True,
            "stageAuthorityVerified": True,
            "persistedStageAuthorityValidated": True,
            "officialAuditEligible": True,
            "exactLockVectorValidated": exact,
            "exactLockVectorValidationErrors": sorted(validation_errors),
            "selectionLockVectorStatusValidated": status_valid,
            "selectionLockVectorStatusValidationErrors": status_errors,
            "trainingExclusionReasons": sorted(reasons),
            "learningEligible": bool(
                data.get("trainingEligible") is True
                and exact
                and status_valid
                and not reasons
            ),
        }

    rolling = SimpleNamespace(_canonical_lock_authority=authority)

    def verdict(row):
        authority_value = row.get("canonicalLockAuthority") or {}
        reasons = {
            *(authority_value.get("trainingExclusionReasons") or []),
            *(row.get("trainingExclusionReasons") or []),
            *((row.get("mlFeatureFreeze") or {}).get(
                "trainingExclusionReasons"
            ) or []),
        }
        if row.get("fundamentalsEligible", True) is not True:
            reasons.add("fundamentals_v2_pregame_sources_incomplete")
        eligible = bool(
            authority_value.get("learningEligible") is True
            and row.get("trainingEligible") is True
            and not reasons
        )
        return eligible, sorted(reasons)

    module = SimpleNamespace(rolling_audit=rolling)
    module._training_verdict = verdict

    def joined(
        slate_date,
        label,
        row,
        *,
        slate_finalized,
    ):
        eligible, reasons = module._training_verdict(row)
        exclusions = {
            *reasons,
            *(label.get("training_exclusion_reasons") or []),
        }
        return {
            "slateDateEt": slate_date,
            "trainingEligible": bool(
                slate_finalized
                and label.get("training_eligible") is True
                and eligible
                and not exclusions
            ),
            "trainingExclusionReasons": sorted(exclusions),
        }

    module._joined_training_row = joined
    return module


def _authority(module, row):
    return module.rolling_audit._canonical_lock_authority(
        {"data": row},
        "2026-08-05",
    )


def _joined(module, source_lock, source_label):
    authority = _authority(module, source_lock)
    locked = copy.deepcopy(source_lock)
    locked["canonicalLockAuthority"] = authority
    result = module._joined_training_row(
        "2026-08-05",
        source_label,
        locked,
        slate_finalized=True,
    )
    return authority, result


def test_existing_exact_lock_and_label_become_eligible_read_only():
    module = repair.install(_labels_module())
    source_lock = _locked(STALE)
    source_label = {
        "training_eligible": False,
        "training_exclusion_reasons": [STALE],
    }

    authority, result = _joined(module, source_lock, source_label)

    assert authority["learningEligible"] is True
    assert authority["trainingExclusionReasons"] == []
    assert result["trainingEligible"] is True
    assert result["trainingExclusionReasons"] == []
    assert result["prospectiveTrainerReadRepairVersion"] == repair.VERSION
    assert result["immutablePregameVectorMutated"] is False
    assert result["immutableLockPayloadMutated"] is False
    assert result["immutableLabelPayloadMutated"] is False
    assert source_lock["trainingEligible"] is False
    assert source_label["training_eligible"] is False


def test_freeze_only_exact_vector_alias_becomes_eligible_read_only():
    module = repair.install(_labels_module())
    source_lock = _locked(STALE)
    source_lock.pop("exactVectorVerified")
    source_label = {
        "training_eligible": False,
        "training_exclusion_reasons": [STALE],
    }

    authority, result = _joined(module, source_lock, source_label)

    assert authority["learningEligible"] is True
    assert authority["exactVectorProofSourceAtRead"] == (
        "STORED_EXACT_VECTOR_METADATA"
    )
    assert result["trainingEligible"] is True
    assert "exactVectorVerified" not in source_lock
    assert source_lock["mlFeatureFreeze"]["trainingEligible"] is False


def test_authority_revalidation_repairs_when_legacy_exact_aliases_are_absent():
    module = repair.install(_labels_module())
    source_lock = _locked(STALE)
    source_lock.pop("exactVectorVerified")
    source_lock["mlFeatureFreeze"].pop("exactVectorVerified")
    source_label = {
        "training_eligible": False,
        "training_exclusion_reasons": [STALE],
    }

    authority, result = _joined(module, source_lock, source_label)

    assert authority["exactLockVectorValidated"] is True
    assert authority["selectionLockVectorStatusValidated"] is True
    assert authority["learningEligible"] is True
    assert authority["exactVectorProofSourceAtRead"] == (
        "CANONICAL_LOCK_AUTHORITY_REVALIDATION"
    )
    assert result["trainingEligible"] is True
    assert "exactVectorVerified" not in source_lock
    assert "exactVectorVerified" not in source_lock["mlFeatureFreeze"]


def test_conflicting_exact_vector_aliases_remain_fail_closed():
    module = repair.install(_labels_module())
    source_lock = _locked(STALE)
    source_lock["exactVectorVerified"] = False
    source_lock["mlFeatureFreeze"]["exactVectorVerified"] = True
    source_label = {
        "training_eligible": False,
        "training_exclusion_reasons": [STALE],
    }

    authority, result = _joined(module, source_lock, source_label)

    assert authority["exactLockVectorValidated"] is True
    assert authority["selectionLockVectorStatusValidated"] is False
    assert authority["learningEligible"] is False
    assert result["trainingEligible"] is False
    assert result["trainingExclusionReasons"] == [STALE]


def test_stale_false_label_boolean_with_no_reasons_is_repaired_read_only():
    module = repair.install(_labels_module())
    source_lock = _locked()
    source_label = {
        "training_eligible": False,
        "training_exclusion_reasons": [],
    }

    authority, result = _joined(module, source_lock, source_label)

    assert authority["learningEligible"] is True
    assert result["trainingEligible"] is True
    assert result["trainingExclusionReasons"] == []
    assert source_lock["trainingEligible"] is False
    assert source_label == {
        "training_eligible": False,
        "training_exclusion_reasons": [],
    }


def test_substantive_label_reason_blocks_stale_false_boolean_repair():
    module = repair.install(_labels_module())
    source_lock = _locked()
    source_label = {
        "training_eligible": False,
        "training_exclusion_reasons": [REAL],
    }

    authority, result = _joined(module, source_lock, source_label)

    assert authority["learningEligible"] is True
    assert result["trainingEligible"] is False
    assert result["trainingExclusionReasons"] == [REAL]
    assert source_label["training_eligible"] is False


def test_real_reliability_exclusion_remains_ineligible():
    module = repair.install(_labels_module())
    source_lock = _locked(STALE, REAL)
    source_label = {
        "training_eligible": False,
        "training_exclusion_reasons": [STALE, REAL],
    }

    authority, result = _joined(module, source_lock, source_label)

    assert authority["learningEligible"] is False
    assert authority["trainingExclusionReasons"] == [REAL]
    assert result["trainingEligible"] is False
    assert result["trainingExclusionReasons"] == [REAL]


def test_fundamentals_capture_gate_is_not_weakened():
    module = repair.install(_labels_module())
    source_lock = _locked(STALE)
    authority = _authority(module, source_lock)
    locked = copy.deepcopy(source_lock)
    locked["canonicalLockAuthority"] = authority
    locked["fundamentalsEligible"] = False
    source_label = {
        "training_eligible": False,
        "training_exclusion_reasons": [STALE],
    }

    result = module._joined_training_row(
        "2026-08-05",
        source_label,
        locked,
        slate_finalized=True,
    )

    assert result["trainingEligible"] is False
    assert result["trainingExclusionReasons"] == [
        "fundamentals_v2_pregame_sources_incomplete"
    ]


def test_invalid_vector_is_never_repaired():
    module = repair.install(_labels_module())
    source_lock = _locked(STALE, exact=False)

    authority = _authority(module, source_lock)

    assert authority["learningEligible"] is False
    assert authority["exactLockVectorValidated"] is False
    assert authority["trainingExclusionReasons"] == [STALE]


def test_trainer_compat_installs_read_repair_before_invocation():
    source = (
        HELLO_WORLD / "mlb_ml_aws_training_v1_compat.py"
    ).read_text(encoding="utf-8")

    assert "mlb_prospective_trainer_read_repair" in source
    assert "prospective_trainer_read_repair.install()" in source
