from __future__ import annotations

from hello_world import mlb_historical_feature_rematerialization_v1 as remat


def _pointers(count, version=None):
    return [
        {
            "slateDateEt": f"2026-05-{index + 1:02d}",
            "featureDatasetVersion": version or remat.FEATURE_DATASET_VERSION,
        }
        for index in range(count)
    ]


def test_new_feature_contract_clears_stale_completion_state():
    state = {
        "phase": "BACKFILLING",
        "featureDatasetVersion": "old-dataset",
        "featureRematerializationComplete": True,
        "featureRematerializedSlateCount": 149,
        "featureRematerializationTotalSlateCount": 149,
        "featureRematerializationCursor": 149,
        "featureRematerializationErrors": [{"error": "stale"}],
    }
    assert remat._migration_is_current(state) is False
    remat._begin_migration(state, 252)
    assert state["phase"] == "REMATERIALIZING_FEATURES"
    assert state["featureRematerializationComplete"] is False
    assert state["featureRematerializationCursor"] == 0
    assert state["featureRematerializedSlateCount"] == 0
    assert state["featureRematerializationTotalSlateCount"] == 252
    assert state["featureRematerializationTargetDatasetVersion"] == remat.FEATURE_DATASET_VERSION
    assert state["featureRematerializationErrors"] == []
    assert state["lastError"] is None


def test_interrupted_different_target_restarts_instead_of_resuming_mixed_pointers():
    state = {
        "phase": "REMATERIALIZING_FEATURES",
        "featureRematerializationPreviousPhase": "BACKFILLING",
        "featureRematerializationTargetDatasetVersion": "older-target",
        "featureRematerializationComplete": True,
        "featureRematerializationCursor": 80,
        "featureRematerializedSlateCount": 80,
        "featureRematerializationTotalSlateCount": 248,
    }
    assert remat._migration_is_current(state) is False
    remat._begin_migration(state, 252)
    assert state["featureRematerializationPreviousPhase"] == "BACKFILLING"
    assert state["featureRematerializationComplete"] is False
    assert state["featureRematerializationCursor"] == 0
    assert state["featureRematerializedSlateCount"] == 0
    assert state["featureRematerializationTotalSlateCount"] == 252


def test_full_state_requires_every_completed_pointer_and_exact_counts():
    completed = _pointers(254)
    state = {
        "featureDatasetVersion": remat.FEATURE_DATASET_VERSION,
        "featureRematerializationTargetDatasetVersion": remat.FEATURE_DATASET_VERSION,
        "featureRematerializationComplete": True,
        "featureRematerializedSlateCount": 252,
        "featureRematerializationTotalSlateCount": 252,
        "featureRematerializationErrors": [],
        "lastError": None,
    }
    # The exact production defect: 252/252 counters looked complete while two
    # newly appended complete slates were outside the migration ledger.
    assert remat._state_is_fully_materialized(state, completed) is False
    state["featureRematerializedSlateCount"] = 254
    state["featureRematerializationTotalSlateCount"] = 254
    assert remat._state_is_fully_materialized(state, completed) is True


def test_mixed_pointer_rolls_resume_cursor_back_to_earliest_mismatch():
    completed = _pointers(254)
    completed[252]["featureDatasetVersion"] = "older-dataset"
    completed[253]["featureDatasetVersion"] = ""
    assert remat._first_mismatched_pointer(completed) == 252
    current_cursor = 254
    resumed = min(current_cursor, remat._first_mismatched_pointer(completed), len(completed))
    assert resumed == 252


def test_current_target_can_resume_but_never_claims_complete_mid_migration():
    state = {
        "featureRematerializationTargetDatasetVersion": remat.FEATURE_DATASET_VERSION,
        "featureRematerializationComplete": True,
        "featureRematerializationCursor": 25,
        "featureRematerializedSlateCount": 25,
        "featureRematerializationTotalSlateCount": 252,
    }
    assert remat._migration_is_current(state) is True
    state["featureRematerializationComplete"] = False
    assert state["featureRematerializationComplete"] is False
