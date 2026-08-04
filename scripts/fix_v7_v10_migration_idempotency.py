#!/usr/bin/env python3
"""One-shot bootstrap for the V7-V10 migration idempotency guard."""
from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).with_name("migrate_v7_v10_stall_fixes.py")
OLD = '''def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
'''
NEW = '''def _replace_once(text: str, old: str, new: str, label: str) -> str:
    # Check the full replacement first because many migration anchors are a
    # strict substring of the replacement block itself.
    if new in text:
        return text
    if old in text:
        return text.replace(old, new, 1)
'''


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if NEW in text:
        print("V7-V10 migration idempotency guard already fixed")
        return 0
    if OLD not in text:
        raise RuntimeError("V7-V10 migration idempotency marker missing")
    PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("Fixed V7-V10 migration idempotency guard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
