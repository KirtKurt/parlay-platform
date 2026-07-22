#!/usr/bin/env python3
"""Read-only gate for the durable MLB r3 release-activation contract."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

import boto3
from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))
import mlb_ml_experiment_v2 as _runtime_experiment


VERSION = "MLB-ML-RELEASE-ACTIVATION-PREDEPLOY-v1"
EXPERIMENT_VERSION = "MLB-ML-EXPERIMENT-v2-fixed-slate-future-prospective-cutover"
RELEASE_ACTIVATION_VERSION = "MLB-ML-RELEASE-ACTIVATION-v1"
EXPERIMENT_ID = "mlb-v2-2026-07-22-future-prospective-r3"
RELEASE_CONTRACT_ID = EXPERIMENT_ID
RELEASE_CUTOFF_UTC = "2026-07-22T04:00:00+00:00"
SNAPSHOTS_LOGICAL_RESOURCE_ID = "SnapshotsTable"
MANIFEST_PK = f"MLB_ML_EXPERIMENT#V2#{EXPERIMENT_ID}"
MANIFEST_SK = "MANIFEST"
MANIFEST_RECORD_TYPE = "mlb_ml_experiment_manifest_v2"

# Ninety minutes covers the bounded 6-minute capacity recovery and ~16-minute
# Lambda admission retry while retaining over an hour for SAM/identity work.
FIRST_ACTIVATION_LEAD = timedelta(minutes=90)
FIRST_ACTIVATION_LEAD_SECONDS = int(FIRST_ACTIVATION_LEAD.total_seconds())
RELEASE_CUTOFF = datetime.fromisoformat(RELEASE_CUTOFF_UTC)
FIRST_ACTIVATION_DEADLINE = RELEASE_CUTOFF - FIRST_ACTIVATION_LEAD


class GateReadError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("non-finite manifest value")
        return format(value, ".17g")
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    runtime_digest = getattr(_runtime_experiment, "manifest_digest", None)
    if callable(runtime_digest):
        return str(runtime_digest(dict(manifest)))
    return _digest(
        {key: value for key, value in manifest.items() if key != "manifestDigest"}
    )


def _is_hex(value: Any, length: int) -> bool:
    text = str(value or "")
    if len(text) != length:
        return False
    try:
        int(text, 16)
    except (TypeError, ValueError):
        return False
    return True


def release_activation_errors(
    value: Any,
    *,
    expected_experiment_id: str,
    expected_release_contract_id: str,
    expected_release_cutoff_utc: str,
    expected_created_at_utc: str,
) -> List[str]:
    """Mirror the runtime's immutable release-activation validation."""
    runtime_validator = getattr(_runtime_experiment, "release_activation_errors", None)
    if callable(runtime_validator):
        return list(
            runtime_validator(
                value,
                expected_experiment_id=expected_experiment_id,
                expected_release_contract_id=expected_release_contract_id,
                expected_release_cutoff_utc=expected_release_cutoff_utc,
                expected_created_at_utc=expected_created_at_utc,
            )
        )
    if not isinstance(value, Mapping):
        return ["release_activation_missing"]
    errors: List[str] = []
    if set(value) != {
        "version",
        "experimentId",
        "releaseContractId",
        "releaseCutoffUtc",
        "activatedAtUtc",
        "deploymentIdentity",
        "immutable",
    }:
        errors.append("release_activation_fields_mismatch")
    if value.get("version") != RELEASE_ACTIVATION_VERSION:
        errors.append("release_activation_version_mismatch")
    if value.get("experimentId") != expected_experiment_id:
        errors.append("release_activation_experiment_identity_mismatch")
    if value.get("releaseContractId") != expected_release_contract_id:
        errors.append("release_activation_contract_identity_mismatch")

    cutoff = _parse_time(expected_release_cutoff_utc)
    marker_cutoff = _parse_time(value.get("releaseCutoffUtc"))
    activated = _parse_time(value.get("activatedAtUtc"))
    created = _parse_time(expected_created_at_utc)
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
        if not _is_hex(identity.get("gitSha"), 40):
            errors.append("release_activation_git_identity_invalid")
        if not _is_hex(identity.get("templateSha256"), 64):
            errors.append("release_activation_template_identity_invalid")
    if value.get("immutable") is not True:
        errors.append("release_activation_not_immutable")
    return sorted(set(errors))


def _stack_missing(exc: ClientError) -> bool:
    error = (exc.response or {}).get("Error") or {}
    return error.get("Code") == "ValidationError" and "exist" in str(
        error.get("Message") or ""
    ).lower()


def _resolve_snapshots_table(
    cloudformation: Any,
    *,
    stack_name: str,
) -> Tuple[bool, Optional[str]]:
    try:
        response = cloudformation.describe_stacks(StackName=stack_name)
    except ClientError as exc:
        if _stack_missing(exc):
            return False, None
        raise GateReadError("cloudformation_stack_lookup_failed") from exc
    except Exception as exc:
        raise GateReadError("cloudformation_stack_lookup_failed") from exc
    stacks = response.get("Stacks") or []
    if len(stacks) != 1:
        raise GateReadError("cloudformation_stack_identity_invalid")
    try:
        response = cloudformation.describe_stack_resource(
            StackName=stack_name,
            LogicalResourceId=SNAPSHOTS_LOGICAL_RESOURCE_ID,
        )
    except Exception as exc:
        raise GateReadError("cloudformation_snapshots_table_resolution_failed") from exc
    detail = response.get("StackResourceDetail") or {}
    if detail.get("LogicalResourceId") != SNAPSHOTS_LOGICAL_RESOURCE_ID:
        raise GateReadError("cloudformation_snapshots_logical_identity_mismatch")
    if detail.get("ResourceType") != "AWS::DynamoDB::Table":
        raise GateReadError("cloudformation_snapshots_resource_type_mismatch")
    physical_id = str(detail.get("PhysicalResourceId") or "").strip()
    if not physical_id:
        raise GateReadError("cloudformation_snapshots_physical_id_missing")
    return True, physical_id


def _read_manifest(dynamodb: Any, *, table_name: str) -> Optional[Dict[str, Any]]:
    try:
        response = dynamodb.Table(table_name).get_item(
            Key={"PK": MANIFEST_PK, "SK": MANIFEST_SK},
            ConsistentRead=True,
        )
    except Exception as exc:
        raise GateReadError("dynamodb_manifest_read_failed") from exc
    item = _plain(response.get("Item") or {})
    return item if isinstance(item, dict) and item else None


def _manifest_errors(
    item: Mapping[str, Any],
    *,
    checked_at: datetime,
) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    if item.get("PK") != MANIFEST_PK:
        errors.append("manifest_envelope_partition_key_mismatch")
    if item.get("SK") != MANIFEST_SK:
        errors.append("manifest_envelope_sort_key_mismatch")
    if item.get("record_type") != MANIFEST_RECORD_TYPE:
        errors.append("manifest_envelope_record_type_mismatch")
    manifest = item.get("data")
    if not isinstance(manifest, Mapping) or not manifest:
        return {}, sorted({*errors, "manifest_envelope_data_missing"})
    manifest = dict(manifest)
    if item.get("manifestDigest") != manifest.get("manifestDigest"):
        errors.append("manifest_envelope_digest_mismatch")
    revision = manifest.get("revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or item.get("revision") != revision
    ):
        errors.append("manifest_envelope_revision_mismatch")
    expected = {
        "version": EXPERIMENT_VERSION,
        "experimentId": EXPERIMENT_ID,
        "releaseContractId": RELEASE_CONTRACT_ID,
        "releaseCutoffUtc": RELEASE_CUTOFF_UTC,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            errors.append(f"manifest_{key}_mismatch")
    created = _parse_time(manifest.get("createdAtUtc"))
    if created is None:
        errors.append("manifest_created_at_invalid")
    elif created >= RELEASE_CUTOFF:
        errors.append("manifest_created_at_not_before_cutoff")
    elif created > checked_at:
        errors.append("manifest_created_at_from_future")
    digest_value = manifest.get("manifestDigest")
    if not _is_hex(digest_value, 64):
        errors.append("manifest_digest_invalid")
    else:
        try:
            if digest_value != manifest_digest(manifest):
                errors.append("manifest_digest_mismatch")
        except Exception:
            errors.append("manifest_digest_mismatch")
    return manifest, sorted(set(errors))


def _base_report(*, checked_at: datetime, stack_name: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "version": VERSION,
        "decision": "BLOCK",
        "checkedAtUtc": checked_at.isoformat(),
        "stackName": stack_name,
        "snapshotsLogicalResourceId": SNAPSHOTS_LOGICAL_RESOURCE_ID,
        "experimentId": EXPERIMENT_ID,
        "releaseCutoffUtc": RELEASE_CUTOFF_UTC,
        "firstActivationLeadSeconds": FIRST_ACTIVATION_LEAD_SECONDS,
        "firstActivationDeadlineUtc": FIRST_ACTIVATION_DEADLINE.isoformat(),
        "stackPresent": None,
        "manifestState": "UNKNOWN",
        "manifestDigestValidated": False,
        "releaseActivationValidated": False,
        "redacted": True,
        "errors": [],
    }


def _first_activation_decision(
    report: Dict[str, Any],
    *,
    checked_at: datetime,
    manifest_state: str,
) -> Dict[str, Any]:
    report["manifestState"] = manifest_state
    if checked_at < FIRST_ACTIVATION_DEADLINE:
        report.update(
            {
                "ok": True,
                "decision": "ALLOW_FIRST_ACTIVATION",
                "errors": [],
            }
        )
    else:
        report["errors"] = ["first_activation_lead_deadline_reached"]
    return report


def verify_predeploy(
    *,
    cloudformation: Any,
    dynamodb: Any,
    stack_name: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Evaluate the gate without making any AWS mutation."""
    checked_at = _utc(now or datetime.now(timezone.utc))
    report = _base_report(checked_at=checked_at, stack_name=stack_name)
    try:
        stack_present, table_name = _resolve_snapshots_table(
            cloudformation, stack_name=stack_name
        )
        report["stackPresent"] = stack_present
        if not stack_present:
            return _first_activation_decision(
                report,
                checked_at=checked_at,
                manifest_state="STACK_AND_MANIFEST_MISSING",
            )
        item = _read_manifest(dynamodb, table_name=str(table_name))
    except GateReadError as exc:
        report["errors"] = [exc.code]
        return report

    if item is None:
        return _first_activation_decision(
            report,
            checked_at=checked_at,
            manifest_state="MANIFEST_MISSING",
        )

    manifest, errors = _manifest_errors(item, checked_at=checked_at)
    if errors:
        report["manifestState"] = "INVALID"
        report["errors"] = errors
        return report
    report["manifestDigestValidated"] = True
    activation = manifest.get("releaseActivation")
    if activation is None:
        return _first_activation_decision(
            report,
            checked_at=checked_at,
            manifest_state="MARKERLESS_MANIFEST",
        )

    activation_errors = release_activation_errors(
        activation,
        expected_experiment_id=EXPERIMENT_ID,
        expected_release_contract_id=RELEASE_CONTRACT_ID,
        expected_release_cutoff_utc=RELEASE_CUTOFF_UTC,
        expected_created_at_utc=str(manifest.get("createdAtUtc") or ""),
    )
    activated_at = _parse_time(
        activation.get("activatedAtUtc")
        if isinstance(activation, Mapping)
        else None
    )
    if activated_at is not None and activated_at > checked_at:
        activation_errors.append("release_activation_timestamp_from_future")
    activation_errors = sorted(set(activation_errors))
    if activation_errors:
        report["manifestState"] = "INVALID_RELEASE_ACTIVATION"
        report["errors"] = activation_errors
        return report
    report.update(
        {
            "ok": True,
            "decision": "ALLOW_EXISTING_ACTIVATION",
            "manifestState": "VALID_RELEASE_ACTIVATION",
            "releaseActivationValidated": True,
            "activationRecordedAtUtc": activation.get("activatedAtUtc"),
            "errors": [],
        }
    )
    return report


def _write_report(path: str, report: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    report = verify_predeploy(
        cloudformation=session.client("cloudformation"),
        dynamodb=session.resource("dynamodb"),
        stack_name=args.stack_name,
    )
    _write_report(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
