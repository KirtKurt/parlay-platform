from __future__ import annotations

import pytest

from engineering_agent.planner import validate


SAFETY_RECEIPTS = [
    "no_direct_production_deploy",
    "no_main_branch_write",
    "no_model_promotion",
    "no_secret_mutation",
    "no_other_sport_change",
]


def _task(*, implementation: list[str], evidence: list[str]) -> dict:
    return {
        "title": "Increase observed starter rate training data",
        "objective": "Accumulate source-observed pre-lock starter rate context for the MLB challenger.",
        "implementation": implementation,
        "likelyFiles": ["hello_world/mlb_successor_model_v2.py"],
        "acceptanceTests": ["Observed starter training count reaches the unchanged minimum."],
        "safetyReceipts": SAFETY_RECEIPTS,
        "evidenceBasis": evidence,
    }


def test_rejects_synthetic_augmentation_of_observed_starter_context() -> None:
    task = _task(
        implementation=[
            "Update data collection pipeline to capture additional starter rate observations.",
            "Adjust training data window to include more historical games.",
            "Implement data augmentation for starter rate features.",
        ],
        evidence=[
            "Current evidence shows training requires 100 observed starters but currently has 0."
        ],
    )
    with pytest.raises(ValueError, match="cannot be synthesized or data-augmented"):
        validate(task, decision_history={"decisions": []})


def test_rejects_historical_expansion_without_point_in_time_pregame_provenance() -> None:
    task = _task(
        implementation=["Adjust training data window to include more historical games."],
        evidence=["Completed game boxes contain starter identities and final pitching totals."],
    )
    with pytest.raises(ValueError, match="requires explicit immutable point-in-time source provenance"):
        validate(task, decision_history={"decisions": []})


def test_allows_verified_point_in_time_historical_pregame_archive() -> None:
    task = _task(
        implementation=["Expand historical training window using the verified historical pregame archive."],
        evidence=[
            "Verified immutable pregame point-in-time pregame records retain source provenance, RetrievedAtUtc, and PayloadFingerprint before each game lock."
        ],
    )
    validated = validate(task, decision_history={"decisions": []})
    assert validated["noModelPromotion"] is True
    assert validated["noDirectProductionDeploy"] is True


def test_allows_future_source_honest_capture_without_historical_reconstruction() -> None:
    task = _task(
        implementation=[
            "Capture additional current pre-lock starter observations from the connected source and persist retrieval provenance."
        ],
        evidence=["Current live pre-lock capture is the only source of new observed starter-rate rows."],
    )
    validated = validate(task, decision_history={"decisions": []})
    assert validated["noOtherSportChange"] is True
