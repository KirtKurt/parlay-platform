from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RELAY = ROOT / ".github/workflows/mlb-progress-pulse-durable-relay.yml"


def test_durable_relay_is_non_billable_bounded_and_fail_closed() -> None:
    relay = RELAY.read_text(encoding="utf-8")

    assert "name: MLB progress pulse durable relay" in relay
    assert "name: mlb-pulse-30m-delay" in relay
    assert "deployment: false" in relay
    assert "REQUIRED_WAIT_MINUTES: '30'" in relay
    assert "MAX_RELAY_HOPS: '96'" in relay
    assert "wait_timer_count" in relay
    assert "incompatible_rule_count" in relay
    assert "environment_valid=true" in relay
    assert "exit 78" in relay
    assert "sleep " not in relay


def test_durable_relay_uses_only_bounded_github_dispatches() -> None:
    relay = RELAY.read_text(encoding="utf-8")

    assert "actions: write" in relay
    assert "issues: write" not in relay
    assert "AWS_ACCESS_KEY_ID" not in relay
    assert "secrets." not in relay
    assert "gh workflow run mlb-30m-progress-pulse.yml" in relay
    assert "--field force=false" in relay
    assert "gh workflow run mlb-progress-pulse-durable-relay.yml" in relay
    assert '--field remaining_hops="$NEXT_HOPS"' in relay
    assert "steps.guard.outputs.next_hops != '0'" in relay
    assert "other_active_count" in relay
    assert '.status != "completed"' in relay


def test_durable_relay_has_independent_seeds_and_serial_dedupe() -> None:
    relay = RELAY.read_text(encoding="utf-8")

    assert "cron: '23 3 * * *'" in relay
    assert "workflow_run:" in relay
    assert "workflow_dispatch:" in relay
    assert "push:" in relay
    assert "branches: [main]" in relay
    assert "group: mlb-progress-pulse-durable-relay" in relay
    assert "cancel-in-progress: false" in relay
    assert "github.event.workflow_run.head_branch == 'main'" in relay
