#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "tests" / "unit" / "test_mlb_daily_per_game_lock.py"


def main() -> int:
    spec = importlib.util.spec_from_file_location("inqsi_mlb_per_game_lock_tests", TEST)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {TEST}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tests = sorted(
        (name, value)
        for name, value in vars(module).items()
        if name.startswith("test_") and callable(value)
    )
    if not tests:
        raise RuntimeError("No per-game lock tests discovered")
    for _, test in tests:
        test()
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
