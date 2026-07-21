#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "tests" / "unit" / "test_mlb_daily_per_game_lock.py"


def main() -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(TEST)],
        check=False,
    )
    if completed.returncode != 0:
        return completed.returncode
    print(
        "MLB per-game T-minus-45 lock verified: own-cutoff pulls, immediate "
        "canonical write-once game rows with no hidden post-cutoff delay, "
        "response-completion timestamps, canonical retry across diagnostic late backfills, "
        "read-only status, manifest-drift/tamper fail-closed behavior, idempotency, "
        "and final-card gating."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
