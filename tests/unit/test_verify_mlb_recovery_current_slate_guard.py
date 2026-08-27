from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "verify_mlb_recovery_current_slate_guard.py"
)
SPEC = importlib.util.spec_from_file_location(
    "verify_mlb_recovery_current_slate_guard", MODULE_PATH
)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def report(*, pulls: int, missing_movement: int, blockers: list[str]):
    return {
        "guardPassed": not blockers,
        "readOnly": True,
        "slateDateEt": "2026-08-27",
        "blockers": blockers,
        "summary": {
            "officialGameCount": 7,
            "canonicalPullCount": pulls,
            "latestCanonicalPullAtUtc": f"2026-08-27T0{pulls}:00:00+00:00",
            "movementFeatureGameCount": 7 - missing_movement,
            "persistedPredictionGameCount": 7,
            "missingMovementCount": missing_movement,
            "missingPredictionCount": 0,
            "invalidPredictionTeamCount": 0,
            "invalidMovementTeamCount": 0,
        },
    }


def test_expected_maturity_blocker_allowlist_is_exact():
    assert GUARD.EXPECTED_MATURITY_BLOCKERS == frozenset(
        {
            "INSUFFICIENT_CANONICAL_PULL_HISTORY",
            "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE",
        }
    )


def test_expected_early_slate_maturity_is_recovery_eligible_not_scoring_ready():
    proof = GUARD.build_proof(
        report(
            pulls=1,
            missing_movement=7,
            blockers=[
                "INSUFFICIENT_CANONICAL_PULL_HISTORY",
                "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE",
            ],
        )
    )

    assert proof["ok"] is True
    assert proof["before"]["scoringReady"] is False
    assert proof["before"]["unexpectedBlockers"] == []
    assert proof["productionGateRelaxed"] is False
    assert proof["authorityMutationAllowed"] is False
    assert proof["postStartPredictionCreationAllowed"] is False
    assert proof["immutablePredictionRewriteAllowed"] is False


def test_prediction_coverage_blocker_still_fails_closed():
    value = report(
        pulls=1,
        missing_movement=7,
        blockers=[
            "INSUFFICIENT_CANONICAL_PULL_HISTORY",
            "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE",
            "PERSISTED_WINNER_PREDICTION_COVERAGE_INCOMPLETE",
        ],
    )
    value["summary"]["missingPredictionCount"] = 1

    proof = GUARD.build_proof(value)

    assert proof["ok"] is False
    assert proof["before"]["unexpectedBlockers"] == [
        "PERSISTED_WINNER_PREDICTION_COVERAGE_INCOMPLETE"
    ]
    assert "persisted_prediction_coverage_incomplete" in proof["before"]["errors"]


def test_allowed_maturity_blockers_must_match_numeric_evidence():
    value = report(
        pulls=2,
        missing_movement=0,
        blockers=[
            "INSUFFICIENT_CANONICAL_PULL_HISTORY",
            "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE",
        ],
    )

    proof = GUARD.build_proof(value)

    assert proof["ok"] is False
    assert "canonical_pull_maturity_blocker_inconsistent" in proof["before"]["errors"]
    assert "movement_maturity_blocker_inconsistent" in proof["before"]["errors"]


def test_current_slate_may_advance_to_fully_ready_during_recovery():
    before = report(
        pulls=1,
        missing_movement=7,
        blockers=[
            "INSUFFICIENT_CANONICAL_PULL_HISTORY",
            "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE",
        ],
    )
    after = report(pulls=3, missing_movement=0, blockers=[])

    proof = GUARD.build_proof(before, after)

    assert proof["ok"] is True
    assert proof["before"]["scoringReady"] is False
    assert proof["after"]["scoringReady"] is True
    assert proof["continuityErrors"] == []


def test_structural_identity_failure_after_recovery_still_fails_closed():
    before = report(
        pulls=1,
        missing_movement=7,
        blockers=[
            "INSUFFICIENT_CANONICAL_PULL_HISTORY",
            "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE",
        ],
    )
    after = report(
        pulls=3,
        missing_movement=0,
        blockers=["MOVEMENT_TEAM_NOT_IN_MATCHUP"],
    )
    after["summary"]["invalidMovementTeamCount"] = 1

    proof = GUARD.build_proof(before, after)

    assert proof["ok"] is False
    assert proof["after"]["unexpectedBlockers"] == ["MOVEMENT_TEAM_NOT_IN_MATCHUP"]
    assert "movement_team_identity_invalid" in proof["after"]["errors"]


def test_current_slate_pull_history_may_not_regress():
    before = report(pulls=3, missing_movement=0, blockers=[])
    after = report(
        pulls=1,
        missing_movement=7,
        blockers=[
            "INSUFFICIENT_CANONICAL_PULL_HISTORY",
            "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE",
        ],
    )

    proof = GUARD.build_proof(before, after)

    assert proof["ok"] is False
    assert "canonical_pull_count_regressed" in proof["continuityErrors"]
    assert "latest_canonical_pull_time_regressed" in proof["continuityErrors"]
