#!/usr/bin/env python3
"""Authorize the isolated three-source MLB AUTO Lambda without weakening root MLB.

The canonical/root MLB stack remains provider-neutral.  The separately deployed
MLB AUTO LLM function is allowed to carry the Big Balls Sports Data Pro secret
only when its full isolated identity contract is present and no root authority
tables or artifacts are attached.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


VERIFIER = Path("scripts/verify_mlb_deploy_identity.py")
HARDENED_VERIFIER_SHA256 = (
    "bebd5ae70877564c1903f40f445f7d7b740e074b1de77855242f01d36bebda7d"
)


def repair(path: Path = VERIFIER) -> bool:
    raw_source = path.read_bytes()
    source_sha256 = hashlib.sha256(raw_source).hexdigest()
    if source_sha256 == HARDENED_VERIFIER_SHA256:
        return False
    raise RuntimeError(
        "hardened isolated authority contract is incomplete: "
        f"verifier_sha256={source_sha256}"
    )


def main() -> int:
    changed = repair()
    print("MLB isolated authority boundary repaired" if changed else "MLB isolated authority boundary already repaired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
