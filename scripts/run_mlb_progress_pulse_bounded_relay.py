#!/usr/bin/env python3
"""Finite no-admin relay for the trusted MLB progress pulse reporter."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol

RELAY_WORKFLOW = "mlb-progress-pulse-bounded-runner-relay.yml"
REPORTER_WORKFLOW = "mlb-30m-progress-pulse.yml"
STALENESS_SCRIPT = "scripts/check_mlb_progress_pulse_staleness.py"
ACTIVE_RUN_STATUSES = {"queued", "in_progress", "waiting", "pending", "requested"}
MAX_SEGMENTS = 10
DEFAULT_POLLS = 151
DEFAULT_POLL_SECONDS = 120
DEFAULT_FAILURE_THRESHOLD = 3
SUCCESSOR_VERIFY_ATTEMPTS = 3
SUCCESSOR_VERIFY_DELAY_SECONDS = 2


class RelayFailure(RuntimeError):
    """The active segment must fail so its verified pending successor can recover."""


class Decision:
    def __init__(self, dispatch_required: bool, reason: str) -> None:
        self.dispatch_required = dispatch_required
        self.reason = reason


class RelayClient(Protocol):
    def list_successor_run_ids(self, expected_remaining_segments: int) -> list[int]:
        ...

    def dispatch_successor(self, remaining_segments: int) -> None:
        ...

    def evaluate_staleness(self) -> Decision:
        ...

    def dispatch_reporter(self) -> None:
        ...


def resolve_remaining_segments(
    event_name: str,
    input_value: Any,
    *,
    maximum: int = MAX_SEGMENTS,
) -> int:
    """Allow one merge-push seed or an explicit, bounded manual/self dispatch."""

    if event_name == "push":
        return maximum
    if event_name != "workflow_dispatch":
        raise ValueError("relay_event_must_be_push_or_workflow_dispatch")

    raw = str(input_value if input_value not in (None, "") else maximum)
    if re.fullmatch(r"[1-9][0-9]*", raw) is None or len(raw) > len(str(maximum)):
        raise ValueError("remaining_segments_must_be_a_bounded_integer")
    value = int(raw)
    if value < 1 or value > maximum:
        raise ValueError("remaining_segments_out_of_range")
    return value


def _timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def successor_run_title(remaining_segments: int) -> str:
    return f"MLB pulse relay segment {remaining_segments}"


def filter_successor_run_ids(
    runs: Iterable[Mapping[str, Any]],
    *,
    current_run_id: Any,
    current_created_at: Any,
    expected_remaining_segments: int,
    expected_repository: str,
) -> list[int]:
    """Return only a chain-bound, noncompleted main dispatch successor."""

    current = str(current_run_id)
    current_created = _timestamp(current_created_at)
    if current_created is None:
        raise ValueError("current_run_created_at_invalid")
    expected_title = successor_run_title(expected_remaining_segments)
    renewal_title = successor_run_title(MAX_SEGMENTS)
    found: list[int] = []
    for run in runs:
        if str(run.get("id")) == current:
            continue
        event = str(run.get("event") or "")
        title = str(run.get("display_title") or "")
        is_exact_decrement = event == "workflow_dispatch" and title == expected_title
        # A newer explicit manual dispatch or reviewed merge push is a trusted,
        # finite lease renewal. Preserve it instead of replacing it with n-1.
        is_explicit_renewal = (
            event in {"workflow_dispatch", "push"} and title == renewal_title
        )
        if not (is_exact_decrement or is_explicit_renewal):
            continue
        if str(run.get("head_branch") or "") != "main":
            continue
        head_repository = run.get("head_repository")
        if (
            not isinstance(head_repository, Mapping)
            or str(head_repository.get("full_name") or "") != expected_repository
        ):
            continue
        if str(run.get("status") or "") not in ACTIVE_RUN_STATUSES:
            continue
        created = _timestamp(run.get("created_at"))
        if created is None or created <= current_created:
            continue
        try:
            found.append(int(run["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(set(found))


class CommandRelayClient:
    def __init__(self, *, repository: str, current_run_id: str) -> None:
        self.repository = repository
        self.current_run_id = current_run_id
        self._current_created_at: Optional[str] = None

    @staticmethod
    def _run(args: list[str], *, timeout_seconds: int = 60) -> str:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "command_failed").strip()
            raise RuntimeError(detail[-1000:])
        return result.stdout

    def _gh_json(self, path: str) -> Any:
        output = self._run(["gh", "api", path])
        return json.loads(output or "null")

    def _get_current_created_at(self) -> str:
        if self._current_created_at is None:
            payload = self._gh_json(
                f"repos/{self.repository}/actions/runs/{self.current_run_id}"
            )
            if not isinstance(payload, Mapping) or not payload.get("created_at"):
                raise RuntimeError("current_relay_run_missing_created_at")
            self._current_created_at = str(payload["created_at"])
        return self._current_created_at

    def list_successor_run_ids(self, expected_remaining_segments: int) -> list[int]:
        payload = self._gh_json(
            f"repos/{self.repository}/actions/workflows/{RELAY_WORKFLOW}/runs"
            "?branch=main&per_page=100"
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("relay_runs_response_not_an_object")
        rows = payload.get("workflow_runs") or []
        if not isinstance(rows, list):
            raise RuntimeError("relay_runs_not_a_list")
        return filter_successor_run_ids(
            [row for row in rows if isinstance(row, Mapping)],
            current_run_id=self.current_run_id,
            current_created_at=self._get_current_created_at(),
            expected_remaining_segments=expected_remaining_segments,
            expected_repository=self.repository,
        )

    def dispatch_successor(self, remaining_segments: int) -> None:
        self._run(
            [
                "gh",
                "workflow",
                "run",
                RELAY_WORKFLOW,
                "--repo",
                self.repository,
                "--ref",
                "main",
                "--field",
                f"remaining_segments={remaining_segments}",
            ]
        )

    def evaluate_staleness(self) -> Decision:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="mlb-pulse-relay-",
            suffix=".out",
            delete=False,
        ) as handle:
            output_path = Path(handle.name)
        try:
            self._run(
                [
                    sys.executable,
                    STALENESS_SCRIPT,
                    "--stale-after-minutes",
                    "28",
                    "--retry-cooldown-minutes",
                    "10",
                    "--github-output",
                    str(output_path),
                ],
                timeout_seconds=130,
            )
            values: dict[str, str] = {}
            for line in output_path.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    values[key] = value
            required = values.get("dispatch_required")
            reason = values.get("reason")
            if required not in {"true", "false"} or not reason:
                raise RuntimeError("invalid_staleness_decision_output")
            return Decision(required == "true", reason)
        finally:
            output_path.unlink(missing_ok=True)

    def dispatch_reporter(self) -> None:
        self._run(
            [
                "gh",
                "workflow",
                "run",
                REPORTER_WORKFLOW,
                "--repo",
                self.repository,
                "--ref",
                "main",
                "--field",
                "force=false",
            ]
        )


class RelayStateMachine:
    def __init__(
        self,
        *,
        client: RelayClient,
        remaining_segments: int,
        poll_count: int = DEFAULT_POLLS,
        poll_interval_seconds: int = DEFAULT_POLL_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        poll_sleep: Callable[[float], None] = time.sleep,
        verify_sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        verify_attempts: int = SUCCESSOR_VERIFY_ATTEMPTS,
        verify_delay_seconds: int = SUCCESSOR_VERIFY_DELAY_SECONDS,
    ) -> None:
        if remaining_segments < 1 or remaining_segments > MAX_SEGMENTS:
            raise ValueError("remaining_segments_out_of_range")
        if poll_count < 1 or poll_interval_seconds < 0:
            raise ValueError("invalid_poll_geometry")
        if failure_threshold < 1 or verify_attempts < 1 or verify_delay_seconds < 0:
            raise ValueError("invalid_failure_geometry")
        self.client = client
        self.remaining_segments = remaining_segments
        self.next_segments = remaining_segments - 1
        self.poll_count = poll_count
        self.poll_interval_seconds = poll_interval_seconds
        self.failure_threshold = failure_threshold
        self.poll_sleep = poll_sleep
        self.verify_sleep = verify_sleep
        self.monotonic = monotonic
        self.verify_attempts = verify_attempts
        self.verify_delay_seconds = verify_delay_seconds
        self.consecutive_successor_failures = 0
        self.consecutive_decision_failures = 0
        self.consecutive_reporter_failures = 0
        self.successful_polls = 0
        self.reporter_dispatches = 0
        self.last_decision: Optional[Decision] = None

    def _warn_or_raise(
        self,
        counter_name: str,
        label: str,
        error: Exception,
        *,
        final: bool,
    ) -> None:
        count = int(getattr(self, counter_name)) + 1
        setattr(self, counter_name, count)
        message = f"{label} failure {count}/{self.failure_threshold}: {error}"
        if final or count >= self.failure_threshold:
            raise RelayFailure(message)
        print(f"::warning::{message}", flush=True)

    def _ensure_successor(self) -> None:
        if self.next_segments == 0:
            return

        errors: list[str] = []
        try:
            if self.client.list_successor_run_ids(self.next_segments):
                return
        except Exception as exc:
            errors.append(f"initial_list={exc}")

        try:
            self.client.dispatch_successor(self.next_segments)
        except Exception as exc:
            errors.append(f"dispatch={exc}")

        for attempt in range(1, self.verify_attempts + 1):
            try:
                if self.client.list_successor_run_ids(self.next_segments):
                    return
            except Exception as exc:
                errors.append(f"verify_{attempt}={exc}")
            if attempt < self.verify_attempts:
                self.verify_sleep(self.verify_delay_seconds)

        detail = "; ".join(errors) if errors else "successor_not_visible"
        raise RuntimeError(detail)

    def _poll(self, *, final: bool) -> None:
        try:
            self._ensure_successor()
        except Exception as exc:
            self._warn_or_raise(
                "consecutive_successor_failures",
                "successor_liveness",
                exc,
                final=final,
            )
            return
        self.consecutive_successor_failures = 0

        try:
            decision = self.client.evaluate_staleness()
        except Exception as exc:
            self._warn_or_raise(
                "consecutive_decision_failures",
                "staleness_decision",
                exc,
                final=final,
            )
            return
        self.consecutive_decision_failures = 0
        self.last_decision = decision

        if decision.dispatch_required:
            try:
                self.client.dispatch_reporter()
            except Exception as exc:
                self._warn_or_raise(
                    "consecutive_reporter_failures",
                    "reporter_dispatch",
                    exc,
                    final=final,
                )
                return
            self.consecutive_reporter_failures = 0
            self.reporter_dispatches += 1
        elif decision.reason == "VISIBLE_PULSE_FRESH":
            # A trusted fresh comment proves that a prior reporter recovered.
            self.consecutive_reporter_failures = 0

        self.successful_polls += 1

    def assert_final_liveness(self) -> None:
        # The last configured poll is strict. This assertion does not perform a
        # 77th decision that could race the reporter dispatched by poll 76.
        if self.successful_polls < 1 or self.last_decision is None:
            raise RelayFailure("no_successful_staleness_decision")
        if self.consecutive_decision_failures != 0:
            raise RelayFailure("unrecovered_staleness_decision_failure")
        if self.consecutive_reporter_failures != 0:
            raise RelayFailure("unrecovered_reporter_dispatch_failure")
        if self.consecutive_successor_failures != 0:
            raise RelayFailure("unrecovered_successor_liveness_failure")
        if self.next_segments != 0:
            try:
                self._ensure_successor()
            except Exception as exc:
                raise RelayFailure(f"final_successor_liveness_failure: {exc}") from exc

    def run(self) -> dict[str, Any]:
        # Anchor starts to monotonic deadlines. API/checker time therefore uses
        # the interval budget instead of accumulating after every poll.
        started_at = self.monotonic()
        for poll_index in range(1, self.poll_count + 1):
            if poll_index > 1:
                deadline = started_at + (poll_index - 1) * self.poll_interval_seconds
                delay = max(0.0, deadline - self.monotonic())
                if delay > 0:
                    self.poll_sleep(delay)
            self._poll(final=poll_index == self.poll_count)
        self.assert_final_liveness()
        return {
            "remainingSegments": self.remaining_segments,
            "nextSegments": self.next_segments,
            "pollCount": self.poll_count,
            "successfulPolls": self.successful_polls,
            "reporterDispatches": self.reporter_dispatches,
            "finalDecisionReason": self.last_decision.reason if self.last_decision else None,
            "successorRequired": self.next_segments != 0,
        }


def _write_summary(result: Mapping[str, Any]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write("## Bounded MLB pulse runner relay\n\n")
        for key, value in result.items():
            handle.write(f"- {key}: {value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument(
        "--remaining-segments",
        default=os.environ.get("INPUT_REMAINING_SEGMENTS", ""),
    )
    parser.add_argument("--poll-count", type=int, default=DEFAULT_POLLS)
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
    )
    parser.add_argument(
        "--failure-threshold",
        type=int,
        default=DEFAULT_FAILURE_THRESHOLD,
    )
    args = parser.parse_args()

    try:
        remaining = resolve_remaining_segments(
            args.event_name,
            args.remaining_segments,
        )
        client = CommandRelayClient(
            repository=os.environ["GITHUB_REPOSITORY"],
            current_run_id=os.environ["GITHUB_RUN_ID"],
        )
        machine = RelayStateMachine(
            client=client,
            remaining_segments=remaining,
            poll_count=args.poll_count,
            poll_interval_seconds=args.poll_interval_seconds,
            failure_threshold=args.failure_threshold,
        )
        result = machine.run()
    except (KeyError, ValueError, RelayFailure, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"::error::Bounded MLB pulse relay failed closed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True))
    _write_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
