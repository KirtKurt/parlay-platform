from __future__ import annotations

import importlib.util
from pathlib import Path

from hello_world.mlb_historical_evidence_v1 import build_audit_claim
from hello_world.mlb_historical_evidence_v2 import (
    assert_evidence_chain,
    build_evidence_chain,
    evidence_blockers,
    validate_audit_claim,
)


def _manifest():
    return {
        "schedule_sha256": "1" * 64,
        "outcomes_sha256": "2" * 64,
        "plan_sha256": "3" * 64,
        "ledger_sha256": "4" * 64,
        "snapshot_manifest_sha256": "5" * 64,
        "dataset_sha256": "6" * 64,
        "manifest_sha256": "7" * 64,
        "plan_sha256_validated": True,
        "ledger_sha256_validated": True,
        "all_snapshot_hashes_validated": True,
        "dataset_sha256_validated": True,
    }


def test_s3_transport_metadata_does_not_corrupt_the_canonical_claim():
    claim = build_audit_claim(
        experiment_id="exp-1",
        candidate_id="candidate-1",
        artifact_sha256="8" * 64,
        dataset_sha256="6" * 64,
        dataset_manifest_sha256="7" * 64,
    )
    claim.update(
        {
            "s3_uri": "s3://evidence/audits/exp-1.json",
            "etag": "etag-value",
            "version_id": "version-1",
        }
    )
    result = validate_audit_claim(
        claim,
        artifact_sha256="8" * 64,
        dataset_sha256="6" * 64,
        dataset_manifest_sha256="7" * 64,
    )
    assert result["ok"] is True
    assert result["audit_claim_sha256_validated"] is True
    assert result["audit_claim_transport"]["version_id"] == "version-1"


def test_canonical_claim_mutation_is_rejected_even_with_valid_transport_metadata():
    claim = build_audit_claim(
        experiment_id="exp-1",
        candidate_id="candidate-1",
        artifact_sha256="8" * 64,
        dataset_sha256="6" * 64,
        dataset_manifest_sha256="7" * 64,
    )
    claim.update({"s3_uri": "s3://evidence/audits/exp-1.json", "etag": "etag"})
    claim["candidate_id"] = "retuned-candidate"
    result = validate_audit_claim(
        claim,
        artifact_sha256="8" * 64,
        dataset_sha256="6" * 64,
        dataset_manifest_sha256="7" * 64,
    )
    assert result["ok"] is False
    assert "audit_claim_sha256_mismatch" in result["blockers"]


def test_evidence_chain_excludes_transport_metadata_but_binds_all_canonical_digests():
    claim = build_audit_claim(
        experiment_id="exp-1",
        candidate_id="candidate-1",
        artifact_sha256="8" * 64,
        dataset_sha256="6" * 64,
        dataset_manifest_sha256="7" * 64,
    )
    claim.update(
        {
            "s3_uri": "s3://evidence/audits/exp-1.json",
            "etag": "etag-value",
            "version_id": "version-1",
        }
    )
    report = {
        "evidence_chain": build_evidence_chain(
            dataset_manifest=_manifest(),
            artifact_sha256="8" * 64,
            audit_claim=claim,
        )
    }
    assert evidence_blockers(report) == []
    assert_evidence_chain(report)

    report["evidence_chain"]["plan_sha256"] = "9" * 64
    assert "evidence_chain_sha256_mismatch" in evidence_blockers(report)


def test_v2_entrypoint_patches_the_hardened_command_globals():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/mlb_historical_daily_optimizer_v15_11_hardened_v2.py"
    spec = importlib.util.spec_from_file_location("mlb_hardened_v2_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.BASE.build_evidence_chain is build_evidence_chain
    assert module.BASE.evidence_blockers is evidence_blockers
    assert module.BASE.assert_evidence_chain is assert_evidence_chain
