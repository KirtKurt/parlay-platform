from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple

VERSION = "MLB-ML-CLEAN-COHORT-HARDENING-v4-valid-candidate-wins-dedup"


def _row_game_id(row: Dict[str, Any]) -> str:
    return str(
        row.get("id")
        or row.get("gameId")
        or row.get("game_id")
        or (row.get("lockedCardAudit") or {}).get("providerGameId")
        or ""
    )


def _expected_fingerprint(vector: Dict[str, Any]) -> str:
    source = json.dumps(
        {
            "gameId": vector.get("gameId"),
            "lockAtUtc": vector.get("lockAtUtc"),
            "features": vector.get("features") or {},
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _candidate_rank(row: Dict[str, Any]) -> tuple:
    audit = row.get("lockedCardAudit") or {}
    pipeline = audit.get("finalPipelineState") or {}
    vector = row.get("frozenFeatureVector") or {}
    return (
        int(bool(row.get("finalGuardedStored"))),
        int(bool(row.get("featureVectorFrozenAtLock"))),
        int(bool(vector.get("fingerprint"))),
        int(pipeline.get("pipelineDepth") or 0),
        str(row.get("createdAt") or row.get("created_at") or audit.get("rowCreatedAtUtc") or ""),
    )


def apply(cohort_module: Any):
    if getattr(cohort_module, "_INQSI_MLB_CLEAN_COHORT_HARDENING_V4_APPLIED", False):
        return cohort_module

    original_eligibility = cohort_module.eligibility
    original_build = cohort_module.build

    def eligibility(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
        _, reasons = original_eligibility(row)
        reasons = list(reasons or [])
        freeze = row.get("mlFeatureFreeze") or {}
        coverage = row.get("slateCoverage") or {}
        frozen_vector = row.get("frozenFeatureVector") or {}

        coverage_complete = bool(
            freeze.get("completeSlateCoverage") is True
            or coverage.get("coverageComplete") is True
        )
        if not coverage_complete:
            reasons.append("incomplete_or_unproven_slate_coverage")
        if freeze and freeze.get("trainingEligible") is not True:
            reasons.extend(str(value) for value in (freeze.get("trainingExclusionReasons") or []))
        if not isinstance(frozen_vector, dict) or not frozen_vector.get("fingerprint"):
            reasons.append("missing_exact_stored_lock_time_feature_vector")
        if str(frozen_vector.get("version") or "") != str(cohort_module.FEATURE_SNAPSHOT_VERSION):
            reasons.append("wrong_frozen_feature_vector_version")
        if frozen_vector.get("immutableSource") != "locked_prediction_row_pre_game_features":
            reasons.append("feature_vector_source_not_lock_time_prediction")
        if frozen_vector.get("derivedOnceFromImmutableLockedRow") is not True:
            reasons.append("feature_vector_not_proven_immutable")

        vector_source = cohort_module._parse_dt(frozen_vector.get("sourcePullAtUtc"))
        vector_lock = cohort_module._parse_dt(frozen_vector.get("lockAtUtc"))
        row_lock = cohort_module._lock_at(row)
        if not vector_source or not vector_lock or vector_source > vector_lock:
            reasons.append("frozen_vector_source_after_or_missing_lock")
        if vector_lock and row_lock and vector_lock != row_lock:
            reasons.append("frozen_vector_lock_mismatch")

        row_game_id = _row_game_id(row)
        if row_game_id and str(frozen_vector.get("gameId") or "") != row_game_id:
            reasons.append("frozen_vector_game_identity_mismatch")

        fingerprint = str(frozen_vector.get("fingerprint") or "")
        if fingerprint and fingerprint != _expected_fingerprint(frozen_vector):
            reasons.append("frozen_vector_fingerprint_mismatch")
        if not isinstance(frozen_vector.get("features"), dict) or not frozen_vector.get("features"):
            reasons.append("frozen_vector_features_missing")

        reasons = sorted(set(reasons))
        return not reasons, reasons

    def build(rows):
        rows_list = list(rows or [])
        grouped: Dict[tuple, List[Dict[str, Any]]] = {}
        for row in rows_list:
            key = (_row_game_id(row), str(cohort_module._lock_at(row) or ""))
            grouped.setdefault(key, []).append(row)

        selected_rows: List[Dict[str, Any]] = []
        superseded_invalid: List[Dict[str, Any]] = []
        superseded_valid_count = 0

        for candidates in grouped.values():
            evaluated = [(row, *eligibility(row)) for row in candidates]
            valid = [row for row, ok, _ in evaluated if ok]
            if valid:
                chosen = max(valid, key=_candidate_rank)
                selected_rows.append(chosen)
                superseded_valid_count += max(0, len(valid) - 1)
                for row, ok, reasons in evaluated:
                    if not ok:
                        superseded_invalid.append(
                            {
                                "gameId": _row_game_id(row),
                                "slateDateEt": row.get("slateDateEt"),
                                "reasons": sorted(set(list(reasons) + ["superseded_by_valid_duplicate"])),
                            }
                        )
            else:
                # Keep one representative invalid candidate so the base builder records
                # the failure. Other invalid duplicates are preserved in diagnostics.
                chosen_row, _, _ = max(evaluated, key=lambda item: _candidate_rank(item[0]))
                selected_rows.append(chosen_row)
                for row, _, reasons in evaluated:
                    if row is chosen_row:
                        continue
                    superseded_invalid.append(
                        {
                            "gameId": _row_game_id(row),
                            "slateDateEt": row.get("slateDateEt"),
                            "reasons": sorted(set(list(reasons) + ["superseded_invalid_duplicate"])),
                        }
                    )

        out = original_build(selected_rows)
        quarantined = list(out.get("quarantinedRows") or []) + superseded_invalid
        reason_counts: Dict[str, int] = {}
        for item in quarantined:
            for reason in item.get("reasons") or []:
                reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1

        out["inputRows"] = len(rows_list)
        out["quarantinedRows"] = quarantined
        out["quarantinedRowCount"] = len(quarantined)
        out["quarantineReasonCounts"] = reason_counts
        out["deduplicatedIdentityCount"] = len(grouped)
        out["supersededValidDuplicateCount"] = superseded_valid_count
        out["validCandidateWinsDuplicateSelection"] = True
        out["hardeningVersion"] = VERSION
        out["completeSlateCoverageRequired"] = True
        out["immutableFrozenFeatureVectorRequired"] = True
        out["fingerprintRecalculationRequired"] = True
        out["gameIdentityAndLockMustMatchAuditRow"] = True
        out["laterFeatureReconstructionAllowed"] = False
        out["requiredFeatureVectorVersion"] = cohort_module.FEATURE_SNAPSHOT_VERSION
        out["version"] = str(out.get("version") or "") + "+" + VERSION
        return out

    cohort_module.eligibility = eligibility
    cohort_module.build = build
    cohort_module._INQSI_MLB_CLEAN_COHORT_HARDENING_APPLIED = True
    cohort_module._INQSI_MLB_CLEAN_COHORT_HARDENING_V2_APPLIED = True
    cohort_module._INQSI_MLB_CLEAN_COHORT_HARDENING_V3_APPLIED = True
    cohort_module._INQSI_MLB_CLEAN_COHORT_HARDENING_V4_APPLIED = True
    return cohort_module
