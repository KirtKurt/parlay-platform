#!/usr/bin/env python3
"""Add the deploy-identity verifier to isolated no-BBD test fixtures.

The synthetic verifier is deliberately credential-free; tests should fail if
active deploy identity source reintroduces a retired provider token.
"""
from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "unit" / "test_verify_mlb_no_bbd_runtime.py"
MARKER = '        "scripts/stabilize_mlb_deploy_source.py": "PROVIDER_NEUTRAL = True\\n",\n'
INSERTION = (
    MARKER
    + '        "scripts/verify_mlb_deploy_identity.py": "PROVIDER_NEUTRAL = True\\n",\n'
)


def patch(text: str) -> str:
    if INSERTION in text:
        return text
    if MARKER not in text:
        raise RuntimeError("no-BBD fixture insertion marker missing")
    return text.replace(MARKER, INSERTION, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    before = TARGET.read_text(encoding="utf-8")
    after = patch(before)
    if args.check:
        if before != after:
            print("pending_no_bbd_fixture_provider_identity")
            return 1
        print("no-BBD fixture includes provider-neutral deploy identity")
        return 0
    TARGET.write_text(after, encoding="utf-8")
    print("migrated:tests/unit/test_verify_mlb_no_bbd_runtime.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
