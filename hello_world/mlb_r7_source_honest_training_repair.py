from __future__ import annotations

import copy
import functools
import math
from collections.abc import Mapping, Sequence
from typing import Any, Dict, Iterable, List, Optional, Tuple


VERSION = "MLB-R7-SOURCE-HONEST-TRAINING-ADMISSION-v2-label-lock-bound"
SNAPSHOT_POLICY_VERSION = "MLB-R7-SNAPSHOT-POLICY-v1-lock-safe-missingness"
LABEL_LOCK_BINDING_VERSION = "MLB-R7-LABEL-LOCK-BINDING-v1-exact-immutable"
MODEL_POLICY_VERSION = "MLB-R7-MODEL-POLICY-v1-inactive-all-missing-features"
INCOMPLETE_PREFIX = "fundamentals_v2_incomplete:"
REQUIRED_MISSINGNESS_MASKS = frozenset(
    {
        "fundamentalPitchingMissing",
        "fundamentalOffenseLineupMissing",
    }
)
OPTIONAL_ALL_MISSING_NUMERIC_FEATURES = frozenset(
    {
        "starterCompositeGapHome",
        "bullpenCompositeGapHome",
        "lineupWrcPlusGapHome",
    }
)
OPTIONAL_FEATURE_MASK = {
    "starterCompositeGapHome": "fundamentalPitchingMissing",
    "bullpenCompositeGapHome": "fundamentalPitchingMissing",
    "lineupWrcPlusGapHome": "fundamentalOffenseLineupMissing",
}
_AUTHORITY_INTEGRITY_FLAGS = (
    "verified",
    "consistentRead",
    "immutableLocked",
    "stageAuthorityVerified",
    "persistedStageAuthorityValidated",
    "officialAuditEligible",
    "exactLockVectorValidated",
    "selectionLockVectorStatusValidated",
)
_LABEL_PATCH_FLAG = "_INQSI_MLB_R7_SOURCE_HONEST_LABEL_PATCH_V2"
_EXPERIMENT_PATCH_FLAG = "_INQSI_MLB_R7_SOURCE_HONEST_EXPERIMENT_PATCH_V2"
_DUAL_PATCH_FLAG = "_INQSI_MLB_R7_SOURCE_HONEST_DUAL_PATCH_V2"


def _strings(values: Any) -> set[str]:
    return {str(value) for value in (values or []) if str(value)}


def _optional_number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _vector(row: Mapping[str, Any]) -> Dict[str, Any]:
    value = row.get("featureSnapshot") or row.get("frozenFeatureVector") or {}
    return dict(value) if isinstance(value, Mapping) else {}


def _snapshot(row: Mapping[str, Any]) -> Dict[str, Any]:
    vector = _vector(row)
    value = row.get("fundamentalsSnapshotV2") or vector.get(
        "fundamentalsSnapshotV2"
    )
    return dict(value) if isinstance(value, Mapping) else {}


def _prediction_time(row: Mapping[str, Any]) -> Any:
    vector = _vector(row)
    return row.get("predictionPersistedAtUtc") or vector.get(
        "predictionPersistedAtUtc"
    )


def _lock_time(row: Mapping[str, Any]) -> Any:
    vector = _vector(row)
    slate_lock = row.get("slatePredictionLock") or {}
    return (
        vector.get("lockAtUtc")
        or row.get("lockAtUtc")
        or row.get("lockedAtUtc")
        or row.get("lockedAt")
        or (slate_lock.get("lockAtUtc") if isinstance(slate_lock, Mapping) else None)
    )


def _source_honest_incomplete_reasons(values: Iterable[Any]) -> set[str]:
    return {
        str(value)
        for value in (values or [])
        if str(value).startswith(INCOMPLETE_PREFIX)
    }


def _authority_integrity_proven(authority: Any) -> bool:
    return bool(
        isinstance(authority, Mapping)
        and authority
        and all(authority.get(flag) is True for flag in _AUTHORITY_INTEGRITY_FLAGS)
    )


def validate_snapshot_for_r7_training(
    snapshot: Any,
    prediction_time_utc: Any,
    lock_time_utc: Any,
) -> Tuple[bool, List[str]]:
    """Accept complete or honestly incomplete immutable pre-lock V2 evidence.

    This is intentionally narrower than production-pick eligibility and broader
    than ``validate_snapshot``. The schema, exact fingerprint, source
    provenance, missing-value masks, no-postgame policy, and time boundary must
    all validate. The only tolerated training exclusions are the V2 builder's
    exact ``fundamentals_v2_incomplete:<group>`` reasons.
    """

    try:
        import mlb_fundamentals_snapshot_v2 as fundamentals
    except Exception:
        return False, ["r7_fundamentals_v2_validator_unavailable"]

    errors = list(fundamentals.validate(snapshot))
    if errors:
        return False, sorted(set(str(value) for value in errors if str(value)))
    if not fundamentals.provenance_is_lock_safe(
        snapshot,
        prediction_persisted_at=prediction_time_utc,
        lock_at=lock_time_utc,
    ):
        return False, ["r7_fundamentals_v2_evidence_not_lock_safe"]

    missing_groups = {
        str(value) for value in (snapshot.get("missingGroups") or []) if str(value)
    }
    expected_exclusions = {
        f"{INCOMPLETE_PREFIX}{group}" for group in missing_groups
    }
    actual_exclusions = _strings(snapshot.get("trainingExclusionReasons"))
    reasons: List[str] = []
    if actual_exclusions != expected_exclusions:
        reasons.append("r7_fundamentals_v2_incomplete_reason_contract_mismatch")
    expected_complete = not missing_groups
    if snapshot.get("pregameComplete") is not expected_complete:
        reasons.append("r7_fundamentals_v2_pregame_complete_contract_mismatch")
    if snapshot.get("trainingEligibleAtCapture") is not expected_complete:
        reasons.append("r7_fundamentals_v2_capture_eligibility_contract_mismatch")
    if snapshot.get("missingValuesAreNull") is not True:
        reasons.append("r7_fundamentals_v2_null_missingness_policy_missing")
    if snapshot.get("immutableAtTMinus45") is not True:
        reasons.append("r7_fundamentals_v2_immutable_lock_policy_missing")
    if snapshot.get("latePlayabilityMayRewriteSnapshotOrVector") is not False:
        reasons.append("r7_fundamentals_v2_late_rewrite_protection_missing")
    if snapshot.get("closingLineValueCountsTowardPregameCompleteness") is not False:
        reasons.append("r7_fundamentals_v2_postgame_clv_exclusion_missing")
    forbidden = {"closing_line_value", "closingLineValue", "beatsClose"}
    if forbidden & set((snapshot.get("groups") or {}).keys()):
        reasons.append("r7_fundamentals_v2_contains_postgame_group")
    return not reasons, sorted(set(reasons))


def derived_missingness(snapshot: Mapping[str, Any]) -> Dict[str, float]:
    groups = snapshot.get("groups") or {}

    def group_missing(name: str, required_values: Sequence[str]) -> float:
        group = groups.get(name) if isinstance(groups, Mapping) else None
        if not isinstance(group, Mapping):
            return 1.0
        if str(group.get("status") or "").upper() not in {
            "CONNECTED",
            "PARTIAL",
            "PARTIAL_MISSING_REQUIRED_VALUES",
        }:
            return 1.0
        values = group.get("values") or {}
        return (
            0.0
            if all(_optional_number(values.get(key)) is not None for key in required_values)
            else 1.0
        )

    return {
        "fundamentalPitchingMissing": max(
            group_missing(
                "starter_quality", ("homeComposite", "awayComposite")
            ),
            group_missing(
                "bullpen_availability", ("homeComposite", "awayComposite")
            ),
        ),
        "fundamentalOffenseLineupMissing": group_missing(
            "confirmed_lineups", ("homeWrcPlus", "awayWrcPlus")
        ),
    }


def _exact_lock_proven(row: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    vector = _vector(row)
    freeze = row.get("mlFeatureFreeze") or {}
    authority = row.get("canonicalLockAuthority") or {}
    exact_errors = sorted(
        {
            *_strings(row.get("exactVectorValidationErrors")),
            *_strings(
                freeze.get("exactVectorValidationErrors")
                if isinstance(freeze, Mapping)
                else []
            ),
            *_strings(
                authority.get("exactLockVectorValidationErrors")
                if isinstance(authority, Mapping)
                else []
            ),
            *_strings(
                authority.get("selectionLockVectorStatusValidationErrors")
                if isinstance(authority, Mapping)
                else []
            ),
        }
    )
    stored_values: List[Any] = []
    if "exactVectorVerified" in row:
        stored_values.append(row.get("exactVectorVerified"))
    if isinstance(freeze, Mapping) and "exactVectorVerified" in freeze:
        stored_values.append(freeze.get("exactVectorVerified"))
    stored_exact = bool(stored_values and all(value is True for value in stored_values))
    authority_exact = _authority_integrity_proven(authority)
    immutable = bool(
        row.get("immutablePerGameStage") is True
        or row.get("immutableLockedStorage") is True
    )
    reasons: List[str] = []
    if row.get("lockedPrediction") is not True:
        reasons.append("r7_locked_prediction_marker_missing")
    if not immutable:
        reasons.append("r7_immutable_lock_marker_missing")
    if not isinstance(vector, Mapping) or not str(vector.get("fingerprint") or ""):
        reasons.append("r7_frozen_vector_fingerprint_missing")
    if exact_errors:
        reasons.extend(exact_errors)
    if not stored_exact and not authority_exact:
        reasons.append("r7_exact_vector_proof_missing")
    return not reasons, sorted(set(reasons))


def _label_lock_binding_reasons(
    label: Mapping[str, Any], locked: Mapping[str, Any]
) -> List[str]:
    """Prove the write-once FINAL label belongs to this exact immutable lock."""

    authority = locked.get("canonicalLockAuthority") or {}
    vector = _vector(locked)
    snapshot = _snapshot(locked)
    reasons: List[str] = []
    if not _authority_integrity_proven(authority):
        reasons.append("r7_label_lock_canonical_authority_not_proven")
    if label.get("write_once") is not True or label.get("completed") is not True:
        reasons.append("r7_label_not_write_once_final")
    if not str(label.get("settlement_fingerprint") or ""):
        reasons.append("r7_label_settlement_fingerprint_missing")
    if not str(label.get("record_fingerprint") or ""):
        reasons.append("r7_label_record_fingerprint_missing")

    official_game_pk = str(
        locked.get("officialGamePk")
        or vector.get("officialGamePk")
        or authority.get("officialGamePk")
        or ""
    )
    if not official_game_pk or str(label.get("official_game_pk") or "") != official_game_pk:
        reasons.append("r7_label_official_game_pk_not_bound_to_lock")

    comparisons = {
        "canonical_lock_pk": authority.get("sourcePk"),
        "canonical_lock_sk": authority.get("sourceSk"),
        "canonical_stage_fingerprint": authority.get("stageFingerprint"),
        "frozen_feature_vector_fingerprint": vector.get("fingerprint"),
        "fundamentals_snapshot_v2_version": snapshot.get("version"),
        "fundamentals_snapshot_v2_fingerprint": snapshot.get("fingerprint"),
    }
    for field, expected in comparisons.items():
        if not str(expected or "") or str(label.get(field) or "") != str(expected):
            reasons.append(f"r7_label_{field}_not_bound_to_lock")
    return sorted(set(reasons))


def _joined_row_source_honest_proof(
    row: Mapping[str, Any],
) -> Optional[Tuple[bool, List[str], Dict[str, float]]]:
    annotation_fields = {
        "r7SourceHonestTrainingAdmission",
        "r7SourceHonestTrainingPolicyVersion",
        "r7SourceHonestSnapshotPolicyVersion",
        "r7SourceHonestLabelLockBindingVersion",
        "r7SourceHonestMissingnessMasks",
    }
    if not (annotation_fields & set(row)):
        return None

    reasons: List[str] = []
    if row.get("r7SourceHonestTrainingAdmission") is not True:
        reasons.append("r7_joined_source_honest_admission_not_proven")
    if row.get("r7SourceHonestTrainingPolicyVersion") != VERSION:
        reasons.append("r7_joined_training_policy_version_mismatch")
    if row.get("r7SourceHonestSnapshotPolicyVersion") != SNAPSHOT_POLICY_VERSION:
        reasons.append("r7_joined_snapshot_policy_version_mismatch")
    if (
        row.get("r7SourceHonestLabelLockBindingVersion")
        != LABEL_LOCK_BINDING_VERSION
    ):
        reasons.append("r7_joined_label_lock_binding_version_mismatch")
    if row.get("slateFinalized") is not True or row.get("labelStatus") != "FINAL":
        reasons.append("r7_joined_row_not_finalized")
    if not str(row.get("labelFingerprint") or ""):
        reasons.append("r7_joined_label_fingerprint_missing")
    if not str(row.get("labelRecordFingerprint") or ""):
        reasons.append("r7_joined_label_record_fingerprint_missing")
    for field in (
        "immutablePregameVectorMutated",
        "immutableLockPayloadMutated",
        "immutableLabelPayloadMutated",
        "productionPickEligibilityChanged",
    ):
        if row.get(field) is not False:
            reasons.append(f"r7_joined_{field}_must_be_false")

    vector = _vector(row)
    if not str(vector.get("fingerprint") or ""):
        reasons.append("r7_joined_frozen_vector_fingerprint_missing")
    snapshot = _snapshot(row)
    snapshot_ok, snapshot_reasons = validate_snapshot_for_r7_training(
        snapshot,
        _prediction_time(row),
        _lock_time(row),
    )
    if not snapshot_ok:
        reasons.extend(snapshot_reasons)

    masks_raw = row.get("r7SourceHonestMissingnessMasks") or {}
    masks = dict(masks_raw) if isinstance(masks_raw, Mapping) else {}
    expected_masks = derived_missingness(snapshot) if snapshot_ok else {}
    if set(masks) != REQUIRED_MISSINGNESS_MASKS:
        reasons.append("r7_joined_missingness_mask_set_mismatch")
    if masks != expected_masks:
        reasons.append("r7_joined_missingness_mask_values_mismatch")
    if any(value not in {0.0, 1.0} for value in masks.values()):
        reasons.append("r7_joined_missingness_mask_not_binary")
    return not reasons, sorted(set(reasons)), masks if not reasons else {}


def row_is_source_honest_training_safe(
    row: Mapping[str, Any],
) -> Tuple[bool, List[str], Dict[str, float]]:
    joined_proof = _joined_row_source_honest_proof(row)
    if joined_proof is not None:
        return joined_proof

    exact, exact_reasons = _exact_lock_proven(row)
    if not exact:
        return False, exact_reasons, {}
    snapshot = _snapshot(row)
    valid, snapshot_reasons = validate_snapshot_for_r7_training(
        snapshot,
        _prediction_time(row),
        _lock_time(row),
    )
    if not valid:
        return False, snapshot_reasons, {}
    return True, [], derived_missingness(snapshot)


def manifest_missingness_reasons(
    manifest: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> List[str]:
    if snapshot.get("pregameComplete") is True:
        return []
    feature_names = {
        str(value) for value in (manifest.get("featureNames") or []) if str(value)
    }
    reasons = [
        f"r7_prespecified_missingness_mask_absent:{name}"
        for name in sorted(REQUIRED_MISSINGNESS_MASKS - feature_names)
    ]
    schemas = manifest.get("modelFeatureSchemas") or {}
    if isinstance(schemas, Mapping):
        for model_name in ("outcome", "reliability"):
            model_features = {
                str(value)
                for value in (schemas.get(model_name) or [])
                if str(value)
            }
            for name in sorted(REQUIRED_MISSINGNESS_MASKS - model_features):
                reasons.append(
                    f"r7_{model_name}_missingness_mask_absent:{name}"
                )
    else:
        reasons.append("r7_model_feature_schemas_missing")
    return sorted(set(reasons))


def _install_label_patch(labels: Any) -> None:
    if getattr(labels, _LABEL_PATCH_FLAG, False):
        return

    original_verdict = getattr(labels, "_training_verdict", None)
    if callable(original_verdict):

        @functools.wraps(original_verdict)
        def training_verdict(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
            eligible, reasons = original_verdict(row)
            safe, _safety_reasons, _masks = row_is_source_honest_training_safe(row)
            if not safe:
                return bool(eligible), sorted(
                    set(str(value) for value in (reasons or []) if str(value))
                )
            remaining = sorted(
                set(str(value) for value in (reasons or []) if str(value))
                - _source_honest_incomplete_reasons(reasons)
            )
            return not remaining, remaining

        training_verdict._mlb_r7_source_honest_policy_version = VERSION
        labels._training_verdict = training_verdict

    original_join = getattr(labels, "_joined_training_row", None)
    if callable(original_join):

        @functools.wraps(original_join)
        def joined_training_row(
            slate_date: str,
            label: Dict[str, Any],
            locked: Dict[str, Any],
            *,
            slate_finalized: bool,
        ) -> Dict[str, Any]:
            joined = original_join(
                slate_date,
                label,
                locked,
                slate_finalized=slate_finalized,
            )
            if not isinstance(joined, dict):
                return joined
            safe, safety_reasons, masks = row_is_source_honest_training_safe(locked)
            binding_reasons = _label_lock_binding_reasons(label, locked)
            if not safe or binding_reasons:
                return joined
            exclusions = _strings(joined.get("trainingExclusionReasons"))
            remaining = sorted(
                exclusions - _source_honest_incomplete_reasons(exclusions)
            )
            admitted = bool(
                slate_finalized
                and joined.get("labelStatus") == "FINAL"
                and bool(joined.get("labelFingerprint"))
                and bool(joined.get("labelRecordFingerprint"))
                and not remaining
            )
            out = copy.deepcopy(joined)
            out.update(
                {
                    "trainingEligible": admitted,
                    "trainingExclusionReasons": remaining,
                    "r7SourceHonestTrainingAdmission": admitted,
                    "r7SourceHonestMissingnessMasks": masks,
                    "r7SourceHonestTrainingPolicyVersion": VERSION,
                    "r7SourceHonestSnapshotPolicyVersion": SNAPSHOT_POLICY_VERSION,
                    "r7SourceHonestLabelLockBindingVersion": LABEL_LOCK_BINDING_VERSION,
                    "r7SourceHonestSafetyReasons": sorted(
                        set(safety_reasons + binding_reasons)
                    ),
                    "immutablePregameVectorMutated": False,
                    "immutableLockPayloadMutated": False,
                    "immutableLabelPayloadMutated": False,
                    "productionPickEligibilityChanged": False,
                }
            )
            return out

        joined_training_row._mlb_r7_source_honest_policy_version = VERSION
        labels._joined_training_row = joined_training_row

    labels.MLB_R7_SOURCE_HONEST_TRAINING_POLICY_VERSION = VERSION
    setattr(labels, _LABEL_PATCH_FLAG, True)


def _install_experiment_patch(experiment: Any) -> None:
    if getattr(experiment, _EXPERIMENT_PATCH_FLAG, False):
        return
    original_validate = getattr(experiment, "validate_record", None)
    if not callable(original_validate):
        raise RuntimeError("R7_EXPERIMENT_VALIDATE_RECORD_UNAVAILABLE")

    @functools.wraps(original_validate)
    def validate_record(
        row: Dict[str, Any],
        manifest: Dict[str, Any],
        snapshot_validator: Optional[Any] = None,
    ) -> Tuple[bool, List[str]]:
        production_id = str(
            getattr(experiment, "PRODUCTION_EXPERIMENT_ID", "") or ""
        )
        is_r7 = bool(
            production_id
            and str(manifest.get("experimentId") or "") == production_id
        )
        effective_validator = snapshot_validator
        if is_r7 and effective_validator is None:
            effective_validator = validate_snapshot_for_r7_training
        _ok, reasons = original_validate(
            row,
            manifest,
            snapshot_validator=effective_validator,
        )
        normalized = {
            str(value) for value in (reasons or []) if str(value)
        }
        if is_r7:
            safe, safety_reasons, masks = row_is_source_honest_training_safe(row)
            if not safe:
                normalized.update(safety_reasons)
            else:
                normalized.update(
                    manifest_missingness_reasons(manifest, _snapshot(row))
                )
                if set(masks) != REQUIRED_MISSINGNESS_MASKS:
                    normalized.add("r7_derived_missingness_mask_set_mismatch")
        final_reasons = sorted(normalized)
        return not final_reasons, final_reasons

    validate_record._mlb_r7_source_honest_policy_version = VERSION
    experiment.validate_record = validate_record
    experiment.MLB_R7_SOURCE_HONEST_TRAINING_POLICY_VERSION = VERSION
    setattr(experiment, _EXPERIMENT_PATCH_FLAG, True)


def _install_dual_model_patch(dual_model: Any) -> None:
    if getattr(dual_model, _DUAL_PATCH_FLAG, False):
        return
    original_fit = getattr(dual_model, "fit_logistic", None)
    if not callable(original_fit):
        raise RuntimeError("R7_DUAL_MODEL_FIT_UNAVAILABLE")

    @functools.wraps(original_fit)
    def fit_logistic(
        records: Sequence[Dict[str, Any]],
        features: Sequence[str],
        target: str,
        version: str,
        epochs: int = 320,
        learning_rate: float = 0.035,
        l2: float = 0.02,
    ) -> Dict[str, Any]:
        requested = list(features or [])
        result = original_fit(
            records,
            requested,
            target,
            version,
            epochs=epochs,
            learning_rate=learning_rate,
            l2=l2,
        )
        if not isinstance(result, dict) or result.get("reason") != (
            "prespecified_feature_has_no_observed_training_values"
        ):
            return result
        empty = {
            str(value) for value in (result.get("emptyFeatures") or []) if str(value)
        }
        if not empty or not empty <= OPTIONAL_ALL_MISSING_NUMERIC_FEATURES:
            return result
        if any(OPTIONAL_FEATURE_MASK[name] not in requested for name in empty):
            return result
        active = [name for name in requested if name not in empty]
        if not active:
            return result
        retry = original_fit(
            records,
            active,
            target,
            version,
            epochs=epochs,
            learning_rate=learning_rate,
            l2=l2,
        )
        if not isinstance(retry, dict) or retry.get("ok") is not True:
            return retry
        out = copy.deepcopy(retry)
        weights = dict(out.get("weights") or {})
        means = dict(out.get("means") or {})
        scales = dict(out.get("scales") or {})
        for name in sorted(empty):
            weights[name] = 0.0
            means[name] = 0.0
            scales[name] = 1.0
        out.update(
            {
                "features": requested,
                "weights": weights,
                "means": means,
                "scales": scales,
                "inactiveAllMissingFeatures": sorted(empty),
                "effectiveFeatures": active,
                "effectiveFeatureCount": len(active),
                "sourceHonestMissingnessPolicyVersion": MODEL_POLICY_VERSION,
                "missingValuePolicy": (
                    "train_mean_imputation_with_prespecified_frozen_missingness_"
                    "masks_and_zero_weight_for_all_missing_optional_fundamentals"
                ),
            }
        )
        return out

    fit_logistic._mlb_r7_source_honest_policy_version = VERSION
    dual_model.fit_logistic = fit_logistic
    dual_model.MLB_R7_SOURCE_HONEST_MODEL_POLICY_VERSION = MODEL_POLICY_VERSION
    setattr(dual_model, _DUAL_PATCH_FLAG, True)


def install(
    *,
    labels: Optional[Any] = None,
    experiment: Optional[Any] = None,
    dual_model: Optional[Any] = None,
) -> Dict[str, Any]:
    if labels is None:
        import mlb_canonical_final_labels_v1 as labels
    if experiment is None:
        import mlb_ml_experiment_v2 as experiment
    if dual_model is None:
        import mlb_ml_dual_model_v2 as dual_model

    _install_label_patch(labels)
    _install_experiment_patch(experiment)
    _install_dual_model_patch(dual_model)
    return {
        "ok": True,
        "version": VERSION,
        "snapshotPolicyVersion": SNAPSHOT_POLICY_VERSION,
        "labelLockBindingVersion": LABEL_LOCK_BINDING_VERSION,
        "modelPolicyVersion": MODEL_POLICY_VERSION,
        "immutablePredictionOrLockMutated": False,
        "immutableLabelMutated": False,
        "productionPickEligibilityChanged": False,
        "promotionGateChanged": False,
        "retiredAuthorityRestored": False,
    }
