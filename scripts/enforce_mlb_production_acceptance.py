from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_aws_training_v1 as trainer
import mlb_ml_experiment_v2 as experiment


V2_TRAINING_STATUS_MAX_AGE_MINUTES = 8.0 * 60.0
V2_SELECTION_CAPTURE_STATUS_MAX_AGE_MINUTES = 45.0
PRODUCTION_ACCEPTANCE_SCOPE_VERSION = (
    "MLB-PRODUCTION-ACCEPTANCE-SCOPE-v3-canonical-finalized-outcomes"
)
PRODUCTION_ACCEPTANCE_SCOPE_FINGERPRINT_VERSION = (
    "MLB-PRODUCTION-ACCEPTANCE-SCOPE-SHA256-v3"
)
PRODUCTION_ACCEPTANCE_ROWS_FINGERPRINT_VERSION = (
    "MLB-PRODUCTION-ACCEPTANCE-RAW-ROWS-SHA256-v1"
)
LOCK_MINUTES_BEFORE_GAME = 45


def _load(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return payload


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _scope_fingerprint(value: Dict[str, Any]) -> str:
    material = {
        key: item
        for key, item in value.items()
        if key != "scopeFingerprint"
    }
    encoded = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pct(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator * 100.0, 2) if denominator else None


def _pct_matches(actual: Any, expected: Optional[float]) -> bool:
    if expected is None:
        return actual is None
    parsed = _float(actual)
    return parsed is not None and abs(parsed - expected) < 0.001


def _official_outcome_binding_errors(proof: Any) -> list[str]:
    try:
        from scripts.run_mlb_ml_v3_audit_report import (
            _audit_row_official_outcome_binding_errors as validate_binding,
        )
    except ImportError:
        from run_mlb_ml_v3_audit_report import (  # type: ignore
            _audit_row_official_outcome_binding_errors as validate_binding,
        )
    if not isinstance(proof, dict):
        return ["official_finalized_game_outcome_authority_missing"]
    row = {
        key: proof.get(key)
        for key in (
            "awayTeam",
            "homeTeam",
            "awayScore",
            "homeScore",
            "winner",
            "predictedWinner",
            "correct",
        )
    }
    return validate_binding(
        row,
        proof.get("officialOutcomeAuthority"),
        expected_slate_date_et=str(proof.get("slateDateEt") or ""),
        expected_official_game_pk=str(proof.get("officialGamePk") or ""),
    )


def _post_cutoff_scope_valid(
    value: Any,
    *,
    audit: Optional[Dict[str, Any]] = None,
) -> bool:
    if not isinstance(value, dict):
        return False
    expected_identity = {
        "experimentVersion": experiment.VERSION,
        "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
        "releaseContractId": experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        "releaseCutoffUtc": experiment.PRODUCTION_RELEASE_CUTOFF_UTC,
    }
    if value.get("experimentIdentity") != expected_identity:
        return False
    if value.get("releaseCutoffUtc") != experiment.PRODUCTION_RELEASE_CUTOFF_UTC:
        return False
    cutoff = _parse_dt(value.get("releaseCutoffUtc"))
    if cutoff is None:
        return False
    integer_fields = (
        "sourceRowCount",
        "unscopedInvalidRowCount",
        "preCutoffQuarantinedFinalGameCount",
        "postCutoffCompletedFinalGameCount",
        "postCutoffGradedPredictionCount",
        "postCutoffMissingPredictionCount",
        "postCutoffOfficialPredictionCount",
        "postCutoffExactOfficialGameIdCount",
        "postCutoffCanonicalAuthorityCount",
        "postCutoffOfficialOutcomeAuthorityCount",
        "postCutoffCanonicalLockAtOrAfterCutoffCount",
        "postCutoffFinalizedSlateCount",
        "postCutoffExactFullSlateCount",
        "postCutoffFullSlateCoverageDefectCount",
    )
    if any(
        not isinstance(value.get(field), int)
        or isinstance(value.get(field), bool)
        or value.get(field) < 0
        for field in integer_fields
    ):
        return False
    if not (
        value.get("ok") is True
        and value.get("version") == PRODUCTION_ACCEPTANCE_SCOPE_VERSION
        and value.get("scopeBasis")
        == "scheduled_game_tminus45_at_or_after_release_cutoff"
        and value.get("scheduledLockOffsetMinutes") == LOCK_MINUTES_BEFORE_GAME
        and value.get("canonicalLockMustAlsoBeAtOrAfterReleaseCutoff") is True
        and value.get("exactOfficialGameIdRequired") is True
        and value.get("exactOfficialFinalizedSlateRequired") is True
        and value.get("exactOfficialFinalOutcomeRequired") is True
        and value.get("sourceRowsFingerprintVersion")
        == PRODUCTION_ACCEPTANCE_ROWS_FINGERPRINT_VERSION
        and re.fullmatch(
            r"[0-9a-f]{64}", str(value.get("sourceRowsFingerprint") or "")
        )
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(value.get("canonicalFinalizedSlateEvidenceFingerprint") or ""),
        )
        and value.get("scopeFingerprintVersion")
        == PRODUCTION_ACCEPTANCE_SCOPE_FINGERPRINT_VERSION
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("scopeFingerprint") or ""))
        and value.get("scopeFingerprint") == _scope_fingerprint(value)
    ):
        return False

    construction_blockers = value.get("scopeConstructionBlockers")
    errors = value.get("errors")
    invalid_rows = value.get("invalidTimeRows")
    quarantined_games = value.get("preCutoffQuarantinedGames")
    proofs = value.get("postCutoffGameProofs")
    official_game_pks = value.get("postCutoffOfficialGamePks")
    defects = value.get("postCutoffDefects")
    blocker_codes = value.get("postCutoffBlockerCodes")
    coverage = value.get("coverage")
    finalized_slate_proofs = value.get("postCutoffFinalizedSlateProofs")
    first_complete_slate_proof = value.get(
        "firstCompletePostCutoffSlateProof"
    )
    if not (
        isinstance(construction_blockers, list)
        and not construction_blockers
        and isinstance(errors, list)
        and not errors
        and isinstance(invalid_rows, list)
        and isinstance(quarantined_games, list)
        and isinstance(proofs, list)
        and isinstance(official_game_pks, list)
        and isinstance(defects, list)
        and isinstance(blocker_codes, list)
        and isinstance(coverage, dict)
        and isinstance(finalized_slate_proofs, list)
        and isinstance(first_complete_slate_proof, dict)
    ):
        return False

    source = value["sourceRowCount"]
    invalid = value["unscopedInvalidRowCount"]
    quarantined = value["preCutoffQuarantinedFinalGameCount"]
    completed = value["postCutoffCompletedFinalGameCount"]
    if not (
        invalid == len(invalid_rows) == 0
        and quarantined == len(quarantined_games)
        and completed == len(proofs)
        and source == invalid + quarantined + completed
    ):
        return False
    for game in quarantined_games:
        if not isinstance(game, dict):
            return False
        commence = _parse_dt(game.get("commenceTime"))
        scheduled = _parse_dt(game.get("scheduledLockAtUtc"))
        if (
            commence is None
            or scheduled is None
            or scheduled
            != commence - timedelta(minutes=LOCK_MINUTES_BEFORE_GAME)
            or scheduled >= cutoff
        ):
            return False

    derived_defects = []
    derived_blocker_codes = set()
    graded = official = exact_ids = canonical_authorities = 0
    official_outcome_authorities = post_cutoff_locks = 0
    graded_correct = official_correct = 0
    accepted_official_ids = set()
    derived_official_game_pks = []
    for proof in proofs:
        if not isinstance(proof, dict):
            return False
        commence = _parse_dt(proof.get("commenceTime"))
        scheduled = _parse_dt(proof.get("scheduledLockAtUtc"))
        if (
            commence is None
            or scheduled is None
            or scheduled
            != commence - timedelta(minutes=LOCK_MINUTES_BEFORE_GAME)
            or scheduled < cutoff
        ):
            return False
        proof_blockers = proof.get("blockers")
        if not isinstance(proof_blockers, list) or proof_blockers != sorted(
            set(proof_blockers)
        ):
            return False
        derived_blocker_codes.update(proof_blockers)
        accepted = proof.get("acceptedCanonicalGrade") is True
        if accepted != (not proof_blockers):
            return False
        outcome_binding_errors = _official_outcome_binding_errors(proof)
        outcome_authority_proven = not outcome_binding_errors
        if proof.get("officialOutcomeAuthorityProven") is not outcome_authority_proven:
            return False
        if any(error not in proof_blockers for error in outcome_binding_errors):
            return False
        official_outcome_authorities += int(outcome_authority_proven)
        if proof.get("exactOfficialGameIdProven") is True:
            exact_ids += 1
            derived_official_game_pks.append(str(proof.get("officialGamePk") or ""))
        if proof.get("canonicalAuthorityProven") is True:
            canonical_authorities += 1
        if proof.get("canonicalLockAtOrAfterCutoffProven") is True:
            post_cutoff_locks += 1
        if accepted:
            official_pk = str(proof.get("officialGamePk") or "")
            lock_at = _parse_dt(proof.get("canonicalLockAtUtc"))
            authority_lock_at = _parse_dt(
                proof.get("canonicalAuthorityLockAtUtc")
            )
            if not (
                proof.get("completed") is True
                and proof.get("status") == "GRADED"
                and isinstance(proof.get("correct"), bool)
                and proof.get("canonicalAuthorityProven") is True
                and proof.get("officialOutcomeAuthorityProven") is True
                and proof.get("exactOfficialGameIdProven") is True
                and proof.get("canonicalLockAtOrAfterCutoffProven") is True
                and re.fullmatch(r"[0-9]+", official_pk)
                and official_pk not in accepted_official_ids
                and lock_at is not None
                and authority_lock_at == lock_at
                and lock_at >= cutoff
                and str(proof.get("canonicalSourcePk") or "").startswith(
                    "GAME_WINNERS#mlb#"
                )
                and str(proof.get("canonicalSourceSk") or "").startswith(
                    "LOCKED#GAME#"
                )
            ):
                return False
            accepted_official_ids.add(official_pk)
            graded += 1
            graded_correct += int(proof["correct"] is True)
            if proof.get("officialPrediction") is True:
                official += 1
                official_correct += int(proof["correct"] is True)
        else:
            derived_defects.append(
                {
                    key: proof.get(key)
                    for key in (
                        "gameId",
                        "officialGamePk",
                        "scheduledLockAtUtc",
                        "canonicalLockAtUtc",
                        "status",
                        "blockers",
                    )
                }
            )

    missing = completed - graded
    exact_full_slate_count = 0
    full_slate_defect_count = 0
    finalized_slate_dates = set()
    for slate_proof in finalized_slate_proofs:
        if not isinstance(slate_proof, dict):
            return False
        slate_date = str(slate_proof.get("slateDateEt") or "")
        expected = slate_proof.get("officialGamePks")
        observed = slate_proof.get("observedOfficialGamePks")
        missing_pks = slate_proof.get("missingOfficialGamePks")
        unexpected_pks = slate_proof.get("unexpectedOfficialGamePks")
        duplicate_pks = slate_proof.get("duplicateObservedOfficialGamePks")
        noncanonical_pks = slate_proof.get("noncanonicalOfficialGamePks")
        slate_errors = slate_proof.get("errors")
        if not (
            slate_date
            and slate_date not in finalized_slate_dates
            and isinstance(expected, list)
            and expected == sorted(set(expected))
            and expected
            and isinstance(observed, list)
            and observed == sorted(observed)
            and isinstance(missing_pks, list)
            and isinstance(unexpected_pks, list)
            and isinstance(duplicate_pks, list)
            and isinstance(noncanonical_pks, list)
            and isinstance(slate_errors, list)
            and slate_errors == sorted(set(slate_errors))
            and isinstance(slate_proof.get("unidentifiedAuditRowCount"), int)
            and not isinstance(slate_proof.get("unidentifiedAuditRowCount"), bool)
            and slate_proof.get("unidentifiedAuditRowCount") >= 0
            and re.fullmatch(
                r"[0-9a-f]{64}",
                str(slate_proof.get("officialGameSetFingerprint") or ""),
            )
            and re.fullmatch(
                r"[0-9a-f]{64}",
                str(slate_proof.get("authorityFingerprint") or ""),
            )
        ):
            return False
        finalized_slate_dates.add(slate_date)
        slate_rows = [proof for proof in proofs if proof.get("slateDateEt") == slate_date]
        derived_observed = sorted(
            str(proof.get("officialGamePk") or "")
            for proof in slate_rows
            if str(proof.get("officialGamePk") or "")
        )
        derived_duplicates = sorted(
            game_pk
            for game_pk in set(derived_observed)
            if derived_observed.count(game_pk) > 1
        )
        expected_set = set(expected)
        derived_missing = sorted(expected_set - set(derived_observed))
        derived_unexpected = sorted(set(derived_observed) - expected_set)
        derived_noncanonical = sorted(
            str(proof.get("officialGamePk") or "")
            for proof in slate_rows
            if (
                proof.get("acceptedCanonicalGrade") is not True
                and str(proof.get("officialGamePk") or "") in expected_set
            )
        )
        unidentified = sum(
            not str(proof.get("officialGamePk") or "") for proof in slate_rows
        )
        derived_slate_errors = []
        if derived_missing:
            derived_slate_errors.append("official_finalized_slate_missing_audit_rows")
        if derived_unexpected:
            derived_slate_errors.append("audit_rows_not_in_official_finalized_slate")
        if derived_duplicates:
            derived_slate_errors.append("duplicate_audit_official_game_pk")
        if derived_noncanonical or unidentified:
            derived_slate_errors.append("official_slate_rows_not_all_canonically_graded")
        if len(slate_rows) != len(expected):
            derived_slate_errors.append("official_finalized_slate_row_count_mismatch")
        derived_slate_errors = sorted(set(derived_slate_errors))
        achieved = not derived_slate_errors
        if not (
            slate_proof.get("officialGameCount") == len(expected)
            and slate_proof.get("observedAuditRowCount") == len(slate_rows)
            and observed == derived_observed
            and missing_pks == derived_missing
            and unexpected_pks == derived_unexpected
            and duplicate_pks == derived_duplicates
            and noncanonical_pks == derived_noncanonical
            and slate_proof.get("unidentifiedAuditRowCount") == unidentified
            and slate_errors == derived_slate_errors
            and slate_proof.get("exactFullSlateSettlementProven") is achieved
        ):
            return False
        exact_full_slate_count += int(achieved)
        full_slate_defect_count += int(not achieved)

    qualifying_dates = [
        proof["slateDateEt"]
        for proof in finalized_slate_proofs
        if proof.get("exactFullSlateSettlementProven") is True
    ]
    if not (
        value["postCutoffFinalizedSlateCount"] == len(finalized_slate_proofs)
        and value["postCutoffExactFullSlateCount"] == exact_full_slate_count
        and value["postCutoffFullSlateCoverageDefectCount"]
        == full_slate_defect_count
        and value.get("fullSlateSettlementCoverageOk")
        is (full_slate_defect_count == 0)
        and first_complete_slate_proof.get("achieved")
        is bool(qualifying_dates)
        and first_complete_slate_proof.get("qualifyingSlateDateEt")
        == (qualifying_dates[0] if qualifying_dates else None)
        and first_complete_slate_proof.get("evaluatedFinalizedSlateCount")
        == len(finalized_slate_proofs)
        and first_complete_slate_proof.get("exactFullSlateCount")
        == exact_full_slate_count
        and isinstance(first_complete_slate_proof.get("authority"), str)
        and bool(first_complete_slate_proof.get("authority"))
    ):
        return False

    if not (
        value["postCutoffGradedPredictionCount"] == graded
        and value["postCutoffMissingPredictionCount"] == missing
        and value["postCutoffOfficialPredictionCount"] == official
        and value["postCutoffExactOfficialGameIdCount"] == exact_ids
        and value["postCutoffCanonicalAuthorityCount"] == canonical_authorities
        and value["postCutoffOfficialOutcomeAuthorityCount"]
        == official_outcome_authorities
        and value["postCutoffCanonicalLockAtOrAfterCutoffCount"]
        == post_cutoff_locks
        and official_game_pks == sorted(derived_official_game_pks)
        and defects == derived_defects
        and blocker_codes == sorted(derived_blocker_codes)
        and value.get("settlementCoverageOk")
        is (missing == 0 and full_slate_defect_count == 0)
        and _pct_matches(
            value.get("postCutoffOfficialAccuracyPct"),
            _pct(official_correct, official),
        )
        and _pct_matches(
            value.get("postCutoffAllGamesAccuracyPct"),
            _pct(graded_correct, graded),
        )
    ):
        return False

    expected_coverage = {
        "canonicalGradedPct": _pct(graded, completed),
        "canonicalAuthorityPct": _pct(canonical_authorities, completed),
        "officialOutcomeAuthorityPct": _pct(
            official_outcome_authorities, completed
        ),
        "exactOfficialGameIdPct": _pct(exact_ids, completed),
        "canonicalLockAtOrAfterCutoffPct": _pct(post_cutoff_locks, completed),
        "officialPredictionPct": _pct(official, completed),
        "canonicalGradedComplete": graded == completed,
        "officialOutcomeAuthorityComplete": (
            official_outcome_authorities == completed
        ),
        "exactOfficialGameIdComplete": exact_ids == completed,
        "canonicalLockAtOrAfterCutoffComplete": post_cutoff_locks == completed,
    }
    if coverage != expected_coverage:
        return False
    if audit is not None:
        try:
            from scripts.run_mlb_ml_v3_audit_report import (
                _post_cutoff_production_acceptance_scope as rebuild_scope,
            )
        except ImportError:
            from run_mlb_ml_v3_audit_report import (  # type: ignore
                _post_cutoff_production_acceptance_scope as rebuild_scope,
            )
        try:
            if rebuild_scope(audit) != value:
                return False
        except Exception:
            return False
    return True


def _mode_status_health(
    value: Any,
    *,
    expected_mode: str,
    maximum_age_minutes: float,
    now_utc: datetime,
    current_manifest_digest: Any,
    deployed_identity: Any,
    manifest_read_stable: bool,
) -> Dict[str, Any]:
    health = value if isinstance(value, dict) else {}
    latest = health.get("latestRun") if isinstance(health.get("latestRun"), dict) else {}
    created_at = _parse_dt(latest.get("createdAtUtc"))
    age_minutes = (
        round((now_utc - created_at).total_seconds() / 60.0, 2)
        if created_at
        else None
    )
    deployment = latest.get("deploymentIdentity") or {}
    deployment_valid = bool(deployment and deployment == deployed_identity)
    fresh = bool(
        age_minutes is not None
        and 0 <= age_minutes <= maximum_age_minutes
    )
    errors = list(health.get("errors") or [])
    contract_errors = []
    if latest.get("ok") is not True:
        contract_errors.append("status_not_ok")
    if latest.get("version") != trainer.VERSION:
        contract_errors.append("status_version_mismatch")
    if latest.get("experimentId") != experiment.PRODUCTION_EXPERIMENT_ID:
        contract_errors.append("status_experiment_mismatch")
    if latest.get("executionMode") != expected_mode:
        contract_errors.append("status_mode_mismatch")
    if latest.get("statusFingerprintVersion") != trainer.STATUS_FINGERPRINT_VERSION:
        contract_errors.append("status_fingerprint_version_mismatch")
    if latest.get("statusFingerprint") != trainer._status_fingerprint(latest):
        contract_errors.append("status_fingerprint_mismatch")
    if latest.get("executionConcurrencyControl") != (
        trainer.execution_concurrency_control(acquired_for_run=True)
    ):
        contract_errors.append("status_execution_lease_contract_mismatch")
    if not current_manifest_digest or latest.get("manifestDigest") != current_manifest_digest:
        contract_errors.append("status_manifest_mismatch")
    if not manifest_read_stable:
        contract_errors.append("manifest_changed_during_status_read")
    if not deployment_valid:
        contract_errors.append("status_deployment_identity_mismatch")
    valid = bool(
        health.get("ok") is True
        and health.get("statusPresent") is True
        and health.get("executionMode") == expected_mode
        and health.get("latestRunTimestampValid") is True
        and health.get("latestRunFresh") is True
        and not errors
        and not contract_errors
        and deployment_valid
        and fresh
    )
    return {
        "ok": valid,
        "executionMode": expected_mode,
        "statusPresent": health.get("statusPresent") is True,
        "latestRunStatus": latest.get("status"),
        "latestRunCreatedAtUtc": latest.get("createdAtUtc"),
        "latestRunTimestampValid": created_at is not None,
        "latestRunAgeMinutes": age_minutes,
        "latestRunFresh": fresh,
        "latestRunMaxAgeMinutes": maximum_age_minutes,
        "deploymentIdentity": deployment if isinstance(deployment, dict) else {},
        "deploymentIdentityValid": deployment_valid,
        "auditReportedErrors": errors,
        "contractErrors": contract_errors,
        "statusVersion": latest.get("version"),
        "experimentId": latest.get("experimentId"),
        "manifestDigest": latest.get("manifestDigest"),
        "statusFingerprintVersion": latest.get("statusFingerprintVersion"),
        "executionConcurrencyControl": latest.get(
            "executionConcurrencyControl"
        ),
    }


def build_acceptance(
    *,
    pull_guard: Dict[str, Any],
    verifier: Dict[str, Any],
    audit: Dict[str, Any],
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    infrastructure_blockers: List[str] = []
    model_blockers: List[str] = []
    warnings: List[str] = []

    if pull_guard.get("guardPassed") is not True:
        infrastructure_blockers.append("PULL_GUARD_FAILED")
    if pull_guard.get("officialScheduleVerified") is not True:
        infrastructure_blockers.append("OFFICIAL_SCHEDULE_UNVERIFIED")
    if pull_guard.get("pullsRequired") is True and pull_guard.get("fresh") is not True:
        infrastructure_blockers.append("LATEST_PULL_NOT_FRESH")
    if pull_guard.get("missingCleanScheduledSlots"):
        infrastructure_blockers.append("MISSING_15_MINUTE_PULL_SLOTS")
    if _int(pull_guard.get("duplicateOrExtraPullsSinceStart")) > 0:
        infrastructure_blockers.append("DUPLICATE_OR_EXTRA_SCHEDULED_PULLS")
    if _int(pull_guard.get("preStartPollutedPullCount")) > 0:
        infrastructure_blockers.append("PRESTART_PULLS_EXIST_ON_CURRENT_SLATE")

    if verifier.get("ok") is not True:
        infrastructure_blockers.append("LIVE_PRODUCTION_VERIFIER_FAILED")
    for blocker in verifier.get("blockers") or []:
        infrastructure_blockers.append(f"VERIFIER:{blocker}")

    summary = audit.get("summary") or {}
    optimization = audit.get("mlOptimizationV3") or {}
    raw_v2_training = audit.get("mlTrainingV2")
    v2_training = raw_v2_training if isinstance(raw_v2_training, dict) else {}
    if audit.get("ok") is not True:
        infrastructure_blockers.append("AWS_BACKED_ML_AUDIT_FAILED")
    audit_created = _parse_dt(audit.get("createdAtUtc"))
    audit_age_minutes = None
    if audit_created:
        audit_age_minutes = round((now_utc - audit_created).total_seconds() / 60.0, 2)
        if audit_age_minutes < 0 or audit_age_minutes > 45:
            infrastructure_blockers.append("AWS_BACKED_ML_AUDIT_STALE")
    else:
        infrastructure_blockers.append("AWS_BACKED_ML_AUDIT_TIMESTAMP_MISSING")

    legacy_completed = _int(summary.get("completedFinalGames"))
    legacy_graded = _int(summary.get("gradedPredictionCount"))
    legacy_missing = _int(summary.get("missingPredictionCount"))
    legacy_official_count = _int(summary.get("officialPredictionCount"))
    legacy_official_accuracy = _float(
        summary.get("rolling24hOfficialAccuracyPct")
    )
    legacy_all_games_accuracy = _float(
        summary.get("rolling24hAllGamesAccuracyPct")
    )
    post_cutoff_scope = audit.get("productionAcceptanceScope")
    invalid_scope_missing_official_accuracy = bool(
        isinstance(post_cutoff_scope, dict)
        and _int(post_cutoff_scope.get("postCutoffGradedPredictionCount")) > 0
        and _int(post_cutoff_scope.get("postCutoffOfficialPredictionCount")) > 0
        and post_cutoff_scope.get("postCutoffOfficialAccuracyPct") is None
    )
    post_cutoff_scope_valid = _post_cutoff_scope_valid(
        post_cutoff_scope,
        audit=audit,
    )
    if not post_cutoff_scope_valid:
        infrastructure_blockers.append(
            "POST_CUTOFF_SETTLEMENT_SCOPE_MISSING_OR_INVALID"
        )
        post_cutoff_scope = {}
    completed = _int(post_cutoff_scope.get("postCutoffCompletedFinalGameCount"))
    graded = _int(post_cutoff_scope.get("postCutoffGradedPredictionCount"))
    missing = _int(post_cutoff_scope.get("postCutoffMissingPredictionCount"))
    official_count = _int(
        post_cutoff_scope.get("postCutoffOfficialPredictionCount")
    )
    official_accuracy = _float(
        post_cutoff_scope.get("postCutoffOfficialAccuracyPct")
    )
    all_games_accuracy = _float(
        post_cutoff_scope.get("postCutoffAllGamesAccuracyPct")
    )
    full_slate_coverage_defect_count = _int(
        post_cutoff_scope.get("postCutoffFullSlateCoverageDefectCount")
    )
    target = _float(summary.get("targetAccuracyPct")) or 90.0

    if completed > graded or missing > 0:
        infrastructure_blockers.append("COMPLETED_GAMES_WITHOUT_IMMUTABLE_GRADEABLE_PREDICTIONS")
    if full_slate_coverage_defect_count > 0:
        infrastructure_blockers.append(
            "OFFICIAL_FINALIZED_SLATE_COVERAGE_INCOMPLETE"
        )
    if completed > 0 and official_count == 0:
        infrastructure_blockers.append("NO_OFFICIAL_PREDICTIONS_FOR_COMPLETED_WINDOW")
    elif official_count != completed:
        infrastructure_blockers.append("OFFICIAL_PREDICTION_COVERAGE_INCOMPLETE")

    if graded == 0 and invalid_scope_missing_official_accuracy:
        accuracy_status = "UNMEASURABLE_OFFICIAL_ACCURACY_MISSING"
        infrastructure_blockers.append(
            "AUTHORITATIVE_OFFICIAL_ACCURACY_MISSING_FOR_GRADED_ROWS"
        )
    elif graded == 0:
        accuracy_status = "UNMEASURABLE_NO_GRADED_PREDICTIONS"
    elif official_accuracy is None:
        accuracy_status = "UNMEASURABLE_OFFICIAL_ACCURACY_MISSING"
        infrastructure_blockers.append(
            "AUTHORITATIVE_OFFICIAL_ACCURACY_MISSING_FOR_GRADED_ROWS"
        )
    elif official_accuracy >= target:
        accuracy_status = "TARGET_MET"
    else:
        accuracy_status = "BELOW_TARGET"
        warnings.append("ASPIRATIONAL_90_PCT_DASHBOARD_TARGET_NOT_MET")

    clean_rows = optimization.get("cleanRowCount")
    quarantined_rows = optimization.get("quarantinedRowCount")
    disposition_complete = isinstance(clean_rows, int) and isinstance(quarantined_rows, int)
    if not disposition_complete:
        infrastructure_blockers.append("ML_CLEAN_QUARANTINE_DISPOSITION_MISSING")
    elif clean_rows < 500:
        warnings.append("ML_PROMOTION_REMAINS_UNPROVEN_BELOW_500_CLEAN_ROWS")
    if not v2_training or v2_training.get("ok") is not True:
        infrastructure_blockers.append("AWS_V2_TRAINER_STATUS_MISSING_OR_INVALID")
    current_manifest_digest = v2_training.get("manifestDigest")
    manifest_read_before = v2_training.get("manifestReadBefore")
    manifest_read_after = v2_training.get("manifestReadAfter")
    manifest_read_stable = bool(
        isinstance(manifest_read_before, dict)
        and isinstance(manifest_read_after, dict)
        and manifest_read_before == manifest_read_after
        and manifest_read_after.get("manifestDigest") == current_manifest_digest
        and manifest_read_after.get("revision") is not None
        and v2_training.get("manifestReadStable") is True
    )
    deployed_identity = v2_training.get("deployedTrainerIdentity")
    deployed_identity_valid = bool(
        isinstance(deployed_identity, dict)
        and re.fullmatch(r"[0-9a-f]{40}", str(deployed_identity.get("gitSha") or ""))
        and re.fullmatch(
            r"[0-9a-f]{64}", str(deployed_identity.get("templateSha256") or "")
        )
    )
    if v2_training.get("experimentId") != experiment.PRODUCTION_EXPERIMENT_ID:
        infrastructure_blockers.append("AWS_V2_EXPERIMENT_IDENTITY_INVALID")
    if not deployed_identity_valid:
        infrastructure_blockers.append("AWS_V2_DEPLOYED_TRAINER_IDENTITY_INVALID")
    if not manifest_read_stable:
        infrastructure_blockers.append("AWS_V2_MANIFEST_STATUS_SNAPSHOT_UNSTABLE")
    training_health = _mode_status_health(
        v2_training.get("trainingHealth"),
        expected_mode="training",
        maximum_age_minutes=V2_TRAINING_STATUS_MAX_AGE_MINUTES,
        now_utc=now_utc,
        current_manifest_digest=current_manifest_digest,
        deployed_identity=deployed_identity,
        manifest_read_stable=manifest_read_stable,
    )
    capture_health = _mode_status_health(
        v2_training.get("selectionCaptureHealth"),
        expected_mode="selection_capture",
        maximum_age_minutes=V2_SELECTION_CAPTURE_STATUS_MAX_AGE_MINUTES,
        now_utc=now_utc,
        current_manifest_digest=current_manifest_digest,
        deployed_identity=deployed_identity,
        manifest_read_stable=manifest_read_stable,
    )
    if not training_health["ok"]:
        infrastructure_blockers.append("AWS_V2_TRAINING_STATUS_MISSING_STALE_OR_INVALID")
    if not capture_health["ok"]:
        infrastructure_blockers.append(
            "AWS_V2_SELECTION_CAPTURE_STATUS_MISSING_STALE_OR_INVALID"
        )
    deployment_identity_agreement = bool(
        v2_training.get("deploymentIdentityAgreement") is True
        and training_health["deploymentIdentity"]
        and training_health["deploymentIdentity"]
        == capture_health["deploymentIdentity"]
    )
    if not deployment_identity_agreement:
        infrastructure_blockers.append("AWS_V2_MODE_DEPLOYMENT_IDENTITY_MISMATCH")
    if v2_training.get("manifestPresent") is not True:
        infrastructure_blockers.append("AWS_V2_EXPERIMENT_MANIFEST_NOT_INITIALIZED")
    elif v2_training.get("manifestValid") is not True:
        infrastructure_blockers.append("AWS_V2_EXPERIMENT_MANIFEST_INVALID")
    elif v2_training.get("releaseActivationValid") is not True:
        infrastructure_blockers.append("AWS_V2_RELEASE_ACTIVATION_INVALID")
    if v2_training.get("automaticPromotionEnabled") is True:
        infrastructure_blockers.append("AWS_V2_AUTOMATIC_FIRST_PROMOTION_ENABLED")
    elif v2_training.get("automaticPromotionEnabled") is not False:
        infrastructure_blockers.append("AWS_V2_AUTOMATIC_PROMOTION_STATE_MISSING")
    v2_counts = v2_training.get("partitionCounts") or {}
    v2_train = _int(v2_counts.get("train"))
    v2_validation = _int(v2_counts.get("validation"))
    v2_prospective = _int(v2_counts.get("prospectiveTest"))
    v2_total = v2_train + v2_validation + v2_prospective
    reported_v2_milestones = v2_training.get("milestones") or {}
    training_latest = (
        (v2_training.get("trainingHealth") or {}).get("latestRun") or {}
    )
    status_v2_milestones = training_latest.get("milestones") or {}
    milestone_status_agreement = bool(
        isinstance(reported_v2_milestones, dict)
        and isinstance(status_v2_milestones, dict)
        and reported_v2_milestones == status_v2_milestones
    )
    if not milestone_status_agreement:
        infrastructure_blockers.append("AWS_V2_MILESTONE_STATUS_MISMATCH")
    v2_milestones = (
        status_v2_milestones if milestone_status_agreement else {}
    )
    first_full_clean_slate_proof = (
        v2_milestones.get("firstFullCleanSlateProof")
        if isinstance(v2_milestones.get("firstFullCleanSlateProof"), dict)
        else {}
    )
    first_full_clean_slate_achieved = bool(
        first_full_clean_slate_proof.get("achieved") is True
    )
    v2_milestone_counts = v2_milestones.get("counts") or {}
    v2_selected = _int(
        v2_milestone_counts.get("settledProspectiveSelectedRecommendations")
    )
    candidate_decision = v2_training.get("candidatePromotionDecision")
    champion_present = v2_training.get("championPresent") is True

    infrastructure_blockers = sorted(set(infrastructure_blockers))
    model_blockers = sorted(set(model_blockers))
    warnings = sorted(set(warnings))
    infrastructure_ok = not infrastructure_blockers
    accuracy_target_met = accuracy_status == "TARGET_MET"
    # A one-day or rolling 90% result is an aspirational dashboard metric, not
    # a production-health or ML-promotion gate. Integrity failures still stop
    # acceptance; ordinary predictive variance does not.
    overall_ok = infrastructure_ok

    return {
        "ok": overall_ok,
        "proofType": "INQSI_MLB_END_TO_END_PRODUCTION_ACCEPTANCE",
        "createdAtUtc": now_utc.isoformat(),
        "slateDateEt": pull_guard.get("slateDateEt") or verifier.get("slateDateEt"),
        "infrastructureOk": infrastructure_ok,
        "accuracyStatus": accuracy_status,
        "accuracyTargetPct": target,
        "accuracyTargetMet": accuracy_target_met if graded > 0 else None,
        "signalTuningFrozen": not infrastructure_ok,
        "pullCoverage": {
            "officialGameCount": pull_guard.get("officialGameCount"),
            "cleanExpectedPullCount": pull_guard.get("cleanExpectedPullCount"),
            "cleanActualScheduledSlotCount": pull_guard.get("cleanActualScheduledSlotCount"),
            "missingSlots": pull_guard.get("missingCleanScheduledSlots") or [],
            "duplicateOrExtraPulls": pull_guard.get("duplicateOrExtraPullsSinceStart"),
            "preStartPollutedPullCount": pull_guard.get("preStartPollutedPullCount"),
            "latestPullAgeMinutes": pull_guard.get("latestRawPullAgeMinutes"),
            "guardPassed": pull_guard.get("guardPassed"),
        },
        "predictionAndLock": {
            "gameCount": verifier.get("gameCount"),
            "predictionCount": verifier.get("predictionCount"),
            "allGamesPredicted": verifier.get("allGamesPredicted"),
            "lock": verifier.get("lock"),
            "lockedRowIntegrity": verifier.get("lockedRowIntegrity"),
            "verifierOk": verifier.get("ok"),
        },
        "settlementAndAccuracy": {
            "scopeValid": post_cutoff_scope_valid,
            "scopeVersion": post_cutoff_scope.get("version"),
            "scopeFingerprintVersion": post_cutoff_scope.get(
                "scopeFingerprintVersion"
            ),
            "scopeFingerprint": post_cutoff_scope.get("scopeFingerprint"),
            "experimentIdentity": post_cutoff_scope.get("experimentIdentity"),
            "releaseCutoffUtc": post_cutoff_scope.get("releaseCutoffUtc"),
            "preCutoffQuarantinedFinalGameCount": post_cutoff_scope.get(
                "preCutoffQuarantinedFinalGameCount"
            ),
            "completedFinalGames": completed,
            "gradedPredictionCount": graded,
            "missingPredictionCount": missing,
            "officialPredictionCount": official_count,
            "rolling24hOfficialAccuracyPct": official_accuracy,
            "rolling24hAllGamesAccuracyPct": all_games_accuracy,
            "auditAgeMinutes": audit_age_minutes,
            "postCutoffDefects": post_cutoff_scope.get("postCutoffDefects")
            or [],
            "postCutoffBlockerCodes": post_cutoff_scope.get(
                "postCutoffBlockerCodes"
            )
            or [],
            "postCutoffOfficialGamePks": post_cutoff_scope.get(
                "postCutoffOfficialGamePks"
            )
            or [],
            "postCutoffFinalizedSlateCount": post_cutoff_scope.get(
                "postCutoffFinalizedSlateCount"
            ),
            "postCutoffExactFullSlateCount": post_cutoff_scope.get(
                "postCutoffExactFullSlateCount"
            ),
            "postCutoffFullSlateCoverageDefectCount": (
                full_slate_coverage_defect_count
            ),
            "postCutoffFinalizedSlateProofs": post_cutoff_scope.get(
                "postCutoffFinalizedSlateProofs"
            )
            or [],
            "firstCompletePostCutoffSlateProof": post_cutoff_scope.get(
                "firstCompletePostCutoffSlateProof"
            )
            or {},
            "fullSlateSettlementCoverageOk": post_cutoff_scope.get(
                "fullSlateSettlementCoverageOk"
            ),
            "coverage": post_cutoff_scope.get("coverage") or {},
            "legacyRolling24hDiagnostic": {
                "completedFinalGames": legacy_completed,
                "gradedPredictionCount": legacy_graded,
                "missingPredictionCount": legacy_missing,
                "officialPredictionCount": legacy_official_count,
                "officialAccuracyPct": legacy_official_accuracy,
                "allGamesAccuracyPct": legacy_all_games_accuracy,
                "acceptanceAuthority": False,
            },
        },
        "mlDisposition": {
            "cleanRowCount": clean_rows,
            "quarantinedRowCount": quarantined_rows,
            "dispositionComplete": disposition_complete,
            "mode": optimization.get("mode"),
            "automaticPromotionEnabled": optimization.get("automaticPromotionEnabled"),
            "legacyV1AuthorityEnabled": False,
            "v2PromotionPolicy": "fixed_300_100_100_prospective_manual_first",
            "v2Training": {
                "manifestReadStable": manifest_read_stable,
                "manifestReadBefore": (
                    manifest_read_before
                    if isinstance(manifest_read_before, dict)
                    else {}
                ),
                "manifestReadAfter": (
                    manifest_read_after
                    if isinstance(manifest_read_after, dict)
                    else {}
                ),
                "statusPresent": training_health["statusPresent"],
                "latestRunStatus": training_health["latestRunStatus"],
                "latestRunCreatedAtUtc": training_health[
                    "latestRunCreatedAtUtc"
                ],
                "latestRunTimestampValid": training_health[
                    "latestRunTimestampValid"
                ],
                "latestRunAgeMinutes": training_health["latestRunAgeMinutes"],
                "latestRunFresh": training_health["latestRunFresh"],
                "latestRunMaxAgeMinutes": training_health[
                    "latestRunMaxAgeMinutes"
                ],
                "trainingHealth": training_health,
                "selectionCaptureHealth": capture_health,
                "deploymentIdentityAgreement": deployment_identity_agreement,
                "manifestPresent": v2_training.get("manifestPresent"),
                "manifestValid": v2_training.get("manifestValid"),
                "releaseActivationValid": v2_training.get(
                    "releaseActivationValid"
                ),
                "releaseActivationErrors": v2_training.get(
                    "releaseActivationErrors"
                ),
                "manifestPhase": v2_training.get("manifestPhase"),
                "partitionCounts": {
                    "train": v2_train,
                    "validation": v2_validation,
                    "prospectiveTest": v2_prospective,
                },
                "totalRows": v2_total,
                "settledProspectiveSelectedRecommendations": v2_selected,
                "milestoneStage": v2_milestones.get("stage"),
                "milestoneStatusAgreement": milestone_status_agreement,
                "firstFullCleanSlateProof": first_full_clean_slate_proof,
                "firstFullCleanSlateProofAchieved": (
                    first_full_clean_slate_achieved
                ),
                "projectedFullCleanSlatesRemaining": v2_milestones.get(
                    "projectedFullCleanSlatesRemaining"
                ),
                "candidatePromotionDecision": candidate_decision,
                "championPresent": champion_present,
                "firstPromotionRequiresManualReview": True,
                "automaticPromotionEnabled": v2_training.get(
                    "automaticPromotionEnabled"
                ),
            },
        },
        "infrastructureBlockers": infrastructure_blockers,
        "modelBlockers": model_blockers,
        "warnings": warnings,
        "unproven": [
            item
            for item, condition in [
                (
                    "one complete uncontaminated live slate",
                    not first_full_clean_slate_achieved,
                ),
                ("500 clean V2 rows across fixed 300/100/100 partitions", v2_total < 500),
                ("100 sealed prospective-test rows", v2_prospective < 100),
                (
                    "100 prospectively selected recommendations for playability",
                    v2_selected < 100,
                ),
                ("first V2 champion manual review", not champion_present),
            ]
            if condition
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull-guard", required=True, type=Path)
    parser.add_argument("--verifier", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    result = build_acceptance(
        pull_guard=_load(args.pull_guard),
        verifier=_load(args.verifier),
        audit=_load(args.audit),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    if result.get("ok") is not True:
        raise SystemExit(
            "MLB production acceptance failed: "
            + json.dumps({
                "infrastructureBlockers": result.get("infrastructureBlockers"),
                "modelBlockers": result.get("modelBlockers"),
                "unproven": result.get("unproven"),
            }, default=str)
        )


if __name__ == "__main__":
    main()
