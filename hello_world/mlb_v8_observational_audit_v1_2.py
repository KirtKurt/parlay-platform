"""Candidate-lifecycle hardening for the MLB V8 observational audit.

A trainer report advances its frozen retrospective boundary whenever a new slate
settles. Replacing the observational candidate on every such report would move the
boundary forward before any post-boundary row could be graded. This layer keeps one
candidate active until its independent evidence window is complete. A newer trainer
configuration is recorded as pending and may replace the active candidate only on a
later cycle after completion.

Candidate artifacts are immutable. A runtime/schema upgrade therefore must not mutate
an already frozen candidate merely to replace its version string. Known compatible
legacy candidates are accepted only after their authority, digest, model bundle and
frozen boundary are independently revalidated under the current runtime.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import mlb_v8_observational_audit_v1 as _core
import mlb_v8_observational_audit_v1_1 as _stable


VERSION = "MLB-V8-OBSERVATIONAL-AUDIT-v1.2-latched-evidence-window"
LEGACY_CANDIDATE_VERSIONS = frozenset(
    {
        "MLB-V8-OBSERVATIONAL-AUDIT-v1-independent-non-promotable",
        "MLB-V8-OBSERVATIONAL-AUDIT-v1.1-stable-identities",
        "MLB-V8-OBSERVATIONAL-AUDIT-v1.2-latched-evidence-window",
    }
)
_STRICT_VERIFY_CANDIDATE = _core.verify_candidate
_core.VERSION = VERSION
_stable.VERSION = VERSION


def verify_candidate(candidate: Mapping[str, Any]) -> None:
    """Verify current or explicitly compatible immutable shadow candidates.

    The candidate's original version remains part of its content-addressed digest.
    Compatibility never rewrites that version or digest and never relaxes any
    authority, bundle, model or corpus-boundary invariant.
    """

    candidate_version = str(candidate.get("version") or "")
    if candidate_version == VERSION:
        _STRICT_VERIFY_CANDIDATE(candidate)
        return
    if candidate_version not in LEGACY_CANDIDATE_VERSIONS:
        raise ValueError("observational candidate version mismatch")
    if (
        candidate.get("authority") != "SHADOW_ONLY"
        or candidate.get("observationalOnly") is not True
        or candidate.get("promotionEligible") is not False
        or candidate.get("promotionRequested") is not False
        or candidate.get("automaticWagerAllowed") is not False
        or candidate.get("productionAuthorityChanged") is not False
    ):
        raise ValueError("observational candidate attempted to change authority")
    material = {
        key: item for key, item in candidate.items() if key != "candidateDigest"
    }
    if candidate.get("candidateDigest") != _core._sha(material):
        raise ValueError("observational candidate digest mismatch")
    _core.runtime.verify_bundle(candidate.get("modelBundle") or {})
    if candidate.get("modelDigest") != (
        candidate.get("modelBundle") or {}
    ).get("modelDigest"):
        raise ValueError("observational model digest mismatch")
    if not candidate.get("frozenCorpusLastDate"):
        raise ValueError("observational corpus boundary is missing")


_COMPATIBLE_VERIFY_CANDIDATE = verify_candidate
_core.verify_candidate = _COMPATIBLE_VERIFY_CANDIDATE
_stable.verify_candidate = _COMPATIBLE_VERIFY_CANDIDATE


def _load_reusable_candidate(
    pointer: Mapping[str, Any],
    *,
    current_source_identity: str,
    s3: Any,
) -> Tuple[Optional[Dict[str, Any]], Mapping[str, Any], bool]:
    artifact = pointer.get("candidateArtifact") or {}
    if not isinstance(artifact, Mapping) or not artifact:
        return None, {}, False
    complete = pointer.get("observationalEvidenceComplete") is True
    same_source = pointer.get("sourceTrainingIdentity") == current_source_identity
    if complete and not same_source:
        return None, {}, False
    candidate = _core._load_pointer_value(s3, artifact)
    _core.verify_candidate(candidate)
    return candidate, copy.deepcopy(dict(artifact)), not same_source


def advance(
    *,
    training: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    table: Any,
    s3: Any,
    bucket: str,
    created_at: str,
) -> Dict[str, Any]:
    configuration = _core.best_learned_configuration(training)
    latest_training_identity = _core._source_identity(training, configuration)
    pointer, revision = _core._current_pointer(table)
    candidate, candidate_pointer, held_for_evidence = _load_reusable_candidate(
        pointer,
        current_source_identity=latest_training_identity,
        s3=s3,
    )

    if candidate is None:
        candidate = _core.build_candidate(training, records)
        candidate_key = (
            "mlb/v8/observational-candidates/"
            f"{candidate['modelDigest']}/{candidate['candidateDigest']}.json"
        )
        candidate_pointer = _core._put_immutable(
            s3,
            bucket=bucket,
            key=candidate_key,
            value=candidate,
            record_type="mlb-v8-observational-candidate",
        )

    active_source_identity = str(candidate["sourceTrainingIdentity"])
    candidate_version = str(candidate.get("version") or "")
    candidate_compatibility_mode = candidate_version != VERSION
    audit = _core.evaluate_candidate(candidate, records)
    grade_pointers = []
    for row in audit.get("gradedRows") or []:
        key = (
            "mlb/v8/observational-grades/"
            f"{candidate['candidateDigest']}/{row['slateDateEt']}/"
            f"{_core._safe_game_id(row['officialGamePk'])}.json"
        )
        grade_pointers.append(
            _core._put_immutable(
                s3,
                bucket=bucket,
                key=key,
                value=row,
                record_type="mlb-v8-observational-grade-row",
            )
        )
    audit["gradeArtifacts"] = grade_pointers
    audit["gradeArtifactCount"] = len(grade_pointers)
    audit["auditDigest"] = _core._sha(
        {key: item for key, item in audit.items() if key != "auditDigest"}
    )
    audit_key = (
        "mlb/v8/observational-audits/"
        f"{candidate['candidateDigest']}/{audit['auditDigest']}.json"
    )
    audit_pointer = _core._put_immutable(
        s3,
        bucket=bucket,
        key=audit_key,
        value=audit,
        record_type="mlb-v8-observational-audit",
    )

    pending_replacement = latest_training_identity != active_source_identity
    if pointer.get("auditDigest") == audit["auditDigest"]:
        pointer_revision = revision
    else:
        pointer_revision = _core._write_pointer(
            table,
            previous_revision=revision,
            created_at=created_at,
            data={
                "version": VERSION,
                "status": audit["status"],
                "sourceTrainingIdentity": active_source_identity,
                "latestTrainerSourceIdentity": latest_training_identity,
                "candidateGenerationHeldForEvidence": held_for_evidence,
                "pendingReplacementAvailable": pending_replacement,
                "candidateVersion": candidate_version,
                "candidateCompatibilityMode": candidate_compatibility_mode,
                "candidateDigest": candidate["candidateDigest"],
                "modelDigest": candidate["modelDigest"],
                "frozenCorpusLastDate": candidate["frozenCorpusLastDate"],
                "candidateArtifact": candidate_pointer,
                "auditDigest": audit["auditDigest"],
                "auditArtifact": audit_pointer,
                "sampleSize": audit["sampleSize"],
                "dayCount": audit["dayCount"],
                "wins": audit["wins"],
                "losses": audit["losses"],
                "pushes": audit["pushes"],
                "voids": audit["voids"],
                "observationalEvidenceComplete": audit[
                    "observationalEvidenceComplete"
                ],
                "promotionEligible": False,
                "promotionRequested": False,
                "automaticWagerAllowed": False,
                "productionAuthorityChanged": False,
            },
        )

    report = {
        key: copy.deepcopy(value)
        for key, value in audit.items()
        if key != "gradedRows"
    }
    report.update(
        {
            "createdAtUtc": created_at,
            "pointerRevision": pointer_revision,
            "sourceTrainingIdentity": active_source_identity,
            "latestTrainerSourceIdentity": latest_training_identity,
            "candidateGenerationHeldForEvidence": held_for_evidence,
            "pendingReplacementAvailable": pending_replacement,
            "candidateVersion": candidate_version,
            "candidateCompatibilityMode": candidate_compatibility_mode,
            "candidateArtifact": candidate_pointer,
            "auditArtifact": audit_pointer,
            "retrospectiveGuardEligible": candidate[
                "retrospectiveGuardEligible"
            ],
            "retrospectiveGuardErrors": candidate["retrospectiveGuardErrors"],
            "promotionEligible": False,
            "promotionRequested": False,
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
        }
    )
    report["reportDigest"] = _core._sha(report)
    return report


_core.advance = advance
_stable.advance = advance

for _name in dir(_stable):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_stable, _name)

globals()["VERSION"] = VERSION
globals()["LEGACY_CANDIDATE_VERSIONS"] = LEGACY_CANDIDATE_VERSIONS
globals()["verify_candidate"] = _COMPATIBLE_VERIFY_CANDIDATE
globals()["advance"] = advance
