from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts import run_mlb_historical_watchdog as watchdog


CEILING = "2026-12-31"


def waiting_state(*, revision=10, wait_version=None):
    return {
        "phase": watchdog.WAITING_PHASE,
        "endDate": "2026-08-04",
        "currentDate": "2026-08-05",
        "currentSlotIndex": 0,
        "networkRequestCount": 26320,
        "eligibleGameCount": 4114,
        "targetSettledGames": 4405,
        "completeSlateCount": 332,
        "optimizationRound": 10,
        "revision": revision,
        "updatedAtUtc": "2026-08-05T10:49:41+00:00",
        "lastError": None,
        "lastQuota": {"x-requests-remaining": 4961594},
        "featureRematerializationComplete": True,
        "featureRematerializationErrors": [],
        "featureRematerializedSlateCount": 332,
        "featureRematerializationTotalSlateCount": 332,
        "rangeExtensionNextRetryDate": "2026-08-05",
        "settledHorizonWait": {
            "version": wait_version or watchdog.WAITING_CONTRACT_VERSION,
            "authorizedThroughDate": "2026-08-04",
            "settledHorizonDate": "2026-08-04",
            "configuredCeilingDate": CEILING,
            "nextEligibleSlateDate": "2026-08-05",
            "blockingError": False,
        },
    }


def active_state():
    value = waiting_state()
    value.pop("settledHorizonWait")
    value.pop("rangeExtensionNextRetryDate")
    value["phase"] = "BACKFILLING"
    value["endDate"] = "2026-08-04"
    value["currentDate"] = "2026-08-04"
    return value


def test_template_boundary_is_read_as_deployment_ceiling(tmp_path):
    template = tmp_path / "template.yaml"
    template.write_text(
        "Parameters:\n  HistoricalEndDate:\n    Type: String\n    Default: '2026-12-31'\n",
        encoding="utf-8",
    )

    assert watchdog.canonical_end_date(template) == CEILING


def test_healthy_wait_accepts_current_contract():
    proof = watchdog.validate_transition(
        waiting_state(),
        waiting_state(revision=11),
        expected_ceiling=CEILING,
        published={},
    )

    assert proof["phase"] == watchdog.WAITING_PHASE
    assert proof["waitingHealthy"] is True
    assert proof["waitContractVersion"] == watchdog.WAITING_CONTRACT_VERSION
    assert proof["authorizedThroughDate"] == "2026-08-04"
    assert proof["configuredCeilingDate"] == CEILING
    assert proof["nextEligibleSlateDate"] == "2026-08-05"
    assert proof["remainingEvidenceGames"] == 291
    assert proof["blockingError"] is False


def test_healthy_wait_accepts_legacy_persisted_contract():
    proof = watchdog.validate_waiting_state(
        waiting_state(
            wait_version=watchdog.LEGACY_WAITING_CONTRACT_VERSION
        ),
        expected_ceiling=CEILING,
    )

    assert (
        proof["waitContractVersion"]
        == watchdog.LEGACY_WAITING_CONTRACT_VERSION
    )


def test_waiting_state_fails_closed_on_unknown_contract():
    state = waiting_state(wait_version="unknown")

    with pytest.raises(
        ValueError, match="settled_horizon_wait_version_mismatch"
    ):
        watchdog.validate_waiting_state(
            state, expected_ceiling=CEILING
        )


def test_waiting_state_fails_closed_on_mismatched_ceiling():
    state = waiting_state()
    state["settledHorizonWait"]["configuredCeilingDate"] = "2026-08-31"

    with pytest.raises(
        ValueError,
        match="wait_ceiling_does_not_match_deployment_ceiling",
    ):
        watchdog.validate_waiting_state(
            state, expected_ceiling=CEILING
        )


def test_waiting_state_fails_closed_on_blocking_error():
    state = waiting_state()
    state["settledHorizonWait"]["blockingError"] = True

    with pytest.raises(
        ValueError, match="settled_horizon_wait_is_blocking"
    ):
        watchdog.validate_waiting_state(
            state, expected_ceiling=CEILING
        )


def test_waiting_state_fails_closed_on_noncontiguous_retry_date():
    state = waiting_state()
    state["rangeExtensionNextRetryDate"] = "2026-08-06"

    with pytest.raises(
        ValueError,
        match="retry_date_does_not_match_next_eligible_slate",
    ):
        watchdog.validate_waiting_state(
            state, expected_ceiling=CEILING
        )


def test_repeated_wait_must_not_create_duplicate_revision():
    first = waiting_state(revision=10)
    second = waiting_state(revision=11)

    with pytest.raises(
        ValueError,
        match="repeated_wait_created_duplicate_revision",
    ):
        watchdog.validate_repeated_wait_is_idempotent(
            first,
            second,
            expected_ceiling=CEILING,
        )


def test_repeated_wait_is_healthy_when_only_timestamp_text_differs():
    first = waiting_state(revision=10)
    second = copy.deepcopy(first)
    second["updatedAtUtc"] = "2026-08-05T10:50:41+00:00"

    proof = watchdog.validate_repeated_wait_is_idempotent(
        first,
        second,
        expected_ceiling=CEILING,
    )

    assert proof["idempotent"] is True
    assert proof["firstRevision"] == proof["secondRevision"] == 10


def test_active_phase_requires_substantive_progress_not_revision_churn():
    before = active_state()
    after = copy.deepcopy(before)
    after["revision"] += 1

    with pytest.raises(
        ValueError,
        match="active_optimizer_did_not_make_substantive_progress",
    ):
        watchdog.validate_transition(
            before,
            after,
            expected_ceiling=CEILING,
            published=after,
        )


def test_active_phase_accepts_real_cursor_progress():
    before = active_state()
    after = copy.deepcopy(before)
    after["currentSlotIndex"] = 1

    proof = watchdog.validate_transition(
        before,
        after,
        expected_ceiling=CEILING,
        published=before,
    )

    assert proof["advancedInRun"] is True
    assert proof["waitingHealthy"] is False


def test_authorized_range_may_not_exceed_configured_ceiling():
    state = active_state()
    state["endDate"] = "2027-01-01"

    with pytest.raises(
        ValueError,
        match="authorized_range_exceeds_configured_ceiling",
    ):
        watchdog.validate_common_state(
            state, expected_ceiling=CEILING
        )


def test_false_data_range_exhaustion_before_ceiling_is_blocking():
    state = active_state()
    state["phase"] = "DATA_RANGE_EXHAUSTED"

    with pytest.raises(
        ValueError,
        match="data_range_exhausted_before_configured_ceiling",
    ):
        watchdog.validate_common_state(
            state, expected_ceiling=CEILING
        )


def test_data_range_exhaustion_at_configured_ceiling_is_valid_terminal():
    state = active_state()
    state["phase"] = "DATA_RANGE_EXHAUSTED"
    state["endDate"] = CEILING

    proof = watchdog.validate_common_state(
        state, expected_ceiling=CEILING
    )

    assert proof["phase"] == "DATA_RANGE_EXHAUSTED"


def test_rematerialization_must_cover_every_completed_slate():
    state = active_state()
    state["completeSlateCount"] = 337
    state["featureRematerializedSlateCount"] = 335
    state["featureRematerializationTotalSlateCount"] = 335

    with pytest.raises(
        ValueError,
        match="feature_rematerialization_does_not_cover_completed_slates",
    ):
        watchdog.validate_common_state(
            state, expected_ceiling=CEILING
        )


def test_quota_pause_and_rematerialization_errors_remain_blocking():
    paused = active_state()
    paused["phase"] = "PAUSED_QUOTA"
    with pytest.raises(
        ValueError,
        match="historical_ingestion_blocked_by_quota",
    ):
        watchdog.validate_common_state(
            paused, expected_ceiling=CEILING
        )

    broken = active_state()
    broken["featureRematerializationErrors"] = [{"error": "bad"}]
    with pytest.raises(
        ValueError,
        match="feature_rematerialization_errors_remain",
    ):
        watchdog.validate_common_state(
            broken, expected_ceiling=CEILING
        )
