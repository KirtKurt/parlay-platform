from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/mlb-progress-pulse-bounded-runner-relay.yml"
SCRIPT = ROOT / "scripts/run_mlb_progress_pulse_bounded_relay.py"
REPOSITORY = "KirtKurt/parlay-platform"
SPEC = importlib.util.spec_from_file_location(
    "run_mlb_progress_pulse_bounded_relay",
    SCRIPT,
)
assert SPEC and SPEC.loader
relay = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = relay
SPEC.loader.exec_module(relay)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.value += seconds

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeClient:
    def __init__(
        self,
        *,
        successor_snapshots=None,
        decisions=None,
        reporter_failures: int = 0,
        successor_dispatch_failure: bool = False,
    ) -> None:
        self.successor_snapshots = list(successor_snapshots or [[9001]])
        self.decisions = list(
            decisions or [relay.Decision(False, "VISIBLE_PULSE_FRESH")]
        )
        self.reporter_failures = reporter_failures
        self.successor_dispatch_failure = successor_dispatch_failure
        self.successor_dispatches: list[int] = []
        self.reporter_dispatches = 0
        self.successor_list_calls = 0
        self.successor_expectations: list[int] = []
        self.decision_calls = 0

    @staticmethod
    def _next(values):
        value = values.pop(0) if len(values) > 1 else values[0]
        if isinstance(value, Exception):
            raise value
        return value

    def list_successor_run_ids(self, expected_remaining_segments: int):
        self.successor_list_calls += 1
        self.successor_expectations.append(expected_remaining_segments)
        return list(self._next(self.successor_snapshots))

    def dispatch_successor(self, remaining_segments: int) -> None:
        self.successor_dispatches.append(remaining_segments)
        if self.successor_dispatch_failure:
            raise RuntimeError("successor dispatch failed")

    def evaluate_staleness(self):
        self.decision_calls += 1
        return self._next(self.decisions)

    def dispatch_reporter(self) -> None:
        self.reporter_dispatches += 1
        if self.reporter_failures > 0:
            self.reporter_failures -= 1
            raise RuntimeError("reporter dispatch failed")


def _machine(client: FakeClient, **kwargs):
    return relay.RelayStateMachine(
        client=client,
        remaining_segments=kwargs.pop("remaining_segments", 10),
        poll_count=kwargs.pop("poll_count", 1),
        poll_interval_seconds=kwargs.pop("poll_interval_seconds", 0),
        failure_threshold=kwargs.pop("failure_threshold", 3),
        poll_sleep=kwargs.pop("poll_sleep", lambda _seconds: None),
        verify_sleep=kwargs.pop("verify_sleep", lambda _seconds: None),
        **kwargs,
    )


def test_only_merge_push_or_explicit_dispatch_can_seed_a_finite_chain() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = workflow.split("on:", 1)[1].split("permissions:", 1)[0]

    assert "workflow_dispatch:" in trigger_block
    assert "push:" in trigger_block
    assert "schedule:" not in trigger_block
    assert "workflow_run:" not in trigger_block
    assert (
        "'.github/workflows/mlb-progress-pulse-bounded-runner-relay.yml'"
        in trigger_block
    )

    assert relay.resolve_remaining_segments("push", "") == 10
    assert relay.resolve_remaining_segments("workflow_dispatch", "10") == 10
    assert relay.resolve_remaining_segments("workflow_dispatch", "1") == 1
    for forbidden in ("schedule", "workflow_run"):
        with pytest.raises(ValueError, match="push_or_workflow_dispatch"):
            relay.resolve_remaining_segments(forbidden, "")


@pytest.mark.parametrize("bad", ("0", "11", "01", "1.0", "1e1", "999999999999"))
def test_explicit_seed_rejects_unbounded_or_noninteger_inputs(bad: str) -> None:
    with pytest.raises(ValueError):
        relay.resolve_remaining_segments("workflow_dispatch", bad)


def test_segment_count_decrements_exactly_once_and_stops_at_zero() -> None:
    assert [
        _machine(FakeClient(), remaining_segments=value).next_segments
        for value in range(10, 0, -1)
    ] == list(range(9, -1, -1))

    last_client = FakeClient(
        successor_snapshots=[],
        decisions=[relay.Decision(False, "VISIBLE_PULSE_FRESH")],
    )
    result = _machine(last_client, remaining_segments=1).run()
    assert result["nextSegments"] == 0
    assert result["successorRequired"] is False
    assert last_client.successor_list_calls == 0
    assert last_client.successor_dispatches == []


def test_successor_filter_is_chain_bound_created_after_and_exact_decrement() -> None:
    rows = [
        {
            "id": 10,
            "event": "workflow_dispatch",
            "status": "in_progress",
            "head_branch": "main",
            "head_repository": {"full_name": REPOSITORY},
            "display_title": "MLB pulse relay segment 9",
            "created_at": "2026-08-27T20:00:00Z",
        },
        {
            "id": 11,
            "event": "workflow_dispatch",
            "status": "completed",
            "head_branch": "main",
            "head_repository": {"full_name": REPOSITORY},
            "display_title": "MLB pulse relay segment 9",
            "created_at": "2026-08-27T20:01:00Z",
        },
        {
            "id": 12,
            "event": "push",
            "status": "pending",
            "head_branch": "main",
            "head_repository": {"full_name": REPOSITORY},
            "display_title": "MLB pulse relay segment 9",
            "created_at": "2026-08-27T20:02:00Z",
        },
        {
            "id": 13,
            "event": "workflow_dispatch",
            "status": "pending",
            "head_branch": "feature",
            "head_repository": {"full_name": REPOSITORY},
            "display_title": "MLB pulse relay segment 10",
            "created_at": "2026-08-27T20:03:00Z",
        },
        {
            "id": 14,
            "event": "workflow_dispatch",
            "status": "pending",
            "head_branch": "main",
            "head_repository": {"full_name": REPOSITORY},
            "display_title": "MLB pulse relay segment 9",
            "created_at": "2026-08-27T19:59:00Z",
        },
        {
            "id": 15,
            "event": "workflow_dispatch",
            "status": "pending",
            "head_branch": "main",
            "head_repository": {"full_name": REPOSITORY},
            "display_title": "MLB pulse relay segment 9",
            "created_at": "2026-08-27T20:04:00Z",
        },
        {
            "id": 16,
            "event": "workflow_dispatch",
            "status": "pending",
            "head_branch": "main",
            "head_repository": {"full_name": "attacker/parlay-platform"},
            "display_title": "MLB pulse relay segment 10",
            "created_at": "2026-08-27T20:05:00Z",
        },
    ]

    assert relay.filter_successor_run_ids(
        rows,
        current_run_id=10,
        current_created_at="2026-08-27T20:00:00Z",
        expected_remaining_segments=9,
        expected_repository=REPOSITORY,
    ) == [15]


@pytest.mark.parametrize("event", ("workflow_dispatch", "push"))
def test_newer_trusted_segment_ten_renews_instead_of_being_overwritten(
    event: str,
) -> None:
    rows = [
        {
            "id": 20,
            "event": event,
            "status": "pending",
            "head_branch": "main",
            "head_repository": {"full_name": REPOSITORY},
            "display_title": "MLB pulse relay segment 10",
            "created_at": "2026-08-27T20:04:00Z",
        }
    ]

    assert relay.filter_successor_run_ids(
        rows,
        current_run_id=10,
        current_created_at="2026-08-27T20:00:00Z",
        expected_remaining_segments=8,
        expected_repository=REPOSITORY,
    ) == [20]


def test_client_queries_all_main_events_so_reviewed_push_renewal_is_visible(
    monkeypatch,
) -> None:
    client = relay.CommandRelayClient(repository=REPOSITORY, current_run_id="100")
    client._current_created_at = "2026-08-27T20:00:00Z"
    paths: list[str] = []

    def fake_gh_json(path: str):
        paths.append(path)
        return {"workflow_runs": []}

    monkeypatch.setattr(client, "_gh_json", fake_gh_json)

    assert client.list_successor_run_ids(8) == []
    assert paths == [
        f"repos/{REPOSITORY}/actions/workflows/{relay.RELAY_WORKFLOW}/runs"
        "?branch=main&per_page=100"
    ]
    assert "event=" not in paths[0]


def test_verified_explicit_renewal_is_not_overwritten_by_decrement() -> None:
    client = FakeClient(
        successor_snapshots=[[777]],
        decisions=[relay.Decision(False, "VISIBLE_PULSE_FRESH")],
    )

    result = _machine(client, remaining_segments=9).run()

    assert client.successor_dispatches == []
    assert result["nextSegments"] == 8
    assert result["successorRequired"] is True


def test_pending_loss_is_requeued_with_decremented_input() -> None:
    client = FakeClient(
        # Poll 1: successor exists. Poll 2: it was canceled or otherwise lost.
        # The exact decremented dispatch is then verified as run 202.
        successor_snapshots=[[201], [], [202], [202], [202]],
        decisions=[
            relay.Decision(False, "VISIBLE_PULSE_FRESH"),
            relay.Decision(False, "VISIBLE_PULSE_FRESH"),
            relay.Decision(False, "VISIBLE_PULSE_FRESH"),
        ],
    )

    result = _machine(client, remaining_segments=9, poll_count=2).run()

    assert client.successor_dispatches == [8]
    assert result["nextSegments"] == 8
    assert result["successorRequired"] is True
    assert set(client.successor_expectations) == {8}


def test_third_staleness_failure_still_requeues_successor_before_exit() -> None:
    client = FakeClient(
        # The exact successor disappears on the fatal third decision poll.
        successor_snapshots=[[201], [201], [], [202]],
        decisions=[
            RuntimeError("decision one"),
            RuntimeError("decision two"),
            RuntimeError("decision three"),
        ],
    )

    with pytest.raises(relay.RelayFailure, match="staleness_decision failure 3/3"):
        _machine(client, poll_count=3).run()

    assert client.successor_list_calls == 4
    assert client.successor_dispatches == [9]
    assert client.reporter_dispatches == 0


def test_third_reporter_failure_still_requeues_successor_before_exit() -> None:
    client = FakeClient(
        successor_snapshots=[[201], [201], [], [202]],
        decisions=[
            relay.Decision(True, "VISIBLE_PULSE_STALE"),
            relay.Decision(True, "VISIBLE_PULSE_STALE"),
            relay.Decision(True, "VISIBLE_PULSE_STALE"),
        ],
        reporter_failures=3,
    )

    with pytest.raises(relay.RelayFailure, match="reporter_dispatch failure 3/3"):
        _machine(client, poll_count=3).run()

    assert client.reporter_dispatches == 3
    assert client.successor_list_calls == 4
    assert client.successor_dispatches == [9]


def test_staleness_and_reporter_run_before_successor_maintenance() -> None:
    order: list[str] = []

    class OrderedClient(FakeClient):
        def evaluate_staleness(self):
            order.append("staleness")
            return super().evaluate_staleness()

        def dispatch_reporter(self) -> None:
            order.append("reporter")
            super().dispatch_reporter()

        def list_successor_run_ids(self, expected_remaining_segments: int):
            order.append("successor")
            return super().list_successor_run_ids(expected_remaining_segments)

    client = OrderedClient(
        successor_snapshots=[[201]],
        decisions=[relay.Decision(True, "VISIBLE_PULSE_STALE")],
    )
    _machine(client).run()

    # The second successor call is the independent final-liveness assertion.
    assert order == ["staleness", "reporter", "successor", "successor"]


def test_successful_probe_resets_consecutive_decision_failures() -> None:
    client = FakeClient(
        decisions=[
            RuntimeError("temporary decision failure"),
            relay.Decision(False, "VISIBLE_PULSE_FRESH"),
            relay.Decision(False, "VISIBLE_PULSE_FRESH"),
        ]
    )

    result = _machine(client, poll_count=2).run()

    assert result["successfulPolls"] == 1
    assert result["finalDecisionReason"] == "VISIBLE_PULSE_FRESH"


def test_final_probe_cannot_exit_green_on_a_new_decision_failure() -> None:
    client = FakeClient(
        decisions=[
            relay.Decision(False, "VISIBLE_PULSE_FRESH"),
            RuntimeError("final decision failure"),
        ]
    )

    with pytest.raises(relay.RelayFailure, match="staleness_decision failure 1/3"):
        _machine(client, poll_count=2).run()


def test_final_liveness_rejects_unrecovered_reporter_warning() -> None:
    client = FakeClient(
        decisions=[
            relay.Decision(True, "VISIBLE_PULSE_STALE"),
            relay.Decision(False, "RECENT_PULSE_ATTEMPT_IN_COOLDOWN"),
        ],
        reporter_failures=1,
    )

    with pytest.raises(relay.RelayFailure, match="unrecovered_reporter"):
        _machine(client, poll_count=2).run()


def test_successor_failure_is_bounded_and_never_warns_for_five_hours() -> None:
    client = FakeClient(
        successor_snapshots=[[]],
        successor_dispatch_failure=True,
    )

    with pytest.raises(relay.RelayFailure, match="successor_liveness failure 3/3"):
        _machine(
            client,
            poll_count=3,
            verify_attempts=1,
            verify_delay_seconds=0,
        ).run()

    assert client.successor_dispatches == [9, 9, 9]


def test_configured_151st_poll_is_final_without_a_152nd_decision() -> None:
    clock = FakeClock()
    client = FakeClient(
        successor_snapshots=[],
        decisions=[relay.Decision(False, "VISIBLE_PULSE_FRESH")],
    )
    result = _machine(
        client,
        remaining_segments=1,
        poll_count=151,
        poll_interval_seconds=120,
        poll_sleep=clock.sleep,
        monotonic=clock,
    ).run()

    assert client.decision_calls == 151
    assert result["successfulPolls"] == 151
    assert clock.sleeps == [120] * 150
    assert clock.value == 5 * 60 * 60


def test_slow_poll_work_does_not_accumulate_into_start_time_drift() -> None:
    clock = FakeClock()

    class SlowClient(FakeClient):
        def __init__(self) -> None:
            super().__init__(
                successor_snapshots=[[9001]],
                decisions=[relay.Decision(False, "VISIBLE_PULSE_FRESH")],
            )
            self.decision_starts: list[float] = []

        def evaluate_staleness(self):
            self.decision_starts.append(clock())
            clock.advance(37)
            return super().evaluate_staleness()

    client = SlowClient()
    _machine(
        client,
        poll_count=4,
        poll_interval_seconds=120,
        poll_sleep=clock.sleep,
        monotonic=clock,
    ).run()

    assert client.decision_starts == [0, 120, 240, 360]
    assert clock.sleeps == [83, 83, 83]


def test_workflow_invokes_state_machine_with_exact_finite_geometry() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "scripts/run_mlb_progress_pulse_bounded_relay.py" in workflow
    assert "RELAY_MAX_SEGMENTS: '10'" in workflow
    assert "RELAY_POLL_INTERVAL_SECONDS: '120'" in workflow
    assert "RELAY_POLLS_PER_SEGMENT: '151'" in workflow
    assert "RELAY_FAILURE_THRESHOLD: '3'" in workflow
    assert "run-name: MLB pulse relay segment" in workflow
    assert "timeout-minutes: 325" in workflow
    permission_block = workflow.split("permissions:\n", 1)[1].split("\n\n", 1)[0]
    assert permission_block.splitlines() == [
        "  actions: write",
        "  contents: read",
        "  issues: read",
    ]
    assert "issues: write" not in workflow
    assert "secrets." not in workflow
    assert "AWS_ACCESS_KEY_ID" not in workflow
    assert "environment:" not in workflow
    assert "cancel-in-progress: false" in workflow
    assert "--failure-threshold \"$RELAY_FAILURE_THRESHOLD\"" in workflow
    assert "sleep " not in workflow
    assert "gh issue" not in SCRIPT.read_text(encoding="utf-8")

    # Strictly-over-28 staleness can wait at most one fixed two-minute phase.
    # Include the checker's outer timeout, dispatch API budget, and the observed
    # one-minute reporter runtime; this is objective margin, not a platform SLA.
    # Successor maintenance is post-report and therefore outside this path.
    maximum_detection_age_seconds = 28 * 60 + 2 * 60
    checker_budget_seconds = 130
    dispatch_api_budget_seconds = 60
    reporter_runtime_budget_seconds = 60
    maximum_visible_age_seconds = (
        maximum_detection_age_seconds
        + checker_budget_seconds
        + dispatch_api_budget_seconds
        + reporter_runtime_budget_seconds
    )
    assert maximum_visible_age_seconds == 34 * 60 + 10
    assert maximum_visible_age_seconds < 35 * 60

    # A relay poll makes one comment request, one run request, at most four job
    # proof requests, and one successor request. Thirty fixed-rate polls/hour
    # remain well below the shared 1,000-request repository token limit.
    assert 30 * 7 == 210
