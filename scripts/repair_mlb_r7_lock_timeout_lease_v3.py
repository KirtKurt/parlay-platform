#!/usr/bin/env python3
"""Align MLB R7 protected lock timeout and lease contracts.

This repair changes only the MLB protected lock runtime, its SAM resource,
deployment/schedule verifiers, and focused tests. It does not change prediction,
lock, label, settlement, promotion, or production-authority semantics.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "template.yaml",
    "scripts/patch_template_mlb_v1.py",
    "scripts/verify_mlb_deploy_identity.py",
    "scripts/verify_mlb_schedule_invariants.py",
    "hello_world/mlb_daily_pick_lock_protected.py",
    "hello_world/mlb_daily_per_game_lock_patch.py",
    "tests/unit/test_mlb_daily_pick_lock_runtime.py",
    "tests/unit/test_mlb_deploy_identity.py",
    "tests/unit/test_mlb_daily_per_game_lock.py",
    "tests/unit/test_mlb_daily_pick_lock_lease_duration.py",
    "tests/unit/test_mlb_daily_pick_lock_protected.py",
)


DIRECT_REPLACEMENTS = (
    ("LOCK_EXECUTION_LEASE_REQUIRED_SECONDS = 360", "LOCK_EXECUTION_LEASE_REQUIRED_SECONDS = 960"),
    ("LOCK_EXECUTION_LEASE_SECONDS = 360", "LOCK_EXECUTION_LEASE_SECONDS = 960"),
    ('LOCK_EXECUTION_LEASE_SECONDS = "360"', 'LOCK_EXECUTION_LEASE_SECONDS = "960"'),
    ("LOCK_EXECUTION_LEASE_SECONDS = '360'", "LOCK_EXECUTION_LEASE_SECONDS = '960'"),
    ("LOCK_TIMEOUT_SECONDS = 300", "LOCK_TIMEOUT_SECONDS = 900"),
    (
        "if '\\n      Timeout: 300\\n' not in lock_resource:",
        "if '\\n      Timeout: 900\\n' not in lock_resource:",
    ),
    (
        "daily lock must use one exact 360-second outer execution lease",
        "daily lock must use one exact 960-second outer execution lease",
    ),
    ("lease_seconds=360", "lease_seconds=960"),
    ("lease_seconds = 360", "lease_seconds = 960"),
    ("lease_seconds=359", "lease_seconds=959"),
    ("lease_seconds = 359", "lease_seconds = 959"),
    ('"leaseSeconds": 360', '"leaseSeconds": 960'),
    ("'leaseSeconds': 360", "'leaseSeconds': 960"),
    ('"requiredLeaseSeconds": 360', '"requiredLeaseSeconds": 960'),
    ("'requiredLeaseSeconds': 360", "'requiredLeaseSeconds': 960"),
    ('"lambdaTimeoutSeconds": 300', '"lambdaTimeoutSeconds": 900'),
    ("'lambdaTimeoutSeconds': 300", "'lambdaTimeoutSeconds': 900"),
    ("remaining_millis=300_001", "remaining_millis=900_001"),
    ("remaining_millis = 300_001", "remaining_millis = 900_001"),
    ('["timeoutSeconds"] == 300', '["timeoutSeconds"] == 900'),
    ("['timeoutSeconds'] == 300", "['timeoutSeconds'] == 900"),
    ('["executionLeaseSeconds"] == 360', '["executionLeaseSeconds"] == 960'),
    ("['executionLeaseSeconds'] == 360", "['executionLeaseSeconds'] == 960"),
    ("_lock_execution_lease_seconds == 360", "_lock_execution_lease_seconds == 960"),
    ('clock["now"] += timedelta(seconds=361)', 'clock["now"] += timedelta(seconds=961)'),
    ("clock['now'] += timedelta(seconds=361)", "clock['now'] += timedelta(seconds=961)"),
    ('expires_at = clock["now"] + timedelta(seconds=360)', 'expires_at = clock["now"] + timedelta(seconds=960)'),
    ("expires_at = clock['now'] + timedelta(seconds=360)", "expires_at = clock['now'] + timedelta(seconds=960)"),
    ("360-second lease", "960-second lease"),
)


def _patch_lock_resource(text: str, source: str) -> str:
    """Patch only MLBDailyPickLockFunction blocks in YAML or embedded YAML."""
    marker = "MLBDailyPickLockFunction:"
    positions = [match.start() for match in re.finditer(marker, text)]
    if not positions:
        raise RuntimeError(f"{source}: MLBDailyPickLockFunction marker missing")

    result = text
    offset = 0
    for original_start in positions:
        start = original_start + offset
        tail = result[start:]
        candidates: list[int] = []
        next_resource = re.search(
            r"(?m)^  [A-Za-z][A-Za-z0-9]*:\s*$",
            tail[len(marker) :],
        )
        if next_resource:
            candidates.append(len(marker) + next_resource.start())
        triple_quote = tail.find('"""', len(marker))
        if triple_quote >= 0:
            candidates.append(triple_quote)
        end_relative = min(candidates) if candidates else len(tail)
        block = tail[:end_relative]
        patched = re.sub(
            r"(?m)^(\s*Timeout:\s*)(?:300|900)\s*$",
            r"\g<1>900",
            block,
        )
        patched = patched.replace(
            "MLB_LOCK_EXECUTION_LEASE_SECONDS: '360'",
            "MLB_LOCK_EXECUTION_LEASE_SECONDS: '960'",
        ).replace(
            'MLB_LOCK_EXECUTION_LEASE_SECONDS: "360"',
            'MLB_LOCK_EXECUTION_LEASE_SECONDS: "960"',
        )
        if patched != block:
            result = result[:start] + patched + result[start + len(block) :]
            offset += len(patched) - len(block)
    return result


def _patch_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    if path.name in {"template.yaml", "patch_template_mlb_v1.py"}:
        text = _patch_lock_resource(text, str(path.relative_to(ROOT)))
        text = text.replace(
            "lease_environment = \"          MLB_LOCK_EXECUTION_LEASE_SECONDS: '360'\\n\"",
            "lease_environment = \"          MLB_LOCK_EXECUTION_LEASE_SECONDS: '960'\\n\"",
        )

    for old, new in DIRECT_REPLACEMENTS:
        text = text.replace(old, new)

    updated_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        lowered = line.lower()
        if any(
            token in lowered
            for token in (
                "lockexecutionlease",
                "lock_execution_lease",
                "mlb_lock_execution_lease",
                "executionleaseseconds",
                "leaseseconds",
                "requiredleaseseconds",
            )
        ):
            line = re.sub(r"(?<!\d)360(?!\d)", "960", line)
            line = re.sub(r"(?<!\d)359(?!\d)", "959", line)
        if any(
            token in lowered
            for token in (
                "lock_timeout",
                "lock timeout",
                "lambdatimeoutseconds",
                "timeoutseconds",
            )
        ):
            line = re.sub(r"(?<!\d)300(?!\d)", "900", line)
        updated_lines.append(line)
    text = "".join(updated_lines)

    if text == original:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def _verify() -> None:
    template = (ROOT / "template.yaml").read_text(encoding="utf-8")
    lock_block = template.split("\n  MLBDailyPickLockFunction:\n", 1)[1].split(
        "\n  MLBProductionVerifierFunction:\n", 1
    )[0]
    assert "Timeout: 900" in lock_block
    assert "MLB_LOCK_EXECUTION_LEASE_SECONDS: '960'" in lock_block
    assert "Timeout: 300" not in lock_block
    assert "MLB_LOCK_EXECUTION_LEASE_SECONDS: '360'" not in lock_block

    protected = (ROOT / "hello_world/mlb_daily_pick_lock_protected.py").read_text(
        encoding="utf-8"
    )
    assert "LOCK_EXECUTION_LEASE_REQUIRED_SECONDS = 960" in protected
    assert '"lambdaTimeoutSeconds": 900' in protected
    assert "_lock_execution_lease_seconds == 960" in protected
    assert '"leaseSeconds": 360' not in protected

    per_game = (ROOT / "hello_world/mlb_daily_per_game_lock_patch.py").read_text(
        encoding="utf-8"
    )
    assert "LOCK_EXECUTION_LEASE_SECONDS = 960" in per_game
    assert '"leaseSeconds": 360' not in per_game

    schedule_verify = (ROOT / "scripts/verify_mlb_schedule_invariants.py").read_text(
        encoding="utf-8"
    )
    assert "if '\\n      Timeout: 900\\n' not in lock_resource:" in schedule_verify
    assert "if '\\n      Timeout: 300\\n' not in lock_resource:" not in schedule_verify
    assert "daily lock must use one exact 960-second outer execution lease" in schedule_verify

    runtime_test = (ROOT / "tests/unit/test_mlb_daily_pick_lock_runtime.py").read_text(
        encoding="utf-8"
    )
    stale = (
        "lease_seconds=360",
        "module.MLB_LOCK_EXECUTION_LEASE_SECONDS = 360",
        '"leaseSeconds": 360',
        '"requiredLeaseSeconds": 360',
        '"lambdaTimeoutSeconds": 300',
        "remaining_millis=300_001",
        'clock["now"] += timedelta(seconds=361)',
        'expires_at = clock["now"] + timedelta(seconds=360)',
    )
    for value in stale:
        assert value not in runtime_test, value
    assert "lease_seconds=960" in runtime_test
    assert "remaining_millis=900_001" in runtime_test
    assert 'clock["now"] += timedelta(seconds=961)' in runtime_test
    assert 'expires_at = clock["now"] + timedelta(seconds=960)' in runtime_test


def main() -> int:
    changed: list[str] = []
    for relative in TARGETS:
        path = ROOT / relative
        if path.exists() and _patch_file(path):
            changed.append(relative)
    _verify()
    print("MLB R7 timeout/lease repair changed:")
    for relative in changed:
        print(relative)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
