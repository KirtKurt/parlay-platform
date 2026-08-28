from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_mlb_progress_pulse_durable_relay.py"
WORKFLOW = ROOT / ".github" / "workflows" / "mlb-progress-pulse-durable-relay.yml"
AFTER_WAIT = datetime(2026, 8, 27, 21, 31, tzinfo=timezone.utc)
SPEC = importlib.util.spec_from_file_location(
    "run_mlb_progress_pulse_durable_relay",
    SCRIPT,
)
assert SPEC and SPEC.loader
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)


def _repo() -> dict:
    return {"full_name": relay.EXPECTED_REPOSITORY}


def _environment(*rules: dict) -> dict:
    return {
        "name": relay.RELAY_ENVIRONMENT,
        "protection_rules": list(rules)
        or [{"type": "wait_timer", "wait_timer": relay.REQUIRED_WAIT_MINUTES}],
    }


def _run(
    *,
    run_id: int,
    hop: int,
    created: str,
    event: str = "workflow_dispatch",
    status: str = "pending",
    repository: str | None = None,
) -> dict:
    return {
        "id": run_id,
        "display_title": f"MLB durable pulse relay hop {hop}",
        "event": event,
        "status": status,
        "created_at": created,
        "head_branch": "main",
        "head_repository": {"full_name": repository or relay.EXPECTED_REPOSITORY},
        "path": relay.DURABLE_RELAY_PATH,
    }


def _bounded(*, run_id: int = 9, segment: int = 10) -> dict:
    return {
        "id": run_id,
        "display_title": f"MLB pulse relay segment {segment}",
        "event": "workflow_dispatch",
        "status": "in_progress",
        "created_at": "2026-08-27T20:00:00Z",
        "head_branch": "main",
        "head_repository": _repo(),
        "path": relay.BOUNDED_RELAY_PATH,
    }


class FakeClient:
    def __init__(
        self,
        *,
        current_hop: int,
        decision: relay.Decision | Exception | None = None,
    ) -> None:
        self.current = _run(
            run_id=100,
            hop=current_hop,
            created="2026-08-27T21:00:00Z",
            status="in_progress",
        )
        self.environment = _environment()
        self.bounded_runs: list[dict] = []
        self.durable_runs: list[dict] = [self.current]
        self.decision = decision or relay.Decision(False, "VISIBLE_PULSE_FRESH")
        self.calls: list[str] = []
        self.reporter_forces: list[bool] = []
        self.fail_reporter = False
        self.dispatched_hops: list[int] = []

    def get_environment(self):
        self.calls.append("environment")
        return self.environment

    def get_current_run(self):
        self.calls.append("current")
        return self.current

    def list_bounded_runs(self):
        self.calls.append("bounded")
        return self.bounded_runs

    def list_durable_runs(self):
        self.calls.append("durable")
        return self.durable_runs

    def dispatch_successor(self, remaining_hops: int):
        self.calls.append(f"successor:{remaining_hops}")
        self.dispatched_hops.append(remaining_hops)
        self.durable_runs.append(
            _run(
                run_id=101,
                hop=remaining_hops,
                created="2026-08-27T21:00:01Z",
            )
        )

    def evaluate_staleness(self):
        self.calls.append("decision")
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision

    def dispatch_reporter(self, *, force: bool):
        self.calls.append(f"reporter:{force}")
        self.reporter_forces.append(force)
        if self.fail_reporter:
            raise RuntimeError("reporter unavailable")


@pytest.mark.parametrize("event", ["schedule", "workflow_run", "push"])
def test_automatic_seed_resets_finite_lease(event: str) -> None:
    assert relay.resolve_remaining_hops(event, "1") == relay.MAX_RELAY_HOPS


@pytest.mark.parametrize("value", ["1", "9", "10", "96"])
def test_manual_hop_input_is_canonical_and_bounded(value: str) -> None:
    assert relay.resolve_remaining_hops("workflow_dispatch", value) == int(value)


@pytest.mark.parametrize("value", ["0", "00", "01", "97", "999", "-1", "1.0", "x"])
def test_manual_hop_input_rejects_hostile_values(value: str) -> None:
    with pytest.raises(ValueError):
        relay.resolve_remaining_hops("workflow_dispatch", value)


def test_trigger_binds_workflow_run_to_canonical_main_repository() -> None:
    payload = {
        "repository": _repo(),
        "workflow_run": {
            "name": "MLB Daily Yesterday Audit",
            "head_branch": "main",
            "head_repository": _repo(),
            "status": "completed",
        },
    }
    relay.validate_trigger(
        "workflow_run",
        payload,
        repository=relay.EXPECTED_REPOSITORY,
        ref=relay.EXPECTED_REF,
    )

    payload["workflow_run"]["head_repository"] = {"full_name": "fork/project"}
    with pytest.raises(ValueError, match="workflow_run_source_not_trusted"):
        relay.validate_trigger(
            "workflow_run",
            payload,
            repository=relay.EXPECTED_REPOSITORY,
            ref=relay.EXPECTED_REF,
        )


def test_trigger_rejects_wrong_repo_ref_schedule_and_source_name() -> None:
    with pytest.raises(ValueError, match="repository_identity_mismatch"):
        relay.validate_trigger(
            "workflow_dispatch",
            {"repository": _repo()},
            repository="fork/project",
            ref=relay.EXPECTED_REF,
        )
    with pytest.raises(ValueError, match="durable_relay_requires_main_ref"):
        relay.validate_trigger(
            "workflow_dispatch",
            {"repository": _repo()},
            repository=relay.EXPECTED_REPOSITORY,
            ref="refs/heads/feature",
        )
    with pytest.raises(ValueError, match="schedule_source_not_exact"):
        relay.validate_trigger(
            "schedule",
            {"repository": _repo(), "schedule": "0 * * * *"},
            repository=relay.EXPECTED_REPOSITORY,
            ref=relay.EXPECTED_REF,
        )
    with pytest.raises(ValueError, match="workflow_run_source_not_trusted"):
        relay.validate_trigger(
            "workflow_run",
            {
                "repository": _repo(),
                "workflow_run": {
                    "name": "Untrusted",
                    "head_branch": "main",
                    "head_repository": _repo(),
                    "status": "completed",
                },
            },
            repository=relay.EXPECTED_REPOSITORY,
            ref=relay.EXPECTED_REF,
        )


def test_environment_requires_one_exact_wait_timer_and_no_other_gate() -> None:
    relay.validate_environment(_environment())

    hostile = (
        _environment({"type": "wait_timer", "wait_timer": 29}),
        _environment(
            {"type": "wait_timer", "wait_timer": 30},
            {"type": "required_reviewers"},
        ),
        {
            **_environment(
                {"type": "wait_timer", "wait_timer": 30},
                {"type": "branch_policy"},
            ),
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            },
        },
        {"name": relay.RELAY_ENVIRONMENT, "protection_rules": []},
        {"name": "wrong", "protection_rules": [{"type": "wait_timer", "wait_timer": 30}]},
    )
    for payload in hostile:
        with pytest.raises(ValueError, match="environment_wait_timer_contract_invalid"):
            relay.validate_environment(payload)


def test_elapsed_proof_rejects_environment_bypass() -> None:
    current = _run(
        run_id=100,
        hop=96,
        created="2026-08-27T21:00:00Z",
        status="in_progress",
    )
    with pytest.raises(ValueError, match="environment_wait_timer_not_observed"):
        relay.validate_wait_elapsed(
            current,
            now=datetime(2026, 8, 27, 21, 29, 59, tzinfo=timezone.utc),
        )
    assert relay.validate_wait_elapsed(current, now=AFTER_WAIT) == 31.0


def test_fork_and_wrong_titles_never_count_as_bounded_migration_owner() -> None:
    rows = [
        _bounded(run_id=1),
        {
            **_bounded(run_id=2),
            "head_repository": {"full_name": "fork/project"},
        },
        {
            **_bounded(run_id=3),
            "display_title": "not a bounded segment",
        },
        {
            **_bounded(run_id=4),
            "status": "completed",
        },
    ]
    result = relay.filter_active_bounded_runs(rows)
    assert [row["id"] for row in result] == [1]


def test_active_bounded_relay_blocks_migration_without_any_dispatch() -> None:
    client = FakeClient(current_hop=96)
    client.bounded_runs = [_bounded()]
    result = relay.DurableRelayController(
        client=client,
        current_run_id="100",
        remaining_hops=96,
        verify_delay_seconds=0,
        observed_now=AFTER_WAIT,
    ).run()

    assert result["migrationReady"] is False
    assert result["reason"] == "BOUNDED_RELAY_STILL_ACTIVE"
    assert result["boundedRunId"] == 9
    assert client.dispatched_hops == []
    assert client.reporter_forces == []


def test_stale_hop_preserves_successor_before_stale_gated_reporter() -> None:
    client = FakeClient(
        current_hop=12,
        decision=relay.Decision(True, "VISIBLE_PULSE_STALE"),
    )
    result = relay.DurableRelayController(
        client=client,
        current_run_id="100",
        remaining_hops=12,
        verify_delay_seconds=0,
        observed_now=AFTER_WAIT,
    ).run()

    assert result["successorHop"] == 11
    assert result["reporterDispatched"] is True
    assert client.reporter_forces == [False]
    assert client.calls.index("successor:11") < client.calls.index("decision")
    assert client.calls.index("decision") < client.calls.index("reporter:False")


def test_fresh_hop_preserves_successor_without_redundant_reporter() -> None:
    client = FakeClient(current_hop=12)
    result = relay.DurableRelayController(
        client=client,
        current_run_id="100",
        remaining_hops=12,
        verify_delay_seconds=0,
        observed_now=AFTER_WAIT,
    ).run()

    assert result["reason"] == "VISIBLE_PULSE_FRESH"
    assert result["successorHop"] == 11
    assert result["reporterDispatched"] is False
    assert client.reporter_forces == []


def test_preflight_failure_still_preserves_successor_and_requests_safe_recheck() -> None:
    client = FakeClient(current_hop=12, decision=RuntimeError("api unavailable"))
    with pytest.raises(relay.DurableRelayFailure, match="decision="):
        relay.DurableRelayController(
            client=client,
            current_run_id="100",
            remaining_hops=12,
            verify_delay_seconds=0,
            observed_now=AFTER_WAIT,
        ).run()

    assert client.dispatched_hops == [11]
    assert client.reporter_forces == [False]


def test_reporter_failure_cannot_prevent_successor_creation() -> None:
    client = FakeClient(
        current_hop=8,
        decision=relay.Decision(True, "VISIBLE_PULSE_STALE"),
    )
    client.fail_reporter = True
    with pytest.raises(relay.DurableRelayFailure, match="reporter="):
        relay.DurableRelayController(
            client=client,
            current_run_id="100",
            remaining_hops=8,
            verify_delay_seconds=0,
            observed_now=AFTER_WAIT,
        ).run()

    assert client.dispatched_hops == [7]
    assert client.calls.index("successor:7") < client.calls.index("reporter:False")


def test_terminal_hop_forces_one_visible_diagnostic_without_successor() -> None:
    client = FakeClient(current_hop=1)
    result = relay.DurableRelayController(
        client=client,
        current_run_id="100",
        remaining_hops=1,
        verify_delay_seconds=0,
        observed_now=AFTER_WAIT,
    ).run()

    assert result["terminalWarning"] is True
    assert result["reason"] == "TERMINAL_LEASE_WARNING"
    assert result["successorRunId"] is None
    assert client.dispatched_hops == []
    assert client.reporter_forces == [True]


def test_controller_rejects_bypassed_wait_before_any_relay_action() -> None:
    client = FakeClient(current_hop=12)
    with pytest.raises(ValueError, match="environment_wait_timer_not_observed"):
        relay.DurableRelayController(
            client=client,
            current_run_id="100",
            remaining_hops=12,
            verify_delay_seconds=0,
            observed_now=datetime(
                2026,
                8,
                27,
                21,
                29,
                59,
                tzinfo=timezone.utc,
            ),
        ).run()

    assert client.dispatched_hops == []
    assert client.reporter_forces == []
    assert "bounded" not in client.calls


def test_arbitrary_newer_hop_cannot_replace_exact_successor() -> None:
    client = FakeClient(current_hop=12)
    client.durable_runs.append(
        _run(
            run_id=150,
            hop=5,
            created="2026-08-27T21:00:00.500Z",
        )
    )
    result = relay.DurableRelayController(
        client=client,
        current_run_id="100",
        remaining_hops=12,
        verify_delay_seconds=0,
        observed_now=AFTER_WAIT,
    ).run()

    assert client.dispatched_hops == [11]
    assert result["successorHop"] == 11
    assert result["successorRunId"] == 101


def test_newer_automatic_seed_renews_terminal_hop_without_warning() -> None:
    client = FakeClient(current_hop=1)
    client.durable_runs.append(
        _run(
            run_id=200,
            hop=96,
            event="workflow_run",
            created="2026-08-27T21:01:00Z",
        )
    )
    result = relay.DurableRelayController(
        client=client,
        current_run_id="100",
        remaining_hops=1,
        verify_delay_seconds=0,
        observed_now=AFTER_WAIT,
    ).run()

    assert result["successorRunId"] == 200
    assert result["successorHop"] == 96
    assert result["terminalWarning"] is False
    assert client.dispatched_hops == []
    assert client.reporter_forces == []


def test_newer_durable_successor_requires_repo_main_title_and_created_after() -> None:
    current = _run(
        run_id=100,
        hop=5,
        created="2026-08-27T21:00:00Z",
        status="in_progress",
    )
    rows = [
        current,
        _run(run_id=101, hop=4, created="2026-08-27T21:00:01Z"),
        _run(
            run_id=102,
            hop=4,
            created="2026-08-27T21:00:02Z",
            repository="fork/project",
        ),
        _run(run_id=103, hop=4, created="2026-08-27T20:59:59Z"),
        {
            **_run(run_id=104, hop=4, created="2026-08-27T21:00:03Z"),
            "display_title": "wrong",
        },
    ]
    found = relay.filter_newer_durable_runs(
        rows,
        current_run_id=100,
        current_created_at=current["created_at"],
    )
    assert [row["id"] for row in found] == [101]


def test_workflow_is_non_billable_read_only_bounded_and_mutually_guarded() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    source = SCRIPT.read_text(encoding="utf-8")

    assert "name: MLB progress pulse durable relay" in workflow
    assert "name: mlb-pulse-30m-delay" in workflow
    assert "deployment: false" in workflow
    assert "timeout-minutes: 10" in workflow
    assert "remaining_hops" in workflow
    assert "  preflight:" in workflow
    assert "--mode preflight" in workflow
    assert "needs.preflight.outputs.environment_valid == 'true'" in workflow
    assert "group: mlb-progress-pulse-bounded-runner-relay" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "github.repository == 'KirtKurt/parlay-platform'" in workflow
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in workflow
    assert "always()" not in workflow
    assert "actions: write" in workflow
    assert "contents: read" in workflow
    assert "issues: read" in workflow
    assert "issues: write" not in workflow
    assert "secrets." not in workflow
    assert "AWS_ACCESS_KEY_ID" not in workflow
    assert "sleep " not in workflow
    assert "MAX_RELAY_HOPS = 96" in source
    assert "REQUIRED_WAIT_MINUTES = 30" in source
    assert "MINIMUM_WAIT_SECONDS = REQUIRED_WAIT_MINUTES * 60" in source
    assert "expected_path=BOUNDED_RELAY_PATH" in source
    assert "validate_wait_elapsed" in source
    assert relay.BOUNDED_RELAY_WORKFLOW in source
    assert "force=terminal_warning" in source
    assert "dispatch_reporter(force=terminal_warning)" in source
