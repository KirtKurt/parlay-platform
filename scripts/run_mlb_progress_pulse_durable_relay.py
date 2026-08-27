#!/usr/bin/env python3
"""Non-billable, finite-lease MLB progress pulse relay.

The workflow environment supplies the 30-minute delay before this program runs.
This program verifies that repository-owned protection, preserves one trusted
successor, and dispatches only the existing stale-gated reporter.
"""
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
from typing import Any, Iterable, Mapping, Optional, Protocol

EXPECTED_REPOSITORY = "KirtKurt/parlay-platform"
EXPECTED_REF = "refs/heads/main"
RELAY_ENVIRONMENT = "mlb-pulse-30m-delay"
REQUIRED_WAIT_MINUTES = 30
MINIMUM_WAIT_SECONDS = REQUIRED_WAIT_MINUTES * 60
MAX_RELAY_HOPS = 96
DURABLE_RELAY_WORKFLOW = "mlb-progress-pulse-durable-relay.yml"
BOUNDED_RELAY_WORKFLOW = "mlb-progress-pulse-bounded-runner-relay.yml"
DURABLE_RELAY_PATH = f".github/workflows/{DURABLE_RELAY_WORKFLOW}"
BOUNDED_RELAY_PATH = f".github/workflows/{BOUNDED_RELAY_WORKFLOW}"
SHARED_RELAY_CONCURRENCY = "mlb-progress-pulse-bounded-runner-relay"
REPORTER_WORKFLOW = "mlb-30m-progress-pulse.yml"
STALENESS_SCRIPT = "scripts/check_mlb_progress_pulse_staleness.py"
ACTIVE_RUN_STATUSES = {"queued", "in_progress", "waiting", "pending", "requested"}
ALLOWED_EVENTS = {"schedule", "workflow_run", "workflow_dispatch", "push"}
AUTOMATIC_SEED_WORKFLOWS = {
    "MLB Canonical Runtime Health Watch",
    "MLB Scoring Guard",
    "Deploy SAM to AWS",
    "Verify MLB Scoring Fix After Deploy",
    "MLB Production Source Contract",
    "Unified MLB learning recovery once",
    "MLB Daily Yesterday Audit",
    "Tennis Autonomy Status Backstop",
    "Publish Tennis Autonomy Status",
    "NFL Auto AWS Stack Probe",
}
DAILY_SEED_CRON = "23 3 * * *"
VERIFY_ATTEMPTS = 3
VERIFY_DELAY_SECONDS = 2


class DurableRelayFailure(RuntimeError):
    """The relay could not preserve reporting or successor liveness."""


class Decision:
    def __init__(self, dispatch_required: bool, reason: str) -> None:
        self.dispatch_required = dispatch_required
        self.reason = reason


class DurableRelayClient(Protocol):
    def get_environment(self) -> Mapping[str, Any]:
        ...

    def get_current_run(self) -> Mapping[str, Any]:
        ...

    def list_bounded_runs(self) -> list[dict[str, Any]]:
        ...

    def list_durable_runs(self) -> list[dict[str, Any]]:
        ...

    def dispatch_successor(self, remaining_hops: int) -> None:
        ...

    def evaluate_staleness(self) -> Decision:
        ...

    def dispatch_reporter(self, *, force: bool) -> None:
        ...


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


def resolve_remaining_hops(event_name: str, input_value: Any) -> int:
    if event_name not in ALLOWED_EVENTS:
        raise ValueError("durable_relay_event_not_allowed")
    if event_name != "workflow_dispatch":
        return MAX_RELAY_HOPS
    raw = str(input_value if input_value not in (None, "") else MAX_RELAY_HOPS)
    if re.fullmatch(r"[1-9][0-9]*", raw) is None or len(raw) > 2:
        raise ValueError("remaining_hops_must_be_canonical_integer")
    value = int(raw)
    if value < 1 or value > MAX_RELAY_HOPS:
        raise ValueError("remaining_hops_out_of_range")
    return value


def validate_trigger(
    event_name: str,
    payload: Mapping[str, Any],
    *,
    repository: str,
    ref: str,
) -> None:
    if repository != EXPECTED_REPOSITORY:
        raise ValueError("repository_identity_mismatch")
    if ref != EXPECTED_REF:
        raise ValueError("durable_relay_requires_main_ref")
    payload_repository = payload.get("repository")
    if (
        not isinstance(payload_repository, Mapping)
        or payload_repository.get("full_name") != EXPECTED_REPOSITORY
    ):
        raise ValueError("event_repository_identity_mismatch")

    if event_name == "workflow_run":
        source = payload.get("workflow_run")
        if not isinstance(source, Mapping):
            raise ValueError("workflow_run_payload_missing")
        head_repository = source.get("head_repository")
        if (
            source.get("name") not in AUTOMATIC_SEED_WORKFLOWS
            or source.get("head_branch") != "main"
            or not isinstance(head_repository, Mapping)
            or head_repository.get("full_name") != EXPECTED_REPOSITORY
            or source.get("status") != "completed"
        ):
            raise ValueError("workflow_run_source_not_trusted")
    elif event_name == "push":
        if payload.get("ref") != EXPECTED_REF:
            raise ValueError("push_source_not_main")
    elif event_name == "schedule":
        if payload.get("schedule") != DAILY_SEED_CRON:
            raise ValueError("schedule_source_not_exact")
    elif event_name != "workflow_dispatch":
        raise ValueError("durable_relay_event_not_allowed")


def validate_environment(payload: Mapping[str, Any]) -> None:
    rules = payload.get("protection_rules")
    if not isinstance(rules, list):
        raise ValueError("environment_protection_rules_missing")
    wait_timers = [
        row
        for row in rules
        if isinstance(row, Mapping) and row.get("type") == "wait_timer"
    ]
    branch_policies = [
        row
        for row in rules
        if isinstance(row, Mapping) and row.get("type") == "branch_policy"
    ]
    incompatible = [
        row
        for row in rules
        if not isinstance(row, Mapping)
        or row.get("type") not in {"wait_timer", "branch_policy"}
    ]
    if (
        payload.get("name") != RELAY_ENVIRONMENT
        or len(wait_timers) != 1
        or wait_timers[0].get("wait_timer") != REQUIRED_WAIT_MINUTES
        or len(branch_policies) > 1
        or incompatible
        or payload.get("deployment_branch_policy") is not None
    ):
        raise ValueError("environment_wait_timer_contract_invalid")


def _workflow_path(run: Mapping[str, Any]) -> str:
    return str(run.get("path") or "").split("@", 1)[0]


def _trusted_run_common(
    run: Mapping[str, Any],
    *,
    expected_path: str,
) -> bool:
    head_repository = run.get("head_repository")
    return (
        run.get("head_branch") == "main"
        and isinstance(head_repository, Mapping)
        and head_repository.get("full_name") == EXPECTED_REPOSITORY
        and _workflow_path(run) == expected_path
        and run.get("status") in ACTIVE_RUN_STATUSES
        and _timestamp(run.get("created_at")) is not None
    )


def validate_wait_elapsed(
    run: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> float:
    created = _timestamp(run.get("created_at"))
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    observed = observed.astimezone(timezone.utc)
    if created is None:
        raise ValueError("current_run_created_at_invalid")
    seconds = (observed - created).total_seconds()
    if seconds < MINIMUM_WAIT_SECONDS:
        raise ValueError("environment_wait_timer_not_observed")
    return round(seconds / 60.0, 3)


def filter_active_bounded_runs(
    runs: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    trusted: list[dict[str, Any]] = []
    for source in runs:
        row = dict(source)
        title = str(row.get("display_title") or "")
        match = re.fullmatch(r"MLB pulse relay segment (10|[1-9])", title)
        if (
            _trusted_run_common(row, expected_path=BOUNDED_RELAY_PATH)
            and row.get("event") in {"push", "workflow_dispatch"}
            and match is not None
        ):
            row["_segment"] = int(match.group(1))
            trusted.append(row)
    trusted.sort(key=lambda row: _timestamp(row.get("created_at")), reverse=True)
    return trusted


def durable_run_hop(run: Mapping[str, Any]) -> Optional[int]:
    match = re.fullmatch(
        r"MLB durable pulse relay hop ([1-9][0-9]?)",
        str(run.get("display_title") or ""),
    )
    if match is None:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= MAX_RELAY_HOPS else None


def filter_newer_durable_runs(
    runs: Iterable[Mapping[str, Any]],
    *,
    current_run_id: Any,
    current_created_at: Any,
) -> list[dict[str, Any]]:
    current_created = _timestamp(current_created_at)
    if current_created is None:
        raise ValueError("current_run_created_at_invalid")
    current_id = str(current_run_id)
    trusted: list[dict[str, Any]] = []
    for source in runs:
        row = dict(source)
        created = _timestamp(row.get("created_at"))
        hop = durable_run_hop(row)
        if (
            str(row.get("id")) != current_id
            and _trusted_run_common(row, expected_path=DURABLE_RELAY_PATH)
            and row.get("event") in ALLOWED_EVENTS
            and created is not None
            and created > current_created
            and hop is not None
        ):
            row["_hop"] = hop
            trusted.append(row)
    trusted.sort(key=lambda row: _timestamp(row.get("created_at")), reverse=True)
    return trusted


class CommandDurableRelayClient:
    def __init__(self, *, repository: str, current_run_id: str) -> None:
        self.repository = repository
        self.current_run_id = current_run_id

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
        return json.loads(self._run(["gh", "api", path]) or "null")

    def _workflow_runs(self, workflow: str) -> list[dict[str, Any]]:
        payload = self._gh_json(
            f"repos/{self.repository}/actions/workflows/{workflow}/runs"
            "?branch=main&per_page=100"
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("workflow_runs_response_not_an_object")
        rows = payload.get("workflow_runs")
        if not isinstance(rows, list):
            raise RuntimeError("workflow_runs_not_a_list")
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def _repository_runs(self) -> list[dict[str, Any]]:
        payload = self._gh_json(
            f"repos/{self.repository}/actions/runs?branch=main&per_page=100"
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("repository_runs_response_not_an_object")
        rows = payload.get("workflow_runs")
        if not isinstance(rows, list):
            raise RuntimeError("repository_runs_not_a_list")
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def get_environment(self) -> Mapping[str, Any]:
        payload = self._gh_json(
            f"repos/{self.repository}/environments/{RELAY_ENVIRONMENT}"
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("environment_response_not_an_object")
        return payload

    def get_current_run(self) -> Mapping[str, Any]:
        payload = self._gh_json(
            f"repos/{self.repository}/actions/runs/{self.current_run_id}"
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("current_run_response_not_an_object")
        return payload

    def list_bounded_runs(self) -> list[dict[str, Any]]:
        # Repository-wide lookup remains valid even if a later migration removes
        # the legacy workflow file while already-admitted legacy runs continue.
        return self._repository_runs()

    def list_durable_runs(self) -> list[dict[str, Any]]:
        return self._workflow_runs(DURABLE_RELAY_WORKFLOW)

    def dispatch_successor(self, remaining_hops: int) -> None:
        self._run(
            [
                "gh",
                "workflow",
                "run",
                DURABLE_RELAY_WORKFLOW,
                "--repo",
                self.repository,
                "--ref",
                "main",
                "--field",
                f"remaining_hops={remaining_hops}",
            ]
        )

    def evaluate_staleness(self) -> Decision:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="mlb-durable-relay-",
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

    def dispatch_reporter(self, *, force: bool) -> None:
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
                f"force={'true' if force else 'false'}",
            ]
        )


class DurableRelayController:
    def __init__(
        self,
        *,
        client: DurableRelayClient,
        current_run_id: str,
        remaining_hops: int,
        verify_attempts: int = VERIFY_ATTEMPTS,
        verify_delay_seconds: int = VERIFY_DELAY_SECONDS,
        verify_sleep=time.sleep,
        observed_now: Optional[datetime] = None,
    ) -> None:
        if remaining_hops < 1 or remaining_hops > MAX_RELAY_HOPS:
            raise ValueError("remaining_hops_out_of_range")
        if verify_attempts < 1 or verify_delay_seconds < 0:
            raise ValueError("invalid_verification_geometry")
        self.client = client
        self.current_run_id = current_run_id
        self.remaining_hops = remaining_hops
        self.next_hops = remaining_hops - 1
        self.verify_attempts = verify_attempts
        self.verify_delay_seconds = verify_delay_seconds
        self.verify_sleep = verify_sleep
        self.observed_now = observed_now

    @staticmethod
    def _validate_current_run(
        row: Mapping[str, Any],
        *,
        current_run_id: str,
        remaining_hops: int,
    ) -> None:
        if (
            str(row.get("id")) != str(current_run_id)
            or not _trusted_run_common(row, expected_path=DURABLE_RELAY_PATH)
            or row.get("event") not in ALLOWED_EVENTS
            or durable_run_hop(row) != remaining_hops
        ):
            raise DurableRelayFailure("current_run_identity_invalid")

    def _ensure_successor(
        self,
        current: Mapping[str, Any],
    ) -> tuple[Optional[int], Optional[int]]:
        def newer() -> list[dict[str, Any]]:
            return filter_newer_durable_runs(
                self.client.list_durable_runs(),
                current_run_id=self.current_run_id,
                current_created_at=current.get("created_at"),
            )

        def acceptable(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
            return [
                dict(row)
                for row in rows
                if row.get("_hop") == self.next_hops
                or row.get("_hop") == MAX_RELAY_HOPS
            ]

        found = acceptable(newer())
        if found:
            return int(found[0]["id"]), int(found[0]["_hop"])
        if self.next_hops == 0:
            return None, None

        self.client.dispatch_successor(self.next_hops)
        errors: list[str] = []
        for attempt in range(1, self.verify_attempts + 1):
            try:
                found = acceptable(newer())
            except Exception as exc:
                errors.append(f"verify_{attempt}={exc}")
            else:
                if found:
                    return int(found[0]["id"]), int(found[0]["_hop"])
            if attempt < self.verify_attempts:
                self.verify_sleep(self.verify_delay_seconds)
        detail = ";".join(errors) if errors else "successor_not_visible"
        raise DurableRelayFailure(f"successor_liveness_failure:{detail}")

    def run(self) -> dict[str, Any]:
        validate_environment(self.client.get_environment())
        current = self.client.get_current_run()
        self._validate_current_run(
            current,
            current_run_id=self.current_run_id,
            remaining_hops=self.remaining_hops,
        )
        actual_wait_minutes = validate_wait_elapsed(
            current,
            now=self.observed_now,
        )

        bounded = filter_active_bounded_runs(self.client.list_bounded_runs())
        if bounded:
            return {
                "migrationReady": False,
                "reason": "BOUNDED_RELAY_STILL_ACTIVE",
                "boundedRunId": bounded[0].get("id"),
                "boundedSegment": bounded[0].get("_segment"),
                "remainingHops": self.remaining_hops,
                "actualWaitMinutes": actual_wait_minutes,
                "environmentValid": True,
                "successorRunId": None,
                "reporterDispatched": False,
                "terminalWarning": False,
            }

        failures: list[str] = []
        successor_id: Optional[int] = None
        successor_hop: Optional[int] = None
        try:
            successor_id, successor_hop = self._ensure_successor(current)
        except Exception as exc:
            failures.append(f"successor={exc}")

        terminal_warning = self.next_hops == 0 and successor_id is None
        decision: Optional[Decision] = None
        try:
            decision = self.client.evaluate_staleness()
        except Exception as exc:
            failures.append(f"decision={exc}")

        reporter_dispatched = False
        # A terminal diagnostic must be visible. Otherwise dispatch only when
        # stale, or when the preflight failed and the primary can safely recheck.
        should_dispatch = (
            terminal_warning
            or decision is None
            or bool(decision.dispatch_required)
        )
        if should_dispatch:
            try:
                self.client.dispatch_reporter(force=terminal_warning)
            except Exception as exc:
                failures.append(f"reporter={exc}")
            else:
                reporter_dispatched = True

        result = {
            "migrationReady": True,
            "relayMode": "durable_environment_timer",
            "currentRunId": int(self.current_run_id),
            "environment": RELAY_ENVIRONMENT,
            "environmentValid": True,
            "waitTimerMinutes": REQUIRED_WAIT_MINUTES,
            "actualWaitMinutes": actual_wait_minutes,
            "reason": (
                "TERMINAL_LEASE_WARNING"
                if terminal_warning
                else decision.reason
                if decision is not None
                else "PREFLIGHT_FAILED_REPORTER_RECHECK"
            ),
            "remainingHops": self.remaining_hops,
            "nextHops": self.next_hops,
            "successorRunId": successor_id,
            "successorHop": successor_hop,
            "reporterDispatched": reporter_dispatched,
            "terminalWarning": terminal_warning,
        }
        if failures:
            raise DurableRelayFailure(";".join(failures))
        return result


def _event_payload(path: str) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("github_event_payload_not_an_object")
    return value


def _write_summary(result: Mapping[str, Any]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write("## Durable MLB progress pulse relay\n\n")
        for key, value in result.items():
            handle.write(f"- {key}: {value}\n")


def _write_github_output(path: str, values: Mapping[str, Any]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preflight", "relay"), default="relay")
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument(
        "--remaining-hops",
        default=os.environ.get("INPUT_REMAINING_HOPS", ""),
    )
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args()

    try:
        remaining_hops = resolve_remaining_hops(
            args.event_name,
            args.remaining_hops,
        )
        validate_trigger(
            args.event_name,
            _event_payload(args.event_path),
            repository=os.environ["GITHUB_REPOSITORY"],
            ref=os.environ["GITHUB_REF"],
        )
        client = CommandDurableRelayClient(
            repository=os.environ["GITHUB_REPOSITORY"],
            current_run_id=os.environ["GITHUB_RUN_ID"],
        )
        if args.mode == "preflight":
            validate_environment(client.get_environment())
            result = {
                "preflightValid": True,
                "remainingHops": remaining_hops,
                "environment": RELAY_ENVIRONMENT,
                "waitTimerMinutes": REQUIRED_WAIT_MINUTES,
            }
            _write_github_output(
                args.github_output,
                {
                    "remaining_hops": remaining_hops,
                    "environment_valid": "true",
                },
            )
        else:
            result = DurableRelayController(
                client=client,
                current_run_id=os.environ["GITHUB_RUN_ID"],
                remaining_hops=remaining_hops,
            ).run()
    except (
        KeyError,
        OSError,
        ValueError,
        DurableRelayFailure,
        RuntimeError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        print(f"::error::Durable MLB pulse relay failed closed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True))
    _write_summary(result)
    if result.get("migrationReady") is False:
        print(
            "::error::Durable MLB pulse relay stopped: incumbent bounded relay "
            "still owns the reporting clock; reseed only after it is terminal.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
