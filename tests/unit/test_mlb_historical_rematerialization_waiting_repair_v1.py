import copy

import mlb_historical_feature_rematerialization_v1 as rematerialization
import mlb_historical_rematerialization_waiting_repair_v1 as subject


def test_waiting_phase_becomes_eligible_without_removing_existing_phases():
    class Rematerialization:
        ELIGIBLE_PHASES = {"BACKFILLING", "DATA_RANGE_EXHAUSTED"}

    subject.install(Rematerialization)
    assert Rematerialization.ELIGIBLE_PHASES == {
        "BACKFILLING",
        "DATA_RANGE_EXHAUSTED",
        "WAITING_FOR_SETTLED_HORIZON",
    }
    assert Rematerialization.REMATERIALIZATION_WAITING_REPAIR_VERSION == subject.VERSION


def test_install_is_idempotent():
    class Rematerialization:
        ELIGIBLE_PHASES = {"BACKFILLING"}

    subject.install(Rematerialization)
    first = Rematerialization.ELIGIBLE_PHASES
    subject.install(Rematerialization)
    assert Rematerialization.ELIGIBLE_PHASES is first


def test_waiting_state_reconciles_new_completed_slate(monkeypatch):
    subject.install(rematerialization)
    dataset_version = rematerialization.FEATURE_DATASET_VERSION
    state = {
        "phase": subject.WAITING_PHASE,
        "completedSlates": [
            {
                "slateDateEt": "2026-07-28",
                "eligibleGameCount": 10,
                "featureDatasetVersion": dataset_version,
            },
            {
                "slateDateEt": "2026-07-29",
                "eligibleGameCount": 12,
                "featureDatasetVersion": dataset_version,
            },
        ],
        "featureDatasetVersion": dataset_version,
        "featureRematerializationTargetDatasetVersion": dataset_version,
        "featureRematerializationComplete": True,
        "featureRematerializationCursor": 1,
        "featureRematerializedSlateCount": 1,
        "featureRematerializationTotalSlateCount": 1,
        "featureRematerializationErrors": [],
        "freshAuditExpansionRequired": True,
        "lastError": None,
    }
    persisted = copy.deepcopy(state)
    rebuilt = []

    monkeypatch.setattr(rematerialization.handler, "_acquire_lease", lambda owner: True)
    monkeypatch.setattr(rematerialization.handler, "_release_lease", lambda owner: None)
    monkeypatch.setattr(
        rematerialization.handler,
        "_load_state",
        lambda: copy.deepcopy(persisted),
    )

    def save(value):
        persisted.clear()
        persisted.update(copy.deepcopy(value))
        return copy.deepcopy(persisted)

    monkeypatch.setattr(rematerialization.handler, "_save_state", save)

    def rebuild(day):
        rebuilt.append(day)
        return {
            "slateDateEt": day,
            "eligibleGameCount": 12,
            "featureDatasetVersion": dataset_version,
        }

    monkeypatch.setattr(rematerialization, "_rebuild_slate", rebuild)

    result = rematerialization.run_once()

    assert rebuilt == ["2026-07-29"]
    assert result["status"] == "FEATURE_REMATERIALIZATION_COMPLETE"
    assert result["state"]["featureRematerializedSlateCount"] == 2
    assert result["state"]["featureRematerializationTotalSlateCount"] == 2
    assert result["state"]["featureRematerializationComplete"] is True
    assert result["state"]["completeSlateCount"] == 2
    assert result["state"]["eligibleGameCount"] == 22
    assert result["state"]["phase"] == "BACKFILLING"
