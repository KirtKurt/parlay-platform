from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

VERSION = "MLB-DAILY-LOCK-ML-VECTOR-PRESERVATION-v1-fail-closed"
EXPECTED_VECTOR_VERSION = "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v1-home-away-outcome"

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
    payload = json.dumps(
        {
            "gameId": vector.get("gameId"),
            "lockAtUtc": vector.get("lockAtUtc"),
            "features": vector.get("features") or {},
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _selected_price_proven(row: Dict[str, Any]) -> bool:
    price = row.get("lockedAmericanOdds")
    if price in (None, "", 0, 0.0):
        price = row.get("americanOdds")
    book = row.get("priceBook")
    source = str(row.get("priceSource") or "").lower()
    return bool(price not in (None, "", 0, 0.0) and (book or source in {"real_book", "locked_real_book"}))


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

    lock_at = _parse_dt(vector.get("lockAtUtc"))
    source_at = _parse_dt(vector.get("sourcePullAtUtc"))
    if not lock_at:
        errors.append("frozen_vector_lock_timestamp_missing")
    if not source_at:
        errors.append("frozen_vector_source_timestamp_missing")
    if lock_at and source_at and source_at > lock_at:
        errors.append("frozen_vector_source_after_lock")

    labels = vector.get("labels") or {}
    if labels.get("homeWon") is not None or labels.get("pickCorrect") is not None:
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
    if freeze.get("exactVectorCreated") is False:
        errors.append("exact_vector_creation_failed")
    if compact.get("predictionSemanticsVersion") in (None, "") and compact.get("probabilitySemanticsFixed") is not True:
        errors.append("modern_probability_semantics_missing")
    if compact.get("teamWinProbabilityPct") in (None, ""):
        errors.append("team_win_probability_missing")
    if not _selected_price_proven(compact):
        errors.append("selected_side_locked_price_not_proven")

    return sorted(set(errors))


def apply(daily_lock_module: Any) -> Dict[str, Any]:
    if getattr(daily_lock_module, "_INQSI_MLB_DAILY_LOCK_ML_VECTOR_PRESERVATION_V1", False):
        return {
            "ok": True,
            "version": VERSION,
            "alreadyApplied": True,
            "failClosed": True,
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
        compact["mlLockVectorStorageVerified"] = not errors
        compact["mlLockVectorStorageErrors"] = errors
        if errors:
            game_id = compact.get("gameId") or "unknown"
            raise RuntimeError(
                f"MLB_DAILY_LOCK_ML_VECTOR_INVALID:{game_id}:" + ",".join(errors)
            )
        return compact

    daily_lock_module._compact_pick = compact_pick_with_ml_contract
    daily_lock_module.MLB_DAILY_LOCK_ML_VECTOR_PRESERVATION_VERSION = VERSION
    daily_lock_module._INQSI_MLB_DAILY_LOCK_ML_VECTOR_PRESERVATION_V1 = True
    return {
        "ok": True,
        "version": VERSION,
        "preservedFields": list(PRESERVED_FIELDS),
        "failClosed": True,
        "outcomeLabelsForbiddenAtLock": True,
    }
