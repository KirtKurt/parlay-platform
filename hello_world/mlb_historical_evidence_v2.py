from __future__ import annotations

from typing import Any, Dict, Mapping

from mlb_historical_evidence_v1 import *  # noqa: F401,F403
from mlb_historical_evidence_v1 import VERSION as BASE_VERSION
from mlb_historical_policy_v1 import canonical_digest

VERSION = "MLB-HISTORICAL-EVIDENCE-v15.11.1.1"
_CANONICAL_AUDIT_CLAIM_FIELDS = (
    "version",
    "record_type",
    "write_once",
    "experiment_id",
    "candidate_id",
    "artifact_sha256",
    "dataset_sha256",
    "dataset_manifest_sha256",
    "claimed_at_utc",
)


def _canonical_claim_material(claim: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: claim.get(key) for key in _CANONICAL_AUDIT_CLAIM_FIELDS}


def validate_audit_claim(
    claim: Mapping[str, Any],
    *,
    artifact_sha256: str,
    dataset_sha256: str,
    dataset_manifest_sha256: str,
) -> Dict[str, Any]:
    expected = str(claim.get("claim_sha256") or "")
    observed = canonical_digest(_canonical_claim_material(claim))
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
    if claim.get("version") not in {BASE_VERSION, VERSION}:
        blockers.append("audit_claim_version_invalid")
    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "audit_claim_write_once": claim.get("write_once") is True,
        "audit_claim_sha256": expected,
        "audit_claim_sha256_validated": expected == observed and bool(expected),
        "audit_claim_transport": {
            "s3_uri": claim.get("s3_uri"),
            "etag": claim.get("etag"),
            "version_id": claim.get("version_id"),
        },
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
        "audit_claim_transport": claim_result["audit_claim_transport"],
    }
    material = dict(chain)
    material.pop("audit_claim_transport", None)
    chain["evidence_chain_sha256"] = canonical_digest(material)
    return chain


def evidence_blockers(report: Mapping[str, Any]) -> list[str]:
    from mlb_historical_evidence_v1 import (
        REQUIRED_EVIDENCE_DIGESTS,
        REQUIRED_EVIDENCE_FLAGS,
    )

    chain = report.get("evidence_chain") or {}
    blockers = []
    for flag in REQUIRED_EVIDENCE_FLAGS:
        if chain.get(flag) is not True:
            blockers.append(f"evidence_flag_false:{flag}")
    for key in REQUIRED_EVIDENCE_DIGESTS:
        value = str(chain.get(key) or "")
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value.lower()
        ):
            blockers.append(f"evidence_digest_invalid:{key}")
    expected = str(chain.get("evidence_chain_sha256") or "")
    material = dict(chain)
    material.pop("evidence_chain_sha256", None)
    material.pop("audit_claim_transport", None)
    if not expected or expected != canonical_digest(material):
        blockers.append("evidence_chain_sha256_mismatch")
    blockers.extend(str(value) for value in chain.get("claim_blockers") or [])
    return sorted(set(blockers))


def assert_evidence_chain(report: Mapping[str, Any]) -> None:
    blockers = evidence_blockers(report)
    if blockers:
        raise PermissionError({"evidence_chain_blockers": blockers})
