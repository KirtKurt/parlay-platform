from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RELAY = ROOT / ".github/workflows/mlb-progress-pulse-bounded-runner-relay.yml"


def _integer_constant(relay: str, name: str) -> int:
    match = re.search(rf"^  {name}: '(\\d+)'$", relay, flags=re.MULTILINE)
    assert match is not None
    return int(match.group(1))


def test_runner_relay_is_finite_main_only_and_self_protecting() -> None:
    relay = RELAY.read_text(encoding="utf-8")

    assert "name: MLB progress pulse bounded runner relay" in relay
    assert "github.event.workflow_run.head_branch == 'main'" in relay
    assert "if [ \"$GITHUB_REF\" != 'refs/heads/main' ]" in relay
    assert "remaining_segments must be an integer from 1 through" in relay
    assert "^ [1-9]" not in relay
    assert "^[1-9][0-9]*$" in relay
    assert "next_segments=$((10#$remaining_segments - 1))" in relay
    assert "timeout-minutes: 325" in relay
    assert "group: mlb-progress-pulse-bounded-runner-relay" in relay
    assert "cancel-in-progress: false" in relay

    max_segments = _integer_constant(relay, "RELAY_MAX_SEGMENTS")
    poll_seconds = _integer_constant(relay, "RELAY_POLL_INTERVAL_SECONDS")
    polls = _integer_constant(relay, "RELAY_POLLS_PER_SEGMENT")
    assert max_segments == 10
    assert poll_seconds == 240
    assert polls == 76
    assert (polls - 1) * poll_seconds == 5 * 60 * 60
    assert max_segments * (polls - 1) * poll_seconds == 50 * 60 * 60


def test_runner_relay_preserves_the_existing_read_only_staleness_gate() -> None:
    relay = RELAY.read_text(encoding="utf-8")

    assert "actions: write" in relay
    assert "issues: write" not in relay
    assert "secrets." not in relay
    assert "AWS_ACCESS_KEY_ID" not in relay
    assert "environment:" not in relay
    assert "scripts/check_mlb_progress_pulse_staleness.py" in relay
    assert "--stale-after-minutes \"$MLB_PROGRESS_STALE_AFTER_MINUTES\"" in relay
    assert "--retry-cooldown-minutes 10" in relay
    assert "gh workflow run mlb-30m-progress-pulse.yml" in relay
    assert "--field force=false" in relay
    assert "gh issue comment" not in relay
    assert "sleep \"$RELAY_POLL_INTERVAL_SECONDS\"" in relay


def test_runner_relay_queues_one_bounded_successor_before_waiting() -> None:
    relay = RELAY.read_text(encoding="utf-8")

    assert "gh workflow run mlb-progress-pulse-bounded-runner-relay.yml" in relay
    assert '--field remaining_segments="$NEXT_SEGMENTS"' in relay
    assert "queue_successor || true" in relay
    assert relay.index("queue_successor || true") < relay.index(
        'sleep "$RELAY_POLL_INTERVAL_SECONDS"'
    )
    assert "the next poll will retry" in relay
    assert "could not secure its finite successor" in relay


def test_runner_relay_has_only_trusted_independent_main_seeds() -> None:
    relay = RELAY.read_text(encoding="utf-8")

    for producer in (
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
    ):
        assert producer in relay

    assert "branches: [main]" in relay
    assert "cron: '23 3 * * *'" in relay
    assert "workflow_dispatch:" in relay
    assert "MLB 30-minute production progress pulse" not in relay
