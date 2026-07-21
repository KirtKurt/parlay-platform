from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

VERSION = "MLB-DAILY-LOCK-ML-VECTOR-PRESERVATION-v2-selection-training-separated"
EXPECTED_VECTOR_VERSION = "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v2-lock-safe-temporal-missingness"
TRAINING_EXCLUSION_PREFIX = "exact_lock_vector_validation:"

# These fields are intentionally retained in the write-once daily card because the
# postgame outcome and reliability models must be trained from the exact pregame
# state. Outcome labels are deliberately not copied at lock time.
PRESERVED_FIELDS: Tuple[str, ...] = (
    "slateDateEt",
    "teamWinProbabilityPct",
    "winProbabilityMeaning",
    "probabilitySemanticsFixed",
    "predictionSemanticsVersion",
    "officialPredictionStatus",
    "lockedPrediction",
    "lockedAtUtc",
    "predictionSourcePullAt",
    "predictionSourcePullId",
    "lockedAmericanOdds",
    "slatePredictionLock",
    "lastPossiblePredictionGate",
    "lockedCardAudit",
    "slateCoverage",
    "mlFeatureFreeze",
    "frozenFeatureVector",
    "frozenFeatureVectorVersion",
    "featureVectorFrozenAtLock",
    "fundamentalsSnapshot",
    "finalGuardedStored",
    "finalGuardedStoreRequested",
    "finalGateStored",
    "fullDataFinalPick",
    "finalPipelineVersion",
    "createdAt",
    "created_at",
)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _expected_fingerprint(vector: Dict[str, Any]) -> str:
    import mlb_ml_clean_cohort_v1 as cohort
    return cohort.fingerprint_for_vector(vector)


def _selected_price_proven(row: Dict[str, Any]) -> bool:
    price = row.get("lockedAmericanOdds")
    if price in (None, "", 0, 0.0):
        price = row.get("americanOdds")
    book = row.get("priceBook")
    source = str(row.get("priceSource") or "").lower()
    return bool(price not in (None, "", 0, 0.0) and (book or source in {"real_book", "locked_real_book"}))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except Exception:
        return None


def _validate(row: Dict[str, Any], compact: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    vector = compact.get("frozenFeatureVector")
    freeze = compact.get("mlFeatureFreeze") or {}
    coverage = compact.get("slateCoverage") or {}

    if not isinstance(vector, dict):
        return ["missing_frozen_feature_vector"]
    if vector.get("version") != EXPECTED_VECTOR_VERSION:
        errors.append("wrong_frozen_feature_vector_version")
    if not isinstance(vector.get("features"), dict) or not vector.get("features"):
        errors.append("frozen_vector_features_missing")
    if not vector.get("fingerprint"):
        errors.append("frozen_vector_fingerprint_missing")
    elif vector.get("fingerprint") != _expected_fingerprint(vector):
        errors.append("frozen_vector_fingerprint_mismatch")

    row_game_id = str(compact.get("gameId") or row.get("gameId") or "")
    if not row_game_id or str(vector.get("gameId") or "") != row_game_id:
        errors.append("frozen_vector_game_identity_mismatch")
    for vector_key, camel_key, snake_key in (
        ("homeTeam", "homeTeam", "home_team"),
        ("awayTeam", "awayTeam", "away_team"),
    ):
        row_team = compact.get(camel_key) or row.get(camel_key) or row.get(snake_key)
        if str(vector.get(vector_key) or "").strip().lower() != str(row_team or "").strip().lower():
            errors.append(f"frozen_vector_{vector_key.lower()}_mismatch")
    if vector.get("predictedSide") != compact.get("predictedSide"):
        errors.append("frozen_vector_predicted_side_mismatch")
    if str(vector.get("predictedWinner") or "").strip().lower() != str(compact.get("predictedWinner") or "").strip().lower():
        errors.append("frozen_vector_predicted_winner_mismatch")

    lock_at = _parse_dt(vector.get("lockAtUtc"))
    source_at = _parse_dt(vector.get("sourcePullAtUtc"))
    row_gate = compact.get("slatePredictionLock") or compact.get("lastPossiblePredictionGate") or {}
    row_lock_at = _parse_dt(compact.get("lockedAtUtc") or row_gate.get("lockAtUtc"))
    row_source_at = _parse_dt(
        compact.get("predictionSourcePullAt")
        or row_gate.get("latestScoringPullAt")
    )
    if not lock_at:
        errors.append("frozen_vector_lock_timestamp_missing")
    if not source_at:
        errors.append("frozen_vector_source_timestamp_missing")
    if lock_at and source_at and source_at > lock_at:
        errors.append("frozen_vector_source_after_lock")
    if lock_at and row_lock_at and lock_at != row_lock_at:
        errors.append("frozen_vector_lock_timestamp_mismatch")
    if source_at and row_source_at and source_at != row_source_at:
        errors.append("frozen_vector_source_timestamp_mismatch")

    try:
        import mlb_ml_clean_cohort_v1 as cohort
        import mlb_ml_dual_model_v1 as dual

        features = vector.get("features") or {}
        if vector.get("temporalFeatureVersion") != cohort.temporal_features.VERSION:
            errors.append("frozen_vector_temporal_feature_version_missing_or_wrong")
        if vector.get("temporalFeaturesAtOrBeforeLock") is not True:
            errors.append("frozen_vector_temporal_features_not_lock_safe")
        temporal_source = _parse_dt(vector.get("temporalSourcePullAtUtc"))
        if not temporal_source or not source_at or not lock_at or temporal_source > source_at or temporal_source > lock_at:
            errors.append("frozen_vector_temporal_source_after_or_missing_lock")
        if _number(features.get("homeTemporalAvailable")) != 1.0:
            errors.append("frozen_vector_home_temporal_history_missing")
        if _number(features.get("awayTemporalAvailable")) != 1.0:
            errors.append("frozen_vector_away_temporal_history_missing")

        temporal_times = []
        temporal_summaries: Dict[str, Any] = {}
        for side in ("home", "away"):
            signal = row.get(f"{side}Signal") or {}
            summary = signal.get("temporalFeatures") if isinstance(signal, dict) else None
            if not cohort.temporal_features.provenance_is_lock_safe(summary, source_at, lock_at):
                errors.append(f"{side}_temporal_summary_not_lock_safe")
                continue
            parsed = _parse_dt(summary.get("asOfUtc"))
            if parsed:
                temporal_times.append(parsed)
                temporal_summaries[side] = summary
        if temporal_source and temporal_times and max(temporal_times) != temporal_source:
            errors.append("frozen_vector_temporal_source_summary_mismatch")
        if len(temporal_summaries) == 2:
            expected_temporal, expected_safe, expected_temporal_source = cohort._temporal_vector_features(
                temporal_summaries["home"],
                temporal_summaries["away"],
                str(row.get("predictedSide") or "home"),
                source_at,
                lock_at,
            )
            if not expected_safe or expected_temporal_source != temporal_source:
                errors.append("frozen_vector_temporal_recalculation_provenance_mismatch")
            if any(_number(features.get(key)) != float(value) for key, value in expected_temporal.items()):
                errors.append("frozen_vector_temporal_feature_recalculation_mismatch")

        if vector.get("missingnessFeatureVersion") != cohort.feature_missingness.VERSION:
            errors.append("frozen_vector_missingness_feature_version_missing_or_wrong")
        if vector.get("fundamentalsSnapshotVersion") != cohort.feature_missingness.FUNDAMENTALS_VERSION:
            errors.append("frozen_vector_fundamentals_snapshot_version_missing_or_wrong")
        if vector.get("fundamentalMasksAtOrBeforeLock") is not True:
            errors.append("frozen_vector_fundamental_masks_not_lock_safe")
        fundamental_source = _parse_dt(vector.get("fundamentalsSnapshotAsOfUtc"))
        if not fundamental_source or not source_at or not lock_at or fundamental_source > source_at or fundamental_source > lock_at:
            errors.append("frozen_vector_fundamental_source_after_or_missing_lock")
        fundamentals = compact.get("fundamentalsSnapshot") or row.get("fundamentalsSnapshot") or {}
        if fundamentals.get("version") != vector.get("fundamentalsSnapshotVersion"):
            errors.append("frozen_vector_fundamentals_snapshot_version_mismatch")
        if _parse_dt(fundamentals.get("asOfUtc")) != fundamental_source:
            errors.append("frozen_vector_fundamentals_snapshot_source_mismatch")
        if not cohort.feature_missingness.provenance_is_lock_safe(fundamentals, source_at, lock_at, _parse_dt):
            errors.append("fundamentals_snapshot_not_lock_safe")
        expected_masks = cohort.feature_missingness.build_masks(fundamentals)
        if any(_number(features.get(key)) != float(value) for key, value in expected_masks.items()):
            errors.append("frozen_vector_fundamental_mask_recalculation_mismatch")

        required_features = set(dual.OUTCOME_FEATURES) | set(dual.RELIABILITY_FEATURES)
        if required_features - set(features):
            errors.append("frozen_vector_required_model_features_missing")
        elif any(_number(features.get(key)) is None for key in required_features):
            errors.append("frozen_vector_required_model_feature_not_numeric")
    except Exception:
        errors.append("frozen_vector_temporal_missingness_validation_failed")

    labels = vector.get("labels")
    if not isinstance(labels, dict) or not {"homeWon", "pickCorrect"}.issubset(labels):
        errors.append("pregame_vector_explicit_null_labels_missing")
    elif labels.get("homeWon") is not None or labels.get("pickCorrect") is not None:
        errors.append("pregame_vector_contains_outcome_label")
    if vector.get("immutableSource") != "locked_prediction_row_pre_game_features":
        errors.append("wrong_immutable_vector_source")
    if vector.get("derivedOnceFromImmutableLockedRow") is not True:
        errors.append("vector_not_proven_immutable")
    if compact.get("featureVectorFrozenAtLock") is not True:
        errors.append("feature_vector_frozen_at_lock_flag_missing")

    coverage_complete = bool(
        freeze.get("completeSlateCoverage") is True
        or coverage.get("coverageComplete") is True
    )
    if not coverage_complete:
        errors.append("complete_slate_coverage_not_proven")
    if freeze.get("exactVectorCreated") is not True:
        errors.append("exact_vector_creation_not_proven")
    if compact.get("predictionSemanticsVersion") in (None, "") and compact.get("probabilitySemanticsFixed") is not True:
        errors.append("modern_probability_semantics_missing")
    if compact.get("teamWinProbabilityPct") in (None, ""):
        errors.append("team_win_probability_missing")
    if not _selected_price_proven(compact):
        errors.append("selected_side_locked_price_not_proven")

    try:
        import mlb_ml_clean_cohort_v1 as cohort
        fingerprint_version = str(vector.get("fingerprintVersion") or "")
        if fingerprint_version != cohort.FINGERPRINT_VERSION:
            errors.append(
                "missing_frozen_vector_fingerprint_version"
                if not fingerprint_version
                else "unsupported_frozen_vector_fingerprint_version"
            )
        if fingerprint_version == cohort.FINGERPRINT_VERSION:
            signal = compact.get("homeSignal") if compact.get("predictedSide") == "home" else compact.get("awaySignal")
            signal = signal if isinstance(signal, dict) else {}
            row_price = compact.get("lockedAmericanOdds")
            if row_price in (None, "", 0, 0.0):
                row_price = compact.get("americanOdds")
            if row_price in (None, "", 0, 0.0):
                row_price = signal.get("americanOdds")
            vector_price = vector.get("selectedAmericanOdds")
            if vector_price in (None, "") or row_price in (None, "") or float(vector_price) != float(row_price):
                errors.append("frozen_vector_selected_price_mismatch")
            row_book = compact.get("priceBook") or signal.get("priceBook")
            row_source = compact.get("priceSource") or signal.get("priceSource")
            if str(vector.get("selectedPriceBook") or "") != str(row_book or ""):
                errors.append("frozen_vector_selected_price_book_mismatch")
            if str(vector.get("selectedPriceSource") or "") != str(row_source or ""):
                errors.append("frozen_vector_selected_price_source_mismatch")
    except Exception:
        errors.append("frozen_vector_bound_context_validation_failed")

    return sorted(set(errors))


def validate_exact_locked_row(row: Dict[str, Any]) -> List[str]:
    """Return exact-vector contract errors for any locked prediction row.

    ML cohort admission remains fail-closed on every error returned here. The
    immutable selection store records these errors but does not use them as
    winner-lock authority.
    """
    source = copy.deepcopy(row or {})
    return _validate(source, source)


def effective_selection_lock_vector_errors(row: Dict[str, Any]) -> List[str]:
    """Return the immutable lock-time vector verdict without reconstructing it."""
    source = row or {}
    if source.get("exactVectorStatusUnavailableAtLock") is True:
        stored = source.get("exactVectorValidationErrors") or []
        return sorted(set(str(error) for error in stored if str(error))) or [
            "exact_vector_status_unavailable_at_lock"
        ]
    return validate_exact_locked_row(source)


def _vector_training_exclusions(errors: List[str]) -> List[str]:
    return [
        f"{TRAINING_EXCLUSION_PREFIX}{str(error).strip()}"
        for error in sorted(set(errors))
        if str(error).strip()
    ]


def apply_exact_vector_training_status(
    row: Dict[str, Any],
    validation_errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Persist ML-vector eligibility without making it lock authority.

    The winner is authorized by its persisted pre-cutoff stage chain. The exact
    vector is a separate, stricter contract used only for ML cohort admission.
    """
    out = copy.deepcopy(row or {})
    out.pop("exactVectorStatusUnavailableAtLock", None)
    if validation_errors is None:
        try:
            validation_errors = validate_exact_locked_row(out)
        except Exception as exc:
            validation_errors = [
                f"exact_vector_validator_unavailable:{type(exc).__name__}:{exc}"
            ]
    errors = sorted(set(str(error) for error in validation_errors if str(error)))
    exact = not errors
    freeze = dict(out.get("mlFeatureFreeze") or {})
    existing_reasons = {
        str(reason)
        for reason in (
            freeze.get("trainingExclusionReasons")
            or out.get("trainingExclusionReasons")
            or []
        )
        if str(reason)
    }
    if errors:
        existing_reasons.update(_vector_training_exclusions(errors))
        freeze["trainingEligible"] = False
        out["trainingEligible"] = False
        out["trainingEligibilityStatus"] = "INELIGIBLE"
    else:
        eligible = bool(freeze.get("trainingEligible") is True and not existing_reasons)
        freeze["trainingEligible"] = eligible
        out["trainingEligible"] = eligible
        out["trainingEligibilityStatus"] = "ELIGIBLE" if eligible else "INELIGIBLE"

    reasons = sorted(existing_reasons)
    freeze.update({
        "exactVectorVerified": exact,
        "exactVectorValidationErrors": errors,
        "trainingExclusionReasons": reasons,
        "selectionLockIndependentOfTrainingVector": True,
    })
    out.update({
        "exactVectorVerified": exact,
        "exactVectorValidationErrors": errors,
        "trainingExclusionReasons": reasons,
        "selectionTrainingSeparationVersion": VERSION,
        "mlFeatureFreeze": freeze,
    })
    return out


def validate_selection_lock_vector_status(row: Dict[str, Any]) -> List[str]:
    """Validate separation metadata while allowing an invalid training vector."""
    source = copy.deepcopy(row or {})
    if source.get("exactVectorStatusUnavailableAtLock") is True:
        freeze = source.get("mlFeatureFreeze") or {}
        errors = []
        if source.get("exactVectorVerified") is not False:
            errors.append("fallback_vector_status_not_explicitly_unverified")
        if source.get("trainingEligible") is not False:
            errors.append("fallback_vector_status_not_training_ineligible")
        if source.get("trainingEligibilityStatus") != "INELIGIBLE":
            errors.append("fallback_vector_training_status_missing")
        if not isinstance(freeze, dict) or freeze.get("trainingEligible") is not False:
            errors.append("fallback_vector_freeze_not_training_ineligible")
        if not source.get("exactVectorValidationErrors"):
            errors.append("fallback_vector_validation_error_missing")
        if not source.get("trainingExclusionReasons") or not freeze.get("trainingExclusionReasons"):
            errors.append("fallback_vector_training_exclusion_missing")
        return sorted(set(errors))
    try:
        vector_errors = validate_exact_locked_row(source)
    except Exception as exc:
        vector_errors = [
            f"exact_vector_validator_unavailable:{type(exc).__name__}:{exc}"
        ]
    exact = not vector_errors
    errors: List[str] = []
    freeze = source.get("mlFeatureFreeze") or {}
    stored_errors = source.get("exactVectorValidationErrors")
    freeze_errors = freeze.get("exactVectorValidationErrors") if isinstance(freeze, dict) else None

    for label, value in (("row", stored_errors), ("freeze", freeze_errors)):
        if value is not None and sorted(set(str(item) for item in (value or []))) != vector_errors:
            errors.append(f"{label}_exact_vector_validation_errors_mismatch")

    row_exact = source.get("exactVectorVerified")
    freeze_exact = freeze.get("exactVectorVerified") if isinstance(freeze, dict) else None
    if row_exact is not None and row_exact is not exact:
        errors.append("row_exact_vector_verified_mismatch")
    if freeze_exact is not None and freeze_exact is not exact:
        errors.append("freeze_exact_vector_verified_mismatch")

    if vector_errors:
        required_reasons = set(_vector_training_exclusions(vector_errors))
        row_reasons = set(str(reason) for reason in (source.get("trainingExclusionReasons") or []))
        freeze_reasons = (
            set(str(reason) for reason in (freeze.get("trainingExclusionReasons") or []))
            if isinstance(freeze, dict)
            else set()
        )
        if row_exact is not False or freeze_exact is not False:
            errors.append("invalid_vector_not_explicitly_unverified")
        if (
            source.get("trainingEligible") is not False
            or not isinstance(freeze, dict)
            or freeze.get("trainingEligible") is not False
        ):
            errors.append("invalid_vector_not_training_ineligible")
        if source.get("trainingEligibilityStatus") != "INELIGIBLE":
            errors.append("invalid_vector_training_status_missing")
        if source.get("selectionTrainingSeparationVersion") != VERSION:
            errors.append("invalid_vector_selection_training_separation_version_missing")
        if (
            not required_reasons.issubset(row_reasons)
            or not required_reasons.issubset(freeze_reasons)
        ):
            errors.append("invalid_vector_training_exclusions_missing")

    return sorted(set(errors))


def require_exact_locked_row(row: Dict[str, Any]) -> None:
    errors = validate_exact_locked_row(row)
    if errors:
        game_id = row.get("gameId") or row.get("game_id") or "unknown"
        raise RuntimeError(
            f"MLB_LOCKED_ML_VECTOR_INVALID:{game_id}:" + ",".join(errors)
        )


def apply(daily_lock_module: Any) -> Dict[str, Any]:
    if getattr(daily_lock_module, "_INQSI_MLB_DAILY_LOCK_ML_VECTOR_PRESERVATION_V1", False):
        return {
            "ok": True,
            "version": VERSION,
            "expectedVectorVersion": EXPECTED_VECTOR_VERSION,
            "alreadyApplied": True,
            "failClosed": True,
            "selectionLockIndependentOfTrainingVector": True,
        }

    original_compact_pick = daily_lock_module._compact_pick

    def compact_pick_with_ml_contract(row: Dict[str, Any]) -> Dict[str, Any]:
        source = copy.deepcopy(row or {})
        compact = dict(original_compact_pick(source))
        for field in PRESERVED_FIELDS:
            if field in source:
                compact[field] = copy.deepcopy(source.get(field))

        vector = compact.get("frozenFeatureVector") or {}
        compact["frozenFeatureVectorVersion"] = (
            compact.get("frozenFeatureVectorVersion") or vector.get("version")
        )
        compact["lockedAtUtc"] = compact.get("lockedAtUtc") or vector.get("lockAtUtc")
        compact["predictionSourcePullAt"] = (
            compact.get("predictionSourcePullAt") or vector.get("sourcePullAtUtc")
        )
        compact["lockedAmericanOdds"] = (
            compact.get("lockedAmericanOdds")
            if compact.get("lockedAmericanOdds") not in (None, "")
            else compact.get("americanOdds")
        )
        compact["lockedPrediction"] = True
        compact["officialPredictionStatus"] = (
            compact.get("officialPredictionStatus") or "OFFICIAL_LOCKED_PREDICTION"
        )
        compact["mlLockVectorStorageVersion"] = VERSION

        # Never persist postgame labels into the pregame card, even if an accidental
        # caller supplies them. The only permitted label slots are null inside the
        # fingerprinted vector until settlement performs a separate join.
        for forbidden in ("winner", "correct", "success", "homeWon", "pickCorrect"):
            compact.pop(forbidden, None)

        errors = _validate(source, compact)
        compact = apply_exact_vector_training_status(compact, errors)
        compact["mlLockVectorStorageVerified"] = not errors
        compact["mlLockVectorStorageErrors"] = errors
        return compact

    daily_lock_module._compact_pick = compact_pick_with_ml_contract
    daily_lock_module.MLB_DAILY_LOCK_ML_VECTOR_PRESERVATION_VERSION = VERSION
    daily_lock_module._INQSI_MLB_DAILY_LOCK_ML_VECTOR_PRESERVATION_V1 = True
    return {
        "ok": True,
        "version": VERSION,
        "expectedVectorVersion": EXPECTED_VECTOR_VERSION,
        "preservedFields": list(PRESERVED_FIELDS),
        "failClosed": True,
        "outcomeLabelsForbiddenAtLock": True,
        "selectionLockFailClosed": True,
        "mlTrainingFailClosed": True,
        "selectionLockIndependentOfTrainingVector": True,
    }
