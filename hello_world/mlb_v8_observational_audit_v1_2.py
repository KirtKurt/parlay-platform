"""Candidate-lifecycle hardening for the MLB V8 observational audit.

A trainer report advances its frozen retrospective boundary whenever a new slate
settles.  Replacing the observational candidate on every such report would move the
boundary forward before any post-boundary row could be graded.  This layer keeps one
candidate active until its independent evidence window is complete.  A newer trainer
configuration is recorded as pending and may replace the active candidate only on a
later cycle after completion.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import mlb_v8_observational_audit_v1 as _core
import mlb_v8_observational_audit_v1_1 as _stable


VERSION = "MLB-V8-OBSERVATIONAL-AUDIT-v1.2-latched-evidence-window"
_core.VERSION = VERSION
_stable.VERSION = VERSION


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
globals()["advance"] = advance
