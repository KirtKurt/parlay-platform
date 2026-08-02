#!/usr/bin/env python3
"""Fail closed if an active MLB deployment path can read or call BBD/BBS.

Legacy source files may remain temporarily for historical artifact compatibility, but
no active SAM template, GitHub workflow, scheduled collector, or provider-neutral
context entrypoint may require a BBD credential or endpoint.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]

# Build tokens in pieces so this verifier does not flag its own source file.
FORBIDDEN = (
    "BBS" + "_API_KEY",
    "BBS" + "_API_SECRET_ARN",
    "BBS" + "_SHADOW_CAPTURE_ENABLED",
    "Bbs" + "ApiKey",
    "Bbs" + "ApiSecret",
    "api." + "bigballsdata.com",
    "verify_" + "bbs_api_live_contract.py",
    "verify_mlb_" + "bbs_sam_wiring.py",
    "mlb/providers/" + "bbs/",
)

ACTIVE_FILES = (
    Path("template.yaml"),
    Path(".github/workflows/deploy.yml"),
    Path(".github/workflows/mlb-v8-historical-context-backfill.yml"),
    Path("scripts/run_mlb_v8_historical_context_backfill_entrypoint.py"),
)


def _read(path: Path) -> str:
    resolved = ROOT / path
    if not resolved.is_file():
        raise RuntimeError(f"required active file is missing: {path}")
    return resolved.read_text(encoding="utf-8")


def verify_files(paths: Iterable[Path] = ACTIVE_FILES) -> list[str]:
    errors: list[str] = []
    for path in paths:
        text = _read(path)
        for token in FORBIDDEN:
            if token in text:
                errors.append(f"active_bbd_reference:{path}:{token}")

    workflows = ROOT / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        if "schedule:" not in text and "workflow_dispatch:" not in text:
            continue
        lower_name = path.name.lower()
        if "bbd" in lower_name or "bbs" in lower_name:
            errors.append(f"active_bbd_workflow_name:{path.relative_to(ROOT)}")
        for token in FORBIDDEN:
            if token in text:
                errors.append(
                    f"active_bbd_workflow_reference:{path.relative_to(ROOT)}:{token}"
                )

    context = _read(
        Path("scripts/run_mlb_v8_historical_context_backfill_entrypoint.py")
    )
    required = (
        "OfficialContextClient",
        '"official_mlb"',
        '"bbsApiUsed": False',
        '"bbsCredentialRead": False',
        '"productionAuthorityChanged": False',
    )
    for token in required:
        if token not in context:
            errors.append(f"official_context_contract_missing:{token}")
    return sorted(set(errors))


def main() -> int:
    errors = verify_files()
    if errors:
        for error in errors:
            print(error)
        return 1
    print("MLB active runtime is provider-neutral and contains no BBD/BBS dependency.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
