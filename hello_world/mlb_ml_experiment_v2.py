from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "MLB-ML-EXPERIMENT-v2-fixed-slate-future-prospective-cutover"
PRODUCTION_EXPERIMENT_ID = "mlb-v2-2026-07-24-future-prospective-r4"
PRODUCTION_RELEASE_CONTRACT_ID = PRODUCTION_EXPERIMENT_ID
PRODUCTION_RELEASE_CUTOFF_UTC = "2026-07-24T04:00:00+00:00"
RELEASE_ACTIVATION_VERSION = "MLB-ML-RELEASE-ACTIVATION-v1"
PARTITION_ORDER = ("train", "validation", "prospectiveTest")
PARTITION_MINIMUMS = {"train": 300, "validation": 100, "prospectiveTest": 100}
FIRST_FULL_CLEAN_SLATE_PROOF_ROWS = 15
MECHANICAL_SHADOW_PROOF_ROWS = 140
OFFICIAL_FINALIZED_SLATE_AUTHORITY_VERSION = (
    "MLB-ML-OFFICIAL-FINALIZED-SLATE-AUTHORITY-v1-exact-game-pk-set"
)
OFFICIAL_GAME_SET_FINGERPRINT_VERSION = (
    "MLB-ML-OFFICIAL-GAME-PK-SET-SHA256-v1"
)
MIN_FEATURES = 8
MAX_FEATURES = 10
REQUIRED_FUNDAMENTALS_VERSION = "MLB-FUNDAMENTALS-SNAPSHOT-v2-immutable-source-provenance"
REQUIRED_FUNDAMENTALS_FINGERPRINT_VERSION = "INQSI-EXACT-TYPED-JSON-SHA256-v1"
SELECTION_LEDGER_VERSION = "MLB-ML-PROSPECTIVE-SELECTION-LEDGER-v2-fingerprinted-decision"
SELECTION_DECISION_FINGERPRINT_VERSION = "MLB-ML-SELECTION-DECISION-SHA256-v1"
SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V1 = (
    "MLB-ML-SELECTION-IDEMPOTENCY-SHA256-v1"
)
SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V2 = (
    "MLB-ML-SELECTION-IDEMPOTENCY-SHA256-v2-semantic"
)
SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION = (
    SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V2
)
SUPPORTED_SELECTION_IDEMPOTENCY_FINGERPRINT_VERSIONS = frozenset(
    {
        SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V1,
        SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V2,
    }
)
SELECTION_RECORD_FINGERPRINT_VERSION = "MLB-ML-SELECTION-RECORD-SHA256-v1"
SELECTION_LEDGER_FIELDS = frozenset(
    {
        "version",
        "experimentId",
        "releaseContractId",
        "experimentManifestDigest",
        "challengerArtifactDigest",
        "challengerArtifact",
        "recordIdentity",
        "gameId",
        "officialGamePk",
        "slateDateEt",
        "commenceTime",
        "capturedAtUtc",
        "prospectiveCutoverAtUtc",
        "reliabilityProbability",
        "selectedThreshold",
        "selected",
        "outcomeKnownAtCapture",
        "writeOnce",
        "liveInferenceAuthority",
        "deploymentIdentity",
        "decisionFingerprintVersion",
        "decisionFingerprint",
        "idempotencyFingerprintVersion",
        "idempotencyFingerprint",
        "recordFingerprintVersion",
        "recordFingerprint",
    }
)


class ExperimentContractError(ValueError):
    pass


class FrozenPartitionConflict(ExperimentContractError):
    pass


SnapshotValidator = Callable[[Dict[str, Any], Optional[str], Optional[str]], Tuple[bool, Sequence[str]]]


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ExperimentContractError("non-finite value in experiment manifest")
        return format(value, ".17g")
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def digest(value: Any) -> str:
    encoded = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _official_game_set_material(
    slate_date_et: str,
    official_game_pks: Sequence[str],
) -> Dict[str, Any]:
    return {
        "version": OFFICIAL_GAME_SET_FINGERPRINT_VERSION,
        "slateDateEt": str(slate_date_et or ""),
        "officialGamePks": sorted(str(value or "").strip() for value in official_game_pks),
    }


def official_game_set_fingerprint(
    slate_date_et: str,
    official_game_pks: Sequence[str],
) -> str:
    return digest(_official_game_set_material(slate_date_et, official_game_pks))


def build_official_finalized_slate_authority(
    *,
    slate_date_et: str,
    official_game_pks: Sequence[str],
    schedule_source: str,
    schedule_source_url: str,
) -> Dict[str, Any]:
    """Bind one verified official FINAL slate to its exact gamePk set."""
    slate_date = str(slate_date_et or "").strip()
    game_pks = sorted(str(value or "").strip() for value in official_game_pks)
    source = str(schedule_source or "").strip()
    source_url = str(schedule_source_url or "").strip()
    if not slate_date:
        raise ExperimentContractError("official finalized slate date is required")
    if not game_pks or any(not value for value in game_pks):
        raise ExperimentContractError("official finalized slate gamePk set must be nonempty")
    if len(set(game_pks)) != len(game_pks):
        raise ExperimentContractError("official finalized slate gamePk set must be unique")
    if not source or not source_url:
        raise ExperimentContractError("official finalized slate source proof is required")
    authority = {
        "version": OFFICIAL_FINALIZED_SLATE_AUTHORITY_VERSION,
        "slateDateEt": slate_date,
        "slateFinalized": True,
        "officialGameCount": len(game_pks),
        "officialGamePks": game_pks,
        "scheduleSource": source,
        "scheduleSourceUrl": source_url,
        "officialGameSetFingerprintVersion": OFFICIAL_GAME_SET_FINGERPRINT_VERSION,
        "officialGameSetFingerprint": official_game_set_fingerprint(
            slate_date, game_pks
        ),
    }
    authority["authorityFingerprint"] = digest(authority)
    return authority


def official_finalized_slate_authority_errors(
    authority: Any,
    *,
    expected_slate_date_et: Optional[str] = None,
) -> List[str]:
    if not isinstance(authority, Mapping):
        return ["official_finalized_slate_authority_missing"]
    errors: List[str] = []
    slate_date = str(authority.get("slateDateEt") or "").strip()
    game_pks_raw = authority.get("officialGamePks")
    game_pks = (
        [str(value or "").strip() for value in game_pks_raw]
        if isinstance(game_pks_raw, list)
        else []
    )
    if authority.get("version") != OFFICIAL_FINALIZED_SLATE_AUTHORITY_VERSION:
        errors.append("official_finalized_slate_authority_version_mismatch")
    if not slate_date or (
        expected_slate_date_et is not None
        and slate_date != str(expected_slate_date_et)
    ):
        errors.append("official_finalized_slate_date_mismatch")
    if authority.get("slateFinalized") is not True:
        errors.append("official_slate_not_finalized")
    if not game_pks or any(not value for value in game_pks):
        errors.append("official_game_pk_set_empty_or_invalid")
    if len(set(game_pks)) != len(game_pks):
        errors.append("official_game_pk_set_not_unique")
    count = authority.get("officialGameCount")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(game_pks):
        errors.append("official_game_count_set_mismatch")
    if not str(authority.get("scheduleSource") or "").strip():
        errors.append("official_schedule_source_missing")
    if not str(authority.get("scheduleSourceUrl") or "").strip():
        errors.append("official_schedule_source_url_missing")
    if (
        authority.get("officialGameSetFingerprintVersion")
        != OFFICIAL_GAME_SET_FINGERPRINT_VERSION
    ):
        errors.append("official_game_set_fingerprint_version_mismatch")
    expected_game_set_fingerprint = official_game_set_fingerprint(
        slate_date, game_pks
    )
    if authority.get("officialGameSetFingerprint") != expected_game_set_fingerprint:
        errors.append("official_game_set_fingerprint_mismatch")
    material = {
        key: copy.deepcopy(value)
        for key, value in authority.items()
        if key != "authorityFingerprint"
    }
    if authority.get("authorityFingerprint") != digest(material):
        errors.append("official_finalized_slate_authority_fingerprint_mismatch")
    return sorted(set(errors))


def _is_hex_digest(value: Any, length: int = 64) -> bool:
    text = str(value or "")
    if len(text) != length:
        return False
    try:
        int(text, 16)
    except Exception:
        return False
    return True


def _manifest_payload(manifest: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "manifestDigest"}


def manifest_digest(manifest: Dict[str, Any]) -> str:
    return digest(_manifest_payload(manifest))


def release_activation(
    *,
    experiment_id: str,
    release_contract_id: str,
    release_cutoff_utc: str,
    activated_at_utc: str,
    deployment_git_sha: str,
    deployment_template_sha256: str,
) -> Dict[str, Any]:
    """Create the immutable, digest-bound proof that an experiment began in time."""
    cutoff = _parse_dt(release_cutoff_utc)
    activated = _parse_dt(activated_at_utc)
    if not str(experiment_id or "").strip():
        raise ExperimentContractError("release activation experiment ID is required")
    if not str(release_contract_id or "").strip():
        raise ExperimentContractError("release activation contract ID is required")
    if cutoff is None or activated is None:
        raise ExperimentContractError(
            "release activation timestamps must be ISO-8601 timestamps"
        )
    if activated >= cutoff:
        raise ExperimentContractError(
            "release activation must occur strictly before the release cutoff"
        )
    for value, length, name in (
        (deployment_git_sha, 40, "git SHA"),
        (deployment_template_sha256, 64, "template SHA-256"),
    ):
        text = str(value or "")
        if len(text) != length or not _is_hex_digest(text, length):
            raise ExperimentContractError(
                f"release activation deployment {name} is invalid"
            )
    return {
        "version": RELEASE_ACTIVATION_VERSION,
        "experimentId": str(experiment_id),
        "releaseContractId": str(release_contract_id),
        "releaseCutoffUtc": cutoff.isoformat(),
        "activatedAtUtc": activated.isoformat(),
        "deploymentIdentity": {
            "gitSha": str(deployment_git_sha),
            "templateSha256": str(deployment_template_sha256),
        },
        "immutable": True,
    }


def release_activation_errors(
    value: Any,
    *,
    expected_experiment_id: str,
    expected_release_contract_id: str,
    expected_release_cutoff_utc: str,
    expected_created_at_utc: str,
) -> List[str]:
    """Validate persisted activation without tying it to a later deployment."""
    if not isinstance(value, Mapping):
        return ["release_activation_missing"]
    errors: List[str] = []
    allowed_fields = {
        "version",
        "experimentId",
        "releaseContractId",
        "releaseCutoffUtc",
        "activatedAtUtc",
        "deploymentIdentity",
        "immutable",
    }
    if set(value) != allowed_fields:
        errors.append("release_activation_fields_mismatch")
    if value.get("version") != RELEASE_ACTIVATION_VERSION:
        errors.append("release_activation_version_mismatch")
    if value.get("experimentId") != expected_experiment_id:
        errors.append("release_activation_experiment_identity_mismatch")
    if value.get("releaseContractId") != expected_release_contract_id:
        errors.append("release_activation_contract_identity_mismatch")

    cutoff = _parse_dt(expected_release_cutoff_utc)
    marker_cutoff = _parse_dt(value.get("releaseCutoffUtc"))
    activated = _parse_dt(value.get("activatedAtUtc"))
    created = _parse_dt(expected_created_at_utc)
    if cutoff is None or marker_cutoff != cutoff:
        errors.append("release_activation_cutoff_mismatch")
    if activated is None:
        errors.append("release_activation_timestamp_invalid")
    elif cutoff is not None and activated >= cutoff:
        errors.append("release_activation_not_strictly_before_cutoff")
    if created is None:
        errors.append("release_activation_manifest_created_at_invalid")
    elif activated is not None and activated < created:
        errors.append("release_activation_predates_manifest_creation")

    identity = value.get("deploymentIdentity")
    if not isinstance(identity, Mapping):
        errors.append("release_activation_deployment_identity_missing")
    else:
        if set(identity) != {"gitSha", "templateSha256"}:
            errors.append("release_activation_deployment_identity_fields_mismatch")
        if not _is_hex_digest(identity.get("gitSha"), 40):
            errors.append("release_activation_git_identity_invalid")
        if not _is_hex_digest(identity.get("templateSha256"), 64):
            errors.append("release_activation_template_identity_invalid")
    if value.get("immutable") is not True:
        errors.append("release_activation_not_immutable")
    return sorted(set(errors))


def _partition() -> Dict[str, Any]:
    return {
        "minimumRows": 0,
        "rowCount": 0,
        "slateDates": [],
        "slates": {},
        "frozen": False,
        "startSlateDate": None,
        "endSlateDate": None,
        "partitionFingerprint": None,
        "frozenAtUtc": None,
    }


def new_manifest(
    *,
    experiment_id: str,
    release_contract_id: str,
    release_cutoff_utc: str,
    feature_vector_version: str,
    feature_names: Optional[Sequence[str]] = None,
    model_feature_schemas: Optional[Mapping[str, Sequence[str]]] = None,
    created_at_utc: Optional[str] = None,
    release_activation: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    schemas = {
        str(model): [str(name) for name in names]
        for model, names in (model_feature_schemas or {}).items()
    }
    if not schemas:
        schemas = {"shared": [str(name) for name in (feature_names or [])]}
    if not experiment_id.strip():
        raise ExperimentContractError("experiment_id is required")
    if not release_contract_id.strip():
        raise ExperimentContractError("release_contract_id is required")
    if _parse_dt(release_cutoff_utc) is None:
        raise ExperimentContractError("release_cutoff_utc must be an ISO-8601 timestamp")
    if not feature_vector_version.strip():
        raise ExperimentContractError("feature_vector_version is required")
    if not schemas:
        raise ExperimentContractError("at least one prespecified model feature schema is required")
    for model, names in schemas.items():
        if not model.strip():
            raise ExperimentContractError("model feature schema names may not be blank")
        if len(names) < MIN_FEATURES or len(names) > MAX_FEATURES or len(names) != len(set(names)):
            raise ExperimentContractError(
                f"the prespecified {model} feature schema must contain 8-10 unique names"
            )
    all_names = sorted({name for names in schemas.values() for name in names})

    partitions = {name: _partition() for name in PARTITION_ORDER}
    for name, minimum in PARTITION_MINIMUMS.items():
        partitions[name]["minimumRows"] = minimum
    created_at = created_at_utc or datetime.now(timezone.utc).isoformat()
    if _parse_dt(created_at) is None:
        raise ExperimentContractError("created_at_utc must be an ISO-8601 timestamp")
    if release_activation is not None:
        activation_errors = release_activation_errors(
            release_activation,
            expected_experiment_id=experiment_id,
            expected_release_contract_id=release_contract_id,
            expected_release_cutoff_utc=release_cutoff_utc,
            expected_created_at_utc=created_at,
        )
        if activation_errors:
            raise ExperimentContractError(
                "release activation is invalid: " + ",".join(activation_errors)
            )

    manifest = {
        "ok": True,
        "version": VERSION,
        "experimentId": experiment_id,
        "releaseContractId": release_contract_id,
        "releaseCutoffUtc": _parse_dt(release_cutoff_utc).isoformat(),
        "featureVectorVersion": feature_vector_version,
        "fundamentalsSnapshotVersion": REQUIRED_FUNDAMENTALS_VERSION,
        "fundamentalsFingerprintVersion": REQUIRED_FUNDAMENTALS_FINGERPRINT_VERSION,
        "featureNames": all_names,
        "modelFeatureSchemas": schemas,
        "featureSchemaFingerprint": digest(schemas),
        "createdAtUtc": created_at,
        "revision": 0,
        "phase": "ACCUMULATING_TRAIN",
        "partitions": partitions,
        "assignedSlateDates": {},
        "historicalDiagnosticSlateDates": {},
        "validationFrozenAtUtc": None,
        "validationEndSlateDate": None,
        "frozenChallenger": None,
        "prospectiveCutoverAtUtc": None,
        "prospectiveAfterSlateDate": None,
        "prospectiveTestSealed": False,
        "prospectiveTestEvaluated": False,
        "selectedRecommendationLedger": {
            "authority": "conditional_write_from_unlabeled_immutable_lock_before_game_start",
            "minimumSettledSelected": 100,
            "storedSeparatelyFromDirectionTest": True,
        },
        "legacyArtifacts": {
            "fortyRow": "diagnostic_only",
            "moving80_30_30": "diagnostic_only",
            "movingUntouchedTest": "diagnostic_only",
        },
        "automaticPromotionEnabled": False,
        "firstPromotionRequiresManualReview": True,
        "policy": (
            "Whole MLB slate dates are assigned once in chronological order. Historical rows may fill only "
            "train and validation. After validation freezes, its exact challenger is stored durably; only "
            "games commencing strictly after that persisted cutover, with final labels observed after game "
            "commencement, may enter the next >=100-game future prospective test. No row or slate date may "
            "move between partitions."
        ),
    }
    if release_activation is not None:
        manifest["releaseActivation"] = copy.deepcopy(dict(release_activation))
    manifest["manifestDigest"] = manifest_digest(manifest)
    return manifest


def _feature_vector(row: Dict[str, Any]) -> Dict[str, Any]:
    value = row.get("featureSnapshot") or row.get("frozenFeatureVector") or {}
    return value if isinstance(value, dict) else {}


def _snapshot_v2(row: Dict[str, Any], vector: Dict[str, Any]) -> Dict[str, Any]:
    value = row.get("fundamentalsSnapshotV2")
    if not isinstance(value, dict):
        value = vector.get("fundamentalsSnapshotV2")
    return value if isinstance(value, dict) else {}


def _snapshot_ref(row: Dict[str, Any], vector: Dict[str, Any], snapshot: Dict[str, Any]) -> Any:
    return (
        row.get("fundamentalsSnapshotRefV2")
        or row.get("fundamentalsSnapshotV2Ref")
        or vector.get("fundamentalsSnapshotRefV2")
        or row.get("snapshotRef")
        or vector.get("fundamentalsSnapshotV2Ref")
        or vector.get("snapshotRef")
        or snapshot.get("snapshotRef")
    )


def _default_snapshot_validator(
    snapshot: Dict[str, Any], prediction_time_utc: Optional[str], lock_time_utc: Optional[str]
) -> Tuple[bool, Sequence[str]]:
    try:
        import mlb_fundamentals_snapshot_v2 as snapshot_v2
    except Exception:
        return False, ["fundamentals_v2_validator_unavailable"]
    validator = getattr(snapshot_v2, "validate_snapshot", None)
    if not callable(validator):
        return False, ["fundamentals_v2_validator_unavailable"]
    result = validator(
        snapshot,
        prediction_time_utc=prediction_time_utc,
        lock_time_utc=lock_time_utc,
    )
    if isinstance(result, tuple) and len(result) == 2:
        return bool(result[0]), list(result[1] or [])
    if isinstance(result, dict):
        return result.get("ok") is True, list(result.get("reasons") or result.get("errors") or [])
    return bool(result), [] if result else ["fundamentals_v2_validation_failed"]


def record_identity(row: Dict[str, Any]) -> str:
    vector = _feature_vector(row)
    game_id = str(row.get("gameId") or row.get("id") or vector.get("gameId") or "").strip()
    slate_date = str(row.get("slateDateEt") or vector.get("slateDateEt") or "").strip()
    fingerprint = str(vector.get("fingerprint") or row.get("featureFingerprint") or "").strip()
    if not game_id or not slate_date or not fingerprint:
        return ""
    return f"{slate_date}|{game_id}|{fingerprint}"


def _nonnegative_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except Exception:
        return None


def _canonical_pull_contract_reasons(vector: Dict[str, Any]) -> List[str]:
    """Enforce the post-dedupe pull proof only at V2 cohort admission."""
    try:
        import inqsi_pull_history as pull_contract
    except Exception:
        return ["canonical_pull_contract_authority_unavailable"]
    integrity = vector.get("pullHistoryIntegrity") or {}
    source = vector.get("predictionSourceCanonicalSlot") or {}
    reasons: List[str] = []
    if not isinstance(integrity, dict) or not integrity:
        return ["pull_history_integrity_missing"]
    if integrity.get("version") != pull_contract.PULL_HISTORY_INTEGRITY_VERSION:
        reasons.append("pull_history_integrity_version_mismatch")
    if integrity.get("canonicalizationVersion") != pull_contract.PULL_SLOT_VERSION:
        reasons.append("pull_slot_canonicalization_version_mismatch")
    raw = _nonnegative_int(integrity.get("rawPullCount"))
    unique = _nonnegative_int(integrity.get("uniqueSlotCount"))
    if raw is None or unique is None or raw <= 0 or raw != unique:
        reasons.append("raw_and_unique_pull_slot_counts_must_match_and_be_positive")
    for field in ("duplicatePullCount", "invalidPullCount", "contaminatedSlotCount"):
        if _nonnegative_int(integrity.get(field)) != 0:
            reasons.append(f"{field}_must_be_zero")
    if integrity.get("duplicateContaminated") is not False:
        reasons.append("duplicate_contaminated_must_be_false")
    if not str(integrity.get("canonicalSlotFingerprint") or ""):
        reasons.append("canonical_pull_slot_fingerprint_missing")
    slot_starts = integrity.get("slotStartsUtc") or []
    if (
        not isinstance(slot_starts, list)
        or len(slot_starts) != unique
        or not all(str(value or "") for value in slot_starts)
    ):
        reasons.append("canonical_pull_slot_start_list_invalid")
    elif len({str(value) for value in slot_starts}) != len(slot_starts):
        reasons.append("canonical_pull_slot_starts_not_unique")

    if not isinstance(source, dict) or not source:
        reasons.append("prediction_source_canonical_slot_missing")
        return sorted(set(reasons))
    if source.get("version") != pull_contract.PULL_SLOT_VERSION:
        reasons.append("prediction_source_canonical_slot_version_mismatch")
    if source.get("canonical") is not True:
        reasons.append("prediction_source_slot_not_canonical")
    if source.get("contaminated") is not False:
        reasons.append("prediction_source_slot_contaminated")
    if _nonnegative_int(source.get("duplicatePullCount")) != 0:
        reasons.append("prediction_source_duplicate_count_nonzero")
    if _nonnegative_int(source.get("invalidPullCount")) != 0:
        reasons.append("prediction_source_invalid_count_nonzero")
    if not str(source.get("canonicalPullFingerprint") or ""):
        reasons.append("prediction_source_canonical_fingerprint_missing")
    source_start = str(source.get("slotStartUtc") or "")
    if not source_start or source_start not in {str(value) for value in slot_starts}:
        reasons.append("prediction_source_slot_not_in_pull_history")
    return sorted(set(reasons))


def validate_record(
    row: Dict[str, Any],
    manifest: Dict[str, Any],
    snapshot_validator: Optional[SnapshotValidator] = None,
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    vector = _feature_vector(row)
    snapshot = _snapshot_v2(row, vector)
    identity = record_identity(row)
    lock_at = vector.get("lockAtUtc") or row.get("lockAtUtc") or row.get("lockedAt")
    # V2 admission requires the durable pregame persistence proof. A client
    # timestamp, object creation time, or source-pull time is not equivalent.
    prediction_at = row.get("predictionPersistedAtUtc") or vector.get(
        "predictionPersistedAtUtc"
    )
    release_cutoff = _parse_dt(manifest.get("releaseCutoffUtc"))
    parsed_lock = _parse_dt(lock_at)

    if not identity:
        reasons.append("missing_game_date_or_frozen_vector_fingerprint")
    if row.get("trainingEligible") is not True:
        reasons.append("current_canonical_row_not_training_eligible")
    if str(vector.get("version") or "") != str(manifest.get("featureVectorVersion") or ""):
        reasons.append("wrong_or_legacy_feature_vector_version")
    reasons.extend(_canonical_pull_contract_reasons(vector))
    if parsed_lock is None or (release_cutoff and parsed_lock < release_cutoff):
        reasons.append("pre_release_or_missing_lock_timestamp")
    if _parse_dt(prediction_at) is None:
        reasons.append("immutable_prediction_persistence_time_missing")
    if str(snapshot.get("version") or "") != REQUIRED_FUNDAMENTALS_VERSION:
        reasons.append("missing_immutable_fundamentals_v2_or_v1_backfill")
    if str(snapshot.get("fingerprintVersion") or "") != REQUIRED_FUNDAMENTALS_FINGERPRINT_VERSION:
        reasons.append("wrong_fundamentals_v2_fingerprint_version")
    if not str(snapshot.get("fingerprint") or ""):
        reasons.append("missing_fundamentals_v2_fingerprint")
    if not _snapshot_ref(row, vector, snapshot):
        reasons.append("missing_fundamentals_v2_snapshot_reference")

    feature_values = vector.get("features") or {}
    if not isinstance(feature_values, dict):
        reasons.append("frozen_feature_values_missing")
    else:
        # Values computed strictly from the immutable V2 snapshot or the
        # frozen canonical market-probability fields are intentionally not
        # duplicated into the legacy feature map.
        derived = {
            "homeMarketDeVigProbability",
            "selectedMarketDeVigProbability",
            "starterCompositeGapHome",
            "bullpenCompositeGapHome",
            "lineupWrcPlusGapHome",
            "fundamentalPitchingMissing",
            "fundamentalOffenseLineupMissing",
        }
        missing = [
            name
            for name in manifest.get("featureNames") or []
            if name not in feature_values and name not in derived
        ]
        if missing:
            reasons.append("prespecified_features_absent:" + ",".join(sorted(missing)))

    if not reasons:
        validator = snapshot_validator or _default_snapshot_validator
        valid, validation_reasons = validator(snapshot, prediction_at, lock_at)
        if not valid:
            reasons.extend(str(reason) for reason in validation_reasons or ["fundamentals_v2_validation_failed"])

    return not reasons, sorted(set(reasons))


def filter_records(
    rows: Iterable[Dict[str, Any]],
    manifest: Dict[str, Any],
    snapshot_validator: Optional[SnapshotValidator] = None,
) -> Dict[str, Any]:
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    identities: Dict[str, str] = {}
    for row in rows or []:
        ok, reasons = validate_record(row, manifest, snapshot_validator=snapshot_validator)
        identity = record_identity(row)
        game_key = "|".join(identity.split("|")[:2]) if identity else ""
        if ok and game_key:
            prior = identities.get(game_key)
            if prior:
                if prior != identity:
                    raise ExperimentContractError(f"conflicting immutable vectors for {game_key}")
                raise ExperimentContractError(f"duplicate immutable vector for {game_key}")
            if not prior:
                identities[game_key] = identity
                accepted.append(row)
        else:
            rejected.append({
                "gameId": row.get("gameId") or row.get("id"),
                "slateDateEt": row.get("slateDateEt"),
                "reasons": reasons,
            })
    accepted.sort(key=lambda row: (str(row.get("slateDateEt") or ""), str(row.get("commenceTime") or ""), record_identity(row)))
    reason_counts: Dict[str, int] = {}
    for item in rejected:
        for reason in item.get("reasons") or []:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "ok": True,
        "acceptedRows": accepted,
        "acceptedRowCount": len(accepted),
        "rejectedRows": rejected,
        "rejectedRowCount": len(rejected),
        "rejectionReasonCounts": reason_counts,
    }


def slate_fingerprint(rows: Sequence[Dict[str, Any]]) -> str:
    return digest(sorted(record_identity(row) for row in rows))


def label_final_at(row: Mapping[str, Any]) -> Optional[datetime]:
    """Return the canonical write-once label observation time.

    The prospective boundary is an evidence-time boundary, not merely a slate
    date boundary.  A historical label first read after the challenger was
    persisted is still historical and is also rejected by the slate-date
    boundary in ``advance_manifest``.
    """
    for field in (
        "labelRetrievedAtUtc",
        "labelFinalAtUtc",
        "outcomeFinalAtUtc",
        "settledAtUtc",
    ):
        parsed = _parse_dt(row.get(field))
        if parsed is not None:
            return parsed
    label = row.get("officialLabel") or {}
    if isinstance(label, Mapping):
        for field in ("observedAtUtc", "retrievedAtUtc", "createdAtUtc"):
            parsed = _parse_dt(label.get(field))
            if parsed is not None:
                return parsed
    return None


def game_commence_at(row: Mapping[str, Any]) -> Optional[datetime]:
    vector = _feature_vector(dict(row))
    for value in (
        row.get("commenceTime"),
        row.get("commence_time"),
        vector.get("commenceTime"),
        vector.get("commence_time"),
    ):
        parsed = _parse_dt(value)
        if parsed is not None:
            return parsed
    return None


def _groups(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        date = str(row.get("slateDateEt") or _feature_vector(row).get("slateDateEt") or "")
        if date:
            grouped.setdefault(date, []).append(row)
    for date in grouped:
        grouped[date] = sorted(grouped[date], key=record_identity)
    return grouped


def _active_partition(manifest: Dict[str, Any]) -> Optional[str]:
    for name in PARTITION_ORDER:
        if not bool((manifest.get("partitions") or {}).get(name, {}).get("frozen")):
            return name
    return None


def _freeze_if_ready(manifest: Dict[str, Any], name: str, frozen_at_utc: str) -> None:
    partition = manifest["partitions"][name]
    if int(partition.get("rowCount") or 0) < int(partition.get("minimumRows") or 0):
        return
    partition["frozen"] = True
    dates = list(partition.get("slateDates") or [])
    partition["startSlateDate"] = dates[0] if dates else None
    partition["endSlateDate"] = dates[-1] if dates else None
    partition["partitionFingerprint"] = digest(partition.get("slates") or {})
    partition["frozenAtUtc"] = frozen_at_utc
    if name == "train":
        manifest["phase"] = "ACCUMULATING_VALIDATION"
    elif name == "validation":
        manifest["validationFrozenAtUtc"] = frozen_at_utc
        manifest["validationEndSlateDate"] = partition["endSlateDate"]
        manifest["phase"] = "AWAITING_PERSISTED_FROZEN_CHALLENGER"
    else:
        manifest["phase"] = "PROSPECTIVE_TEST_SEALED_AWAITING_EVALUATION"
        manifest["prospectiveTestSealed"] = True


def bind_frozen_challenger(
    manifest: Dict[str, Any],
    *,
    artifact: Mapping[str, Any],
    artifact_digest: str,
    selected_threshold: float,
    bound_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Bind exactly one validation-selected artifact and open the future test.

    ``artifact`` must be an immutable S3 pointer (bucket/key/version/checksum).
    The manifest digest makes the cutover durable before any prospective row
    can be assigned.
    """
    if manifest_digest(manifest) != manifest.get("manifestDigest"):
        raise ExperimentContractError("manifest fingerprint mismatch")
    validation = (manifest.get("partitions") or {}).get("validation") or {}
    if validation.get("frozen") is not True:
        raise ExperimentContractError("validation must be frozen before binding a challenger")
    required_pointer = {
        "bucket": str(artifact.get("bucket") or ""),
        "key": str(artifact.get("key") or ""),
        "versionId": str(artifact.get("versionId") or ""),
        "sha256": str(artifact.get("sha256") or ""),
    }
    if (
        not all(required_pointer.values())
        or len(required_pointer["sha256"]) != 64
        or len(str(artifact_digest or "")) != 64
    ):
        raise ExperimentContractError("verified versioned challenger artifact is required")
    threshold = float(selected_threshold)
    if not 0.0 < threshold < 1.0:
        raise ExperimentContractError("selected threshold must be between zero and one")
    requested = {
        "artifact": required_pointer,
        "artifactDigest": str(artifact_digest),
        "selectedThreshold": threshold,
        "trainingPartitionFingerprint": (
            (manifest.get("partitions") or {}).get("train") or {}
        ).get("partitionFingerprint"),
        "validationPartitionFingerprint": validation.get("partitionFingerprint"),
    }
    current = manifest.get("frozenChallenger")
    if current:
        comparable = {key: current.get(key) for key in requested}
        if comparable != requested:
            raise FrozenPartitionConflict("a different challenger is already bound")
        return copy.deepcopy(manifest)
    bound = _parse_dt(bound_at_utc or datetime.now(timezone.utc).isoformat())
    if bound is None:
        raise ExperimentContractError("bound_at_utc must be an ISO-8601 timestamp")
    updated = copy.deepcopy(manifest)
    updated["frozenChallenger"] = {
        **requested,
        "boundAtUtc": bound.isoformat(),
        "automaticAuthority": False,
    }
    updated["prospectiveCutoverAtUtc"] = bound.isoformat()
    updated["prospectiveAfterSlateDate"] = updated.get("validationEndSlateDate")
    updated["phase"] = "ACCUMULATING_GENUINELY_FUTURE_PROSPECTIVE_TEST"
    updated["revision"] = int(updated.get("revision") or 0) + 1
    updated["updatedAtUtc"] = bound.isoformat()
    updated["manifestDigest"] = manifest_digest(updated)
    return updated


def advance_manifest(
    manifest: Dict[str, Any],
    rows: Iterable[Dict[str, Any]],
    *,
    finalized_slate_dates: Iterable[str],
    updated_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    if manifest_digest(manifest) != manifest.get("manifestDigest"):
        raise ExperimentContractError("manifest fingerprint mismatch")
    original = copy.deepcopy(manifest)
    updated = copy.deepcopy(manifest)
    grouped = _groups(rows)
    finalized = {str(value) for value in finalized_slate_dates}
    updated_at = _parse_dt(updated_at_utc or datetime.now(timezone.utc).isoformat())
    if updated_at is None:
        raise ExperimentContractError("updated_at_utc must be an ISO-8601 timestamp")
    updated_at_iso = updated_at.isoformat()

    # Frozen dates are immutable. Re-seeing a date with a changed member set or
    # feature fingerprint is a hard conflict, never a reason to reshuffle it.
    # The authoritative loader supplies the complete historical range, so an
    # assigned date disappearing altogether is the same loss of authority, not
    # permission to retain a stale approved count.
    for date, assignment in (updated.get("assignedSlateDates") or {}).items():
        if date not in grouped:
            raise FrozenPartitionConflict(
                f"frozen slate {date} disappeared from current authority"
            )
        actual = slate_fingerprint(grouped[date])
        if actual != assignment.get("slateFingerprint"):
            raise FrozenPartitionConflict(f"frozen slate {date} changed")

    for date, assignment in (updated.get("historicalDiagnosticSlateDates") or {}).items():
        if date not in grouped:
            raise FrozenPartitionConflict(
                f"diagnostic historical slate {date} disappeared from current authority"
            )
        actual = slate_fingerprint(grouped[date])
        if actual != assignment.get("slateFingerprint"):
            raise FrozenPartitionConflict(f"diagnostic historical slate {date} changed")

    for date in sorted(grouped):
        if date not in finalized or date in (updated.get("assignedSlateDates") or {}):
            continue
        slate_rows = grouped[date]
        if not slate_rows:
            continue
        train = updated["partitions"]["train"]
        validation = updated["partitions"]["validation"]
        prospective = updated["partitions"]["prospectiveTest"]
        if train.get("frozen") is not True:
            active = "train"
        elif validation.get("frozen") is not True:
            train_end = str(train.get("endSlateDate") or "")
            if not train_end or date <= train_end:
                updated.setdefault("historicalDiagnosticSlateDates", {})[date] = {
                    "rowCount": len(slate_rows),
                    "slateFingerprint": slate_fingerprint(slate_rows),
                    "reason": "not_strictly_after_frozen_training_partition",
                    "recordedAtUtc": updated_at_iso,
                    "trainingAuthority": False,
                    "prospectiveAuthority": False,
                }
                continue
            active = "validation"
        else:
            cutover = _parse_dt(updated.get("prospectiveCutoverAtUtc"))
            after_slate = str(updated.get("prospectiveAfterSlateDate") or "")
            label_times = [label_final_at(row) for row in slate_rows]
            commence_times = [game_commence_at(row) for row in slate_rows]
            future_eligible = bool(
                cutover
                and date > after_slate
                and label_times
                and len(label_times) == len(commence_times)
                and all(
                    commence is not None
                    and commence > cutover
                    and label is not None
                    and label > commence
                    for commence, label in zip(commence_times, label_times)
                )
            )
            if prospective.get("frozen") is not True and future_eligible:
                active = "prospectiveTest"
            else:
                updated.setdefault("historicalDiagnosticSlateDates", {})[date] = {
                    "rowCount": len(slate_rows),
                    "slateFingerprint": slate_fingerprint(slate_rows),
                    "reason": (
                        "prospective_direction_test_already_sealed"
                        if prospective.get("frozen") is True
                        else "not_finalized_after_persisted_challenger_cutover"
                    ),
                    "recordedAtUtc": updated_at_iso,
                    "trainingAuthority": False,
                    "prospectiveAuthority": False,
                }
                continue
        identities = [record_identity(row) for row in slate_rows]
        fingerprint = slate_fingerprint(slate_rows)
        partition = updated["partitions"][active]
        slate_entry = {
            "rowCount": len(identities),
            "rowIdentities": identities,
            "slateFingerprint": fingerprint,
        }
        partition["slateDates"] = sorted({*(partition.get("slateDates") or []), date})
        partition["slates"][date] = slate_entry
        partition["rowCount"] = int(partition.get("rowCount") or 0) + len(identities)
        updated["assignedSlateDates"][date] = {
            "partition": active,
            "slateFingerprint": fingerprint,
            "rowCount": len(identities),
        }
        _freeze_if_ready(updated, active, updated_at_iso)

    volatile = {"manifestDigest", "revision", "updatedAtUtc"}
    before_semantic = {key: value for key, value in original.items() if key not in volatile}
    after_semantic = {key: value for key, value in updated.items() if key not in volatile}
    if digest(before_semantic) == digest(after_semantic):
        return original
    updated["revision"] = int(updated.get("revision") or 0) + 1
    updated["updatedAtUtc"] = updated_at_iso
    updated["manifestDigest"] = manifest_digest(updated)
    return updated


def _selection_artifact_identity(challenger: Mapping[str, Any]) -> Dict[str, str]:
    artifact = challenger.get("artifact") or {}
    if not isinstance(artifact, Mapping):
        raise ExperimentContractError("frozen challenger artifact must be an object")
    identity = {
        "bucket": str(artifact.get("bucket") or ""),
        "key": str(artifact.get("key") or ""),
        "versionId": str(artifact.get("versionId") or ""),
        "sha256": str(artifact.get("sha256") or ""),
    }
    artifact_digest = str(challenger.get("artifactDigest") or "")
    if (
        any(not value for value in identity.values())
        or not _is_hex_digest(identity["sha256"])
        or identity["sha256"] != artifact_digest
    ):
        raise ExperimentContractError(
            "complete checksum-bound frozen challenger artifact identity is required"
        )
    return identity


def _selection_deployment_identity(value: Mapping[str, Any]) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        raise ExperimentContractError("deployment identity must be an object")
    identity = {
        "gitSha": str(value.get("gitSha") or ""),
        "templateSha256": str(value.get("templateSha256") or ""),
    }
    if set(value) != set(identity):
        raise ExperimentContractError("exact deployment identity fields are required")
    if not _is_hex_digest(identity["gitSha"], 40) or not _is_hex_digest(
        identity["templateSha256"]
    ):
        raise ExperimentContractError("checksum-bound deployment identity is required")
    return identity


_SELECTION_SEMANTIC_FIELDS = (
    "version",
    "experimentId",
    "releaseContractId",
    "challengerArtifactDigest",
    "challengerArtifact",
    "recordIdentity",
    "gameId",
    "officialGamePk",
    "slateDateEt",
    "commenceTime",
    "prospectiveCutoverAtUtc",
    "reliabilityProbability",
    "selectedThreshold",
    "selected",
    "outcomeKnownAtCapture",
    "writeOnce",
    "liveInferenceAuthority",
)


def _selection_semantic_material(entry: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: copy.deepcopy(entry.get(key))
        for key in _SELECTION_SEMANTIC_FIELDS
    }


def selection_semantic_fingerprint(entry: Mapping[str, Any]) -> str:
    """Normalize a schema-validated legacy-v1 or v2 record to its v2 identity."""
    material = _selection_semantic_material(entry)
    material["idempotencyFingerprintVersion"] = (
        SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V2
    )
    return digest(material)


def _selection_idempotency_material(entry: Mapping[str, Any]) -> Dict[str, Any]:
    version = entry.get("idempotencyFingerprintVersion")
    material = _selection_semantic_material(entry)
    if version == SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V1:
        material["deploymentIdentity"] = copy.deepcopy(
            entry.get("deploymentIdentity")
        )
        material["idempotencyFingerprintVersion"] = copy.deepcopy(version)
        return material
    if version != SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V2:
        raise ExperimentContractError(
            "selection idempotency fingerprint version is unsupported"
        )
    material["idempotencyFingerprintVersion"] = (
        SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V2
    )
    return material


def selection_idempotency_fingerprint(entry: Mapping[str, Any]) -> str:
    return digest(_selection_idempotency_material(entry))


def _selection_decision_material(entry: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        **_selection_idempotency_material(entry),
        "deploymentIdentity": copy.deepcopy(entry.get("deploymentIdentity")),
        "experimentManifestDigest": copy.deepcopy(
            entry.get("experimentManifestDigest")
        ),
        "capturedAtUtc": copy.deepcopy(entry.get("capturedAtUtc")),
        "idempotencyFingerprint": copy.deepcopy(
            entry.get("idempotencyFingerprint")
        ),
        "decisionFingerprintVersion": copy.deepcopy(
            entry.get("decisionFingerprintVersion")
        ),
    }


def selection_decision_fingerprint(entry: Mapping[str, Any]) -> str:
    return digest(_selection_decision_material(entry))


def _selection_record_material(entry: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: copy.deepcopy(entry.get(key))
        for key in sorted(SELECTION_LEDGER_FIELDS - {"recordFingerprint"})
    }


def selection_record_fingerprint(entry: Mapping[str, Any]) -> str:
    return digest(_selection_record_material(entry))


def selection_ledger_schema_reasons(entry: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(entry, Mapping):
        return ["selection_schema_not_object"]
    if set(entry) != SELECTION_LEDGER_FIELDS:
        errors.append("selection_schema_fields_mismatch")
    if entry.get("version") != SELECTION_LEDGER_VERSION:
        errors.append("selection_ledger_version_mismatch")
    if not _is_hex_digest(entry.get("experimentManifestDigest")):
        errors.append("selection_manifest_digest_at_capture_invalid")
    try:
        expected_artifact = _selection_artifact_identity(
            {
                "artifactDigest": entry.get("challengerArtifactDigest"),
                "artifact": entry.get("challengerArtifact"),
            }
        )
        if entry.get("challengerArtifact") != expected_artifact:
            errors.append("selection_challenger_artifact_schema_mismatch")
    except ExperimentContractError:
        errors.append("selection_challenger_artifact_identity_invalid")
    try:
        expected_deployment = _selection_deployment_identity(
            entry.get("deploymentIdentity") or {}
        )
        if entry.get("deploymentIdentity") != expected_deployment:
            errors.append("selection_deployment_identity_schema_mismatch")
    except ExperimentContractError:
        errors.append("selection_deployment_identity_invalid")
    if not str(entry.get("recordIdentity") or ""):
        errors.append("selection_record_identity_missing")
    if not str(entry.get("gameId") or ""):
        errors.append("selection_game_id_missing")
    if not str(entry.get("officialGamePk") or ""):
        errors.append("selection_official_game_pk_missing")
    if not str(entry.get("slateDateEt") or ""):
        errors.append("selection_slate_date_missing")
    if entry.get("outcomeKnownAtCapture") is not False:
        errors.append("outcome_known_at_selection_capture")
    if entry.get("writeOnce") is not True:
        errors.append("selection_write_once_marker_missing")
    if entry.get("liveInferenceAuthority") is not False:
        errors.append("selection_shadow_authority_marker_invalid")
    if (
        entry.get("idempotencyFingerprintVersion")
        not in SUPPORTED_SELECTION_IDEMPOTENCY_FINGERPRINT_VERSIONS
    ):
        errors.append("selection_idempotency_fingerprint_version_mismatch")
    try:
        expected_idempotency = selection_idempotency_fingerprint(entry)
    except Exception:
        expected_idempotency = None
    if entry.get("idempotencyFingerprint") != expected_idempotency:
        errors.append("selection_idempotency_fingerprint_mismatch")
    if entry.get("decisionFingerprintVersion") != SELECTION_DECISION_FINGERPRINT_VERSION:
        errors.append("selection_decision_fingerprint_version_mismatch")
    try:
        expected_decision = selection_decision_fingerprint(entry)
    except Exception:
        expected_decision = None
    if entry.get("decisionFingerprint") != expected_decision:
        errors.append("selection_decision_fingerprint_mismatch")
    if entry.get("recordFingerprintVersion") != SELECTION_RECORD_FINGERPRINT_VERSION:
        errors.append("selection_record_fingerprint_version_mismatch")
    try:
        expected_record = selection_record_fingerprint(entry)
    except Exception:
        expected_record = None
    if entry.get("recordFingerprint") != expected_record:
        errors.append("selection_record_fingerprint_mismatch")
    return sorted(set(errors))


def selection_ledger_entry(
    manifest: Dict[str, Any],
    row: Dict[str, Any],
    *,
    reliability_probability: float,
    deployment_identity: Mapping[str, Any],
    captured_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an exact, outcome-free pregame decision for conditional storage."""
    if manifest_digest(manifest) != manifest.get("manifestDigest"):
        raise ExperimentContractError("manifest fingerprint mismatch")
    challenger = manifest.get("frozenChallenger") or {}
    if not challenger:
        raise ExperimentContractError("frozen challenger is required")
    artifact_identity = _selection_artifact_identity(challenger)
    deployment = _selection_deployment_identity(deployment_identity)
    cutover = _parse_dt(manifest.get("prospectiveCutoverAtUtc"))
    bound = _parse_dt(challenger.get("boundAtUtc"))
    if cutover is None or bound != cutover:
        raise ExperimentContractError("persisted challenger cutover authority is required")
    identity = record_identity(row)
    if not identity:
        raise ExperimentContractError("immutable lock identity is required")
    vector = _feature_vector(row)
    labels = vector.get("labels") or {}
    forbidden = ("winner", "correct", "success", "homeWon", "pickCorrect")
    if any(key in row for key in forbidden) or any(
        labels.get(key) is not None for key in ("homeWon", "pickCorrect")
    ):
        raise ExperimentContractError("selection must be captured before outcome fields exist")
    captured = _parse_dt(captured_at_utc or datetime.now(timezone.utc).isoformat())
    commence = _parse_dt(row.get("commenceTime") or vector.get("commenceTime"))
    if captured is None or captured <= cutover:
        raise ExperimentContractError("selection must be captured after challenger cutover")
    if commence is None or commence <= cutover or captured >= commence:
        raise ExperimentContractError(
            "selection game and capture must be strictly after cutover and before game start"
        )
    try:
        probability = float(reliability_probability)
        threshold = float(challenger.get("selectedThreshold"))
    except Exception as exc:
        raise ExperimentContractError("selection probability contract is invalid") from exc
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ExperimentContractError("reliability probability must be in [0,1]")
    if not math.isfinite(threshold) or not 0.0 < threshold < 1.0:
        raise ExperimentContractError("frozen challenger threshold is invalid")
    entry = {
        "version": SELECTION_LEDGER_VERSION,
        "experimentId": manifest.get("experimentId"),
        "releaseContractId": manifest.get("releaseContractId"),
        "experimentManifestDigest": manifest.get("manifestDigest"),
        "challengerArtifactDigest": challenger.get("artifactDigest"),
        "challengerArtifact": artifact_identity,
        "recordIdentity": identity,
        "gameId": row.get("gameId") or row.get("id"),
        "officialGamePk": row.get("officialGamePk"),
        "slateDateEt": row.get("slateDateEt") or vector.get("slateDateEt"),
        "commenceTime": commence.isoformat(),
        "capturedAtUtc": captured.isoformat(),
        "prospectiveCutoverAtUtc": cutover.isoformat(),
        "reliabilityProbability": probability,
        "selectedThreshold": threshold,
        "selected": probability >= threshold,
        "outcomeKnownAtCapture": False,
        "writeOnce": True,
        "liveInferenceAuthority": False,
        "deploymentIdentity": deployment,
        "decisionFingerprintVersion": SELECTION_DECISION_FINGERPRINT_VERSION,
        "decisionFingerprint": "",
        "idempotencyFingerprintVersion": SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION,
        "idempotencyFingerprint": "",
        "recordFingerprintVersion": SELECTION_RECORD_FINGERPRINT_VERSION,
        "recordFingerprint": "",
    }
    entry["idempotencyFingerprint"] = selection_idempotency_fingerprint(entry)
    entry["decisionFingerprint"] = selection_decision_fingerprint(entry)
    entry["recordFingerprint"] = selection_record_fingerprint(entry)
    errors = selection_ledger_schema_reasons(entry)
    if errors:
        raise ExperimentContractError(
            "invalid selection ledger entry: " + ",".join(errors)
        )
    return entry


def selection_ledger_validation_errors(
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    row: Optional[Mapping[str, Any]] = None,
    challenger_artifact_digest: Optional[str] = None,
) -> List[str]:
    errors = selection_ledger_schema_reasons(entry)
    challenger = manifest.get("frozenChallenger") or {}
    expected_digest = str(
        challenger_artifact_digest or challenger.get("artifactDigest") or ""
    )
    if entry.get("experimentId") != manifest.get("experimentId"):
        errors.append("selection_experiment_id_mismatch")
    if entry.get("releaseContractId") != manifest.get("releaseContractId"):
        errors.append("selection_release_contract_id_mismatch")
    if not expected_digest or entry.get("challengerArtifactDigest") != expected_digest:
        errors.append("selection_challenger_digest_mismatch")
    try:
        expected_artifact = _selection_artifact_identity(challenger)
    except ExperimentContractError:
        expected_artifact = None
        errors.append("manifest_challenger_artifact_identity_invalid")
    if expected_artifact is None or entry.get("challengerArtifact") != expected_artifact:
        errors.append("selection_challenger_artifact_version_mismatch")

    cutover = _parse_dt(manifest.get("prospectiveCutoverAtUtc"))
    entry_cutover = _parse_dt(entry.get("prospectiveCutoverAtUtc"))
    bound = _parse_dt(challenger.get("boundAtUtc"))
    captured = _parse_dt(entry.get("capturedAtUtc"))
    commence = _parse_dt(entry.get("commenceTime"))
    if cutover is None or entry_cutover != cutover or bound != cutover:
        errors.append("selection_cutover_authority_mismatch")
    if captured is None or cutover is None or captured <= cutover:
        errors.append("selection_not_captured_after_cutover")
    if commence is None or cutover is None or commence <= cutover:
        errors.append("selection_game_not_after_cutover")
    if captured is None or commence is None or captured >= commence:
        errors.append("selection_not_captured_before_commence")

    try:
        probability = float(entry.get("reliabilityProbability"))
        threshold = float(entry.get("selectedThreshold"))
        expected_threshold = float(challenger.get("selectedThreshold"))
    except Exception:
        probability = threshold = expected_threshold = float("nan")
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        errors.append("selection_reliability_probability_invalid")
    if (
        not math.isfinite(threshold)
        or not 0.0 < threshold < 1.0
        or not math.isfinite(expected_threshold)
        or threshold != expected_threshold
    ):
        errors.append("selection_threshold_mismatch")
    selected = entry.get("selected")
    if not isinstance(selected, bool) or (
        math.isfinite(probability)
        and math.isfinite(threshold)
        and selected != (probability >= threshold)
    ):
        errors.append("selection_decision_mismatch")

    identity = str(entry.get("recordIdentity") or "")
    if row is not None:
        if identity != record_identity(dict(row)):
            errors.append("selection_record_identity_row_mismatch")
        if str(entry.get("gameId") or "") != str(
            row.get("gameId") or row.get("id") or ""
        ):
            errors.append("selection_game_id_row_mismatch")
        if str(entry.get("officialGamePk") or "") != str(
            row.get("officialGamePk") or ""
        ):
            errors.append("selection_official_game_pk_row_mismatch")
        row_commence = game_commence_at(row)
        if row_commence is None or row_commence != commence:
            errors.append("selection_commence_row_mismatch")
        row_date = str(
            row.get("slateDateEt")
            or _feature_vector(dict(row)).get("slateDateEt")
            or ""
        )
        if str(entry.get("slateDateEt") or "") != row_date:
            errors.append("selection_slate_date_row_mismatch")
    return sorted(set(errors))


def _manifest_slate_identity_errors(
    manifest: Mapping[str, Any],
    slate_date: str,
    rows: Sequence[Dict[str, Any]],
) -> Tuple[List[str], Optional[str]]:
    errors: List[str] = []
    identities = [record_identity(row) for row in rows]
    actual_fingerprint = slate_fingerprint(rows)
    if any(not identity for identity in identities):
        errors.append("clean_slate_record_identity_missing")
    if len(set(identities)) != len(identities):
        errors.append("duplicate_clean_slate_record_identity")

    assigned = (manifest.get("assignedSlateDates") or {}).get(slate_date)
    diagnostic = (manifest.get("historicalDiagnosticSlateDates") or {}).get(
        slate_date
    )
    authority = assigned or diagnostic
    if not isinstance(authority, Mapping):
        errors.append("immutable_manifest_slate_assignment_missing")
        return sorted(set(errors)), actual_fingerprint
    if int(authority.get("rowCount") or 0) != len(rows):
        errors.append("immutable_manifest_slate_row_count_mismatch")
    if authority.get("slateFingerprint") != actual_fingerprint:
        errors.append("immutable_manifest_slate_fingerprint_mismatch")

    if isinstance(assigned, Mapping):
        partition_name = assigned.get("partition")
        partition = (manifest.get("partitions") or {}).get(partition_name) or {}
        partition_slate = (partition.get("slates") or {}).get(slate_date)
        if not isinstance(partition_slate, Mapping):
            errors.append("immutable_partition_slate_assignment_missing")
        else:
            expected_identities = partition_slate.get("rowIdentities")
            if (
                not isinstance(expected_identities, list)
                or sorted(str(value) for value in expected_identities)
                != sorted(identities)
            ):
                errors.append("immutable_partition_slate_identity_set_mismatch")
            if int(partition_slate.get("rowCount") or 0) != len(rows):
                errors.append("immutable_partition_slate_row_count_mismatch")
            if partition_slate.get("slateFingerprint") != actual_fingerprint:
                errors.append("immutable_partition_slate_fingerprint_mismatch")
    return sorted(set(errors)), actual_fingerprint


def _full_clean_slate_proofs(
    manifest: Dict[str, Any],
    *,
    integrity_clean_rows: Sequence[Dict[str, Any]],
    official_finalized_slate_authorities: Mapping[str, Any],
    integrity_clean_row_count: int,
) -> List[Dict[str, Any]]:
    grouped = _groups(integrity_clean_rows)
    completed_dates = sorted(
        {
            *(str(value) for value in (manifest.get("assignedSlateDates") or {}) if str(value)),
            *(
                str(value)
                for value in (manifest.get("historicalDiagnosticSlateDates") or {})
                if str(value)
            ),
        }
    )
    global_count_matches = len(integrity_clean_rows) == integrity_clean_row_count
    proofs: List[Dict[str, Any]] = []
    release_cutoff = _parse_dt(manifest.get("releaseCutoffUtc"))
    for slate_date in completed_dates:
        authority = official_finalized_slate_authorities.get(slate_date)
        authority_errors = official_finalized_slate_authority_errors(
            authority,
            expected_slate_date_et=slate_date,
        )
        official_pks = (
            [str(value or "").strip() for value in authority.get("officialGamePks") or []]
            if isinstance(authority, Mapping)
            else []
        )
        official_pk_set = set(official_pks)
        rows = list(grouped.get(slate_date) or [])
        manifest_errors, clean_slate_fingerprint = _manifest_slate_identity_errors(
            manifest, slate_date, rows
        )
        row_errors: List[str] = []
        eligible_pks: List[str] = []
        observed_pks: List[str] = []
        for row in rows:
            row_pk = str(row.get("officialGamePk") or "").strip()
            if row_pk:
                observed_pks.append(row_pk)
            else:
                row_errors.append("clean_row_official_game_pk_missing")
            valid, reasons = validate_record(row, manifest)
            commence = game_commence_at(row)
            label_time = label_final_at(row)
            vector = _feature_vector(row)
            lock_time = _parse_dt(
                vector.get("lockAtUtc") or row.get("lockAtUtc") or row.get("lockedAt")
            )
            if row.get("slateFinalized") is not True:
                row_errors.append("clean_row_slate_finalized_proof_missing")
            if commence is None or label_time is None or label_time <= commence:
                row_errors.append("clean_row_final_label_time_invalid")
            if lock_time is None or (
                release_cutoff is not None and lock_time < release_cutoff
            ):
                row_errors.append("clean_row_not_after_release_cutoff")
            if not valid:
                row_errors.extend(f"clean_row_invalid:{reason}" for reason in reasons)
            if (
                row_pk
                and valid
                and row.get("slateFinalized") is True
                and commence is not None
                and label_time is not None
                and label_time > commence
                and lock_time is not None
                and (release_cutoff is None or lock_time >= release_cutoff)
            ):
                eligible_pks.append(row_pk)

        duplicate_pks = sorted(
            {
                value
                for value in observed_pks
                if observed_pks.count(value) > 1
            }
        )
        if duplicate_pks:
            row_errors.append("duplicate_clean_official_game_pk")
        eligible_pk_set = set(eligible_pks)
        missing = sorted(official_pk_set - eligible_pk_set)
        unexpected = sorted(eligible_pk_set - official_pk_set)
        if missing:
            row_errors.append("official_game_set_missing_clean_rows")
        if unexpected:
            row_errors.append("clean_rows_outside_official_game_set")
        if not global_count_matches:
            row_errors.append("integrity_clean_row_count_mismatch")

        errors = sorted(set(authority_errors + manifest_errors + row_errors))
        achieved = bool(
            not errors
            and official_pk_set
            and eligible_pk_set == official_pk_set
            and len(eligible_pks) == len(eligible_pk_set)
        )
        proofs.append(
            {
                "slateDateEt": slate_date,
                "achieved": achieved,
                "officialGameCount": len(official_pk_set),
                "cleanEligibleGameCount": len(eligible_pk_set & official_pk_set),
                "missingOfficialGamePks": missing,
                "unexpectedCleanGamePks": unexpected,
                "duplicateCleanOfficialGamePks": duplicate_pks,
                "officialGameSetFingerprint": (
                    authority.get("officialGameSetFingerprint")
                    if isinstance(authority, Mapping)
                    else None
                ),
                "cleanGameSetFingerprint": official_game_set_fingerprint(
                    slate_date, sorted(eligible_pk_set)
                ),
                "cleanSlateFingerprint": clean_slate_fingerprint,
                "authorityFingerprint": (
                    authority.get("authorityFingerprint")
                    if isinstance(authority, Mapping)
                    else None
                ),
                "errors": errors,
            }
        )
    return proofs


def milestone_status(
    manifest: Dict[str, Any],
    *,
    integrity_clean_row_count: int,
    settled_selected_recommendation_count: int,
    clean_games_per_full_slate: int = 15,
    integrity_clean_rows: Optional[Iterable[Dict[str, Any]]] = None,
    official_finalized_slate_authorities: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if manifest_digest(manifest) != manifest.get("manifestDigest"):
        raise ExperimentContractError("manifest fingerprint mismatch")
    counts = {
        name: int(((manifest.get("partitions") or {}).get(name) or {}).get("rowCount") or 0)
        for name in PARTITION_ORDER
    }
    clean = int(integrity_clean_row_count or 0)
    clean_rows = list(integrity_clean_rows or [])
    selected = int(settled_selected_recommendation_count or 0)
    completed_finalized_slate_dates = sorted(
        {
            *(
                str(value)
                for value in (manifest.get("assignedSlateDates") or {})
                if str(value)
            ),
            *(
                str(value)
                for value in (manifest.get("historicalDiagnosticSlateDates") or {})
                if str(value)
            ),
        }
    )
    completed_finalized_slate_count = len(completed_finalized_slate_dates)
    finalized_authorities = (
        official_finalized_slate_authorities
        if isinstance(official_finalized_slate_authorities, Mapping)
        else {}
    )
    full_slate_proofs = _full_clean_slate_proofs(
        manifest,
        integrity_clean_rows=clean_rows,
        official_finalized_slate_authorities=finalized_authorities,
        integrity_clean_row_count=clean,
    )
    qualifying_full_slate_proofs = [
        proof for proof in full_slate_proofs if proof.get("achieved") is True
    ]
    qualifying_full_slate_proof = (
        qualifying_full_slate_proofs[0] if qualifying_full_slate_proofs else None
    )
    first_full_clean_slate_achieved = qualifying_full_slate_proof is not None
    best_full_slate_proof = (
        sorted(
            full_slate_proofs,
            key=lambda proof: (
                -int(proof.get("cleanEligibleGameCount") or 0),
                len(proof.get("missingOfficialGamePks") or []),
                str(proof.get("slateDateEt") or ""),
            ),
        )[0]
        if full_slate_proofs
        else None
    )
    first_full_clean_target = int(
        (best_full_slate_proof or {}).get("officialGameCount")
        or FIRST_FULL_CLEAN_SLATE_PROOF_ROWS
    )
    first_full_clean_current = int(
        (best_full_slate_proof or {}).get("cleanEligibleGameCount") or 0
    )
    first_full_clean_remaining = (
        0
        if first_full_clean_slate_achieved
        else max(0, first_full_clean_target - first_full_clean_current)
    )
    if first_full_clean_slate_achieved:
        first_full_clean_slate_state = "FIRST_FULL_CLEAN_SLATE_PROOF_ACHIEVED"
    elif completed_finalized_slate_count:
        first_full_clean_slate_state = "COLLECTING_FIRST_EXACT_FULL_CLEAN_SLATE"
    else:
        first_full_clean_slate_state = (
            "WAITING_FOR_FIRST_COMPLETE_FINALIZED_SLATE"
        )
    targets = {
        "firstFullCleanSlateProof": FIRST_FULL_CLEAN_SLATE_PROOF_ROWS,
        "mechanicalShadowTrainerProof": MECHANICAL_SHADOW_PROOF_ROWS,
        "train": PARTITION_MINIMUMS["train"],
        "validation": PARTITION_MINIMUMS["validation"],
        "prospectiveTest": PARTITION_MINIMUMS["prospectiveTest"],
        "totalClean": sum(PARTITION_MINIMUMS.values()),
        "prospectiveSelectedRecommendations": 100,
    }
    remaining = {
        "firstFullCleanSlateProof": first_full_clean_remaining,
        "mechanicalShadowTrainerProof": max(0, targets["mechanicalShadowTrainerProof"] - clean),
        "train": max(0, targets["train"] - counts["train"]),
        "validation": max(0, targets["validation"] - counts["validation"]),
        "prospectiveTest": max(0, targets["prospectiveTest"] - counts["prospectiveTest"]),
        "totalClean": max(0, targets["totalClean"] - clean),
        "prospectiveSelectedRecommendations": max(0, 100 - selected),
    }
    slate_size = max(1, int(clean_games_per_full_slate or 15))
    if counts["train"] < 300:
        stage = "COLLECTING_TRAIN"
    elif counts["validation"] < 100:
        stage = "COLLECTING_VALIDATION"
    elif not manifest.get("frozenChallenger"):
        stage = "PERSISTING_VALIDATION_SELECTED_CHALLENGER"
    elif counts["prospectiveTest"] < 100:
        stage = "COLLECTING_GENUINELY_FUTURE_PROSPECTIVE_TEST"
    elif selected < 100:
        stage = "COLLECTING_PRE_OUTCOME_SELECTED_RECOMMENDATIONS"
    else:
        stage = "METRIC_GATES_OR_MANUAL_REVIEW_PENDING"
    return {
        "ok": True,
        "version": VERSION,
        "experimentId": manifest.get("experimentId"),
        "stage": stage,
        "counts": {
            "integrityClean": clean,
            "completedFinalizedSlates": completed_finalized_slate_count,
            **counts,
            "settledProspectiveSelectedRecommendations": selected,
        },
        "targets": targets,
        "remainingRows": remaining,
        "firstFullCleanSlateProof": {
            "state": first_full_clean_slate_state,
            "achieved": first_full_clean_slate_achieved,
            "targetCleanGames": first_full_clean_target,
            "planningEstimateCleanGames": FIRST_FULL_CLEAN_SLATE_PROOF_ROWS,
            "currentCleanGames": first_full_clean_current,
            "globalIntegrityCleanGames": clean,
            "remainingCleanGames": remaining["firstFullCleanSlateProof"],
            "completedFinalizedSlateCount": completed_finalized_slate_count,
            "completedFinalizedSlateDates": completed_finalized_slate_dates,
            "qualifyingSlateDate": (
                qualifying_full_slate_proof.get("slateDateEt")
                if qualifying_full_slate_proof
                else None
            ),
            "qualifyingOfficialGameSetFingerprint": (
                qualifying_full_slate_proof.get("officialGameSetFingerprint")
                if qualifying_full_slate_proof
                else None
            ),
            "exactOfficialGameSetEqualityRequired": True,
            "evaluatedSlateProofs": full_slate_proofs,
            "authority": (
                "one nonempty verified official FINAL gamePk set must exactly "
                "equal the same immutable slate's unique, current, post-cutoff "
                "clean eligible gamePk set"
            ),
        },
        "projectedFullCleanSlatesRemaining": {
            name: int(math.ceil(value / slate_size)) for name, value in remaining.items()
        },
        "assumedCleanGamesPerFullSlate": slate_size,
        "aspirationalAccuracyPct": 90.0,
        "aspirationalAccuracyAffectsPromotion": False,
        "automaticPromotionEnabled": False,
        "firstPromotionRequiresManualReview": True,
    }


def partition_for_row(manifest: Dict[str, Any], row: Dict[str, Any]) -> Optional[str]:
    date = str(row.get("slateDateEt") or _feature_vector(row).get("slateDateEt") or "")
    assignment = (manifest.get("assignedSlateDates") or {}).get(date) or {}
    name = assignment.get("partition")
    if name not in PARTITION_ORDER:
        return None
    expected = ((manifest.get("partitions") or {}).get(name, {}).get("slates") or {}).get(date) or {}
    return name if record_identity(row) in set(expected.get("rowIdentities") or []) else None


def rows_by_partition(manifest: Dict[str, Any], rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    output = {name: [] for name in PARTITION_ORDER}
    for row in rows or []:
        name = partition_for_row(manifest, row)
        if name:
            output[name].append(row)
    for name in output:
        output[name].sort(key=lambda row: (str(row.get("slateDateEt") or ""), str(row.get("commenceTime") or ""), record_identity(row)))
    return output


def mark_prospective_evaluated(
    manifest: Dict[str, Any], *, evaluation_fingerprint: str, evaluated_at_utc: Optional[str] = None
) -> Dict[str, Any]:
    if not manifest.get("prospectiveTestSealed"):
        raise ExperimentContractError("prospective test is not sealed")
    if manifest.get("prospectiveTestEvaluated"):
        if manifest.get("prospectiveEvaluationFingerprint") != evaluation_fingerprint:
            raise FrozenPartitionConflict("prospective test was already evaluated with a different artifact")
        return copy.deepcopy(manifest)
    updated = copy.deepcopy(manifest)
    updated["prospectiveTestEvaluated"] = True
    updated["prospectiveEvaluationFingerprint"] = str(evaluation_fingerprint)
    updated["prospectiveTestEvaluatedAtUtc"] = evaluated_at_utc or datetime.now(timezone.utc).isoformat()
    updated["phase"] = "PROSPECTIVE_TEST_EVALUATED"
    updated["revision"] = int(updated.get("revision") or 0) + 1
    updated["manifestDigest"] = manifest_digest(updated)
    return updated
