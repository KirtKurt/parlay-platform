#!/usr/bin/env python3
"""Verify current-slate integrity without coupling historical recovery to maturity.

The production scoring guard remains fail closed. A manual historical R7
recovery, however, must be able to run before today's second canonical pull has
created movement features. This verifier accepts only those read-only maturity
conditions while continuing to reject roster, prediction, and team-identity
integrity failures.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


EXPECTED_MATURITY_BLOCKERS = frozenset(
    {
        "INSUFFICIENT_CANONICAL_PULL_HISTORY",
        "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE",
    }
)


def _count(summary: Mapping[str, Any], key: str) -> int:
    try:
        return int(summary.get(key) or 0)
    except (TypeError, ValueError):
        return -1


def _timestamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def assess_report(report: Mapping[str, Any], *, label: str) -> Dict[str, Any]:
    summary_value = report.get("summary")
    summary = summary_value if isinstance(summary_value, Mapping) else {}
    blockers_value = report.get("blockers")
    blockers = (
        {str(value) for value in blockers_value}
        if isinstance(blockers_value, list)
        else set()
    )
    unexpected = sorted(blockers - EXPECTED_MATURITY_BLOCKERS)
    acknowledged = sorted(blockers & EXPECTED_MATURITY_BLOCKERS)
    errors = []

    if report.get("readOnly") is not True:
        errors.append("guard_report_not_read_only")
    if not isinstance(blockers_value, list):
        errors.append("guard_blockers_not_a_list")
    if not isinstance(summary_value, Mapping):
        errors.append("guard_summary_not_an_object")
    if unexpected:
        errors.append("unexpected_current_slate_blocker")

    official_count = _count(summary, "officialGameCount")
    pull_count = _count(summary, "canonicalPullCount")
    missing_movement_count = _count(summary, "missingMovementCount")
    missing_prediction_count = _count(summary, "missingPredictionCount")
    invalid_prediction_count = _count(summary, "invalidPredictionTeamCount")
    invalid_movement_count = _count(summary, "invalidMovementTeamCount")
    numeric_counts = (
        official_count,
        pull_count,
        missing_movement_count,
        missing_prediction_count,
        invalid_prediction_count,
        invalid_movement_count,
    )

    if any(value < 0 for value in numeric_counts):
        errors.append("guard_summary_counts_invalid")
    if official_count <= 0:
        errors.append("official_roster_not_resolved")
    if missing_prediction_count != 0:
        errors.append("persisted_prediction_coverage_incomplete")
    if invalid_prediction_count != 0:
        errors.append("predicted_winner_identity_invalid")
    if invalid_movement_count != 0:
        errors.append("movement_team_identity_invalid")

    pull_maturity_blocked = "INSUFFICIENT_CANONICAL_PULL_HISTORY" in blockers
    movement_maturity_blocked = "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE" in blockers
    if pull_maturity_blocked is not (pull_count < 2):
        errors.append("canonical_pull_maturity_blocker_inconsistent")
    if movement_maturity_blocked is not (missing_movement_count > 0):
        errors.append("movement_maturity_blocker_inconsistent")

    latest_pull_value = summary.get("latestCanonicalPullAtUtc")
    latest_pull = _timestamp(latest_pull_value)
    if pull_count > 0 and latest_pull is None:
        errors.append("latest_canonical_pull_time_invalid")

    guard_passed = report.get("guardPassed")
    if guard_passed is not (not blockers):
        errors.append("guard_passed_does_not_match_blockers")

    return {
        "ok": not errors,
        "label": label,
        "slateDateEt": report.get("slateDateEt"),
        "scoringReady": guard_passed is True,
        "acknowledgedMaturityBlockers": acknowledged,
        "unexpectedBlockers": unexpected,
        "errors": errors,
        "summary": {
            "officialGameCount": official_count,
            "canonicalPullCount": pull_count,
            "latestCanonicalPullAtUtc": latest_pull_value,
            "movementFeatureGameCount": _count(summary, "movementFeatureGameCount"),
            "persistedPredictionGameCount": _count(
                summary, "persistedPredictionGameCount"
            ),
            "missingMovementCount": missing_movement_count,
            "missingPredictionCount": missing_prediction_count,
            "invalidPredictionTeamCount": invalid_prediction_count,
            "invalidMovementTeamCount": invalid_movement_count,
        },
    }


def build_proof(
    before: Mapping[str, Any],
    after: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    before_proof = assess_report(before, label="before")
    after_proof = assess_report(after, label="after") if after is not None else None
    continuity_errors = []

    if after_proof is not None:
        if before_proof.get("slateDateEt") != after_proof.get("slateDateEt"):
            continuity_errors.append("current_slate_date_changed")
        before_summary = before_proof["summary"]
        after_summary = after_proof["summary"]
        if after_summary["canonicalPullCount"] < before_summary["canonicalPullCount"]:
            continuity_errors.append("canonical_pull_count_regressed")
        before_latest = _timestamp(before_summary.get("latestCanonicalPullAtUtc"))
        after_latest = _timestamp(after_summary.get("latestCanonicalPullAtUtc"))
        if before_latest and after_latest and after_latest < before_latest:
            continuity_errors.append("latest_canonical_pull_time_regressed")

    ok = (
        before_proof["ok"] is True
        and (after_proof is None or after_proof["ok"] is True)
        and not continuity_errors
    )
    return {
        "ok": ok,
        "proofType": "MLB-MANUAL-HISTORICAL-RECOVERY-CURRENT-SLATE-INTEGRITY-v1",
        "before": before_proof,
        "after": after_proof,
        "continuityErrors": continuity_errors,
        "productionScoringGuardChanged": False,
        "productionGateRelaxed": False,
        "authorityMutationAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
    }


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"Expected a JSON object in {path}")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    proof = build_proof(
        _read(args.before),
        _read(args.after) if args.after is not None else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if proof["ok"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
