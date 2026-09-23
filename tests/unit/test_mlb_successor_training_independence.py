"""Successor readiness must progress without crossing R8 continuity gates."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

import mlb_ml_aws_training_v1 as training
import mlb_ml_experiment_v2 as experiment
import mlb_successor_model_v2 as model
import mlb_successor_runtime_v1 as runtime
from tests.unit import test_mlb_ml_aws_training_v1 as trainer_fixtures
from tests.unit.test_mlb_successor import (
    Memory, NOW, DEPLOYMENT, freeze, locked_row, training_records,
)


# PR #932: exercise the real trainer/runtime, not extracted function bodies.
# Persistence and the post-admission boundary are isolated by test doubles.
# The real-snapshot test below separately exercises unchanged provenance checks.
# Synthetic ready-model records do not prove production source admissibility.
BLOCKED = {"ok": False, "blocker": "OFFICIAL_SLATE_UNRESOLVED"}


def forbidden(*args, **kwargs):
    pytest.fail("a continuity wait must not fit, qualify, activate or write artifacts")


class AuditedMemory(Memory):
    def __init__(self, events, *, immutable_writes=False):
        super().__init__()
        self.events = events
        self.immutable_writes = immutable_writes

    def once(self, key, value):
        if not self.immutable_writes:
            forbidden()
        self.events.append(("immutable", key))
        return super().once(key, value)

    def status(self, mode, value):
        assert mode == "TRAINING"
        super().status(mode, value)
        self.events.append(("successor_status", deepcopy(value)))

    def predictions(self, day):
        forbidden()


class AuditedStore(trainer_fixtures.FakeStore):
    def __init__(self, manifest, repo, events):
        super().__init__(manifest)
        self.repo, self.events = repo, events

    def successor_repository(self):
        return self.repo

    def save_status(self, experiment_id, status):
        super().save_status(experiment_id, status)
        self.events.append(("parent_status", deepcopy(status)))

    def put_versioned_json(self, *args, **kwargs):
        forbidden()

    def promote_candidate(self, *args, **kwargs):
        forbidden()


def wait_service(kind, repo, events, trainer=training):
    day = "2026-09-01"
    manifest = (trainer_fixtures.manifest_with_persisted_slate(day)
                if kind == "missing" else trainer_fixtures.new_manifest())
    store = AuditedStore(manifest, repo, events)
    loaded = (trainer_fixtures.continuity_rows(skippedUnresolvedSlateDates=[day])
              if kind == "missing" else trainer_fixtures.continuity_rows(
                  ok=False, blockedSlateDate=day, blocker="OFFICIAL_SLATE_UNRESOLVED"))
    service = trainer.TrainingService(
        store, trainer_fixtures.config(), row_loader=lambda _config: loaded,
        now=lambda: trainer_fixtures.NOW,
    )
    trainer_fixtures.attest_test_execution_lease(service)
    return service, store


def assert_parent_wait(result, store):
    assert result["ok"] is False
    assert result["status"] == "CANONICAL_SLATE_CONTINUITY_BLOCKED"
    assert result["modelTrained"] is False
    assert result["championChanged"] is False
    assert result["manifestDigest"] == store.manifest["manifestDigest"]
    assert result["statusFingerprint"] == training._status_fingerprint(result)
    assert store.statuses == [result]
    assert not store.artifacts and not store.candidates
    assert store.champion is None and not store.selections


@pytest.mark.parametrize("kind", ["missing", "unhealthy"])
@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("protocol_present", [False, True])
def test_both_runtime_waits_refresh_only_status_before_parent(
    monkeypatch, kind, frozen, protocol_present,
):
    events = []
    repo = AuditedMemory(events)
    if protocol_present:
        repo.data["PROTOCOL"] = {"protocol": deepcopy(model.PROTOCOL)}
    if frozen:
        repo.data["FROZEN"] = freeze()
        # These receipts must not even be evaluated, let alone overwritten.
        repo.data["QUALIFICATION"] = {"sealed": "unchanged"}
        repo.data["ACTIVE"] = {"review": "unchanged"}
    repo.data["PREDICTION#2026-09-01#1"] = {"locked": "unchanged"}
    before = deepcopy(repo.data)
    service, store = wait_service(kind, repo, events)
    manifest_before = deepcopy(store.manifest)
    advance = Mock(wraps=experiment.advance_manifest)
    monkeypatch.setattr(experiment, "advance_manifest", advance)
    monkeypatch.setattr(runtime, "develop", forbidden)
    monkeypatch.setattr(runtime, "evaluate_prospective", forbidden)
    monkeypatch.setattr(model, "fit", forbidden)

    result = service.run()

    assert_parent_wait(result, store)
    assert [event[0] for event in events] == ["successor_status", "parent_status"]
    receipt = result["successorDevelopment"]
    assert receipt == repo.get("STATUS#TRAINING") == events[0][1]
    assert receipt["ok"] is True  # The readiness observation succeeded, not a fit.
    assert receipt["statusOnly"] is True
    for flag in ("trainingReady", "inputSetComplete", "modelFitAttempted",
                 "qualificationEvaluated", "productionAuthorityChanged",
                 "automaticPromotionEnabled"):
        assert receipt[flag] is False
    assert receipt["updatedAtUtc"] == trainer_fixtures.NOW.isoformat()
    assert receipt["canonicalSlateContinuity"] == result["canonicalSlateContinuity"]
    assert receipt["protocolPersisted"] is protocol_present
    assert "CANONICAL_SLATE_CONTINUITY_BLOCKED" in receipt["blockers"]
    assert {k: v for k, v in repo.data.items() if k != "STATUS#TRAINING"} == before
    if kind == "missing":
        advance.assert_not_called()
        assert store.manifest == manifest_before
    else:
        advance.assert_called_once()  # Preserve the existing second-gate position.


@pytest.mark.parametrize("kind", ["missing", "unhealthy"])
@pytest.mark.parametrize("failure", ["adapter", "factory", "protocol", "frozen", "read", "write"])
def test_refresh_failure_is_nested_and_never_converted_to_success(
    monkeypatch, kind, failure,
):
    events = []
    repo = AuditedMemory(events)
    service, store = wait_service(kind, repo, events)
    if failure == "adapter":
        store.successor_repository = None
    elif failure == "factory":
        store.successor_repository = Mock(side_effect=RuntimeError("repository unavailable"))
    elif failure == "protocol":
        repo.data["PROTOCOL"] = {"protocol": {**model.PROTOCOL, "trainMinimum": 1}}
    elif failure == "frozen":
        repo.data["FROZEN"] = {**freeze(), "artifactDigest": "bad"}
    elif failure == "read":
        monkeypatch.setattr(repo, "get", Mock(side_effect=RuntimeError("read failed")))
    else:
        monkeypatch.setattr(repo, "status", Mock(side_effect=RuntimeError("write failed")))
    before = deepcopy(repo.data)
    monkeypatch.setattr(runtime, "develop", forbidden)
    monkeypatch.setattr(model, "fit", forbidden)
    monkeypatch.setattr(runtime, "evaluate_prospective", forbidden)

    result = service.run()

    assert_parent_wait(result, store)
    receipt = result["successorDevelopment"]
    assert receipt["ok"] is False and receipt["statusOnly"] is True
    assert receipt["trainingReady"] is False
    expected = ("STORE_ADAPTER_UNAVAILABLE" if failure == "adapter"
                else "SUCCESSOR_STATUS_REFRESH_FAILED")
    assert receipt["status"] == expected
    assert repo.data == before
    assert [event[0] for event in events] == ["parent_status"]


def test_real_source_validator_is_reused_and_rejection_is_preserved(monkeypatch):
    good = {**locked_row(), "winner": "Home", "correct": True}
    bad = deepcopy(good)
    bad["fundamentalsSnapshotV2"]["groups"]["starter_quality"]["values"]["homeEra"] = 900
    before = deepcopy([good, bad])
    with pytest.raises(ValueError) as rejected:
        model.record(bad, labeled=True)
    # Validate the fixture with the real snapshot and successor admission code.
    expected_record = model.record(good, labeled=True)
    repo = AuditedMemory([])
    monkeypatch.setattr(model, "fit", forbidden)
    monkeypatch.setattr(runtime, "evaluate_prospective", forbidden)

    result = runtime.refresh_development_status(
        repo, [good, bad], NOW,
        DEPLOYMENT, BLOCKED,
    )

    assert result["acceptedDevelopmentRows"] == 1
    assert result["rejectedRows"] == {str(rejected.value): 1}
    assert result["counts"] == model.development([expected_record], readiness_only=True)["counts"]
    assert [good, bad] == before
    assert set(repo.data) == {"STATUS#TRAINING"}


@pytest.mark.parametrize("frozen", [False, True])
def test_candidate_ready_input_still_cannot_freeze_or_qualify(monkeypatch, frozen):
    records = training_records()
    before = deepcopy(records)
    repo = AuditedMemory([])
    if frozen:
        repo.data["FROZEN"] = freeze()
        repo.data["QUALIFICATION"] = {"sealed": "unchanged"}
        repo.data["ACTIVE"] = {"active": "unchanged"}
    durable_before = deepcopy(repo.data)
    # Isolate post-admission control flow. Provenance is tested independently above.
    validator = Mock(side_effect=lambda row, *, labeled: deepcopy(row))
    monkeypatch.setattr(model, "record", validator)
    monkeypatch.setattr(model, "fit", forbidden)
    monkeypatch.setattr(runtime, "evaluate_prospective", forbidden)

    result = runtime.refresh_development_status(
        repo, records, NOW,
        DEPLOYMENT, BLOCKED,
    )

    assert validator.call_count == len(records)
    assert all(call.kwargs == {"labeled": True} for call in validator.call_args_list)
    assert result["counts"] == {"train": 345, "calibration": 45, "selection": 60}
    assert result["blockers"] == ["CANONICAL_SLATE_CONTINUITY_BLOCKED"]
    assert "candidate" not in result and result["trainingReady"] is False
    assert {k: v for k, v in repo.data.items() if k != "STATUS#TRAINING"} == durable_before
    assert records == before


@pytest.mark.parametrize("continuity", [None, [], "blocked", {"ok": True}])
def test_noncanonical_status_only_input_is_rejected_without_a_write(continuity):
    repo = AuditedMemory([])
    with pytest.raises(ValueError, match="blocked canonical continuity"):
        runtime.refresh_development_status(
            repo, [], NOW,
            DEPLOYMENT, continuity,
        )
    assert repo.data == {}


def test_naive_clock_and_duplicate_identities_fail_without_status(monkeypatch):
    repo = AuditedMemory([])
    with pytest.raises(ValueError, match="timezone-aware"):
        runtime.refresh_development_status(
            repo, [], NOW.replace(tzinfo=None),
            DEPLOYMENT, BLOCKED,
        )
    row = {**locked_row(), "winner": "Home", "correct": True}
    with pytest.raises(ValueError, match="duplicate development identity"):
        runtime.refresh_development_status(
            repo, [row, deepcopy(row)], NOW,
            DEPLOYMENT, BLOCKED,
        )
    assert repo.data == {}


def test_readiness_preserves_actual_shortfalls_and_later_selection_rows(monkeypatch):
    records = training_records(163)
    sizes = [49, 26, 27, 30, 31]
    start = 0
    for day, size in enumerate(sizes, start=1):
        for row in records[start:start + size]:
            row.update(slateDateEt=f"2026-08-{day:02}", starterRatesMissing=1.,
                       lineupOpsMissing=1., bullpenWorkloadMissing=1.)
        start += size
    for row in records[-47:]:
        row["starterRatesMissing"] = 0.
    for row in records[-45:]:
        row.update(lineupOpsMissing=0., bullpenWorkloadMissing=0.)
    before, protocol_before = deepcopy(records), deepcopy(model.PROTOCOL)
    monkeypatch.setattr(model, "fit", forbidden)

    result = model.development(records, readiness_only=True)

    assert result["counts"] == {"train": 49, "calibration": 53, "selection": 61}
    assert result["observedStarterCounts"] == {"train": 0, "calibration": 0, "selection": 47}
    assert result["observedTeamCounts"] == {"train": 0, "calibration": 0, "selection": 45}
    assert set(result["blockers"]) == {
        "INSUFFICIENT_WHOLE_SLATE_DEVELOPMENT_ROWS",
        "INSUFFICIENT_OBSERVED_STARTER_RATES_TRAIN",
        "INSUFFICIENT_OBSERVED_STARTER_RATES_CALIBRATION",
        "INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN",
        "INSUFFICIENT_OBSERVED_TEAM_CONTEXT_CALIBRATION",
    }
    assert records == before and model.PROTOCOL == protocol_before
    assert "candidate" not in result


def test_readiness_keeps_the_rolling_whole_slate_window(monkeypatch):
    records = training_records(765)
    before = deepcopy(records)
    monkeypatch.setattr(model, "fit", forbidden)
    result = model.development(records, readiness_only=True)
    assert result["counts"] == {"train": 645, "calibration": 45, "selection": 60}
    assert not result["blockers"] and "candidate" not in result
    assert records == before


def test_normal_development_default_and_explicit_false_are_identical():
    records = training_records()
    before = deepcopy(records)
    implicit = model.development(records)
    explicit = model.development(records, readiness_only=False)
    assert implicit == explicit
    assert implicit["status"] == "DEVELOPMENT_CANDIDATE_EVALUATED"
    assert len(implicit["comparisons"]) == 6 and "candidate" in implicit
    assert records == before


def test_healthy_continuity_calls_full_development_exactly_once(monkeypatch):
    events = []
    repo = AuditedMemory(events, immutable_writes=True)
    store = AuditedStore(trainer_fixtures.new_manifest(), repo, events)
    service = trainer_fixtures.service(store)
    service.row_loader = lambda _config: trainer_fixtures.continuity_rows()
    develop = Mock(wraps=runtime.develop)
    monkeypatch.setattr(runtime, "develop", develop)
    monkeypatch.setattr(runtime, "refresh_development_status", forbidden)

    result = service.run()

    develop.assert_called_once()
    assert result["successorDevelopment"] == repo.get("STATUS#TRAINING")
    assert result["successorDevelopment"].get("statusOnly") is not True
    assert set(repo.data) == {"PROTOCOL", "STATUS#TRAINING"}
    assert not store.artifacts and not store.candidates and store.champion is None
    assert [event[0] for event in events].count("successor_status") == 1


def test_readiness_receipt_uses_real_repository_fingerprint_round_trip(monkeypatch):
    class StatusTable:
        def __init__(self):
            self.items = {}
            self.writes = []

        def get_item(self, *, Key, ConsistentRead):
            assert ConsistentRead is True
            item = self.items.get((Key["PK"], Key["SK"]))
            return {"Item": deepcopy(item)} if item is not None else {}

        def put_item(self, *, Item, **kwargs):
            assert not kwargs
            assert Item["PK"] == runtime.PK and Item["SK"] == "STATUS#TRAINING"
            self.items[(Item["PK"], Item["SK"])] = deepcopy(Item)
            self.writes.append(deepcopy(Item))

    table = StatusTable()
    repo = runtime.Repository(table)
    row = {**locked_row(), "winner": "Home", "correct": True}
    monkeypatch.setattr(model, "fit", forbidden)
    monkeypatch.setattr(runtime, "evaluate_prospective", forbidden)
    report = runtime.refresh_development_status(repo, [row], NOW, DEPLOYMENT, BLOCKED)
    loaded = repo.get("STATUS#TRAINING")
    assert len(table.writes) == 1
    assert model.fingerprint(loaded) == model.fingerprint(report)
    assert loaded["statusOnly"] is True and loaded["trainingReady"] is False
    assert loaded["acceptedDevelopmentRows"] == 1


@pytest.mark.parametrize("kind", ["missing", "unhealthy"])
@pytest.mark.parametrize("refresh_fails", [False, True])
def test_deployed_compat_path_refreshes_parent_identity_and_preserves_nested_failure(
    monkeypatch, kind, refresh_fails,
):
    import mlb_ml_aws_training_v1_compat as compat
    import report_mlb_30m_progress as pulse

    events = []
    repo = AuditedMemory(events)
    service, store = wait_service(kind, repo, events, compat.canonical)
    old = trainer_fixtures.healthy_status(
        "training", created_at=trainer_fixtures.NOW - training.timedelta(days=9),
    )
    old["manifestDigest"] = "previous-manifest"
    old["deploymentIdentity"]["mlbIdentity"] = compat.mlb_deployment_identity(service.config)
    old["statusFingerprint"] = training._status_fingerprint(old)
    store.save_status(service.config.experiment_id, old)
    events.clear()
    history_before = deepcopy(store.run_statuses)

    def health(status):
        return service._latest_status_health(
            status, execution_mode="training",
            maximum_age=training.TRAINING_STATUS_MAX_AGE,
            manifest=store.manifest,
        )

    assert set(health(old)["errors"]) == {
        "latest_status_stale", "latest_status_manifest_mismatch",
    }
    if refresh_fails:
        monkeypatch.setattr(repo, "status", Mock(side_effect=RuntimeError("write failed")))
    monkeypatch.setattr(runtime, "develop", forbidden)
    monkeypatch.setattr(runtime, "evaluate_prospective", forbidden)
    monkeypatch.setattr(model, "fit", forbidden)

    result = service.run_scheduled()

    assert result["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert result["ok"] is True and result["trainingReady"] is False
    assert result == store.load_latest_status(service.config.experiment_id, "training")
    assert result == store.load_status_run(service.config.experiment_id, result["runId"])
    assert result["statusFingerprint"] == training._status_fingerprint(result)
    assert result["manifestDigest"] == store.manifest["manifestDigest"]
    assert health(result)["ok"] is True and health(result)["errors"] == []
    state = pulse._successor_state({"trainingHealth": health(result)})
    assert state["trainingHealth"] == ("FAILED" if refresh_fails else "HEALTHY")
    assert state["status"] == (
        "SUCCESSOR_STATUS_REFRESH_FAILED" if refresh_fails
        else "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    )
    assert result["successorDevelopment"]["statusOnly"] is True
    assert result["successorDevelopment"]["trainingReady"] is False
    assert store.run_statuses[old["runId"]] == history_before[old["runId"]]
    for flag in ("modelTrained", "championChanged", "liveInferenceAuthority",
                 "productionAuthorityChanged", "immutablePredictionRewriteAllowed",
                 "postStartPredictionCreationAllowed", "automaticPromotionEnabled"):
        assert result[flag] is False
    assert not store.artifacts and not store.candidates and not store.selections
    assert store.champion is None


@pytest.mark.parametrize("ok_flag", [None, 0, "false", "missing"])
def test_nonready_continuity_flags_keep_the_existing_fail_closed_path(monkeypatch, ok_flag):
    events = []
    repo = AuditedMemory(events)
    service, store = wait_service("unhealthy", repo, events)
    loaded = trainer_fixtures.continuity_rows(ok=ok_flag)
    if ok_flag == "missing":
        loaded.continuity.pop("ok")
    service.row_loader = lambda _config: loaded
    monkeypatch.setattr(runtime, "develop", forbidden)
    monkeypatch.setattr(model, "fit", forbidden)

    result = service.run()

    assert_parent_wait(result, store)
    assert result["successorDevelopment"]["ok"] is True
    assert result["successorDevelopment"]["trainingReady"] is False
    assert result["successorDevelopment"]["canonicalSlateContinuity"] == loaded.continuity


def test_no_lease_cannot_refresh_either_status():
    events = []
    repo = AuditedMemory(events)
    service, store = wait_service("unhealthy", repo, events)
    service._execution_lease_acquired_for_run = False
    with pytest.raises(training.TrainingContractError):
        service.run()
    assert not events and not repo.data and not store.statuses


@pytest.mark.parametrize("mutation", ["changed", "missing", "empty"])
def test_successor_model_bytes_are_required_by_deployment_identity(tmp_path, monkeypatch, mutation):
    import mlb_ml_aws_training_v1_compat as compat
    from pathlib import Path

    source_root = Path(compat.__file__).parent
    for name in compat._IDENTITY_SOURCE_FILES:
        (tmp_path / name).write_bytes((source_root / name).read_bytes())
    monkeypatch.setattr(compat, "__file__", str(tmp_path / "mlb_ml_aws_training_v1_compat.py"))
    config = trainer_fixtures.config()
    original = compat.mlb_deployment_identity(config)
    target = tmp_path / "mlb_successor_model_v2.py"
    assert target.is_file()
    if mutation == "changed":
        target.write_bytes(target.read_bytes() + b"\n# model-only deployment change\n")
        assert compat.mlb_deployment_identity(config) != original
    else:
        if mutation == "missing":
            target.unlink()
        else:
            target.write_bytes(b"")
        with pytest.raises(RuntimeError, match="mlb_identity_source_" + mutation):
            compat.mlb_deployment_identity(config)
