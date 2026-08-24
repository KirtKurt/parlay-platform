#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


PATH = Path("scripts/verify_mlb_deploy_identity.py")


def main() -> int:
    source = PATH.read_text(encoding="utf-8")
    original = source

    constant = 'ISOLATED_THREE_SOURCE_HANDLER = "orchestrator.lambda_handler"\n'
    expanded = (
        constant
        + 'ISOLATED_THREE_SOURCE_HANDLERS = (\n'
        + '    ISOLATED_THREE_SOURCE_HANDLER,\n'
        + '    "orchestrator_v2.lambda_handler",\n'
        + ')\n'
    )
    if "ISOLATED_THREE_SOURCE_HANDLERS" not in source:
        if source.count(constant) != 1:
            raise SystemExit("isolated handler constant marker missing or ambiguous")
        source = source.replace(constant, expanded, 1)

    old = "        and handler == ISOLATED_THREE_SOURCE_HANDLER\n"
    new = "        and handler in ISOLATED_THREE_SOURCE_HANDLERS\n"
    if new not in source:
        if source.count(old) != 1:
            raise SystemExit("isolated handler comparison marker missing or ambiguous")
        source = source.replace(old, new, 1)

    if source != original:
        PATH.write_text(source, encoding="utf-8")
        print("Authorized strict isolated MLB AUTO v2 handler")
    else:
        print("Strict isolated MLB AUTO v2 handler already authorized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
