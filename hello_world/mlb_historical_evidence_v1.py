from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping
from urllib.parse import urlparse

from mlb_historical_policy_v1 import canonical_digest, sha256_file

VERSION = "MLB-HISTORICAL-EVIDENCE-v15.11.1"
REQUIRED_EVIDENCE_FLAGS = (
    "plan_sha256_validated",
    "ledger_sha256_validated",
    "all_snapshot_hashes_validated",
    "dataset_sha256_validated",
    "artifact_sha256_validated",
    "audit_claim_write_once",
    "audit_claim_sha256_validated",
)
REQUIRED_EVIDENCE_DIGESTS = (
    "schedule_sha256",
    "outcomes_sha256",
    "plan_sha256",
    "ledger_sha256",
    "snapshot_manifest_sha256",
    "dataset_sha256",
    "artifact_sha256",
    "audit_claim_sha256",
)


def _parse_utc(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def normalize_headers(headers: Mapping[str, Any]) -> Dict[str, str]:
    return {str(key).strip().lower(): str(value).strip() for key, value in headers.items()}


def validate_request_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    expected = str(plan.get("plan_sha256") or "")
    material = dict(plan)
    material.pop("plan_sha256", None)
    observed = canonical_digest(material)
    requests = plan.get("requests") or []
    timestamps = [str(row.get("requested_at_utc") or "") for row in requests]
    blockers = []
    if not expected or expected != observed:
        blockers.append("plan_sha256_mismatch")
    if int(plan.get("request_count") or -1) != len(requests):
        blockers.append("plan_request_count_mismatch")
    if len(set(timestamps)) != len(timestamps) or any(not value for value in timestamps):
        blockers.append("plan_request_timestamps_invalid_or_duplicate")
    if str(plan.get("schedule_sha256") or "") == "":
        blockers.append("plan_schedule_sha256_missing")
    for row in requests:
        if row.get("sport_key") != "baseball_mlb":
            blockers.append("plan_sport_key_invalid")
        if row.get("regions") != "us" or row.get("markets") != "h2h":
            blockers.append("plan_market_contract_invalid")
        _parse_utc(row.get("requested_at_utc"))
    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "plan_sha256": expected,
        "plan_sha256_validated": expected == observed,
        "request_count": len(requests),
        "schedule_sha256": str(plan.get("schedule_sha256") or ""),
    }


def validate_ledger(
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
    snapshot_dir: str | Path,
) -> Dict[str, Any]:
    plan_result = validate_request_plan(plan)
    blockers = list(plan_result["blockers"])
    completed = ledger.get("completed") or {}
    planned = {
        str(row.get("requested_at_utc") or "")
        for row in plan.get("requests") or []
    }
    completed_keys = {str(value) for value in completed}
    if ledger.get("plan_sha256") != plan_result["plan_sha256"]:
        blockers.append("ledger_plan_sha256_mismatch")
    if ledger.get("complete") is not True:
        blockers.append("ledger_not_complete")
    if ledger.get("paid_usage_authorized") is not True:
        blockers.append("ledger_paid_usage_authorization_missing")
    if completed_keys != planned:
        blockers.append("ledger_completed_request_set_mismatch")

    snapshot_rows = []
    total_cost = 0
    root = Path(snapshot_dir)
    for requested_at in sorted(planned):
        entry = completed.get(requested_at)
        if not isinstance(entry, dict):
            blockers.append(f"ledger_entry_missing:{requested_at}")
            continue
        filename = str(entry.get("file") or "")
        expected_sha = str(entry.get("sha256") or "")
        path = root / filename
        if not filename or not path.is_file():
            blockers.append(f"snapshot_file_missing:{requested_at}")
            continue
        observed_sha = sha256_file(path)
        if not expected_sha or observed_sha != expected_sha:
            blockers.append(f"snapshot_sha256_mismatch:{requested_at}")
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                envelope = json.load(handle)
        except Exception as exc:
            blockers.append(f"snapshot_envelope_invalid:{requested_at}:{type(exc).__name__}")
            continue
        if str(envelope.get("requested_at_utc") or "") != requested_at:
            blockers.append(f"snapshot_requested_timestamp_mismatch:{requested_at}")
        provider_timestamp = envelope.get("provider_timestamp")
        if provider_timestamp:
            if _parse_utc(provider_timestamp) > _parse_utc(requested_at):
                blockers.append(f"snapshot_provider_timestamp_after_request:{requested_at}")
        if not isinstance(envelope.get("data"), list):
            blockers.append(f"snapshot_data_invalid:{requested_at}")
        response_cost = int(entry.get("response_cost") or 0)
        if response_cost < 0:
            blockers.append(f"snapshot_response_cost_invalid:{requested_at}")
        total_cost += response_cost
        snapshot_rows.append(
            {
                "requested_at_utc": requested_at,
                "file": filename,
                "sha256": observed_sha,
                "provider_timestamp": provider_timestamp,
                "response_cost": response_cost,
            }
        )
    if int(ledger.get("credits_observed") or 0) != total_cost:
        blockers.append("ledger_credit_total_mismatch")

    ledger_sha256 = canonical_digest(dict(ledger))
    snapshot_manifest_sha256 = canonical_digest({"snapshots": snapshot_rows})
    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "plan_sha256": plan_result["plan_sha256"],
        "plan_sha256_validated": plan_result["plan_sha256_validated"],
        "ledger_sha256": ledger_sha256,
        "ledger_sha256_validated": not any(
            blocker.startswith("ledger_") for blocker in blockers
        ),
        "snapshot_manifest_sha256": snapshot_manifest_sha256,
        "all_snapshot_hashes_validated": not any(
            blocker.startswith("snapshot_") for blocker in blockers
        ),
        "snapshot_count": len(snapshot_rows),
        "credits_observed": total_cost,
        "schedule_sha256": plan_result["schedule_sha256"],
    }


def build_dataset_manifest(
    *,
    schedule_path: str | Path,
    outcomes_path: str | Path,
    plan_path: str | Path,
    ledger_path: str | Path,
    snapshot_dir: str | Path,
    dataset_path: str | Path,
) -> Dict[str, Any]:
    plan = json.loads(Path(plan_path).read_text())
    ledger = json.loads(Path(ledger_path).read_text())
    ledger_result = validate_ledger(plan, ledger, snapshot_dir)
    if not ledger_result["ok"]:
        raise ValueError({"evidence_blockers": ledger_result["blockers"]})
    manifest = {
        "version": VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "schedule_sha256": sha256_file(schedule_path),
        "outcomes_sha256": sha256_file(outcomes_path),
        "plan_sha256": ledger_result["plan_sha256"],
        "ledger_sha256": ledger_result["ledger_sha256"],
        "snapshot_manifest_sha256": ledger_result["snapshot_manifest_sha256"],
        "dataset_sha256": sha256_file(dataset_path),
        "plan_sha256_validated": ledger_result["plan_sha256_validated"],
        "ledger_sha256_validated": ledger_result["ledger_sha256_validated"],
        "all_snapshot_hashes_validated": ledger_result["all_snapshot_hashes_validated"],
        "dataset_sha256_validated": True,
        "snapshot_count": ledger_result["snapshot_count"],
        "credits_observed": ledger_result["credits_observed"],
    }
    if manifest["schedule_sha256"] != str(plan.get("schedule_sha256") or ""):
        raise ValueError("schedule file no longer matches the immutable request plan")
    manifest["manifest_sha256"] = canonical_digest(manifest)
    return manifest


def validate_dataset_manifest(
    manifest: Mapping[str, Any],
    *,
    dataset_path: str | Path,
) -> Dict[str, Any]:
    expected_manifest = str(manifest.get("manifest_sha256") or "")
    material = dict(manifest)
    material.pop("manifest_sha256", None)
    observed_manifest = canonical_digest(material)
    observed_dataset = sha256_file(dataset_path)
    blockers = []
    if not expected_manifest or expected_manifest != observed_manifest:
        blockers.append("dataset_manifest_sha256_mismatch")
    if observed_dataset != str(manifest.get("dataset_sha256") or ""):
        blockers.append("dataset_sha256_mismatch")
    for flag in (
        "plan_sha256_validated",
        "ledger_sha256_validated",
        "all_snapshot_hashes_validated",
        "dataset_sha256_validated",
    ):
        if manifest.get(flag) is not True:
            blockers.append(f"dataset_manifest_flag_false:{flag}")
    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "manifest_sha256": expected_manifest,
        "dataset_sha256": observed_dataset,
        "dataset_sha256_validated": observed_dataset
        == str(manifest.get("dataset_sha256") or ""),
    }


def build_audit_claim(
    *,
    experiment_id: str,
    candidate_id: str,
    artifact_sha256: str,
    dataset_sha256: str,
    dataset_manifest_sha256: str,
) -> Dict[str, Any]:
    claim = {
        "version": VERSION,
        "record_type": "mlb_untouched_audit_write_once_claim",
        "write_once": True,
        "experiment_id": str(experiment_id),
        "candidate_id": str(candidate_id),
        "artifact_sha256": str(artifact_sha256),
        "dataset_sha256": str(dataset_sha256),
        "dataset_manifest_sha256": str(dataset_manifest_sha256),
        "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    claim["claim_sha256"] = canonical_digest(claim)
    return claim


def claim_audit_once_local(path: str | Path, claim: Mapping[str, Any]) -> Dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(claim), sort_keys=True, indent=2) + "\n"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(payload)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return dict(claim)


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError("audit claim URI must be a complete s3://bucket/key URI")
    return parsed.netloc, parsed.path.lstrip("/")


def claim_audit_once_s3(
    uri: str,
    claim: Mapping[str, Any],
    *,
    region: str = "us-east-1",
    client: Any = None,
) -> Dict[str, Any]:
    if client is None:
        import boto3

        client = boto3.client("s3", region_name=region)
    bucket, key = _parse_s3_uri(uri)
    body = json.dumps(dict(claim), sort_keys=True, indent=2).encode("utf-8") + b"\n"
    try:
        response = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
            Metadata={
                "experiment-id": str(claim.get("experiment_id") or ""),
                "artifact-sha256": str(claim.get("artifact_sha256") or ""),
                "claim-sha256": str(claim.get("claim_sha256") or ""),
            },
        )
    except Exception as exc:
        response_data = getattr(exc, "response", {}) or {}
        error = response_data.get("Error") or {}
        status = (response_data.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if error.get("Code") in {"PreconditionFailed", "ConditionalRequestConflict"} or status in {409, 412}:
            raise PermissionError("untouched audit was already claimed") from exc
        raise
    result = dict(claim)
    result["s3_uri"] = uri
    result["etag"] = str(response.get("ETag") or "").strip('"')
    result["version_id"] = response.get("VersionId")
    return result


def validate_audit_claim(
    claim: Mapping[str, Any],
    *,
    artifact_sha256: str,
    dataset_sha256: str,
    dataset_manifest_sha256: str,
) -> Dict[str, Any]:
    expected = str(claim.get("claim_sha256") or "")
    material = dict(claim)
    material.pop("claim_sha256", None)
    observed = canonical_digest(material)
    blockers = []
    if claim.get("write_once") is not True:
        blockers.append("audit_claim_not_write_once")
    if expected != observed or not expected:
        blockers.append("audit_claim_sha256_mismatch")
    if str(claim.get("artifact_sha256") or "") != artifact_sha256:
        blockers.append("audit_claim_artifact_sha256_mismatch")
    if str(claim.get("dataset_sha256") or "") != dataset_sha256:
        blockers.append("audit_claim_dataset_sha256_mismatch")
    if str(claim.get("dataset_manifest_sha256") or "") != dataset_manifest_sha256:
        blockers.append("audit_claim_manifest_sha256_mismatch")
    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "audit_claim_write_once": claim.get("write_once") is True,
        "audit_claim_sha256": expected,
        "audit_claim_sha256_validated": expected == observed and bool(expected),
    }


def build_evidence_chain(
    *,
    dataset_manifest: Mapping[str, Any],
    artifact_sha256: str,
    audit_claim: Mapping[str, Any],
) -> Dict[str, Any]:
    claim_result = validate_audit_claim(
        audit_claim,
        artifact_sha256=artifact_sha256,
        dataset_sha256=str(dataset_manifest.get("dataset_sha256") or ""),
        dataset_manifest_sha256=str(dataset_manifest.get("manifest_sha256") or ""),
    )
    chain = {
        "version": VERSION,
        "schedule_sha256": str(dataset_manifest.get("schedule_sha256") or ""),
        "outcomes_sha256": str(dataset_manifest.get("outcomes_sha256") or ""),
        "plan_sha256": str(dataset_manifest.get("plan_sha256") or ""),
        "ledger_sha256": str(dataset_manifest.get("ledger_sha256") or ""),
        "snapshot_manifest_sha256": str(dataset_manifest.get("snapshot_manifest_sha256") or ""),
        "dataset_sha256": str(dataset_manifest.get("dataset_sha256") or ""),
        "artifact_sha256": artifact_sha256,
        "audit_claim_sha256": claim_result["audit_claim_sha256"],
        "plan_sha256_validated": dataset_manifest.get("plan_sha256_validated") is True,
        "ledger_sha256_validated": dataset_manifest.get("ledger_sha256_validated") is True,
        "all_snapshot_hashes_validated": dataset_manifest.get("all_snapshot_hashes_validated") is True,
        "dataset_sha256_validated": dataset_manifest.get("dataset_sha256_validated") is True,
        "artifact_sha256_validated": bool(artifact_sha256),
        "audit_claim_write_once": claim_result["audit_claim_write_once"],
        "audit_claim_sha256_validated": claim_result["audit_claim_sha256_validated"],
        "claim_blockers": claim_result["blockers"],
    }
    chain["evidence_chain_sha256"] = canonical_digest(chain)
    return chain


def evidence_blockers(report: Mapping[str, Any]) -> list[str]:
    chain = report.get("evidence_chain") or {}
    blockers = []
    for flag in REQUIRED_EVIDENCE_FLAGS:
        if chain.get(flag) is not True:
            blockers.append(f"evidence_flag_false:{flag}")
    for key in REQUIRED_EVIDENCE_DIGESTS:
        value = str(chain.get(key) or "")
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
            blockers.append(f"evidence_digest_invalid:{key}")
    expected = str(chain.get("evidence_chain_sha256") or "")
    material = dict(chain)
    material.pop("evidence_chain_sha256", None)
    if not expected or expected != canonical_digest(material):
        blockers.append("evidence_chain_sha256_mismatch")
    blockers.extend(str(value) for value in chain.get("claim_blockers") or [])
    return sorted(set(blockers))


def assert_evidence_chain(report: Mapping[str, Any]) -> None:
    blockers = evidence_blockers(report)
    if blockers:
        raise PermissionError({"evidence_chain_blockers": blockers})
