#!/usr/bin/env python3
"""Persist the AWS-supported R7 trainer memory ceiling in the SAM template."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "template.yaml"
RESOURCE = "  MLBMLTrainingFunction:\n"
NEXT_RESOURCE = "\n  SoccerSchedulerFunction:\n"
OLD = "      MemorySize: 2048\n"
NEW = "      MemorySize: 3008\n"


def patch(source: str) -> str:
    start = source.find(RESOURCE)
    end = source.find(NEXT_RESOURCE, start)
    if start < 0 or end < 0:
        raise RuntimeError("MLBMLTrainingFunction resource boundaries drifted")
    before = source[:start]
    block = source[start:end]
    after = source[end:]
    if block.count(NEW) == 1 and block.count(OLD) == 0:
        return source
    if block.count(OLD) != 1 or block.count(NEW) != 0:
        raise RuntimeError(
            "R7 trainer memory declaration drifted: "
            f"old={block.count(OLD)}, new={block.count(NEW)}"
        )
    return before + block.replace(OLD, NEW, 1) + after


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    patched = patch(source)
    TARGET.write_text(patched, encoding="utf-8")
    print(f"patched={patched != source}; target={TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
