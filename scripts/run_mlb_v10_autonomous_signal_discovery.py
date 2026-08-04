#!/usr/bin/env python3
"""Run leakage-safe V10 against the canonical settled historical corpus."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

MAX_REPORT_BYTES = 20_000_000
CADENCE_VERSION = "MLB-V10-DISCOVERY-CADENCE-v2-material-state-fast-path"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
    if len(payload.encode()) > MAX_REPORT_BYTES:
        signals = list(value.get("signals") or [])
        value = dict(value)
        value["signals"] = signals[:100]
        value["reportTruncated"] = True
        value["untruncatedSignalCount"] = len(signals)
        payload = json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
    path.write_text(payload)


def _load_previous(path: Path):
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except Exception:
        return None


def _state_anchor(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return only material corpus fields; heartbeat revisions are excluded."""

    completed = list(state.get("completedSlates") or [])
    return {
        "version": CADENCE_VERSION,
        "eligibleGameCount": int(state.get("eligibleGameCount") or 0),
        "completeSlateCount": int(
            state.get("completeSlateCount") or len(completed) or 0
        ),
        "featureRematerializedSlateCount": int(
            state.get("featureRematerializedSlateCount") or 0
        ),
        "featureDatasetVersion": str(state.get("featureDatasetVersion") or ""),
    }


def _previous_state_anchor(previous: Mapping[str, Any]) -> dict[str, Any]:
    explicit = previous.get("cadenceAnchor")
    if isinstance(explicit, Mapping):
        return {
            "version": CADENCE_VERSION,
            "eligibleGameCount": int(explicit.get("eligibleGameCount") or 0),
            "completeSlateCount": int(explicit.get("completeSlateCount") or 0),
            "featureRematerializedSlateCount": int(
                explicit.get("featureRematerializedSlateCount") or 0
            ),
            "featureDatasetVersion": str(
                explicit.get("featureDatasetVersion") or ""
            ),
        }
    state = previous.get("state") if isinstance(previous.get("state"), Mapping) else {}
    proof = (
        previous.get("canonicalCorpusProof")
        if isinstance(previous.get("canonicalCorpusProof"), Mapping)
        else {}
    )
    return {
        "version": CADENCE_VERSION,
        "eligibleGameCount": int(
            state.get("eligibleGameCount")
            or previous.get("settledGameCount")
            or 0
        ),
        "completeSlateCount": int(
            state.get("completeSlateCount")
            or proof.get("completedSlateCount")
            or 0
        ),
        "featureRematerializedSlateCount": int(
            state.get("featureRematerializedSlateCount")
            or state.get("completeSlateCount")
            or proof.get("completedSlateCount")
            or 0
        ),
        "featureDatasetVersion": str(
            state.get("featureDatasetVersion")
            or previous.get("featureDatasetVersion")
            or ""
        ),
    }


def _material_state_unchanged(
    previous: Any,
    state: Mapping[str, Any],
    *,
    expected_version: str,
    force_full: bool,
) -> bool:
    if force_full or not isinstance(previous, Mapping):
        return False
    if previous.get("ok") is not True or previous.get("version") != expected_version:
        return False
    current = _state_anchor(state)
    prior = _previous_state_anchor(previous)
    compared = (
        "eligibleGameCount",
        "completeSlateCount",
        "featureRematerializedSlateCount",
        "featureDatasetVersion",
    )
    # Older reports did not persist featureDatasetVersion.  Missing on both
    # sides is acceptable, but a one-sided value is material and forces proof.
    return all(current.get(key) == prior.get(key) for key in compared)


def _stable_row_fingerprint(row: Mapping[str, Any]) -> str:
    """Fingerprint only immutable pregame identity and features, never outcomes."""
    material = {
        "slateDateEt": row.get("slateDateEt"),
        "officialGamePk": row.get("officialGamePk") or row.get("gameId") or row.get("eventId"),
        "predictionLockAtUtc": row.get("predictionLockAtUtc"),
        "homeSignal": row.get("homeSignal"),
        "awaySignal": row.get("awaySignal"),
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _assert_not_contradicted(row: Mapping[str, Any], key: str, expected: Any) -> None:
    if key in row and row.get(key) is not expected and row.get(key) != expected:
        raise RuntimeError(f"canonical row explicitly contradicts {key}:{row.get(key)!r}")


def _load_canonical_records(handler: Any, state: Mapping[str, Any]) -> tuple[list[dict], dict]:
    """Load immutable complete-slate artifacts and derive row eligibility from their proofs.

    Historical dataset rows predate the row-level V10 eligibility fields. The immutable
    artifact contains the stronger authority: complete full-slate coverage, per-game T-45
    clipping, post-lock exclusion, checksums, and a deterministic slate fingerprint. We
    validate those facts before adding the row-level aliases consumed by V10.
    """
    lock_minutes = int(getattr(handler.optimizer, "FULL_SLATE_LOCK_MINUTES", 0) or 0)
    if lock_minutes != 45:
        raise RuntimeError(f"historical optimizer lock contract is not T-45:{lock_minutes}")

    completed_slates = list(state.get("completedSlates") or [])
    if not completed_slates:
        raise RuntimeError("historical optimizer has no completed slate pointers")

    records: list[dict] = []
    derived_flag_rows = 0
    artifact_authorities: list[dict] = []
    for slate in completed_slates:
        if not isinstance(slate, Mapping):
            raise RuntimeError("completed slate pointer is malformed")
        artifact = slate.get("artifact") or {}
        key = str(artifact.get("key") or "")
        if not key:
            raise RuntimeError("completed slate artifact pointer is missing")
        dataset, pointer = handler._get_s3_json(key)
        expected_sha = str(artifact.get("sha256") or "")
        observed_sha = str(pointer.get("sha256") or "")
        if expected_sha and observed_sha and expected_sha != observed_sha:
            raise RuntimeError(f"completed slate artifact checksum changed:{key}")

        rows = list(dataset.get("records") or [])
        official_count = int(dataset.get("officialGameCount") or 0)
        eligible_count = int(dataset.get("eligibleGameCount") or 0)
        exclusions = list(dataset.get("exclusions") or [])
        checks = {
            "completeSlate": dataset.get("completeSlate") is True,
            "postLockDataExcluded": dataset.get("postLockDataExcluded") is True,
            "gameSpecificLockClipping": dataset.get("gameSpecificLockClipping") is True,
            "exactSlateCoverage": float(dataset.get("exactSlateCoverage") or 0.0) >= 1.0 - 1e-12,
            "recordCountMatchesOfficial": official_count > 0 and len(rows) == official_count,
            "eligibleCountMatchesOfficial": eligible_count == official_count,
            "noDatasetExclusions": not exclusions,
        }
        if not all(checks.values()):
            raise RuntimeError(
                "completed slate lost canonical integrity proof:"
                + json.dumps({"key": key, "checks": checks, "exclusionCount": len(exclusions)}, sort_keys=True)
            )

        expected_fingerprint = str(dataset.get("fingerprint") or slate.get("fingerprint") or "")
        calculated_fingerprint = str(handler.optimizer.dataset_fingerprint(rows) or "")
        if not expected_fingerprint or expected_fingerprint != calculated_fingerprint:
            raise RuntimeError(f"completed slate fingerprint mismatch:{key}")
        if slate.get("fingerprint") and str(slate.get("fingerprint")) != calculated_fingerprint:
            raise RuntimeError(f"state slate fingerprint mismatch:{key}")

        seen_game_ids: set[str] = set()
        slate_date = str(dataset.get("slateDateEt") or slate.get("slateDateEt") or "")
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise RuntimeError(f"completed slate contains malformed row:{key}")
            row = copy.deepcopy(dict(raw))
            game_id = str(row.get("officialGamePk") or row.get("gameId") or row.get("eventId") or "")
            if not game_id or game_id in seen_game_ids:
                raise RuntimeError(f"completed slate game identity is missing or duplicated:{key}:{game_id}")
            seen_game_ids.add(game_id)
            if not slate_date or str(row.get("slateDateEt") or "") != slate_date:
                raise RuntimeError(f"completed slate row date authority mismatch:{key}:{game_id}")
            if row.get("postLockDataExcluded") is not True:
                raise RuntimeError(f"row lost post-lock exclusion proof:{key}:{game_id}")
            if row.get("gameSpecificLockClipping") is not True:
                raise RuntimeError(f"row lost per-game clipping proof:{key}:{game_id}")
            if not row.get("predictionLockAtUtc"):
                raise RuntimeError(f"row prediction lock timestamp is missing:{key}:{game_id}")
            if not isinstance(row.get("homeSignal"), Mapping) or not isinstance(row.get("awaySignal"), Mapping):
                raise RuntimeError(f"row pregame signal pair is missing:{key}:{game_id}")
            if row.get("homeWon") not in (True, False, 0, 1):
                raise RuntimeError(f"row settled label is invalid:{key}:{game_id}")

            _assert_not_contradicted(row, "trainingEligible", True)
            _assert_not_contradicted(row, "canonicalLockValid", True)
            _assert_not_contradicted(row, "duplicateContaminated", False)
            existing_cutoff = str(row.get("featureCutoff") or row.get("perGameFeatureCutoff") or "")
            if existing_cutoff and "45" not in existing_cutoff:
                raise RuntimeError(f"row feature cutoff contradicts T-45:{key}:{game_id}:{existing_cutoff}")

            if "trainingEligible" not in row or "canonicalLockValid" not in row or "duplicateContaminated" not in row or not existing_cutoff:
                derived_flag_rows += 1
            row["trainingEligible"] = True
            row["canonicalLockValid"] = True
            row["duplicateContaminated"] = False
            row["featureCutoff"] = existing_cutoff or "each_game_t_minus_45"
            row["featureVectorFingerprint"] = str(
                row.get("featureVectorFingerprint") or row.get("fingerprint") or _stable_row_fingerprint(row)
            )
            row["canonicalEligibilityAuthority"] = "IMMUTABLE_COMPLETE_SLATE_ARTIFACT"
            records.append(row)

        artifact_authorities.append({
            "slateDateEt": slate_date,
            "artifactKey": key,
            "officialGameCount": official_count,
            "fingerprint": calculated_fingerprint,
        })

    state_eligible = int(state.get("eligibleGameCount") or 0)
    if state_eligible and state_eligible != len(records):
        raise RuntimeError(
            f"historical state eligible count disagrees with immutable artifacts:{state_eligible}!={len(records)}"
        )
    return records, {
        "authority": "IMMUTABLE_COMPLETE_SLATE_ARTIFACTS",
        "lockContract": "EACH_GAME_T_MINUS_45",
        "completedSlateCount": len(completed_slates),
        "recordCountLoaded": len(records),
        "rowEligibilityAliasesDerived": derived_flag_rows,
        "artifactChecksumValidationApplied": True,
        "slateFingerprintValidationApplied": True,
        "rowLockProofValidationApplied": True,
        "artifactAuthorities": artifact_authorities[:25],
        "artifactAuthoritiesTruncated": len(artifact_authorities) > 25,
    }


def _status_state(state: Mapping[str, Any], record_count: int) -> dict[str, Any]:
    return {
        "phase": state.get("phase"),
        "eligibleGameCount": state.get("eligibleGameCount") or record_count,
        "completeSlateCount": state.get("completeSlateCount"),
        "featureRematerializedSlateCount": state.get(
            "featureRematerializedSlateCount"
        ),
        "featureDatasetVersion": state.get("featureDatasetVersion"),
        "currentDate": state.get("currentDate"),
        "currentSlotIndex": state.get("currentSlotIndex"),
        "trainingRecordCountLoaded": record_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--previous")
    parser.add_argument("--force-full", action="store_true")
    args = parser.parse_args()
    path = Path(args.output)
    previous_path = Path(args.previous) if args.previous else path
    started = datetime.now(timezone.utc)

    try:
        import mlb_historical_optimizer_handler as handler
        import mlb_v10_autonomous_signal_discovery_v1 as v10
        import mlb_v10_permutation_control_v2 as permutation_v2

        permutation_v2.install(v10)
        state = handler._load_state()
        if not isinstance(state, dict):
            raise RuntimeError("historical optimizer state is missing")
        previous = _load_previous(previous_path)
        if _material_state_unchanged(
            previous,
            state,
            expected_version=v10.VERSION,
            force_full=args.force_full,
        ):
            value = dict(previous)
            value.update(
                {
                    "incrementalNoChange": True,
                    "fastNoChange": True,
                    "fullRebuild": False,
                    "lastCheckedAtUtc": datetime.now(timezone.utc).isoformat(),
                    "cadenceVersion": CADENCE_VERSION,
                    "cadenceAnchor": _state_anchor(state),
                    "learningStatus": "WAITING_FOR_NEW_CANONICAL_SLATE_OR_FEATURE_DATASET",
                    "stalledStage": None,
                    "blockers": [],
                }
            )
            prior_count = int(
                (previous.get("state") or {}).get("trainingRecordCountLoaded")
                or previous.get("settledGameCount")
                or state.get("eligibleGameCount")
                or 0
            )
            value["state"] = _status_state(state, prior_count)
            _write(path, value)
            print(json.dumps({
                "ok": True,
                "version": value.get("version"),
                "settledGameCount": value.get("settledGameCount"),
                "datasetFingerprint": value.get("datasetFingerprint"),
                "incrementalNoChange": True,
                "fastNoChange": True,
                "fullRebuild": False,
                "cadenceAnchor": value.get("cadenceAnchor"),
                "output": str(path),
            }, indent=2, sort_keys=True))
            return 0

        records, canonical_proof = _load_canonical_records(handler, state)
        if not records:
            raise RuntimeError("historical training corpus is empty")

        clean, integrity = v10._deduplicate(records)
        if not clean:
            raise RuntimeError(f"no canonical settled records: {integrity}")
        fingerprint = v10.dataset_fingerprint(clean)
        unchanged = bool(
            previous
            and previous.get("datasetFingerprint") == fingerprint
            and previous.get("version") == v10.VERSION
            and previous.get("ok") is True
            and not args.force_full
        )
        if unchanged:
            value = dict(previous)
            value["incrementalNoChange"] = True
            value["fastNoChange"] = False
            value["lastCheckedAtUtc"] = datetime.now(timezone.utc).isoformat()
            value["canonicalCorpusProof"] = canonical_proof
            value["permutationControlImplementation"] = permutation_v2.VERSION
            value["prospectiveShadow"] = v10.evaluate_frozen_registry(clean, previous)
            value["cadenceVersion"] = CADENCE_VERSION
            value["cadenceAnchor"] = _state_anchor(state)
            value["state"] = _status_state(state, len(records))
            _write(path, value)
            print(json.dumps({
                "ok": True,
                "version": value.get("version"),
                "settledGameCount": value.get("settledGameCount"),
                "datasetFingerprint": fingerprint,
                "incrementalNoChange": True,
                "fastNoChange": False,
                "fullRebuild": False,
                "prospectiveShadowStatus": (value.get("prospectiveShadow") or {}).get("status"),
                "permutationControlImplementation": permutation_v2.VERSION,
                "output": str(path),
            }, indent=2, sort_keys=True))
            return 0

        report = v10.discover(clean, previous_report=previous if isinstance(previous, dict) and previous.get("ok") is True else None)
        completed = datetime.now(timezone.utc)
        report.update({
            "ok": True,
            "proofType": "MLB_V10_AUTONOMOUS_DISCOVERY_RUN",
            "sourceSha": os.environ.get("GITHUB_SHA"),
            "runId": os.environ.get("GITHUB_RUN_ID"),
            "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "startedAtUtc": started.isoformat(),
            "completedAtUtc": completed.isoformat(),
            "durationSeconds": round((completed - started).total_seconds(), 3),
            "blockers": report.get("blockers") or [],
            "storageReader": "immutable_complete_slate_artifacts_with_derived_row_eligibility",
            "canonicalCorpusProof": canonical_proof,
            "permutationControlImplementation": permutation_v2.VERSION,
            "incrementalNoChange": False,
            "fastNoChange": False,
            "reusedPriorRegistryForProspectiveShadow": bool(previous and previous.get("ok") is True),
            "fullRebuild": True,
            "cadenceVersion": CADENCE_VERSION,
            "cadenceAnchor": _state_anchor(state),
            "learningStatus": "DISCOVERY_COMPLETED",
            "stalledStage": None,
        })
        report["state"] = _status_state(state, len(records))
        _write(path, report)
        print(json.dumps({
            "ok": True,
            "version": report.get("version"),
            "settledGameCount": report.get("settledGameCount"),
            "generatedPatternCount": report.get("generatedPatternCount"),
            "retainedPatternCount": report.get("retainedPatternCount"),
            "predictiveSignalCount": report.get("predictiveSignalCount"),
            "datasetFingerprint": report.get("datasetFingerprint"),
            "prospectiveShadowStatus": (report.get("prospectiveShadow") or {}).get("status"),
            "permutationControlImplementation": permutation_v2.VERSION,
            "incrementalNoChange": False,
            "fastNoChange": False,
            "fullRebuild": True,
            "durationSeconds": report.get("durationSeconds"),
            "output": str(path),
        }, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        completed = datetime.now(timezone.utc)
        failure = {
            "ok": False,
            "proofType": "MLB_V10_AUTONOMOUS_DISCOVERY_RUN",
            "mode": "CHRONOLOGICAL_SIDE_APPLICABLE_DISCOVERY",
            "productionAuthority": False,
            "mayWriteChampion": False,
            "mayPublishPicks": False,
            "sourceSha": os.environ.get("GITHUB_SHA"),
            "runId": os.environ.get("GITHUB_RUN_ID"),
            "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "startedAtUtc": started.isoformat(),
            "completedAtUtc": completed.isoformat(),
            "durationSeconds": round((completed - started).total_seconds(), 3),
            "stalledStage": "V10_CORPUS_LOAD_OR_DISCOVERY",
            "errorType": type(exc).__name__,
            "error": str(exc),
            "tracebackTail": traceback.format_exc()[-12000:],
            "blockers": ["v10_discovery_execution_failed"],
            "cadenceVersion": CADENCE_VERSION,
        }
        _write(path, failure)
        print(json.dumps(failure, indent=2, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
