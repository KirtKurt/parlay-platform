from __future__ import annotations

import copy
import functools
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "MLB-ML-AUTONOMY-CHAIN-v1-gap-tolerant-missingness-auto-runtime"
CONTINUITY_VERSION = "MLB-ML-CANONICAL-SLATE-CONTINUITY-v3-gap-tolerant-quarantine"
MISSINGNESS_VERSION = "MLB-ML-MISSINGNESS-TRAINING-v1-explicit-null-mask-only"
PROMOTION_VERSION = "MLB-ML-AUTONOMOUS-PROMOTION-v1-gated-runtime-consumer"
_INSTALL_FLAG = "_INQSI_MLB_ML_AUTONOMY_CHAIN_V1"
_CONTINUITY_FLAG = "_INQSI_MLB_GAP_TOLERANT_CONTINUITY_V1"
_TERMINAL_FLAG = "_INQSI_MLB_TERMINAL_ONLY_FINALIZATION_V1"
_AUTHORITY_FLAG = "_INQSI_MLB_MISSINGNESS_AUTHORITY_V1"
_VERDICT_FLAG = "_INQSI_MLB_MISSINGNESS_VERDICT_V1"
_JOIN_FLAG = "_INQSI_MLB_MISSINGNESS_JOIN_V1"
_PROMOTION_EVALUATE_FLAG = "_INQSI_MLB_AUTONOMOUS_PROMOTION_EVALUATE_V1"
_PROMOTE_FLAG = "_INQSI_MLB_AUTONOMOUS_PROMOTE_V1"
_COMMIT_FLAG = "_INQSI_MLB_AUTONOMOUS_CANDIDATE_V1"
_STATUS_FLAG = "_INQSI_MLB_AUTONOMOUS_STATUS_V1"

_ALLOWED_MISSINGNESS_REASONS = frozenset(
    {
        "fundamentals_v2_pregame_sources_incomplete",
        "fundamentals_v2_not_training_eligible",
    }
)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _reason_is_explicit_missingness(reason: Any) -> bool:
    value = str(reason or "")
    return value in _ALLOWED_MISSINGNESS_REASONS or value.startswith(
        "fundamentals_v2_incomplete:"
    )


def _reasons(values: Any) -> set[str]:
    return {str(value) for value in (values or []) if str(value)}


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _exact_official_schedule(
    official: Mapping[str, Any],
    slate_date: str,
    *,
    expected_schedule_source: Optional[str],
) -> Tuple[int, int, List[str]]:
    if official.get("ok") is not True:
        raise RuntimeError("official schedule response is not healthy")
    if str(official.get("slateDateEt") or "") != slate_date:
        raise RuntimeError("official schedule date is not exact")
    count = official.get("officialGameCount")
    final_count = official.get("officialFinalCount")
    games = official.get("games")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise RuntimeError("official game count is invalid")
    if (
        isinstance(final_count, bool)
        or not isinstance(final_count, int)
        or not 0 <= final_count <= count
    ):
        raise RuntimeError("official final count is invalid")
    if not isinstance(games, list) or len(games) != count:
        raise RuntimeError("official schedule game set is incomplete")
    official_pks = [
        str(game.get("officialGamePk") or "")
        for game in games
        if isinstance(game, Mapping)
    ]
    if (
        len(official_pks) != count
        or any(not value for value in official_pks)
        or len(set(official_pks)) != count
    ):
        raise RuntimeError("official gamePk set is invalid")
    if any(
        str(game.get("officialDate") or "") != slate_date
        for game in games
        if isinstance(game, Mapping)
    ):
        raise RuntimeError("official game set crosses slate dates")
    if expected_schedule_source is not None and official.get("source") != (
        expected_schedule_source
    ):
        raise RuntimeError("official schedule source is not authoritative")
    if not str(official.get("sourceUrl") or ""):
        raise RuntimeError("official schedule source URL is missing")
    return count, final_count, official_pks


def gap_tolerant_finalized_slate_scan(
    slate_dates: Iterable[str],
    *,
    official_schedule_loader: Callable[[str], Dict[str, Any]],
    slate_finalization_loader: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    expected_schedule_source: Optional[str] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Evaluate every date and quarantine gaps without authorizing partial rows.

    A bad date no longer prevents later exact, fully-finalized slates from being
    evaluated.  Only dates whose official FINAL gamePk set and canonical
    lock/label authority both pass are returned for training.  Deferred,
    unproven, or evidence-incomplete dates emit no rows and cannot influence a
    model or a promotion decision.
    """

    training_dates: List[str] = []
    official_final_dates: List[str] = []
    zero_game_dates: List[str] = []
    processed: List[str] = []
    authorities: Dict[str, Dict[str, Any]] = {}
    quarantined: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    unproven: List[Dict[str, Any]] = []

    import mlb_ml_experiment_v2 as experiment

    for slate_date in slate_dates:
        slate = str(slate_date)
        processed.append(slate)
        try:
            official = official_schedule_loader(slate)
            count, final_count, official_pks = _exact_official_schedule(
                official,
                slate,
                expected_schedule_source=expected_schedule_source,
            )
        except Exception as exc:
            unproven.append(
                {
                    "slateDateEt": slate,
                    "reason": f"OFFICIAL_SCHEDULE_UNPROVEN:{type(exc).__name__}:{exc}",
                }
            )
            continue

        if count == 0:
            zero_game_dates.append(slate)
            continue
        if final_count != count:
            deferred.append(
                {
                    "slateDateEt": slate,
                    "reason": "OFFICIAL_SLATE_NOT_YET_FULLY_FINAL",
                    "officialGameCount": count,
                    "officialFinalCount": final_count,
                }
            )
            continue

        authority = experiment.build_official_finalized_slate_authority(
            slate_date_et=slate,
            official_game_pks=official_pks,
            schedule_source=str(official.get("source") or ""),
            schedule_source_url=str(official.get("sourceUrl") or ""),
        )
        authorities[slate] = authority
        official_final_dates.append(slate)

        try:
            finalized = slate_finalization_loader(slate, official)
            diagnostics = finalized.get("slates") or []
            date_diagnostic = next(
                (
                    item
                    for item in diagnostics
                    if isinstance(item, Mapping)
                    and str(item.get("slateDateEt") or "") == slate
                ),
                {},
            )
            valid = bool(
                finalized.get("ok") is True
                and finalized.get("requestedSlateDates") == [slate]
                and finalized.get("finalizedSlateDates") == [slate]
                and date_diagnostic.get("slateFinalized") is True
                and not isinstance(date_diagnostic.get("officialGameCount"), bool)
                and int(date_diagnostic.get("officialGameCount") or -1) == count
            )
            if not valid:
                raise RuntimeError("canonical lock/label evidence is incomplete")
        except Exception as exc:
            quarantined.append(
                {
                    "slateDateEt": slate,
                    "reason": f"CANONICAL_T45_EVIDENCE_QUARANTINED:{type(exc).__name__}:{exc}",
                    "officialGameCount": count,
                    "officialGameSetFingerprint": authority.get(
                        "officialGameSetFingerprint"
                    ),
                }
            )
            continue
        training_dates.append(slate)

    gaps = [*unproven, *deferred, *quarantined]
    return training_dates, {
        "ok": True,
        "version": CONTINUITY_VERSION,
        "scanCompleted": True,
        "trainingMayContinuePastQuarantinedDates": True,
        "strictPerRowFailClosed": True,
        "processedSlateDates": processed,
        "processedThroughSlateDate": processed[-1] if processed else None,
        "provenZeroGameSlateDates": zero_game_dates,
        "officialFinalizedGameSlateDates": official_final_dates,
        "finalizedGameSlateDates": training_dates,
        "trainingEligibleSlateDates": training_dates,
        "finalizedSlateAuthorities": authorities,
        "quarantinedSlateDates": [row["slateDateEt"] for row in quarantined],
        "quarantinedSlates": quarantined,
        "deferredSlateDates": [row["slateDateEt"] for row in deferred],
        "deferredSlates": deferred,
        "unprovenScheduleDates": [row["slateDateEt"] for row in unproven],
        "unprovenScheduleSlates": unproven,
        "hasGaps": bool(gaps),
        "gapCount": len(gaps),
        "blockedSlateDate": gaps[0]["slateDateEt"] if gaps else None,
        "blocker": gaps[0]["reason"] if gaps else None,
        "policy": (
            "Every date is evaluated independently. Only exact official FINAL "
            "gamePk sets with complete immutable T-45 locks and write-once labels "
            "emit training rows; unresolved dates are quarantined and cannot "
            "indefinitely block later finalized slates."
        ),
    }


def _missingness_snapshot_eligible(row: Mapping[str, Any]) -> bool:
    snapshot = row.get("fundamentalsSnapshotV2")
    if not isinstance(snapshot, Mapping) or not snapshot:
        return False
    try:
        import mlb_fundamentals_snapshot_v2 as fundamentals

        if fundamentals.validate(snapshot):
            return False
        if snapshot.get("missingValuesAreNull") is not True:
            return False
        if snapshot.get("immutableAtTMinus45") is not True:
            return False
        if not snapshot.get("missingGroups"):
            return False
        snapshot_reasons = _reasons(snapshot.get("trainingExclusionReasons"))
        if not snapshot_reasons or any(
            not _reason_is_explicit_missingness(reason)
            for reason in snapshot_reasons
        ):
            return False
        lock_at = (
            row.get("lockedAtUtc")
            or (row.get("slatePredictionLock") or {}).get("lockAtUtc")
            or (row.get("frozenFeatureVector") or {}).get("lockAtUtc")
        )
        persisted_at = row.get("predictionPersistedAtUtc")
        if not fundamentals.provenance_is_lock_safe(
            snapshot,
            prediction_persisted_at=persisted_at,
            lock_at=lock_at,
        ):
            return False
    except Exception:
        return False
    vector = row.get("frozenFeatureVector") or {}
    freeze = row.get("mlFeatureFreeze") or {}
    authority = row.get("canonicalLockAuthority") or {}
    exact_errors = _reasons(
        row.get("exactVectorValidationErrors")
        or freeze.get("exactVectorValidationErrors")
        or authority.get("exactVectorValidationErrors")
    )
    exact_verified = bool(
        row.get("exactVectorVerified") is True
        or freeze.get("exactVectorVerified") is True
        or authority.get("exactLockVectorValidated") is True
    )
    fingerprint = (
        vector.get("fingerprint")
        or freeze.get("frozenFeatureVectorFingerprint")
        or authority.get("frozenFeatureVectorFingerprint")
    )
    return bool(
        row.get("lockedPrediction") is True
        and exact_verified
        and not exact_errors
        and isinstance(vector, Mapping)
        and fingerprint
    )


def _clear_missingness_only_training_blocks(row: Mapping[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(dict(row or {}))
    if not _missingness_snapshot_eligible(out):
        return out
    all_reasons = _reasons(out.get("trainingExclusionReasons"))
    freeze = copy.deepcopy(out.get("mlFeatureFreeze") or {})
    all_reasons.update(_reasons(freeze.get("trainingExclusionReasons")))
    authority = copy.deepcopy(out.get("canonicalLockAuthority") or {})
    all_reasons.update(_reasons(authority.get("trainingExclusionReasons")))
    remaining = sorted(
        reason for reason in all_reasons if not _reason_is_explicit_missingness(reason)
    )
    if remaining:
        return out
    metadata = {
        "trainingEligible": True,
        "trainingExclusionReasons": [],
        "marketOnlyMissingnessTrainingEligible": True,
        "missingnessTrainingVersion": MISSINGNESS_VERSION,
        "missingValuesImputed": False,
        "missingValuesRemainNull": True,
        "playabilityAuthorityGranted": False,
    }
    freeze.update(metadata)
    out.update(metadata)
    out["mlFeatureFreeze"] = freeze
    if authority:
        integrity_flags = (
            "verified",
            "consistentRead",
            "immutableLocked",
            "stageAuthorityVerified",
            "persistedStageAuthorityValidated",
            "officialAuditEligible",
            "exactLockVectorValidated",
            "selectionLockVectorStatusValidated",
        )
        authority.update(
            {
                "trainingExclusionReasons": [],
                "learningEligible": all(
                    authority.get(flag) is True for flag in integrity_flags
                ),
                "marketOnlyMissingnessTrainingEligible": True,
                "missingnessTrainingVersion": MISSINGNESS_VERSION,
            }
        )
        out["canonicalLockAuthority"] = authority
    return out


def _runtime_consumer_enabled() -> bool:
    return _truthy(os.environ.get("INQSI_MLB_V2_INFERENCE_ENABLED", "false"))


def install(canonical: Any) -> Any:
    """Install the autonomy chain into the canonical trainer module.

    All repairs are read-time or future-write only. Existing immutable locks,
    labels, selections, candidates, and historical outcomes are never rewritten.
    """

    if getattr(canonical, _INSTALL_FLAG, False):
        return canonical

    import mlb_canonical_final_labels_v1 as labels

    if not getattr(canonical, _CONTINUITY_FLAG, False):
        canonical._contiguous_finalized_slate_prefix = (
            gap_tolerant_finalized_slate_scan
        )
        setattr(canonical, _CONTINUITY_FLAG, True)

    original_proof = labels._proof_from_stored
    if not getattr(original_proof, _TERMINAL_FLAG, False):

        @functools.wraps(original_proof)
        def proof_from_stored(
            slate_date: str,
            current_locks: Dict[str, Dict[str, Any]],
            terminal_outcomes: Dict[str, Dict[str, Any]],
        ) -> Dict[str, Any]:
            result = original_proof(
                slate_date,
                current_locks,
                terminal_outcomes,
            )
            if (
                not current_locks
                and terminal_outcomes
                and int(result.get("invalidStoredCanonicalLabelCount") or 0) == 0
                and not result.get("labels")
            ):
                result = {
                    **result,
                    "ok": True,
                    "status": "VERIFIED_TERMINAL_NO_PREDICTION_SLATE",
                    "verificationComplete": True,
                    "terminalOnlySlate": True,
                    "terminalNoPredictionCount": len(terminal_outcomes),
                }
            return result

        setattr(proof_from_stored, _TERMINAL_FLAG, True)
        labels._proof_from_stored = proof_from_stored

    rolling = getattr(labels, "rolling_audit", None)
    original_authority = getattr(rolling, "_canonical_lock_authority", None)
    if callable(original_authority) and not getattr(
        original_authority, _AUTHORITY_FLAG, False
    ):

        @functools.wraps(original_authority)
        def canonical_lock_authority(
            item: Dict[str, Any], slate_date: str
        ) -> Dict[str, Any]:
            copied = copy.deepcopy(item or {})
            data = copied.get("data")
            if isinstance(data, Mapping):
                copied["data"] = _clear_missingness_only_training_blocks(data)
            authority = original_authority(copied, slate_date)
            if isinstance(authority, dict):
                authority = copy.deepcopy(authority)
                cleaned = _clear_missingness_only_training_blocks(
                    {
                        **dict(copied.get("data") or {}),
                        "canonicalLockAuthority": authority,
                    }
                )
                cleaned_authority = cleaned.get("canonicalLockAuthority") or {}
                if cleaned.get("marketOnlyMissingnessTrainingEligible") is True:
                    authority.update(cleaned_authority)
                    authority["immutableLockPayloadMutated"] = False
            return authority

        setattr(canonical_lock_authority, _AUTHORITY_FLAG, True)
        rolling._canonical_lock_authority = canonical_lock_authority

    original_verdict = labels._training_verdict
    if not getattr(original_verdict, _VERDICT_FLAG, False):

        @functools.wraps(original_verdict)
        def training_verdict(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
            cleaned = _clear_missingness_only_training_blocks(row)
            eligible, reasons = original_verdict(cleaned)
            if eligible:
                return eligible, reasons
            if cleaned.get("marketOnlyMissingnessTrainingEligible") is True and all(
                _reason_is_explicit_missingness(reason) for reason in reasons
            ):
                return True, []
            return eligible, reasons

        setattr(training_verdict, _VERDICT_FLAG, True)
        labels._training_verdict = training_verdict

    original_join = labels._joined_training_row
    if not getattr(original_join, _JOIN_FLAG, False):

        @functools.wraps(original_join)
        def joined_training_row(
            slate_date: str,
            label: Dict[str, Any],
            locked: Dict[str, Any],
            *,
            slate_finalized: bool,
        ) -> Dict[str, Any]:
            cleaned_lock = _clear_missingness_only_training_blocks(locked)
            cleaned_label = copy.deepcopy(label or {})
            if cleaned_lock.get("marketOnlyMissingnessTrainingEligible") is True:
                label_reasons = _reasons(
                    cleaned_label.get("training_exclusion_reasons")
                )
                remaining = sorted(
                    reason
                    for reason in label_reasons
                    if not _reason_is_explicit_missingness(reason)
                )
                if not remaining:
                    cleaned_label["training_eligible"] = True
                    cleaned_label["training_exclusion_reasons"] = []
            joined = original_join(
                slate_date,
                cleaned_label,
                cleaned_lock,
                slate_finalized=slate_finalized,
            )
            if isinstance(joined, dict) and cleaned_lock.get(
                "marketOnlyMissingnessTrainingEligible"
            ) is True:
                joined = copy.deepcopy(joined)
                joined.update(
                    {
                        "trainingEligible": bool(slate_finalized),
                        "trainingExclusionReasons": [],
                        "marketOnlyMissingnessTrainingEligible": True,
                        "missingnessTrainingVersion": MISSINGNESS_VERSION,
                        "playabilityAuthorityGranted": False,
                        "immutablePregameVectorMutated": False,
                        "immutableLockPayloadMutated": False,
                        "immutableLabelPayloadMutated": False,
                    }
                )
            return joined

        setattr(joined_training_row, _JOIN_FLAG, True)
        labels._joined_training_row = joined_training_row

    original_evaluate = canonical.promotion_policy.evaluate
    if not getattr(original_evaluate, _PROMOTION_EVALUATE_FLAG, False):

        @functools.wraps(original_evaluate)
        def evaluate(
            trained: Dict[str, Any],
            manifest: Dict[str, Any],
            *,
            current_champion: Optional[Dict[str, Any]] = None,
            automatic_promotion_enabled: bool = False,
        ) -> Dict[str, Any]:
            result = original_evaluate(
                trained,
                manifest,
                current_champion=current_champion,
                automatic_promotion_enabled=automatic_promotion_enabled,
            )
            result = copy.deepcopy(result)
            result["version"] = PROMOTION_VERSION
            result["firstPromotionRequiresManualReview"] = False
            result["manualReviewRequired"] = False
            result["learningContinuesBelowAspirationalAccuracy"] = True
            result["aspirationalAccuracyBlocksTraining"] = False
            result["aspirationalAccuracyBlocksCandidateEvaluation"] = False
            result["aspirationalAccuracyBlocksPlayabilityAuthority"] = True
            result["runtimeConsumerRequired"] = True
            runtime_ready = _runtime_consumer_enabled()
            result["runtimeAuthorityActivationEligible"] = bool(
                runtime_ready and result.get("promotionEligible") is True
            )
            if (
                automatic_promotion_enabled
                and result.get("promotionEligible") is True
                and runtime_ready
            ):
                result["promotionDecision"] = "AUTO_SHADOW_APPROVAL_ELIGIBLE"
                result["automaticPromotionReason"] = (
                    "all immutable chronological prospective and calibration "
                    "gates passed with the V2 runtime consumer installed"
                )
            return result

        setattr(evaluate, _PROMOTION_EVALUATE_FLAG, True)
        canonical.promotion_policy.evaluate = evaluate

    original_commit = canonical.AwsTrainingStore.commit_candidate
    if not getattr(original_commit, _COMMIT_FLAG, False):

        @functools.wraps(original_commit)
        def commit_candidate(
            self: Any,
            manifest: Dict[str, Any],
            candidate: Dict[str, Any],
            *,
            expected_revision: int,
            expected_digest: str,
        ) -> None:
            value = copy.deepcopy(candidate)
            value["firstActivationRequiresManualReview"] = False
            value["automaticPromotionGatePreserved"] = True
            value["v2InferenceConsumerRequired"] = True
            value["aspirationalAccuracyBlocksCandidateCreation"] = False
            return original_commit(
                self,
                manifest,
                value,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )

        setattr(commit_candidate, _COMMIT_FLAG, True)
        canonical.AwsTrainingStore.commit_candidate = commit_candidate

    original_promote = canonical.AwsTrainingStore.promote_candidate
    if not getattr(original_promote, _PROMOTE_FLAG, False):

        def promote_candidate(
            self: Any,
            candidate: Dict[str, Any],
            *,
            authorities: Sequence[str],
            approval_mode: str,
            reviewer: Optional[str],
            stable_champion: bool,
            expected_champion_digest: Optional[str],
        ) -> Dict[str, Any]:
            allowed = sorted({str(value) for value in authorities})
            runtime_enabled = _runtime_consumer_enabled()
            if not runtime_enabled:
                return original_promote(
                    self,
                    candidate,
                    authorities=authorities,
                    approval_mode=approval_mode,
                    reviewer=reviewer,
                    stable_champion=stable_champion,
                    expected_champion_digest=expected_champion_digest,
                )
            pointer = copy.deepcopy(
                (candidate.get("artifacts") or {}).get("frozenChallenger") or {}
            )
            challenger = self.read_versioned_json(pointer)
            if challenger.get("ok") is not True:
                raise canonical.TrainingContractError(
                    "promoted V2 challenger artifact is not verified"
                )
            pointer_sha = str(pointer.get("sha256") or "")
            if not pointer_sha or canonical._sha256(challenger) != pointer_sha:
                raise canonical.TrainingContractError(
                    "promoted V2 challenger artifact checksum mismatch"
                )
            active = bool(stable_champion and allowed)
            champion = {
                "version": canonical.VERSION,
                "recordType": "mlb_ml_active_champion_v2",
                "artifactDigest": candidate["artifactDigest"],
                "experimentId": candidate["experimentId"],
                "experimentManifestDigest": candidate[
                    "experimentManifestDigest"
                ],
                "artifactBundle": copy.deepcopy(candidate["artifacts"]),
                "frozenChallenger": copy.deepcopy(challenger),
                "frozenChallengerSha256": pointer_sha,
                "deploymentIdentity": copy.deepcopy(
                    candidate.get("deploymentIdentity") or {}
                ),
                "directionApproved": "direction" in allowed,
                "playabilityApproved": "playability" in allowed,
                "stableChampionApproved": bool(stable_champion),
                "directionAuthorityEnabled": bool(
                    active and "direction" in allowed
                ),
                "playabilityAuthorityEnabled": bool(
                    active and "playability" in allowed
                ),
                "stableChampion": active,
                "shadowOnly": not active,
                "runtimeIntegrationRequired": False,
                "runtimeAuthorityActivated": active,
                "approvalStatus": (
                    "AUTO_PROMOTED_V2_RUNTIME_ACTIVE"
                    if active
                    else "AUTO_PROMOTION_INCOMPLETE"
                ),
                "approvalMode": approval_mode,
                "reviewer": reviewer,
                "approvedAtUtc": datetime.now(timezone.utc).isoformat(),
                "promotionGate": copy.deepcopy(candidate["promotionGate"]),
                "firstPromotionRequiresManualReview": False,
                "automaticPromotionGatePreserved": True,
                "automaticWagerAllowed": False,
            }
            item = canonical._ddb_safe(
                {
                    "PK": canonical.CHAMPION_PK,
                    "SK": canonical.CHAMPION_SK,
                    "record_type": "mlb_ml_champion_v2",
                    "artifactDigest": champion["artifactDigest"],
                    "data": champion,
                }
            )
            kwargs: Dict[str, Any] = {"Item": item}
            if expected_champion_digest:
                kwargs.update(
                    {
                        "ConditionExpression": "artifactDigest = :expected",
                        "ExpressionAttributeValues": {
                            ":expected": expected_champion_digest
                        },
                    }
                )
            else:
                kwargs["ConditionExpression"] = (
                    "attribute_not_exists(PK) AND attribute_not_exists(SK)"
                )
            try:
                self.table.put_item(**kwargs)
            except Exception as exc:
                code = str(
                    ((getattr(exc, "response", {}) or {}).get("Error") or {}).get(
                        "Code"
                    )
                    or ""
                )
                if code == "ConditionalCheckFailedException":
                    raise canonical.ConditionalStateConflict(
                        "champion compare-and-swap failed"
                    ) from exc
                raise
            return canonical._plain(champion)

        setattr(promote_candidate, _PROMOTE_FLAG, True)
        canonical.AwsTrainingStore.promote_candidate = promote_candidate

    original_save_status = canonical.TrainingService._save_run_status
    if not getattr(original_save_status, "_mlb_autonomy_runtime_authority_v1", False):

        @functools.wraps(original_save_status)
        def save_run_status(self: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
            value = copy.deepcopy(payload or {})
            try:
                champion = self.store.load_champion() or {}
            except Exception:
                champion = {}
            runtime_active = bool(
                champion.get("runtimeAuthorityActivated") is True
                and champion.get("stableChampion") is True
                and champion.get("shadowOnly") is False
                and (
                    champion.get("directionAuthorityEnabled") is True
                    or champion.get("playabilityAuthorityEnabled") is True
                )
            )
            value["liveInferenceAuthority"] = runtime_active
            value["runtimeAuthorityChanged"] = bool(
                runtime_active
                and (
                    (value.get("promotion") or {}).get("champion")
                    or value.get("championChanged") is True
                )
            )
            promotion = value.get("promotion")
            if isinstance(promotion, dict) and runtime_active:
                promotion = copy.deepcopy(promotion)
                promotion["runtimeAuthorityActivated"] = True
                promotion["shadowChampionApproved"] = True
                value["promotion"] = promotion
            return original_save_status(self, value)

        setattr(save_run_status, "_mlb_autonomy_runtime_authority_v1", True)
        canonical.TrainingService._save_run_status = save_run_status

    original_status = canonical.TrainingService.status
    if not getattr(original_status, _STATUS_FLAG, False):

        @functools.wraps(original_status)
        def status(self: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
            result = copy.deepcopy(original_status(self, *args, **kwargs))
            try:
                import mlb_ml_v2_inference_consumer_v1 as consumer

                consumer_status = consumer.contract_status()
            except Exception as exc:
                consumer_status = {
                    "ok": False,
                    "installed": False,
                    "error": f"{type(exc).__name__}:{exc}",
                }
            enabled = bool(
                self.config.automatic_promotion_enabled
                and _runtime_consumer_enabled()
                and consumer_status.get("installed") is True
            )
            result.update(
                {
                    "automaticPromotionEnabled": self.config.automatic_promotion_enabled,
                    "firstPromotionRequiresManualReview": False,
                    "manualReviewCreatesShadowApprovalOnly": False,
                    "v2InferenceConsumerInstalled": consumer_status.get(
                        "installed"
                    )
                    is True,
                    "runtimeAuthorityActivationAvailable": enabled,
                    "autonomyChainVersion": VERSION,
                    "learningContinuesBelowAspirationalAccuracy": True,
                    "aspirationalAccuracyBlocksTraining": False,
                    "aspirationalAccuracyBlocksCandidateEvaluation": False,
                    "aspirationalAccuracyBlocksPlayableAuthority": True,
                    "v2InferenceConsumer": consumer_status,
                }
            )
            return result

        setattr(status, _STATUS_FLAG, True)
        canonical.TrainingService.status = status

    canonical.MLB_ML_AUTONOMY_CHAIN_VERSION = VERSION
    canonical.MLB_ML_CONTINUITY_VERSION = CONTINUITY_VERSION
    canonical.MLB_ML_MISSINGNESS_TRAINING_VERSION = MISSINGNESS_VERSION
    canonical.MLB_ML_AUTONOMOUS_PROMOTION_VERSION = PROMOTION_VERSION
    setattr(canonical, _INSTALL_FLAG, True)
    return canonical
