from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple
from zoneinfo import ZoneInfo

import mlb_ml_dual_model_v2 as dual_model
import mlb_ml_experiment_v2 as experiment
import mlb_ml_promotion_policy_v2 as promotion_policy


VERSION = "MLB-ML-AWS-TRAINING-v1-persisted-cutover-selection-ledger-shadow"
EXPERIMENT_PK_PREFIX = "MLB_ML_EXPERIMENT#V2#"
MANIFEST_SK = "MANIFEST"
CANDIDATE_SK_PREFIX = "CANDIDATE#"
CANDIDATE_LATEST_SK = "CANDIDATE#LATEST"
CHAMPION_PK = "MLB_ML_CHAMPION#V2"
CHAMPION_SK = "ACTIVE"
SELECTION_SK_PREFIX = "SELECTION#"
SELECTION_RECORD_TYPE = "mlb_ml_prospective_selection_v2"
STATUS_LATEST_SK = "STATUS#LATEST"
STATUS_LATEST_TRAINING_SK = "STATUS#LATEST#TRAINING"
STATUS_LATEST_SELECTION_CAPTURE_SK = "STATUS#LATEST#SELECTION_CAPTURE"
STATUS_RUN_SK_PREFIX = "STATUS#RUN#"
EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID = (
    "mlb-v2-2026-07-21-future-prospective-r2"
)
EXECUTION_LEASE_PK = (
    f"{EXPERIMENT_PK_PREFIX}{EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID}"
)
LEGACY_SHARED_EXECUTION_LEASE_SK = "EXECUTION_LEASE"
STATE_MUTATION_EXECUTION_LEASE_SK = "EXECUTION_LEASE#STATE_MUTATION"
SELECTION_CAPTURE_EXECUTION_LEASE_SK = "EXECUTION_LEASE#SELECTION_CAPTURE"
# Backward-compatible public name now points at the v2 state-mutation domain.
EXECUTION_LEASE_SK = STATE_MUTATION_EXECUTION_LEASE_SK
EXECUTION_LEASE_RECORD_TYPE = "mlb_ml_execution_lease_v1"
EXECUTION_LEASE_VERSION = "MLB-ML-EXECUTION-LEASE-v2-mode-isolated-ddb-conditional"
EXECUTION_LEASE_SECONDS = 960
EXECUTION_LEASE_UNAVAILABLE_MESSAGE = (
    "another MLB ML trainer invocation holds the execution lease"
)
LEGACY_EXECUTION_SENTINEL_RECORD_TYPE = (
    "mlb_ml_execution_lease_migration_sentinel_v2"
)
LEGACY_EXECUTION_SENTINEL_VERSION = (
    "MLB-ML-EXECUTION-SENTINEL-v2-renewable-expiring"
)
LEGACY_EXECUTION_SENTINEL_OWNER = "V2_RENEWABLE_MIGRATION_SENTINEL"
LEGACY_EXECUTION_SENTINEL_MODE = "migration_sentinel"
EXECUTION_LEASE_PROTECTED_MODES = (
    "manual_review",
    "selection_capture",
    "training",
)
EXECUTION_LEASE_DOMAIN_BY_MODE = {
    "manual_review": "state_mutation",
    "training": "state_mutation",
    "selection_capture": "selection_capture",
}
EXECUTION_LEASE_KEY_BY_DOMAIN = {
    "state_mutation": STATE_MUTATION_EXECUTION_LEASE_SK,
    "selection_capture": SELECTION_CAPTURE_EXECUTION_LEASE_SK,
}
STATUS_FINGERPRINT_VERSION = (
    "MLB-ML-AWS-TRAINING-STATUS-SHA256-v2-ddb-roundtrip-canonical"
)
STATUS_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TRAINING_STATUS_MAX_AGE = timedelta(hours=8)
SELECTION_CAPTURE_STATUS_MAX_AGE = timedelta(minutes=45)
SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))


class TrainingContractError(RuntimeError):
    pass


class ConditionalStateConflict(TrainingContractError):
    pass


class ManifestStateConflict(ConditionalStateConflict):
    pass


class ExecutionLeaseUnavailable(ConditionalStateConflict):
    pass


class ExecutionLeaseRequired(TrainingContractError):
    pass


class TrainingStore(Protocol):
    def load_manifest(self, experiment_id: str) -> Optional[Dict[str, Any]]: ...
    def save_manifest(
        self,
        manifest: Dict[str, Any],
        *,
        expected_revision: Optional[int],
        expected_digest: Optional[str],
    ) -> None: ...
    def put_versioned_json(self, key: str, payload: Any) -> Dict[str, Any]: ...
    def read_versioned_json(self, artifact: Mapping[str, Any]) -> Dict[str, Any]: ...
    def record_selection(self, entry: Dict[str, Any]) -> Dict[str, Any]: ...
    def list_selections(self, experiment_id: str) -> List[Dict[str, Any]]: ...
    def save_status(self, experiment_id: str, status: Dict[str, Any]) -> None: ...
    def load_latest_status(
        self, experiment_id: str, execution_mode: Optional[str] = None
    ) -> Optional[Dict[str, Any]]: ...
    def load_status_run(
        self, experiment_id: str, run_id: str
    ) -> Optional[Dict[str, Any]]: ...
    def acquire_execution_lease(
        self,
        experiment_id: str,
        *,
        owner_token: str,
        execution_mode: str,
        acquired_at: datetime,
        lease_seconds: int,
    ) -> Dict[str, Any]: ...
    def release_execution_lease(
        self,
        experiment_id: str,
        *,
        owner_token: str,
        execution_mode: str,
    ) -> None: ...
    def commit_candidate(
        self,
        manifest: Dict[str, Any],
        candidate: Dict[str, Any],
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> None: ...
    def load_candidate(
        self, experiment_id: str, artifact_digest: str
    ) -> Optional[Dict[str, Any]]: ...
    def load_latest_candidate(self, experiment_id: str) -> Optional[Dict[str, Any]]: ...
    def load_champion(self) -> Optional[Dict[str, Any]]: ...
    def promote_candidate(
        self,
        candidate: Dict[str, Any],
        *,
        authorities: Sequence[str],
        approval_mode: str,
        reviewer: Optional[str],
        stable_champion: bool,
        expected_champion_digest: Optional[str],
    ) -> Dict[str, Any]: ...


@dataclass(frozen=True)
class TrainingConfig:
    artifacts_bucket: str
    experiment_id: str
    release_contract_id: str
    release_cutoff_utc: str
    feature_vector_version: str
    deployment_git_sha: str = "unknown"
    deployment_template_sha256: str = "unknown"
    automatic_promotion_enabled: bool = False

    def __post_init__(self) -> None:
        if self.experiment_id != experiment.PRODUCTION_EXPERIMENT_ID:
            raise TrainingContractError(
                "MLB ML production requires the future-prospective r3 experiment ID"
            )
        if self.release_contract_id != experiment.PRODUCTION_RELEASE_CONTRACT_ID:
            raise TrainingContractError(
                "MLB ML production requires the future-prospective r3 release contract"
            )
        try:
            cutoff = datetime.fromisoformat(
                self.release_cutoff_utc.replace("Z", "+00:00")
            )
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=timezone.utc)
            cutoff_value = cutoff.astimezone(timezone.utc).isoformat()
        except Exception as exc:
            raise TrainingContractError("MLB ML release cutoff must be ISO-8601") from exc
        if cutoff_value != experiment.PRODUCTION_RELEASE_CUTOFF_UTC:
            raise TrainingContractError(
                "MLB ML r3 release cutoff must begin at the July 22 ET slate boundary"
            )
        for value, length, name in (
            (self.deployment_git_sha, 40, "git SHA"),
            (self.deployment_template_sha256, 64, "template SHA-256"),
        ):
            if len(str(value or "")) != length:
                raise TrainingContractError(f"MLB ML deployment {name} length is invalid")
            try:
                int(str(value), 16)
            except Exception as exc:
                raise TrainingContractError(
                    f"MLB ML deployment {name} must be hexadecimal"
                ) from exc

    @classmethod
    def from_env(cls) -> "TrainingConfig":
        required = {
            "artifacts_bucket": os.environ.get("MLB_ML_ARTIFACTS_BUCKET", ""),
            "experiment_id": os.environ.get("MLB_ML_EXPERIMENT_ID", ""),
            "release_contract_id": os.environ.get("MLB_ML_RELEASE_CONTRACT_ID", ""),
            "release_cutoff_utc": os.environ.get("MLB_ML_RELEASE_CUTOFF_UTC", ""),
            "feature_vector_version": os.environ.get(
                "MLB_ML_FEATURE_VECTOR_VERSION", ""
            ),
            "deployment_git_sha": os.environ.get("INQSI_DEPLOY_GIT_SHA", ""),
            "deployment_template_sha256": os.environ.get(
                "INQSI_DEPLOY_TEMPLATE_SHA256", ""
            ),
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise TrainingContractError(
                "missing required ML training configuration: " + ",".join(missing)
            )
        return cls(
            **required,
            automatic_promotion_enabled=os.environ.get(
                "INQSI_MLB_ML_AUTO_PROMOTE", "false"
            ).lower()
            in {"1", "true", "yes"},
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        if value != value or abs(value) == float("inf"):
            raise TrainingContractError("non-finite number cannot be stored")
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(key): _ddb_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_ddb_safe(item) for item in value]
    return value


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        _plain(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _status_fingerprint(status: Mapping[str, Any]) -> str:
    # DynamoDB stores Python floats as Decimal values. Canonicalize through the
    # same conversion before hashing so an integral float such as ``90.0``
    # verifies after DynamoDB returns it as Decimal("90.0") and ``_plain``
    # normalizes it to ``90``.
    fingerprint_payload = {
        key: value
        for key, value in status.items()
        if key not in {"statusFingerprint", "statusFingerprintVersion"}
    }
    return _sha256(_ddb_safe(fingerprint_payload))


def _sanitized_failure(exc: BaseException) -> Dict[str, str]:
    """Return a stable public-safe failure without persisting provider/AWS text."""
    if isinstance(exc, ExecutionLeaseUnavailable):
        code = "EXECUTION_LEASE_UNAVAILABLE"
        message = "MLB ML execution lease was unavailable"
    elif isinstance(exc, ConditionalStateConflict):
        code = "CONDITIONAL_STATE_CONFLICT"
        message = "MLB ML conditional state changed"
    elif isinstance(exc, TrainingContractError):
        code = "TRAINING_CONTRACT_ERROR"
        message = "MLB ML training contract validation failed"
    elif isinstance(exc, TimeoutError):
        code = "TIMEOUT"
        message = "MLB ML invocation timed out"
    elif isinstance(exc, PermissionError):
        code = "PERMISSION_DENIED"
        message = "MLB ML invocation lacked required permission"
    else:
        code = "INTERNAL_ERROR"
        message = "MLB ML invocation failed"
    return {
        "type": type(exc).__name__,
        "code": code,
        "message": message,
        "redacted": "true",
    }


def execution_concurrency_control(*, acquired_for_run: bool) -> Dict[str, Any]:
    return {
        "version": EXECUTION_LEASE_VERSION,
        "strategy": "dynamodb_mode_isolated_conditional_leases",
        "scope": "global_across_experiments_with_isolated_mutation_domains",
        "leasePartitionKey": EXECUTION_LEASE_PK,
        "migrationAnchorExperimentId": (
            EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID
        ),
        "leaseKeys": dict(EXECUTION_LEASE_KEY_BY_DOMAIN),
        "legacySharedLeaseSentinelKey": LEGACY_SHARED_EXECUTION_LEASE_SK,
        "legacySharedLeaseSentinelVersion": LEGACY_EXECUTION_SENTINEL_VERSION,
        "legacySharedLeaseSentinelRenewedOnAcquire": True,
        "legacySharedLeaseSentinelExpiresWithLease": True,
        "rollbackSelfRecoverySeconds": EXECUTION_LEASE_SECONDS,
        "bidirectionalLegacyRuntimeFence": True,
        "leaseSeconds": EXECUTION_LEASE_SECONDS,
        "protectedExecutionModes": list(EXECUTION_LEASE_PROTECTED_MODES),
        "modeLeaseDomains": dict(EXECUTION_LEASE_DOMAIN_BY_MODE),
        "trainingCannotBlockSelectionCapture": True,
        "selectionWriteManifestConditionCheck": True,
        "acquiredForRun": bool(acquired_for_run),
        "expiredLeaseReclaimEnabled": True,
        "ownerConditionalRelease": True,
        "reservedLambdaConcurrencyRequired": False,
    }


def _execution_lease_domain(execution_mode: str) -> str:
    mode = str(execution_mode or "").strip().lower()
    domain = EXECUTION_LEASE_DOMAIN_BY_MODE.get(mode)
    if not domain:
        raise TrainingContractError("execution lease mode is invalid")
    return domain


def _execution_lease_key(execution_mode: str) -> str:
    return EXECUTION_LEASE_KEY_BY_DOMAIN[_execution_lease_domain(execution_mode)]


def _parse_status_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validated_status_run_id(value: Any) -> str:
    run_id = str(value or "").strip()
    if not STATUS_RUN_ID_PATTERN.fullmatch(run_id):
        raise TrainingContractError("status runId is invalid")
    return run_id


def _experiment_pk(experiment_id: str) -> str:
    return f"{EXPERIMENT_PK_PREFIX}{experiment_id}"


def _selection_envelope_errors(item: Mapping[str, Any]) -> List[str]:
    value = _plain(dict(item or {}))
    data = value.get("data") or {}
    errors: List[str] = []
    if value.get("record_type") != SELECTION_RECORD_TYPE:
        errors.append("selection_envelope_record_type_mismatch")
    if not isinstance(data, dict) or not data:
        return sorted({*errors, "selection_envelope_data_missing"})
    experiment_id = str(data.get("experimentId") or "")
    identity = str(data.get("recordIdentity") or "")
    slate_date = str(data.get("slateDateEt") or "")
    expected_pk = _experiment_pk(experiment_id) if experiment_id else None
    expected_sk = (
        f"{SELECTION_SK_PREFIX}{slate_date}#"
        f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
        if identity and slate_date
        else None
    )
    if not expected_pk or value.get("PK") != expected_pk:
        errors.append("selection_envelope_pk_mismatch")
    if not expected_sk or value.get("SK") != expected_sk:
        errors.append("selection_envelope_sk_mismatch")
    for envelope_key, data_key in (
        ("artifactDigest", "challengerArtifactDigest"),
        ("decisionFingerprint", "decisionFingerprint"),
        ("recordFingerprint", "recordFingerprint"),
        ("created_at", "capturedAtUtc"),
    ):
        if value.get(envelope_key) != data.get(data_key):
            errors.append(f"selection_envelope_{envelope_key}_mismatch")
    try:
        expected_decision = experiment.selection_decision_fingerprint(data)
    except Exception:
        expected_decision = None
    if data.get("decisionFingerprint") != expected_decision:
        errors.append("selection_envelope_decision_fingerprint_invalid")
    try:
        expected_record = experiment.selection_record_fingerprint(data)
    except Exception:
        expected_record = None
    if data.get("recordFingerprint") != expected_record:
        errors.append("selection_envelope_record_fingerprint_invalid")
    return sorted(set(errors))


class AwsTrainingStore:
    def __init__(
        self,
        *,
        table_name: str,
        artifacts_bucket: str,
        dynamodb_resource: Any = None,
        s3_client: Any = None,
    ):
        if not table_name:
            raise TrainingContractError("SNAPSHOTS_TABLE is not configured")
        if not artifacts_bucket:
            raise TrainingContractError("MLB_ML_ARTIFACTS_BUCKET is not configured")
        if dynamodb_resource is None or s3_client is None:
            import boto3

            dynamodb_resource = dynamodb_resource or boto3.resource("dynamodb")
            s3_client = s3_client or boto3.client("s3")
        self.table = dynamodb_resource.Table(table_name)
        self.table_name = table_name
        self.s3 = s3_client
        self.bucket = artifacts_bucket
        self._versioning_verified = False

    def acquire_execution_lease(
        self,
        experiment_id: str,
        *,
        owner_token: str,
        execution_mode: str,
        acquired_at: datetime,
        lease_seconds: int,
    ) -> Dict[str, Any]:
        mode = str(execution_mode or "").strip().lower()
        owner = str(owner_token or "").strip()
        if mode not in EXECUTION_LEASE_PROTECTED_MODES:
            raise TrainingContractError("execution lease mode is invalid")
        if not owner:
            raise TrainingContractError("execution lease owner is required")
        if acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(tzinfo=timezone.utc)
        acquired = acquired_at.astimezone(timezone.utc)
        if lease_seconds != EXECUTION_LEASE_SECONDS:
            raise TrainingContractError(
                "execution lease duration does not match the production contract"
            )
        expires = acquired + timedelta(seconds=lease_seconds)
        lease_domain = _execution_lease_domain(mode)
        lease_key = _execution_lease_key(mode)
        item = _ddb_safe(
            {
                "PK": EXECUTION_LEASE_PK,
                "SK": lease_key,
                "record_type": EXECUTION_LEASE_RECORD_TYPE,
                "version": EXECUTION_LEASE_VERSION,
                "lease_domain": lease_domain,
                "experiment_id": experiment_id,
                "lease_owner": owner,
                "execution_mode": mode,
                "acquired_at": acquired.isoformat(),
                "lease_expires_at": expires.isoformat(),
                "lease_expires_at_epoch": int(expires.timestamp()),
            }
        )
        sentinel = _ddb_safe(
            {
                "PK": EXECUTION_LEASE_PK,
                "SK": LEGACY_SHARED_EXECUTION_LEASE_SK,
                "record_type": LEGACY_EXECUTION_SENTINEL_RECORD_TYPE,
                "version": LEGACY_EXECUTION_SENTINEL_VERSION,
                "experiment_id": EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID,
                "lease_owner": LEGACY_EXECUTION_SENTINEL_OWNER,
                "execution_mode": LEGACY_EXECUTION_SENTINEL_MODE,
                "acquired_at": acquired.isoformat(),
                "lease_expires_at": expires.isoformat(),
                "lease_expires_at_epoch": int(expires.timestamp()),
            }
        )
        domain_condition = (
            "attribute_not_exists(PK) OR ("
            "record_type = :record_type AND version = :version "
            "AND lease_domain = :lease_domain "
            "AND attribute_type(lease_expires_at_epoch, :number_type) "
            "AND lease_expires_at_epoch <= :now)"
        )
        sentinel_condition = (
            "attribute_not_exists(PK) OR ("
            "record_type = :legacy_record_type "
            "AND attribute_not_exists(version) "
            "AND attribute_type(lease_expires_at_epoch, :number_type) "
            "AND lease_expires_at_epoch <= :now) OR ("
            "record_type = :sentinel_record_type "
            "AND version = :sentinel_version "
            "AND lease_owner = :sentinel_owner "
            "AND execution_mode = :sentinel_mode "
            "AND experiment_id = :migration_anchor "
            "AND attribute_type(lease_expires_at_epoch, :number_type))"
        )
        try:
            self.table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": item,
                            "ConditionExpression": domain_condition,
                            "ExpressionAttributeValues": _ddb_safe(
                                {
                                    ":now": int(acquired.timestamp()),
                                    ":number_type": "N",
                                    ":record_type": EXECUTION_LEASE_RECORD_TYPE,
                                    ":version": EXECUTION_LEASE_VERSION,
                                    ":lease_domain": lease_domain,
                                }
                            ),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": sentinel,
                            "ConditionExpression": sentinel_condition,
                            "ExpressionAttributeValues": _ddb_safe(
                                {
                                    ":now": int(acquired.timestamp()),
                                    ":number_type": "N",
                                    ":legacy_record_type": EXECUTION_LEASE_RECORD_TYPE,
                                    ":sentinel_record_type": (
                                        LEGACY_EXECUTION_SENTINEL_RECORD_TYPE
                                    ),
                                    ":sentinel_version": (
                                        LEGACY_EXECUTION_SENTINEL_VERSION
                                    ),
                                    ":sentinel_owner": (
                                        LEGACY_EXECUTION_SENTINEL_OWNER
                                    ),
                                    ":sentinel_mode": (
                                        LEGACY_EXECUTION_SENTINEL_MODE
                                    ),
                                    ":migration_anchor": (
                                        EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID
                                    ),
                                }
                            ),
                        }
                    },
                ]
            )
        except Exception as exc:
            code = str(
                ((getattr(exc, "response", {}) or {}).get("Error") or {}).get(
                    "Code"
                )
                or ""
            )
            if code == "TransactionCanceledException":
                try:
                    legacy = _plain(
                        self.table.get_item(
                            Key={
                                "PK": EXECUTION_LEASE_PK,
                                "SK": LEGACY_SHARED_EXECUTION_LEASE_SK,
                            },
                            ConsistentRead=True,
                        ).get("Item")
                        or {}
                    )
                    domain_item = _plain(
                        self.table.get_item(
                            Key={"PK": EXECUTION_LEASE_PK, "SK": lease_key},
                            ConsistentRead=True,
                        ).get("Item")
                        or {}
                    )
                except Exception as read_exc:
                    raise TrainingContractError(
                        "execution lease contention state could not be read"
                    ) from read_exc

                now_epoch = int(acquired.timestamp())
                legacy_is_old = bool(
                    legacy.get("record_type") == EXECUTION_LEASE_RECORD_TYPE
                    and "version" not in legacy
                )
                legacy_is_sentinel = bool(
                    legacy.get("record_type")
                    == LEGACY_EXECUTION_SENTINEL_RECORD_TYPE
                    and legacy.get("version") == LEGACY_EXECUTION_SENTINEL_VERSION
                    and legacy.get("lease_owner")
                    == LEGACY_EXECUTION_SENTINEL_OWNER
                    and legacy.get("execution_mode")
                    == LEGACY_EXECUTION_SENTINEL_MODE
                    and legacy.get("experiment_id")
                    == EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID
                )
                if legacy and not (legacy_is_old or legacy_is_sentinel):
                    raise TrainingContractError(
                        "legacy execution lease migration sentinel is invalid"
                    ) from exc
                if legacy and not isinstance(
                    legacy.get("lease_expires_at_epoch"), int
                ):
                    raise TrainingContractError(
                        "legacy execution lease expiry is invalid"
                    ) from exc

                domain_is_current = bool(
                    domain_item.get("record_type") == EXECUTION_LEASE_RECORD_TYPE
                    and domain_item.get("version") == EXECUTION_LEASE_VERSION
                    and domain_item.get("lease_domain") == lease_domain
                )
                if domain_item and not domain_is_current:
                    raise TrainingContractError(
                        "execution lease domain record is invalid"
                    ) from exc
                if domain_item and not isinstance(
                    domain_item.get("lease_expires_at_epoch"), int
                ):
                    raise TrainingContractError(
                        "execution lease domain expiry is invalid"
                    ) from exc

                legacy_active = bool(
                    legacy_is_old
                    and int(legacy.get("lease_expires_at_epoch") or 0) > now_epoch
                )
                domain_active = bool(
                    domain_is_current
                    and int(domain_item.get("lease_expires_at_epoch") or 0)
                    > now_epoch
                )
                if legacy_active or domain_active:
                    raise ExecutionLeaseUnavailable(
                        EXECUTION_LEASE_UNAVAILABLE_MESSAGE
                    ) from exc
                # A concurrent v2 transaction can race only on the renewable
                # sentinel while targeting a different domain. Treat that
                # short transaction conflict as retryable lease contention.
                raise ExecutionLeaseUnavailable(
                    EXECUTION_LEASE_UNAVAILABLE_MESSAGE
                ) from exc
            raise
        return _plain(item)

    def release_execution_lease(
        self,
        experiment_id: str,
        *,
        owner_token: str,
        execution_mode: str,
    ) -> None:
        owner = str(owner_token or "").strip()
        if not owner:
            raise TrainingContractError("execution lease owner is required")
        lease_domain = _execution_lease_domain(execution_mode)
        lease_key = _execution_lease_key(execution_mode)
        try:
            self.table.delete_item(
                Key={"PK": EXECUTION_LEASE_PK, "SK": lease_key},
                ConditionExpression=(
                    "lease_owner = :owner AND record_type = :record_type "
                    "AND version = :version AND lease_domain = :lease_domain"
                ),
                ExpressionAttributeValues={
                    ":owner": owner,
                    ":record_type": EXECUTION_LEASE_RECORD_TYPE,
                    ":version": EXECUTION_LEASE_VERSION,
                    ":lease_domain": lease_domain,
                },
            )
        except Exception as exc:
            code = str(
                ((getattr(exc, "response", {}) or {}).get("Error") or {}).get(
                    "Code"
                )
                or ""
            )
            if code == "ConditionalCheckFailedException":
                raise ConditionalStateConflict(
                    "execution lease ownership changed before release"
                ) from exc
            raise

    def _get_data(self, key: Dict[str, str]) -> Optional[Dict[str, Any]]:
        item = self.table.get_item(Key=key, ConsistentRead=True).get("Item") or {}
        data = _plain(item.get("data") or {})
        return data if isinstance(data, dict) and data else None

    def load_manifest(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        return self._get_data({"PK": _experiment_pk(experiment_id), "SK": MANIFEST_SK})

    def save_manifest(
        self,
        manifest: Dict[str, Any],
        *,
        expected_revision: Optional[int],
        expected_digest: Optional[str],
    ) -> None:
        item = _ddb_safe(
            {
                "PK": _experiment_pk(str(manifest["experimentId"])),
                "SK": MANIFEST_SK,
                "record_type": "mlb_ml_experiment_manifest_v2",
                "revision": int(manifest["revision"]),
                "manifestDigest": manifest["manifestDigest"],
                "updated_at": manifest.get("updatedAtUtc")
                or manifest.get("createdAtUtc"),
                "data": manifest,
            }
        )
        kwargs: Dict[str, Any] = {"Item": item}
        if expected_revision is None:
            kwargs["ConditionExpression"] = (
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            )
        else:
            kwargs.update(
                {
                    "ConditionExpression": (
                        "revision = :revision AND manifestDigest = :digest"
                    ),
                    "ExpressionAttributeValues": _ddb_safe(
                        {
                            ":revision": expected_revision,
                            ":digest": expected_digest,
                        }
                    ),
                }
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
                raise ConditionalStateConflict("manifest compare-and-swap failed") from exc
            raise

    def _ensure_versioning(self) -> None:
        if self._versioning_verified:
            return
        status = self.s3.get_bucket_versioning(Bucket=self.bucket).get("Status")
        if status != "Enabled":
            raise TrainingContractError("ML artifact bucket versioning must be Enabled")
        self._versioning_verified = True

    def put_versioned_json(self, key: str, payload: Any) -> Dict[str, Any]:
        self._ensure_versioning()
        body = _json_bytes(payload)
        sha = hashlib.sha256(body).hexdigest()
        try:
            existing = self.s3.head_object(Bucket=self.bucket, Key=key)
        except Exception:
            existing = {}
        if (
            str((existing.get("Metadata") or {}).get("sha256") or "") == sha
            and existing.get("VersionId")
        ):
            return {
                "bucket": self.bucket,
                "key": key,
                "versionId": existing["VersionId"],
                "sha256": sha,
                "byteLength": len(body),
                "contentType": "application/json",
            }
        response = self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            Metadata={"sha256": sha},
        )
        version_id = response.get("VersionId")
        if not version_id:
            raise TrainingContractError(
                f"versioned S3 write returned no VersionId for {key}"
            )
        head = self.s3.head_object(
            Bucket=self.bucket, Key=key, VersionId=version_id
        )
        if (
            str((head.get("Metadata") or {}).get("sha256") or "") != sha
            or head.get("ContentLength") != len(body)
        ):
            raise TrainingContractError(f"S3 artifact verification failed for {key}")
        return {
            "bucket": self.bucket,
            "key": key,
            "versionId": version_id,
            "sha256": sha,
            "byteLength": len(body),
            "contentType": "application/json",
        }

    def read_versioned_json(self, artifact: Mapping[str, Any]) -> Dict[str, Any]:
        self._ensure_versioning()
        bucket = str(artifact.get("bucket") or "")
        key = str(artifact.get("key") or "")
        version_id = str(artifact.get("versionId") or "")
        expected_sha = str(artifact.get("sha256") or "")
        if (
            bucket != self.bucket
            or not key
            or not version_id
            or len(expected_sha) != 64
        ):
            raise TrainingContractError("complete in-bucket versioned artifact pointer is required")
        response = self.s3.get_object(
            Bucket=bucket,
            Key=key,
            VersionId=version_id,
        )
        body = response["Body"].read()
        actual_sha = hashlib.sha256(body).hexdigest()
        metadata_sha = str((response.get("Metadata") or {}).get("sha256") or "")
        if actual_sha != expected_sha or metadata_sha != expected_sha:
            raise TrainingContractError("versioned S3 artifact checksum mismatch")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TrainingContractError("challenger artifact must be a JSON object")
        return payload

    def record_selection(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        identity = str(entry.get("recordIdentity") or "")
        slate_date = str(entry.get("slateDateEt") or "")
        experiment_id = str(entry.get("experimentId") or "")
        if not identity or not slate_date or not experiment_id:
            raise TrainingContractError("selection identity, slate date, and experiment are required")
        if entry.get("idempotencyFingerprintVersion") != (
            experiment.SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION
        ):
            raise TrainingContractError(
                "new prospective selections require current semantic idempotency"
            )
        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        item = _ddb_safe(
            {
                "PK": _experiment_pk(experiment_id),
                "SK": f"{SELECTION_SK_PREFIX}{slate_date}#{identity_hash}",
                "record_type": SELECTION_RECORD_TYPE,
                "artifactDigest": entry.get("challengerArtifactDigest"),
                "decisionFingerprint": entry.get("decisionFingerprint"),
                "recordFingerprint": entry.get("recordFingerprint"),
                "created_at": entry.get("capturedAtUtc"),
                "data": entry,
            }
        )
        incoming_errors = _selection_envelope_errors(item)
        if incoming_errors:
            raise TrainingContractError(
                "prospective selection entry is invalid: "
                + ",".join(incoming_errors)
            )

        def existing_result(
            current_manifest: Mapping[str, Any],
        ) -> Optional[Dict[str, Any]]:
            existing_item = _plain(
                self.table.get_item(
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    ConsistentRead=True,
                ).get("Item")
                or {}
            )
            if not existing_item:
                return None
            existing_errors = _selection_envelope_errors(existing_item)
            if existing_errors:
                raise TrainingContractError(
                    "immutable prospective selection readback is invalid: "
                    + ",".join(existing_errors)
                )
            existing = _plain(existing_item.get("data") or {})
            existing_contract_errors = experiment.selection_ledger_validation_errors(
                existing,
                current_manifest,
                challenger_artifact_digest=str(
                    (current_manifest.get("frozenChallenger") or {}).get(
                        "artifactDigest"
                    )
                    or ""
                ),
            )
            if existing_contract_errors:
                raise TrainingContractError(
                    "immutable prospective selection contract is invalid: "
                    + ",".join(existing_contract_errors)
                )
            existing_idempotency_version = existing.get(
                "idempotencyFingerprintVersion"
            )
            incoming_idempotency_version = entry.get(
                "idempotencyFingerprintVersion"
            )
            same_semantics = False
            if existing_idempotency_version == incoming_idempotency_version:
                same_semantics = existing.get("idempotencyFingerprint") == entry.get(
                    "idempotencyFingerprint"
                )
            elif existing_idempotency_version == (
                experiment.SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V1
            ):
                same_semantics = (
                    experiment.selection_semantic_fingerprint(existing)
                    == experiment.selection_semantic_fingerprint(entry)
                )
            if not same_semantics:
                raise ConditionalStateConflict(
                    "immutable prospective selection changed"
                )
            return {
                "ok": True,
                "created": False,
                "PK": item["PK"],
                "SK": item["SK"],
                "capturedAtUtc": existing.get("capturedAtUtc"),
                "decisionFingerprint": existing.get("decisionFingerprint"),
                "idempotencyFingerprint": existing.get(
                    "idempotencyFingerprint"
                ),
                "recordFingerprint": existing.get("recordFingerprint"),
            }

        manifest = self.load_manifest(experiment_id)
        if not manifest:
            raise TrainingContractError(
                "persisted experiment manifest is required before selection write"
            )
        expected_revision = manifest.get("revision")
        expected_digest = str(manifest.get("manifestDigest") or "")
        if not isinstance(expected_revision, int) or not expected_digest:
            raise TrainingContractError(
                "persisted experiment manifest revision and digest are required"
            )
        if entry.get("experimentManifestDigest") != expected_digest:
            raise ManifestStateConflict(
                "experiment manifest changed before prospective selection write"
            )
        contract_errors = experiment.selection_ledger_validation_errors(
            entry,
            manifest,
            challenger_artifact_digest=str(
                (manifest.get("frozenChallenger") or {}).get("artifactDigest") or ""
            ),
        )
        if contract_errors:
            raise TrainingContractError(
                "prospective selection contract is invalid: "
                + ",".join(contract_errors)
            )
        existing = existing_result(manifest)
        if existing is not None:
            return existing
        try:
            self.table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "ConditionCheck": {
                            "TableName": self.table_name,
                            "Key": _ddb_safe(
                                {
                                    "PK": _experiment_pk(experiment_id),
                                    "SK": MANIFEST_SK,
                                }
                            ),
                            "ConditionExpression": (
                                "revision = :revision AND manifestDigest = :digest"
                            ),
                            "ExpressionAttributeValues": _ddb_safe(
                                {
                                    ":revision": expected_revision,
                                    ":digest": expected_digest,
                                }
                            ),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _ddb_safe(item),
                            "ConditionExpression": (
                                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
                            ),
                        }
                    },
                ]
            )
            return {
                "ok": True,
                "created": True,
                "PK": item["PK"],
                "SK": item["SK"],
                "capturedAtUtc": entry.get("capturedAtUtc"),
                "decisionFingerprint": entry.get("decisionFingerprint"),
                "idempotencyFingerprint": entry.get("idempotencyFingerprint"),
                "recordFingerprint": entry.get("recordFingerprint"),
            }
        except Exception as exc:
            code = str(
                ((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code")
                or ""
            )
            if code not in {
                "ConditionalCheckFailedException",
                "TransactionCanceledException",
            }:
                raise
            current_manifest = self.load_manifest(experiment_id)
            if not current_manifest:
                raise ManifestStateConflict(
                    "experiment manifest disappeared during selection write"
                ) from exc
            existing = existing_result(current_manifest)
            if existing is not None:
                return existing
            raise ManifestStateConflict(
                "experiment manifest changed before prospective selection write"
            ) from exc

    def list_selections(self, experiment_id: str) -> List[Dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        manifest = self.load_manifest(experiment_id)
        if not manifest:
            raise TrainingContractError(
                "persisted experiment manifest is required before selection read"
            )
        challenger_digest = str(
            (manifest.get("frozenChallenger") or {}).get("artifactDigest") or ""
        )
        values: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {
            "KeyConditionExpression": (
                Key("PK").eq(_experiment_pk(experiment_id))
                & Key("SK").begins_with(SELECTION_SK_PREFIX)
            ),
            "ConsistentRead": True,
        }
        while True:
            response = self.table.query(**kwargs)
            for item in response.get("Items") or []:
                plain_item = _plain(item)
                errors = _selection_envelope_errors(plain_item)
                if errors:
                    raise TrainingContractError(
                        "prospective selection ledger readback is invalid: "
                        + ",".join(errors)
                    )
                data = _plain(plain_item.get("data") or {})
                if isinstance(data, dict) and data:
                    contract_errors = experiment.selection_ledger_validation_errors(
                        data,
                        manifest,
                        challenger_artifact_digest=challenger_digest,
                    )
                    if contract_errors:
                        raise TrainingContractError(
                            "prospective selection ledger contract is invalid: "
                            + ",".join(contract_errors)
                        )
                    values.append(data)
            last = response.get("LastEvaluatedKey")
            if not last:
                break
            kwargs["ExclusiveStartKey"] = last
        return sorted(
            values,
            key=lambda value: (
                str(value.get("slateDateEt") or ""),
                str(value.get("recordIdentity") or ""),
            ),
        )

    def save_status(self, experiment_id: str, status: Dict[str, Any]) -> None:
        run_id = _validated_status_run_id(status.get("runId"))
        if status.get("experimentId") != experiment_id:
            raise TrainingContractError("status experiment identity mismatch")
        if status.get("statusFingerprintVersion") != STATUS_FINGERPRINT_VERSION:
            raise TrainingContractError("status fingerprint version mismatch")
        if status.get("statusFingerprint") != _status_fingerprint(status):
            raise TrainingContractError("status fingerprint mismatch")
        pk = _experiment_pk(experiment_id)
        base = {
            "PK": pk,
            "record_type": "mlb_ml_aws_training_status_v2",
            "created_at": status.get("createdAtUtc"),
            "runId": run_id,
            "data": status,
        }
        mode = str(status.get("executionMode") or "").strip().lower()
        mode_sk = {
            "training": STATUS_LATEST_TRAINING_SK,
            "selection_capture": STATUS_LATEST_SELECTION_CAPTURE_SK,
        }.get(mode)
        if not mode_sk:
            raise TrainingContractError("status executionMode is invalid")
        run_sk = f"{STATUS_RUN_SK_PREFIX}{run_id}"
        created = _parse_status_datetime(status.get("createdAtUtc"))
        if created is None:
            raise TrainingContractError("status createdAtUtc is invalid")

        for _attempt in range(3):
            existing_run = self._get_data({"PK": pk, "SK": run_sk})
            if existing_run and existing_run != _plain(status):
                raise ConditionalStateConflict("immutable training run status changed")

            transaction: List[Dict[str, Any]] = []
            if not existing_run:
                transaction.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _ddb_safe({**base, "SK": run_sk}),
                            "ConditionExpression": (
                                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
                            ),
                        }
                    }
                )
            for latest_sk in (STATUS_LATEST_SK, mode_sk):
                latest = self._get_data({"PK": pk, "SK": latest_sk})
                latest_at = _parse_status_datetime((latest or {}).get("createdAtUtc"))
                if latest_at is not None and latest_at > created:
                    continue
                transaction.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _ddb_safe({**base, "SK": latest_sk}),
                            "ConditionExpression": (
                                "attribute_not_exists(PK) OR created_at <= :created"
                            ),
                            "ExpressionAttributeValues": _ddb_safe(
                                {":created": status["createdAtUtc"]}
                            ),
                        }
                    }
                )
            if not transaction:
                return
            try:
                self.table.meta.client.transact_write_items(
                    TransactItems=transaction
                )
                return
            except Exception as exc:
                code = str(
                    ((getattr(exc, "response", {}) or {}).get("Error") or {}).get(
                        "Code"
                    )
                    or ""
                )
                if code not in {
                    "ConditionalCheckFailedException",
                    "TransactionCanceledException",
                }:
                    raise
        raise ConditionalStateConflict(
            "training status transaction could not establish immutable state"
        )

    def load_latest_status(
        self, experiment_id: str, execution_mode: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        mode = str(execution_mode or "").strip().lower()
        sk = {
            "training": STATUS_LATEST_TRAINING_SK,
            "selection_capture": STATUS_LATEST_SELECTION_CAPTURE_SK,
        }.get(mode, STATUS_LATEST_SK)
        return self._get_data({"PK": _experiment_pk(experiment_id), "SK": sk})

    def load_status_run(
        self, experiment_id: str, run_id: str
    ) -> Optional[Dict[str, Any]]:
        normalized = _validated_status_run_id(run_id)
        return self._get_data(
            {
                "PK": _experiment_pk(experiment_id),
                "SK": f"{STATUS_RUN_SK_PREFIX}{normalized}",
            }
        )

    def commit_candidate(
        self,
        manifest: Dict[str, Any],
        candidate: Dict[str, Any],
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> None:
        pk = _experiment_pk(str(manifest["experimentId"]))
        manifest_item = {
            "PK": pk,
            "SK": MANIFEST_SK,
            "record_type": "mlb_ml_experiment_manifest_v2",
            "revision": int(manifest["revision"]),
            "manifestDigest": manifest["manifestDigest"],
            "updated_at": manifest.get("updatedAtUtc")
            or manifest.get("prospectiveTestEvaluatedAtUtc"),
            "data": manifest,
        }
        candidate_item = {
            "PK": pk,
            "SK": f"{CANDIDATE_SK_PREFIX}{candidate['artifactDigest']}",
            "record_type": "mlb_ml_candidate_v2",
            "artifactDigest": candidate["artifactDigest"],
            "created_at": candidate.get("createdAtUtc"),
            "data": candidate,
        }
        latest_item = {
            "PK": pk,
            "SK": CANDIDATE_LATEST_SK,
            "record_type": "mlb_ml_candidate_latest_v2",
            "artifactDigest": candidate["artifactDigest"],
            "created_at": candidate.get("createdAtUtc"),
            "data": candidate,
        }
        client = self.table.meta.client
        try:
            client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _ddb_safe(manifest_item),
                            "ConditionExpression": (
                                "revision = :revision AND manifestDigest = :digest"
                            ),
                            "ExpressionAttributeValues": _ddb_safe(
                                {
                                    ":revision": expected_revision,
                                    ":digest": expected_digest,
                                }
                            ),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _ddb_safe(candidate_item),
                            "ConditionExpression": (
                                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
                            ),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _ddb_safe(latest_item),
                        }
                    },
                ]
            )
        except Exception as exc:
            code = str(
                ((getattr(exc, "response", {}) or {}).get("Error") or {}).get(
                    "Code"
                )
                or ""
            )
            if code in {
                "ConditionalCheckFailedException",
                "TransactionCanceledException",
            }:
                raise ConditionalStateConflict(
                    "candidate/manifest transaction lost its expected state"
                ) from exc
            raise

    def load_candidate(
        self, experiment_id: str, artifact_digest: str
    ) -> Optional[Dict[str, Any]]:
        return self._get_data(
            {
                "PK": _experiment_pk(experiment_id),
                "SK": f"{CANDIDATE_SK_PREFIX}{artifact_digest}",
            }
        )

    def load_latest_candidate(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        return self._get_data(
            {"PK": _experiment_pk(experiment_id), "SK": CANDIDATE_LATEST_SK}
        )

    def load_champion(self) -> Optional[Dict[str, Any]]:
        return self._get_data({"PK": CHAMPION_PK, "SK": CHAMPION_SK})

    def promote_candidate(
        self,
        candidate: Dict[str, Any],
        *,
        authorities: Sequence[str],
        approval_mode: str,
        reviewer: Optional[str],
        stable_champion: bool,
        expected_champion_digest: Optional[str],
    ) -> Dict[str, Any]:
        allowed = sorted({str(value) for value in authorities})
        champion = {
            "version": VERSION,
            "recordType": "mlb_ml_approved_shadow_champion_v2",
            "artifactDigest": candidate["artifactDigest"],
            "experimentId": candidate["experimentId"],
            "experimentManifestDigest": candidate["experimentManifestDigest"],
            "artifactBundle": copy.deepcopy(candidate["artifacts"]),
            "deploymentIdentity": copy.deepcopy(
                candidate.get("deploymentIdentity") or {}
            ),
            "directionApproved": "direction" in allowed,
            "playabilityApproved": "playability" in allowed,
            "stableChampionApproved": bool(stable_champion),
            "directionAuthorityEnabled": False,
            "playabilityAuthorityEnabled": False,
            "stableChampion": False,
            "shadowOnly": True,
            "runtimeIntegrationRequired": True,
            "runtimeAuthorityActivated": False,
            "approvalStatus": (
                "APPROVED_SHADOW_CHAMPION_AWAITING_V2_RUNTIME_INTEGRATION"
            ),
            "approvalMode": approval_mode,
            "reviewer": reviewer,
            "approvedAtUtc": datetime.now(timezone.utc).isoformat(),
            "promotionGate": copy.deepcopy(candidate["promotionGate"]),
        }
        item = _ddb_safe(
            {
                "PK": CHAMPION_PK,
                "SK": CHAMPION_SK,
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
                raise ConditionalStateConflict("champion compare-and-swap failed") from exc
            raise
        return _plain(champion)


def _daterange(start: str, end: str) -> Iterable[str]:
    current = datetime.fromisoformat(start).date()
    final = datetime.fromisoformat(end).date()
    while current <= final:
        yield current.isoformat()
        current += timedelta(days=1)


class CanonicalTrainingRows(list):
    """List-compatible canonical rows with chronological slate proof attached."""

    def __init__(self, rows: Iterable[Dict[str, Any]], continuity: Dict[str, Any]):
        super().__init__(rows)
        self.continuity = copy.deepcopy(continuity)


def _contiguous_finalized_slate_prefix(
    slate_dates: Iterable[str],
    *,
    official_schedule_loader: Callable[[str], Dict[str, Any]],
    slate_finalization_loader: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    expected_schedule_source: Optional[str] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Stop at the first unresolved date and cross only proven official off-days."""
    game_dates: List[str] = []
    zero_game_dates: List[str] = []
    processed: List[str] = []
    finalized_slate_authorities: Dict[str, Dict[str, Any]] = {}
    blocked_date: Optional[str] = None
    blocker: Optional[str] = None
    for slate_date in slate_dates:
        try:
            official = official_schedule_loader(slate_date)
            count = official.get("officialGameCount")
            final_count = official.get("officialFinalCount")
            games = official.get("games")
            official_pks = [
                str(game.get("officialGamePk") or "")
                for game in games or []
                if isinstance(game, dict)
            ]
            if (
                official.get("ok") is not True
                or str(official.get("slateDateEt") or "") != slate_date
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or isinstance(final_count, bool)
                or not isinstance(final_count, int)
                or not 0 <= final_count <= count
                or not isinstance(games, list)
                or len(games) != count
                or len(official_pks) != count
                or any(not value for value in official_pks)
                or len(set(official_pks)) != count
                or any(
                    str(game.get("officialDate") or "") != slate_date
                    for game in games
                )
                or (
                    expected_schedule_source is not None
                    and official.get("source") != expected_schedule_source
                )
                or not str(official.get("sourceUrl") or "")
            ):
                raise TrainingContractError("official schedule proof is not exact")
        except Exception as exc:
            blocked_date = slate_date
            blocker = f"OFFICIAL_SCHEDULE_UNPROVEN:{type(exc).__name__}:{exc}"
            break
        if count == 0:
            zero_game_dates.append(slate_date)
            processed.append(slate_date)
            continue
        try:
            if final_count != count:
                raise TrainingContractError("official game slate is not fully FINAL")
            finalized = slate_finalization_loader(slate_date, official)
            diagnostics = finalized.get("slates") or []
            date_diagnostic = next(
                (
                    item
                    for item in diagnostics
                    if isinstance(item, dict)
                    and str(item.get("slateDateEt") or "") == slate_date
                ),
                {},
            )
            if (
                finalized.get("ok") is not True
                or finalized.get("requestedSlateDates") != [slate_date]
                or finalized.get("finalizedSlateDates") != [slate_date]
                or date_diagnostic.get("slateFinalized") is not True
                or isinstance(date_diagnostic.get("officialGameCount"), bool)
                or date_diagnostic.get("officialGameCount") != count
            ):
                raise TrainingContractError("official game slate is not fully finalized")
        except Exception as exc:
            blocked_date = slate_date
            blocker = f"OFFICIAL_SLATE_UNRESOLVED:{type(exc).__name__}:{exc}"
            break
        finalized_slate_authorities[slate_date] = (
            experiment.build_official_finalized_slate_authority(
                slate_date_et=slate_date,
                official_game_pks=official_pks,
                schedule_source=str(official.get("source") or ""),
                schedule_source_url=str(official.get("sourceUrl") or ""),
            )
        )
        game_dates.append(slate_date)
        processed.append(slate_date)
    return game_dates, {
        "ok": blocked_date is None,
        "version": "MLB-ML-CANONICAL-SLATE-CONTINUITY-v2-exact-official-game-set",
        "processedSlateDates": processed,
        "processedThroughSlateDate": processed[-1] if processed else None,
        "provenZeroGameSlateDates": zero_game_dates,
        "finalizedGameSlateDates": game_dates,
        "finalizedSlateAuthorities": finalized_slate_authorities,
        "blockedSlateDate": blocked_date,
        "blocker": blocker,
        "policy": (
            "Training stops at the first unresolved official slate; only an exact "
            "official zero-game schedule may be crossed as an off-day."
        ),
    }


def load_canonical_training_rows(
    config: TrainingConfig,
    *,
    now: Optional[datetime] = None,
    official_schedule_loader: Optional[Callable[[str], Dict[str, Any]]] = None,
    slate_finalization_loader: Optional[
        Callable[[str, Dict[str, Any]], Dict[str, Any]]
    ] = None,
) -> List[Dict[str, Any]]:
    """Read only rows authorized by the canonical full-slate FINAL proof.

    A calendar date being earlier than today in ET is not sufficient evidence
    that its slate has finished. The shared label authority verifies the exact
    official schedule, terminal coverage, immutable locks, and write-once
    labels before it marks a slate finalized. Partial prior-date slates never
    reach the experiment manifest.
    """
    import mlb_canonical_final_labels_v1 as labels

    outcomes_name = os.environ.get("OUTCOMES_TABLE", "")
    snapshots_name = os.environ.get("SNAPSHOTS_TABLE", "")
    if not outcomes_name or not snapshots_name:
        raise TrainingContractError(
            "OUTCOMES_TABLE and SNAPSHOTS_TABLE are required for AWS training"
        )
    cutoff = datetime.fromisoformat(
        config.release_cutoff_utc.replace("Z", "+00:00")
    )
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    current = (now or datetime.now(timezone.utc)).astimezone(SLATE_TZ)
    last_complete = current.date() - timedelta(days=1)
    start_date = cutoff.astimezone(SLATE_TZ).date().isoformat()
    requested_dates = list(_daterange(start_date, last_complete.isoformat()))
    if last_complete.isoformat() < start_date:
        requested_dates = []
    reports: Dict[str, Dict[str, Any]] = {}

    def default_finalization_loader(
        slate_date: str, official: Dict[str, Any]
    ) -> Dict[str, Any]:
        def exact_official_fetcher(requested: str) -> Dict[str, Any]:
            if requested != slate_date:
                raise TrainingContractError("unexpected official schedule date")
            return copy.deepcopy(official)

        return labels.load_canonical_training_rows(
            slate_date=slate_date,
            official_fetcher=exact_official_fetcher,
        )

    finalization_loader = slate_finalization_loader or default_finalization_loader

    def retaining_finalization_loader(
        slate_date: str, official: Dict[str, Any]
    ) -> Dict[str, Any]:
        report = finalization_loader(slate_date, official)
        if not isinstance(report, dict):
            raise TrainingContractError(
                "canonical full-slate label authority returned an invalid response"
            )
        reports[slate_date] = copy.deepcopy(report)
        return report

    finalized_dates, continuity = _contiguous_finalized_slate_prefix(
        requested_dates,
        official_schedule_loader=(
            official_schedule_loader or labels.fetch_official_schedule
        ),
        slate_finalization_loader=retaining_finalization_loader,
        expected_schedule_source=labels.SOURCE,
    )
    rows = [
        copy.deepcopy(row)
        for slate_date in finalized_dates
        for row in reports[slate_date].get("rows") or []
        if isinstance(row, dict)
        and row.get("slateFinalized") is True
        and str(row.get("slateDateEt") or "") == slate_date
    ]
    return CanonicalTrainingRows(
        sorted(
            rows,
            key=lambda row: (
                str(row.get("slateDateEt") or ""),
                str(row.get("commenceTime") or ""),
                str(row.get("gameId") or ""),
            ),
        ),
        continuity,
    )


class TrainingService:
    def __init__(
        self,
        store: TrainingStore,
        config: TrainingConfig,
        *,
        row_loader: Callable[[TrainingConfig], List[Dict[str, Any]]] = (
            load_canonical_training_rows
        ),
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.store = store
        self.config = config
        self.row_loader = row_loader
        self.now = now
        self._execution_lease_acquired_for_run = False
        self._execution_lease_context: Optional[Dict[str, Any]] = None
        self._selection_capture_before_training: Optional[Dict[str, Any]] = None
        self._scheduled_selection_capture_status: Optional[Dict[str, Any]] = None

    def attest_execution_lease_acquired(
        self,
        lease: Mapping[str, Any],
        *,
        owner_token: str,
        execution_mode: str,
    ) -> None:
        mode = str(execution_mode or "").strip().lower()
        owner = str(owner_token or "").strip()
        expected_pk = EXECUTION_LEASE_PK
        errors: List[str] = []
        if self._execution_lease_context is not None:
            errors.append("service_already_has_execution_lease")
        if mode not in EXECUTION_LEASE_PROTECTED_MODES:
            errors.append("execution_mode_invalid")
        if not owner:
            errors.append("owner_token_missing")
        if str(lease.get("PK") or "") != expected_pk:
            errors.append("lease_partition_key_mismatch")
        if str(lease.get("experiment_id") or "") != self.config.experiment_id:
            errors.append("experiment_id_mismatch")
        expected_domain = (
            _execution_lease_domain(mode)
            if mode in EXECUTION_LEASE_PROTECTED_MODES
            else ""
        )
        expected_key = (
            _execution_lease_key(mode)
            if mode in EXECUTION_LEASE_PROTECTED_MODES
            else ""
        )
        if str(lease.get("SK") or "") != expected_key:
            errors.append("lease_key_mismatch")
        if str(lease.get("record_type") or "") != EXECUTION_LEASE_RECORD_TYPE:
            errors.append("record_type_mismatch")
        if str(lease.get("version") or "") != EXECUTION_LEASE_VERSION:
            errors.append("version_mismatch")
        if str(lease.get("lease_domain") or "") != expected_domain:
            errors.append("lease_domain_mismatch")
        if str(lease.get("lease_owner") or "") != owner:
            errors.append("owner_mismatch")
        if str(lease.get("execution_mode") or "").strip().lower() != mode:
            errors.append("execution_mode_mismatch")
        try:
            expires_at_epoch = int(lease.get("lease_expires_at_epoch"))
        except Exception:
            expires_at_epoch = 0
        if expires_at_epoch <= int(self.now().timestamp()):
            errors.append("lease_expired_or_missing")
        if errors:
            raise ExecutionLeaseRequired(
                "execution lease attestation is invalid: "
                + ",".join(sorted(set(errors)))
            )
        self._execution_lease_context = {
            "PK": expected_pk,
            "SK": expected_key,
            "experimentId": self.config.experiment_id,
            "leaseOwner": owner,
            "executionMode": mode,
            "leaseDomain": expected_domain,
            "leaseExpiresAtEpoch": expires_at_epoch,
        }
        self._execution_lease_acquired_for_run = True

    def _require_execution_lease(self, *allowed_modes: str) -> Dict[str, Any]:
        context = self._execution_lease_context or {}
        mode = str(context.get("executionMode") or "")
        allowed = {str(value).strip().lower() for value in allowed_modes if value}
        if (
            not self._execution_lease_acquired_for_run
            or not context
            or mode not in allowed
            or int(context.get("leaseExpiresAtEpoch") or 0)
            <= int(self.now().timestamp())
        ):
            raise ExecutionLeaseRequired(
                "an active attested execution lease is required for "
                + ",".join(sorted(allowed))
            )
        return context

    def _new_manifest(self) -> Dict[str, Any]:
        activated_at = self.now()
        if activated_at.tzinfo is None:
            activated_at = activated_at.replace(tzinfo=timezone.utc)
        activated_at = activated_at.astimezone(timezone.utc)
        try:
            activation = experiment.release_activation(
                experiment_id=self.config.experiment_id,
                release_contract_id=self.config.release_contract_id,
                release_cutoff_utc=self.config.release_cutoff_utc,
                activated_at_utc=activated_at.isoformat(),
                deployment_git_sha=self.config.deployment_git_sha,
                deployment_template_sha256=(
                    self.config.deployment_template_sha256
                ),
            )
            return experiment.new_manifest(
                experiment_id=self.config.experiment_id,
                release_contract_id=self.config.release_contract_id,
                release_cutoff_utc=self.config.release_cutoff_utc,
                feature_vector_version=self.config.feature_vector_version,
                model_feature_schemas={
                    "outcome": dual_model.OUTCOME_FEATURES,
                    "reliability": dual_model.RELIABILITY_FEATURES,
                },
                created_at_utc=activated_at.isoformat(),
                release_activation=activation,
            )
        except experiment.ExperimentContractError as exc:
            raise TrainingContractError(
                "r3 experiment release activation failed: " + str(exc)
            ) from exc

    def _normalized_release_cutoff(self) -> str:
        parsed = datetime.fromisoformat(
            self.config.release_cutoff_utc.replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    def _validate_manifest_contract(
        self,
        current: Dict[str, Any],
        *,
        require_release_activation: bool = True,
    ) -> None:
        if current.get("version") != experiment.VERSION:
            raise TrainingContractError(
                "configured experiment conflicts with persisted manifest version; "
                "create a new experiment ID"
            )
        try:
            digest_valid = (
                current.get("manifestDigest") == experiment.manifest_digest(current)
            )
        except Exception:
            digest_valid = False
        if not digest_valid:
            raise TrainingContractError("persisted experiment manifest digest is invalid")
        expected = {
            "experimentId": self.config.experiment_id,
            "releaseContractId": self.config.release_contract_id,
            "releaseCutoffUtc": self._normalized_release_cutoff(),
            "featureVectorVersion": self.config.feature_vector_version,
        }
        for key, value in expected.items():
            if str(current.get(key) or "") != str(value):
                raise TrainingContractError(
                    f"configured experiment conflicts with persisted {key}"
                )
        expected_schemas = {
            "outcome": list(dual_model.OUTCOME_FEATURES),
            "reliability": list(dual_model.RELIABILITY_FEATURES),
        }
        if current.get("modelFeatureSchemas") != expected_schemas:
            raise TrainingContractError(
                "configured experiment conflicts with persisted "
                "modelFeatureSchemas; create a new experiment ID"
            )
        expected_fingerprint = experiment.digest(expected_schemas)
        if current.get("featureSchemaFingerprint") != expected_fingerprint:
            raise TrainingContractError(
                "configured experiment conflicts with persisted "
                "featureSchemaFingerprint; create a new experiment ID"
            )
        activation = current.get("releaseActivation")
        if activation is not None or require_release_activation:
            activation_errors = experiment.release_activation_errors(
                activation,
                expected_experiment_id=self.config.experiment_id,
                expected_release_contract_id=self.config.release_contract_id,
                expected_release_cutoff_utc=self._normalized_release_cutoff(),
                expected_created_at_utc=str(current.get("createdAtUtc") or ""),
            )
            if activation_errors:
                raise TrainingContractError(
                    "persisted r3 release activation is invalid: "
                    + ",".join(activation_errors)
                )

    def _activate_existing_manifest(
        self, current: Dict[str, Any]
    ) -> Dict[str, Any]:
        """One-time CAS migration for a pre-cutoff markerless r3 manifest."""
        attested_at = self.now()
        if attested_at.tzinfo is None:
            attested_at = attested_at.replace(tzinfo=timezone.utc)
        attested_at = attested_at.astimezone(timezone.utc)
        cutoff = datetime.fromisoformat(
            self._normalized_release_cutoff().replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        if attested_at >= cutoff:
            raise TrainingContractError(
                "persisted r3 release activation is absent at or after the cutoff; "
                "create a new experiment ID"
            )
        try:
            activation = experiment.release_activation(
                experiment_id=self.config.experiment_id,
                release_contract_id=self.config.release_contract_id,
                release_cutoff_utc=self.config.release_cutoff_utc,
                activated_at_utc=attested_at.isoformat(),
                deployment_git_sha=self.config.deployment_git_sha,
                deployment_template_sha256=(
                    self.config.deployment_template_sha256
                ),
            )
        except experiment.ExperimentContractError as exc:
            raise TrainingContractError(
                "persisted markerless r3 manifest cannot be activated: " + str(exc)
            ) from exc
        updated = copy.deepcopy(current)
        previous_revision = int(current.get("revision") or 0)
        previous_digest = str(current.get("manifestDigest") or "")
        updated["releaseActivation"] = activation
        updated["revision"] = previous_revision + 1
        updated["updatedAtUtc"] = attested_at.isoformat()
        updated["manifestDigest"] = experiment.manifest_digest(updated)
        self._validate_manifest_contract(updated)
        self.store.save_manifest(
            updated,
            expected_revision=previous_revision,
            expected_digest=previous_digest,
        )
        return updated

    def _load_bound_challenger(
        self, manifest: Dict[str, Any]
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        bound = manifest.get("frozenChallenger") or {}
        if not bound:
            return None, None
        pointer = dict(bound.get("artifact") or {})
        challenger = self.store.read_versioned_json(pointer)
        if _sha256(challenger) != bound.get("artifactDigest"):
            raise TrainingContractError("persisted challenger payload digest mismatch")
        proof = challenger.get("partitionProof") or {}
        partitions = manifest.get("partitions") or {}
        train_partition = partitions.get("train") or {}
        validation_partition = partitions.get("validation") or {}
        mismatches: List[str] = []

        def same_int(actual: Any, expected: Any) -> bool:
            try:
                return actual is not None and int(actual) == int(expected)
            except (TypeError, ValueError):
                return False

        if challenger.get("ok") is not True:
            mismatches.append("challenger_not_ok")
        if challenger.get("experimentId") != manifest.get("experimentId"):
            mismatches.append("experiment_id")
        if challenger.get("featureSchemaFingerprint") != manifest.get(
            "featureSchemaFingerprint"
        ):
            mismatches.append("feature_schema")
        if pointer.get("sha256") != bound.get("artifactDigest"):
            mismatches.append("artifact_pointer_digest")
        if train_partition.get("frozen") is not True:
            mismatches.append("train_not_frozen")
        if validation_partition.get("frozen") is not True:
            mismatches.append("validation_not_frozen")
        if proof.get("trainFingerprint") != train_partition.get(
            "partitionFingerprint"
        ):
            mismatches.append("train_partition")
        if proof.get("validationFingerprint") != validation_partition.get(
            "partitionFingerprint"
        ):
            mismatches.append("validation_partition")
        if bound.get("trainingPartitionFingerprint") != train_partition.get(
            "partitionFingerprint"
        ):
            mismatches.append("bound_train_partition")
        if bound.get("validationPartitionFingerprint") != validation_partition.get(
            "partitionFingerprint"
        ):
            mismatches.append("bound_validation_partition")
        if not same_int(
            proof.get("trainRowCount"), train_partition.get("rowCount") or 0
        ):
            mismatches.append("train_row_count")
        if not same_int(
            proof.get("validationRowCount"),
            validation_partition.get("rowCount") or 0,
        ):
            mismatches.append("validation_row_count")
        if not same_int(proof.get("prospectiveRowsUsedForFitOrThreshold"), 0):
            mismatches.append("prospective_rows_used_for_fit")
        if _parse_status_datetime(bound.get("boundAtUtc")) != _parse_status_datetime(
            manifest.get("prospectiveCutoverAtUtc")
        ) or _parse_status_datetime(bound.get("boundAtUtc")) is None:
            mismatches.append("prospective_cutover")
        if manifest.get("prospectiveAfterSlateDate") != manifest.get(
            "validationEndSlateDate"
        ):
            mismatches.append("prospective_after_slate")
        if bound.get("automaticAuthority") is not False:
            mismatches.append("bound_automatic_authority")
        if challenger.get("thresholdSelectionSource") != (
            "validation_only_before_prospective_cutover"
        ):
            mismatches.append("threshold_selection_source")
        if challenger.get("automaticPromotionEnabled") is not False:
            mismatches.append("challenger_automatic_promotion")
        if challenger.get("liveInferenceAuthority") is not False:
            mismatches.append("challenger_live_authority")
        try:
            threshold_matches = float(challenger.get("selectedThreshold")) == float(
                bound.get("selectedThreshold")
            )
        except Exception:
            threshold_matches = False
        if not threshold_matches:
            mismatches.append("selected_threshold")
        if mismatches:
            raise TrainingContractError(
                "persisted challenger manifest binding is invalid: "
                + ",".join(sorted(set(mismatches)))
            )
        return challenger, pointer

    def _load_or_create_manifest(self) -> Dict[str, Any]:
        current = self.store.load_manifest(self.config.experiment_id)
        if current:
            self._validate_manifest_contract(
                current, require_release_activation=False
            )
            if current.get("releaseActivation") is not None:
                self._validate_manifest_contract(current)
                return current
            return self._activate_existing_manifest(current)
        created = self._new_manifest()
        self.store.save_manifest(
            created, expected_revision=None, expected_digest=None
        )
        return created

    def _latest_status_health(
        self,
        latest: Optional[Dict[str, Any]],
        *,
        execution_mode: str,
        maximum_age: timedelta,
        manifest: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        created = _parse_status_datetime((latest or {}).get("createdAtUtc"))
        age_seconds = (
            (self.now().astimezone(timezone.utc) - created).total_seconds()
            if created is not None
            else None
        )
        deployment = (latest or {}).get("deploymentIdentity") or {}
        deployment_matches = bool(
            deployment.get("gitSha") == self.config.deployment_git_sha
            and deployment.get("templateSha256")
            == self.config.deployment_template_sha256
        )
        errors: List[str] = []
        if not latest:
            errors.append("latest_status_missing")
        if latest and latest.get("ok") is not True:
            errors.append("latest_status_not_ok")
        if latest and latest.get("executionMode") != execution_mode:
            errors.append("latest_status_mode_mismatch")
        if latest and latest.get("version") != VERSION:
            errors.append("latest_status_version_mismatch")
        if latest and latest.get("experimentId") != self.config.experiment_id:
            errors.append("latest_status_experiment_mismatch")
        if latest and latest.get("statusFingerprintVersion") != STATUS_FINGERPRINT_VERSION:
            errors.append("latest_status_fingerprint_version_mismatch")
        if latest and latest.get("statusFingerprint") != _status_fingerprint(latest):
            errors.append("latest_status_fingerprint_mismatch")
        if latest and latest.get("executionConcurrencyControl") != (
            execution_concurrency_control(acquired_for_run=True)
        ):
            errors.append("latest_status_execution_lease_contract_mismatch")
        if manifest and latest and latest.get("manifestDigest") != manifest.get(
            "manifestDigest"
        ):
            errors.append("latest_status_manifest_mismatch")
        if created is None:
            errors.append("latest_status_timestamp_invalid")
        elif age_seconds is not None:
            if age_seconds < 0:
                errors.append("latest_status_from_future")
            elif age_seconds > maximum_age.total_seconds():
                errors.append("latest_status_stale")
        if latest and not deployment_matches:
            errors.append("latest_status_deployment_identity_mismatch")
        return {
            "ok": not errors,
            "executionMode": execution_mode,
            "latestRun": copy.deepcopy(latest),
            "latestRunCreatedAtUtc": created.isoformat() if created else None,
            "ageSeconds": round(age_seconds, 3) if age_seconds is not None else None,
            "maximumAgeSeconds": int(maximum_age.total_seconds()),
            "deploymentIdentityMatches": deployment_matches,
            "errors": errors,
        }

    def _requested_status_run_evidence(
        self,
        requested_run_id: str,
        *,
        execution_mode: str,
        maximum_age: timedelta,
    ) -> Dict[str, Any]:
        normalized = _validated_status_run_id(requested_run_id)
        run = self.store.load_status_run(self.config.experiment_id, normalized)
        if not run:
            return {
                "ok": False,
                "found": False,
                "requestedRunId": normalized,
                "executionMode": execution_mode,
                "run": None,
                "deploymentIdentityMatches": False,
                "errors": ["requested_run_missing"],
            }
        health = self._latest_status_health(
            run,
            execution_mode=execution_mode,
            maximum_age=maximum_age,
            # Immutable deploy-run evidence remains valid if a later run from
            # this exact build advances the current manifest before the query.
            manifest=None,
        )
        errors = [
            error.replace("latest_status", "requested_run", 1)
            for error in health["errors"]
        ]
        if run.get("runId") != normalized:
            errors.append("requested_run_id_mismatch")
        return {
            "ok": not errors,
            "found": True,
            "requestedRunId": normalized,
            "executionMode": execution_mode,
            "run": copy.deepcopy(run),
            "runCreatedAtUtc": health["latestRunCreatedAtUtc"],
            "ageSeconds": health["ageSeconds"],
            "maximumAgeSeconds": health["maximumAgeSeconds"],
            "deploymentIdentityMatches": health[
                "deploymentIdentityMatches"
            ],
            "errors": sorted(set(errors)),
        }

    def status(
        self,
        *,
        training_run_id: Optional[str] = None,
        selection_capture_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        requested = bool(training_run_id or selection_capture_run_id)
        if requested and not (training_run_id and selection_capture_run_id):
            raise TrainingContractError(
                "both training and selection-capture run IDs are required"
            )
        manifest = self.store.load_manifest(self.config.experiment_id)
        if manifest:
            self._validate_manifest_contract(manifest)
        training_health = self._latest_status_health(
            self.store.load_latest_status(self.config.experiment_id, "training"),
            execution_mode="training",
            maximum_age=TRAINING_STATUS_MAX_AGE,
            manifest=manifest,
        )
        selection_capture_health = self._latest_status_health(
            self.store.load_latest_status(
                self.config.experiment_id, "selection_capture"
            ),
            execution_mode="selection_capture",
            maximum_age=SELECTION_CAPTURE_STATUS_MAX_AGE,
            manifest=manifest,
        )
        requested_run_evidence: Dict[str, Any] = {}
        if requested:
            requested_run_evidence = {
                "training": self._requested_status_run_evidence(
                    str(training_run_id),
                    execution_mode="training",
                    maximum_age=TRAINING_STATUS_MAX_AGE,
                ),
                "selectionCapture": self._requested_status_run_evidence(
                    str(selection_capture_run_id),
                    execution_mode="selection_capture",
                    maximum_age=SELECTION_CAPTURE_STATUS_MAX_AGE,
                ),
            }
        requested_runs_ok = bool(
            not requested
            or all(
                evidence.get("ok") is True
                for evidence in requested_run_evidence.values()
            )
        )
        return {
            "ok": bool(
                training_health["ok"]
                and selection_capture_health["ok"]
                and requested_runs_ok
            ),
            "version": VERSION,
            "experimentId": self.config.experiment_id,
            "releaseCutoffUtc": self._normalized_release_cutoff(),
            "manifest": manifest,
            "latestCandidate": self.store.load_latest_candidate(
                self.config.experiment_id
            ),
            "champion": self.store.load_champion(),
            "deploymentIdentity": {
                "gitSha": self.config.deployment_git_sha,
                "templateSha256": self.config.deployment_template_sha256,
            },
            "automaticPromotionEnabled": self.config.automatic_promotion_enabled,
            "firstPromotionRequiresManualReview": True,
            "manualReviewCreatesShadowApprovalOnly": True,
            "v2InferenceConsumerInstalled": False,
            "runtimeAuthorityActivationAvailable": False,
            "trainingHealth": training_health,
            "selectionCaptureHealth": selection_capture_health,
            "requestedRunEvidence": requested_run_evidence,
            "latestStatus": training_health["latestRun"],
            "latestSelectionCaptureStatus": selection_capture_health["latestRun"],
        }

    def _capture_selections(
        self,
        manifest: Dict[str, Any],
        challenger: Dict[str, Any],
    ) -> Dict[str, Any]:
        capture_at = self.now().astimezone(timezone.utc)
        try:
            import mlb_canonical_final_labels_v1 as labels

            slate = capture_at.astimezone(SLATE_TZ).date().isoformat()
            response = labels.load_canonical_locked_rows_without_labels(
                slate_date=slate
            )
        except Exception as exc:
            return {
                "ok": False,
                "capturedCount": 0,
                "selectedCount": 0,
                "errors": [f"{type(exc).__name__}:{exc}"],
            }
        if response.get("ok") is not True:
            return {
                "ok": False,
                "capturedCount": 0,
                "selectedCount": 0,
                "errors": response.get("rejected") or ["canonical_lock_loader_unhealthy"],
            }
        captured = 0
        existing = 0
        selected = 0
        skipped = 0
        skip_reasons: Dict[str, int] = {}
        errors: List[Any] = []
        cutover = _parse_status_datetime(manifest.get("prospectiveCutoverAtUtc"))
        initial_challenger_digest = str(
            (manifest.get("frozenChallenger") or {}).get("artifactDigest") or ""
        )
        for row in response.get("rows") or []:
            authority = row.get("canonicalLockAuthority") or {}
            if authority.get("learningEligible") is not True:
                skipped += 1
                skip_reasons["not_learning_eligible"] = (
                    skip_reasons.get("not_learning_eligible", 0) + 1
                )
                continue
            commence = _parse_status_datetime(
                row.get("commenceTime")
                or (row.get("featureSnapshot") or {}).get("commenceTime")
                or (row.get("frozenFeatureVector") or {}).get("commenceTime")
            )
            if cutover is not None and commence is not None and commence <= cutover:
                skipped += 1
                reason = "game_not_after_challenger_cutover"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                continue
            try:
                current_manifest = manifest
                result: Optional[Dict[str, Any]] = None
                entry: Optional[Dict[str, Any]] = None
                timing_skip = False
                for attempt in range(3):
                    decision_at = self.now().astimezone(timezone.utc)
                    if commence is None or decision_at >= commence:
                        skipped += 1
                        reason = "capture_not_before_commence"
                        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                        timing_skip = True
                        break
                    scored = dual_model.score_unlabeled_lock(row, challenger)
                    entry = experiment.selection_ledger_entry(
                        current_manifest,
                        row,
                        reliability_probability=scored[
                            "reliabilityProbability"
                        ],
                        deployment_identity={
                            "gitSha": self.config.deployment_git_sha,
                            "templateSha256": (
                                self.config.deployment_template_sha256
                            ),
                        },
                        captured_at_utc=decision_at.isoformat(),
                    )
                    try:
                        result = self.store.record_selection(entry)
                        break
                    except ManifestStateConflict:
                        if attempt == 2:
                            raise
                        refreshed_manifest = self.store.load_manifest(
                            self.config.experiment_id
                        )
                        if not refreshed_manifest:
                            raise TrainingContractError(
                                "experiment manifest disappeared during selection retry"
                            )
                        self._validate_manifest_contract(refreshed_manifest)
                        refreshed_digest = str(
                            (
                                refreshed_manifest.get("frozenChallenger")
                                or {}
                            ).get("artifactDigest")
                            or ""
                        )
                        if refreshed_digest != initial_challenger_digest:
                            raise TrainingContractError(
                                "frozen challenger changed during selection capture"
                            )
                        current_manifest = refreshed_manifest
                if timing_skip:
                    # A row that crossed first pitch during a bounded manifest
                    # retry is an honest skip, not an invocation failure.
                    continue
                if result is None or entry is None:
                    raise TrainingContractError(
                        "prospective selection write produced no result"
                    )
                if result.get("created") is True:
                    captured += 1
                else:
                    existing += 1
                selected += int(entry.get("selected") is True)
            except Exception as exc:
                errors.append(
                    {
                        "gameId": row.get("gameId"),
                        "type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
        return {
            "ok": not errors,
            "capturedCount": captured,
            "existingCount": existing,
            "selectedCount": selected,
            "skippedCount": skipped,
            "skipReasonCounts": dict(sorted(skip_reasons.items())),
            "errors": errors,
            "authority": "conditional_write_from_unlabeled_immutable_lock_before_game_start",
        }

    def _save_run_status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        manifest = self.store.load_manifest(self.config.experiment_id)
        result = {
            **payload,
            "version": VERSION,
            "experimentId": self.config.experiment_id,
            "releaseCutoffUtc": self._normalized_release_cutoff(),
            "createdAtUtc": self.now().isoformat(),
            "manifestDigest": (manifest or {}).get("manifestDigest"),
            "deploymentIdentity": {
                "gitSha": self.config.deployment_git_sha,
                "templateSha256": self.config.deployment_template_sha256,
            },
        }
        if (
            str(result.get("executionMode") or "").strip().lower() == "training"
            and self._selection_capture_before_training is not None
        ):
            result.setdefault(
                "selectionCaptureBeforeTraining",
                copy.deepcopy(self._selection_capture_before_training),
            )
        result.setdefault(
            "automaticPromotionEnabled", self.config.automatic_promotion_enabled
        )
        result.setdefault(
            "executionConcurrencyControl",
            execution_concurrency_control(
                acquired_for_run=self._execution_lease_acquired_for_run
            ),
        )
        result.setdefault("championChanged", False)
        result.setdefault("liveInferenceAuthority", False)
        result.setdefault(
            "runId",
            _sha256(
                {
                    "experimentId": self.config.experiment_id,
                    "createdAtUtc": result["createdAtUtc"],
                    "status": result.get("status"),
                }
            )[:24],
        )
        result["statusFingerprintVersion"] = STATUS_FINGERPRINT_VERSION
        result["statusFingerprint"] = _status_fingerprint(result)
        self.store.save_status(self.config.experiment_id, result)
        return result

    def capture_selections(self) -> Dict[str, Any]:
        self._require_execution_lease("selection_capture")
        # This frequent path must never scan historical labels, advance the
        # experiment, fit a model, or create experiment state. Its sole write
        # authority is the pre-outcome selection ledger plus its own status.
        manifest = self.store.load_manifest(self.config.experiment_id)
        if not manifest:
            return self._save_run_status(
                {
                    "ok": True,
                    "status": "WAITING_FOR_EXPERIMENT_MANIFEST",
                    "executionMode": "selection_capture",
                    "selectionCaptureReady": False,
                    "selectionCapture": {
                        "ok": True,
                        "capturedCount": 0,
                        "existingCount": 0,
                        "selectedCount": 0,
                        "skippedCount": 0,
                        "errors": [],
                    },
                    "historicalTrainingScanInvoked": False,
                    "modelTrained": False,
                    "liveInferenceAuthority": False,
                }
            )
        self._validate_manifest_contract(manifest)
        challenger, _pointer = self._load_bound_challenger(manifest)
        if challenger is None:
            return self._save_run_status(
                {
                    "ok": True,
                    "status": "WAITING_FOR_PERSISTED_CHALLENGER",
                    "executionMode": "selection_capture",
                    "selectionCaptureReady": False,
                    "selectionCapture": {
                        "ok": True,
                        "capturedCount": 0,
                        "existingCount": 0,
                        "selectedCount": 0,
                        "skippedCount": 0,
                        "errors": [],
                    },
                    "historicalTrainingScanInvoked": False,
                    "modelTrained": False,
                    "liveInferenceAuthority": False,
                }
            )
        capture = self._capture_selections(manifest, challenger)
        if capture.get("ok") is not True:
            raise TrainingContractError(
                "prospective selection capture failed: "
                + json.dumps(
                    capture.get("errors") or [],
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        return self._save_run_status(
            {
                "ok": True,
                "status": "PROSPECTIVE_SELECTION_CAPTURE_COMPLETE",
                "executionMode": "selection_capture",
                "selectionCaptureReady": True,
                "selectionCapture": capture,
                "challengerArtifactDigest": (
                    manifest.get("frozenChallenger") or {}
                ).get("artifactDigest"),
                "historicalTrainingScanInvoked": False,
                "modelTrained": False,
                "liveInferenceAuthority": False,
            }
        )

    def run_scheduled(self) -> Dict[str, Any]:
        """Initialize/migrate the manifest, then train without blocking capture."""
        self._require_execution_lease("training")
        # This call owns release activation and markerless pre-cutoff migration.
        # It must run before any strict manifest consumer. Selection capture has
        # its own global lease domain and recurring cadence, so a 900-second fit
        # cannot suppress pregame evidence capture.
        self._load_or_create_manifest()
        self._selection_capture_before_training = {
            "status": "OWNED_BY_INDEPENDENT_SELECTION_CAPTURE_CADENCE",
            "invokedByTraining": False,
            "healthEvaluatedByTraining": False,
            "independentHeartbeatRequired": True,
            "collisionPolicy": "separate_global_lease_domain",
            "selectionWritesIdempotent": True,
            "manifestConditionCheckRequired": True,
        }
        return self.run()

    def run(self) -> Dict[str, Any]:
        self._require_execution_lease("training")
        manifest = self._load_or_create_manifest()
        loaded_result = self.row_loader(self.config)
        if loaded_result is None:
            loaded_result = []
        slate_continuity = copy.deepcopy(
            getattr(loaded_result, "continuity", None)
        )
        loaded_rows = list(loaded_result)
        filtered = experiment.filter_records(loaded_rows, manifest)
        accepted = filtered["acceptedRows"]
        today_et = self.now().astimezone(SLATE_TZ).date().isoformat()
        finalized_dates = sorted(
            {
                str(row.get("slateDateEt") or "")
                for row in accepted
                if str(row.get("slateDateEt") or "")
                and str(row.get("slateDateEt") or "") < today_et
                and row.get("slateFinalized") is True
            }
        )
        before_revision = int(manifest.get("revision") or 0)
        before_digest = str(manifest.get("manifestDigest") or "")
        manifest = experiment.advance_manifest(
            manifest,
            accepted,
            finalized_slate_dates=finalized_dates,
            updated_at_utc=self.now().isoformat(),
        )
        self.store.save_manifest(
            manifest,
            expected_revision=before_revision,
            expected_digest=before_digest,
        )
        partition_rows = experiment.rows_by_partition(manifest, accepted)
        if isinstance(slate_continuity, dict) and slate_continuity.get("ok") is not True:
            counts = {
                name: int((manifest["partitions"][name]).get("rowCount") or 0)
                for name in experiment.PARTITION_ORDER
            }
            return self._save_run_status(
                {
                    "ok": False,
                    "status": "CANONICAL_SLATE_CONTINUITY_BLOCKED",
                    "executionMode": "training",
                    "partitionCounts": counts,
                    "acceptedRowCount": filtered["acceptedRowCount"],
                    "rejectedRowCount": filtered["rejectedRowCount"],
                    "rejectionReasonCounts": filtered["rejectionReasonCounts"],
                    "rowsRequired": experiment.PARTITION_MINIMUMS,
                    "canonicalSlateContinuity": slate_continuity,
                    "milestones": experiment.milestone_status(
                        manifest,
                        integrity_clean_row_count=len(accepted),
                        settled_selected_recommendation_count=0,
                        integrity_clean_rows=accepted,
                        official_finalized_slate_authorities=(
                            slate_continuity.get("finalizedSlateAuthorities") or {}
                        ),
                    ),
                    "modelTrained": False,
                    "championChanged": False,
                }
            )

        challenger: Optional[Dict[str, Any]] = None
        challenger_pointer: Optional[Dict[str, Any]] = None
        if (manifest.get("partitions") or {}).get("validation", {}).get("frozen") is True:
            bound = manifest.get("frozenChallenger") or {}
            if bound:
                challenger, challenger_pointer = self._load_bound_challenger(
                    manifest
                )
            else:
                challenger = dual_model.fit_frozen_challenger(
                    partition_rows, manifest
                )
                if challenger.get("ok") is True:
                    fit_digest = _sha256(challenger)
                    prefix = (
                        f"mlb/experiments/{self.config.experiment_id}/challenger/"
                        f"{fit_digest}"
                    )
                    challenger_pointer = self.store.put_versioned_json(
                        f"{prefix}/frozen-challenger.json", challenger
                    )
                    before_revision = int(manifest["revision"])
                    before_digest = str(manifest["manifestDigest"])
                    manifest = experiment.bind_frozen_challenger(
                        manifest,
                        artifact=challenger_pointer,
                        artifact_digest=fit_digest,
                        selected_threshold=float(challenger["selectedThreshold"]),
                        bound_at_utc=self.now().isoformat(),
                    )
                    self.store.save_manifest(
                        manifest,
                        expected_revision=before_revision,
                        expected_digest=before_digest,
                    )
                    # Revisit this invocation only after the challenger pointer
                    # is durable. Already-final backlog becomes diagnostic.
                    before_revision = int(manifest["revision"])
                    before_digest = str(manifest["manifestDigest"])
                    manifest = experiment.advance_manifest(
                        manifest,
                        accepted,
                        finalized_slate_dates=finalized_dates,
                        updated_at_utc=self.now().isoformat(),
                    )
                    self.store.save_manifest(
                        manifest,
                        expected_revision=before_revision,
                        expected_digest=before_digest,
                    )
                    partition_rows = experiment.rows_by_partition(manifest, accepted)

        selection_capture = {
            "status": "OWNED_BY_INDEPENDENT_SELECTION_CAPTURE_CADENCE",
            "invokedByTraining": False,
            "healthEvaluatedByTraining": False,
            "independentHeartbeatRequired": True,
            "manifestConditionCheckRequired": True,
        }
        selection_entries: List[Dict[str, Any]] = []
        selection_evaluation = {
            "ok": True,
            "settledSelectedRecommendationCount": 0,
            "metrics": {},
            "conflicts": [],
        }
        if challenger is not None and challenger.get("ok") is True:
            selection_entries = self.store.list_selections(self.config.experiment_id)
            selection_evaluation = dual_model.evaluate_selection_ledger(
                accepted,
                selection_entries,
                challenger_artifact_digest=str(
                    (manifest.get("frozenChallenger") or {}).get("artifactDigest") or ""
                ),
                experiment_manifest=manifest,
            )

        milestones = experiment.milestone_status(
            manifest,
            integrity_clean_row_count=len(accepted),
            settled_selected_recommendation_count=int(
                selection_evaluation.get("settledSelectedRecommendationCount") or 0
            ),
            integrity_clean_rows=accepted,
            official_finalized_slate_authorities=(
                (slate_continuity or {}).get("finalizedSlateAuthorities") or {}
            ),
        )
        counts = {
            name: int((manifest["partitions"][name]).get("rowCount") or 0)
            for name in experiment.PARTITION_ORDER
        }
        common = {
            "ok": bool(selection_evaluation.get("ok") is True),
            "status": manifest.get("phase"),
            "executionMode": "training",
            "partitionCounts": counts,
            "acceptedRowCount": filtered["acceptedRowCount"],
            "rejectedRowCount": filtered["rejectedRowCount"],
            "rejectionReasonCounts": filtered["rejectionReasonCounts"],
            "rowsRequired": experiment.PARTITION_MINIMUMS,
            "selectionCapture": selection_capture,
            "prospectiveSelectionLedger": selection_evaluation,
            "milestones": milestones,
            "modelTrained": challenger is not None and challenger.get("ok") is True,
            "championChanged": False,
            "automaticPromotionEnabled": self.config.automatic_promotion_enabled,
            "liveInferenceAuthority": False,
        }
        if isinstance(slate_continuity, dict):
            common["canonicalSlateContinuity"] = slate_continuity
        if (
            manifest.get("prospectiveTestSealed") is True
            and selection_evaluation.get("ok") is not True
        ):
            return self._save_run_status(
                {
                    **common,
                    "ok": False,
                    "status": "SELECTION_LEDGER_CONTRACT_INVALID",
                }
            )
        if manifest.get("prospectiveTestSealed") is not True:
            if challenger is not None and challenger.get("ok") is not True:
                common.update({"ok": False, "status": "CHALLENGER_FIT_BLOCKED", "training": challenger})
            return self._save_run_status(common)

        if challenger is None or challenger.get("ok") is not True:
            return self._save_run_status(
                {**common, "ok": False, "status": "PERSISTED_CHALLENGER_UNAVAILABLE"}
            )
        trained = dual_model.evaluate_frozen_challenger(
            partition_rows, manifest, challenger
        )
        if trained.get("ok") is not True:
            return self._save_run_status(
                {**common, "ok": False, "status": "TRAINING_BLOCKED", "training": trained}
            )
        trained["prospectiveSelectionLedger"] = selection_evaluation
        trained["prospectiveSelectedRecommendationCount"] = int(
            selection_evaluation.get("settledSelectedRecommendationCount") or 0
        )
        trained["prospectiveTest"]["selectedReliability"] = (
            selection_evaluation.get("metrics") or {}
        )
        evaluation_fingerprint = _sha256(
            {
                "challengerArtifactDigest": (manifest.get("frozenChallenger") or {}).get("artifactDigest"),
                "prospectivePartitionFingerprint": (
                    manifest["partitions"]["prospectiveTest"].get("partitionFingerprint")
                ),
                "outcome": trained["prospectiveTest"]["outcome"],
            }
        )
        artifact_manifest = experiment.mark_prospective_evaluated(
            manifest,
            evaluation_fingerprint=evaluation_fingerprint,
            evaluated_at_utc=self.now().isoformat(),
        )
        trained["experimentManifestDigest"] = artifact_manifest["manifestDigest"]
        dataset = {
            "version": VERSION,
            "experimentId": self.config.experiment_id,
            "experimentManifestDigest": artifact_manifest["manifestDigest"],
            "partitions": partition_rows,
            "deploymentIdentity": {
                "gitSha": self.config.deployment_git_sha,
                "templateSha256": self.config.deployment_template_sha256,
            },
            "historicalDiagnosticSlateDates": artifact_manifest.get("historicalDiagnosticSlateDates") or {},
        }
        dataset_digest = _sha256(dataset)
        run_id = _sha256(
            {
                "experimentId": self.config.experiment_id,
                "manifestDigest": artifact_manifest["manifestDigest"],
                "datasetDigest": dataset_digest,
                "modelVersion": trained["version"],
                "deploymentGitSha": self.config.deployment_git_sha,
                "deploymentTemplateSha256": self.config.deployment_template_sha256,
                "evaluationFingerprint": evaluation_fingerprint,
                "selectionEvaluation": selection_evaluation,
            }
        )[:24]
        prefix = f"mlb/experiments/{self.config.experiment_id}/runs/{run_id}"
        artifacts = {
            "dataset": self.store.put_versioned_json(f"{prefix}/dataset.json", dataset),
            "manifest": self.store.put_versioned_json(f"{prefix}/manifest.json", artifact_manifest),
            "frozenChallenger": challenger_pointer,
            "evaluation": self.store.put_versioned_json(
                f"{prefix}/evaluation.json",
                {
                    "validation": trained["validation"],
                    "prospectiveTest": trained["prospectiveTest"],
                    "prospectiveSelectionLedger": selection_evaluation,
                    "split": trained["split"],
                },
            ),
            "bundle": self.store.put_versioned_json(f"{prefix}/bundle.json", trained),
        }
        current_champion = self.store.load_champion()
        gate = promotion_policy.evaluate(
            trained,
            artifact_manifest,
            current_champion=current_champion,
            automatic_promotion_enabled=self.config.automatic_promotion_enabled,
        )
        digest_material = {
            "experimentId": self.config.experiment_id,
            "experimentManifestDigest": artifact_manifest["manifestDigest"],
            "datasetDigest": dataset_digest,
            "evaluationFingerprint": evaluation_fingerprint,
            "artifactChecksums": {
                name: value["sha256"]
                for name, value in artifacts.items()
                if isinstance(value, dict) and value.get("sha256")
            },
            "promotionGate": {
                key: value for key, value in gate.items() if key != "evaluatedAtUtc"
            },
            "deploymentIdentity": {
                "gitSha": self.config.deployment_git_sha,
                "templateSha256": self.config.deployment_template_sha256,
            },
        }
        artifact_digest = _sha256(digest_material)
        candidate = {
            "version": VERSION,
            "recordType": "mlb_ml_candidate_v2",
            "experimentId": self.config.experiment_id,
            "experimentManifestDigest": artifact_manifest["manifestDigest"],
            "featureSchemaFingerprint": artifact_manifest["featureSchemaFingerprint"],
            "datasetDigest": dataset_digest,
            "artifactDigest": artifact_digest,
            "runId": run_id,
            "createdAtUtc": self.now().isoformat(),
            "artifacts": artifacts,
            "promotionGate": gate,
            "directionAuthorityEnabled": False,
            "playabilityAuthorityEnabled": False,
            "firstActivationRequiresManualReview": True,
            "deploymentIdentity": {
                "gitSha": self.config.deployment_git_sha,
                "templateSha256": self.config.deployment_template_sha256,
            },
        }
        latest = self.store.load_latest_candidate(self.config.experiment_id)
        if (latest or {}).get("artifactDigest") != artifact_digest:
            self.store.commit_candidate(
                artifact_manifest,
                candidate,
                expected_revision=int(manifest["revision"]),
                expected_digest=str(manifest["manifestDigest"]),
            )
            manifest = artifact_manifest
        promotion: Dict[str, Any] = {
            "shadowChampionApproved": False,
            "runtimeAuthorityActivated": False,
            "reason": gate["promotionDecision"],
        }
        if gate.get("promotionDecision") == "AUTO_SHADOW_APPROVAL_ELIGIBLE":
            authorities = promotion_policy.approved_authorities(gate, ["direction", "playability"])
            if authorities:
                current_digest = str((current_champion or {}).get("artifactDigest") or "") or None
                promoted = self.store.promote_candidate(
                    candidate,
                    authorities=authorities,
                    approval_mode="automatic_stable_champion_replacement",
                    reviewer=None,
                    stable_champion=True,
                    expected_champion_digest=current_digest,
                )
                promotion = {
                    "shadowChampionApproved": True,
                    "runtimeAuthorityActivated": False,
                    "champion": promoted,
                }
        return self._save_run_status(
            {
                **common,
                "ok": True,
                "status": "CANDIDATE_REGISTERED",
                "artifactDigest": artifact_digest,
                "evaluationFingerprint": evaluation_fingerprint,
                "promotionGate": gate,
                "promotion": promotion,
                "championChanged": promotion.get("shadowChampionApproved") is True,
                "runtimeAuthorityChanged": False,
            }
        )

    def manual_review(
        self,
        *,
        artifact_digest: str,
        reviewer: str,
        requested_authorities: Sequence[str],
        stable_champion: bool,
    ) -> Dict[str, Any]:
        self._require_execution_lease("manual_review")
        if not artifact_digest or not reviewer:
            raise TrainingContractError(
                "manual review requires artifactDigest and reviewer"
            )
        candidate = self.store.load_candidate(
            self.config.experiment_id, artifact_digest
        )
        if (
            not candidate
            or candidate.get("artifactDigest") != artifact_digest
            or candidate.get("experimentId") != self.config.experiment_id
        ):
            raise TrainingContractError("reviewed candidate digest was not found")
        gate = candidate.get("promotionGate") or {}
        authorities = promotion_policy.approved_authorities(
            gate, requested_authorities
        )
        if not authorities:
            raise TrainingContractError(
                "no requested authority passed its prospective promotion gate"
            )
        current = self.store.load_champion()
        champion = self.store.promote_candidate(
            candidate,
            authorities=authorities,
            approval_mode="manual_first_shadow_approval",
            reviewer=reviewer,
            stable_champion=stable_champion,
            expected_champion_digest=(
                str((current or {}).get("artifactDigest") or "") or None
            ),
        )
        return {
            "ok": True,
            "version": VERSION,
            "status": "MANUALLY_REVIEWED_SHADOW_CHAMPION_APPROVED",
            "artifactDigest": artifact_digest,
            "approvedForFutureRuntimeIntegration": authorities,
            "runtimeAuthorityActivated": False,
            "runtimeIntegrationRequired": True,
            "champion": champion,
        }


def _service() -> TrainingService:
    config = TrainingConfig.from_env()
    return TrainingService(
        AwsTrainingStore(
            table_name=os.environ.get("SNAPSHOTS_TABLE", ""),
            artifacts_bucket=config.artifacts_bucket,
        ),
        config,
    )


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    request = event if isinstance(event, dict) else {}
    mode = str(request.get("mode") or "scheduled").strip().lower()
    service = _service()
    if mode == "status":
        return service.status(
            training_run_id=request.get("trainingRunId"),
            selection_capture_run_id=request.get("selectionCaptureRunId"),
        )
    if mode not in {"scheduled", "selection_capture", "manual_review"}:
        raise TrainingContractError(f"unsupported training mode: {mode}")
    execution_mode = {
        "scheduled": "training",
        "selection_capture": "selection_capture",
        "manual_review": "manual_review",
    }[mode]
    configured_lease_seconds = int(
        os.environ.get(
            "MLB_ML_EXECUTION_LEASE_SECONDS", str(EXECUTION_LEASE_SECONDS)
        )
    )
    if configured_lease_seconds != EXECUTION_LEASE_SECONDS:
        raise TrainingContractError(
            "configured execution lease duration does not match the production contract"
        )
    remaining_time = getattr(context, "get_remaining_time_in_millis", None)
    if callable(remaining_time):
        remaining_seconds = max(0, int(remaining_time())) / 1000
        if configured_lease_seconds < remaining_seconds + 60:
            raise TrainingContractError(
                "execution lease must outlive the Lambda invocation timeout"
            )
    request_id = str(getattr(context, "aws_request_id", "") or "").strip()
    lease_owner = f"{execution_mode}:{request_id or uuid.uuid4().hex}"
    lease_acquired = False
    primary_error: Optional[BaseException] = None
    try:
        lease = service.store.acquire_execution_lease(
            service.config.experiment_id,
            owner_token=lease_owner,
            execution_mode=execution_mode,
            acquired_at=service.now(),
            lease_seconds=configured_lease_seconds,
        )
        lease_acquired = True
        service.attest_execution_lease_acquired(
            lease,
            owner_token=lease_owner,
            execution_mode=execution_mode,
        )
        if execution_mode == "training":
            result = service.run_scheduled()
        elif execution_mode == "selection_capture":
            result = service.capture_selections()
        else:
            requested = request.get("authorities") or ["direction", "playability"]
            if not isinstance(requested, list):
                raise TrainingContractError(
                    "manual review authorities must be a list"
                )
            result = service.manual_review(
                artifact_digest=str(request.get("artifactDigest") or ""),
                reviewer=str(request.get("reviewer") or ""),
                requested_authorities=requested,
                stable_champion=request.get("stableChampion") is True,
            )
            result.setdefault(
                "executionConcurrencyControl",
                execution_concurrency_control(acquired_for_run=True),
            )
        if result.get("ok") is not True:
            raise TrainingContractError(
                f"{execution_mode} returned an unhealthy status: "
                f"{result.get('status')}"
            )
        return result
    except Exception as exc:
        primary_error = exc
        if not lease_acquired:
            # Scheduled sources intentionally have no overlapping async retry.
            # Deployment invokes use their own exact bounded retry helper. Any
            # acquire error must make zero unlocked status writes.
            raise
        if execution_mode != "manual_review":
            try:
                failure_status = {
                    "ok": False,
                    "status": f"{execution_mode.upper()}_INVOCATION_FAILED",
                    "executionMode": execution_mode,
                    "failure": _sanitized_failure(exc),
                    "liveInferenceAuthority": False,
                }
                request_id = str(getattr(context, "aws_request_id", "") or "")
                if request_id:
                    failure_status["runId"] = request_id
                service._save_run_status(failure_status)
            except Exception:
                # Preserve the original invocation failure. The next scheduled
                # cadence is the recovery path; CloudWatch retains the terminal
                # error without persisting raw exception details publicly.
                pass
        raise
    finally:
        if lease_acquired:
            try:
                service.store.release_execution_lease(
                    service.config.experiment_id,
                    owner_token=lease_owner,
                    execution_mode=execution_mode,
                )
            except Exception:
                if primary_error is None:
                    raise
