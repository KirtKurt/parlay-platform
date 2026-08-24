#!/usr/bin/env python3
"""Apply the MLB production repair and modernize verifier test clients."""

from __future__ import annotations

from pathlib import Path

from repair_mlb_production_chain_20260824 import repair


ROOT = Path(__file__).resolve().parents[1]
CLIENT_CLASS = '''\n\nclass _LambdaClient:\n    def __init__(self, functions: list[dict]) -> None:\n        self._functions = functions\n\n    def list_functions(self, **_kwargs) -> dict:\n        return {"Functions": self._functions}\n'''


def _patch_test(path: str, import_marker: str, alias: str) -> bool:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    changed = False

    if "class _LambdaClient:" not in source:
        if import_marker not in source:
            raise RuntimeError(f"missing import marker in {path}")
        source = source.replace(import_marker, import_marker + CLIENT_CLASS, 1)
        changed = True

    old_call = f"{alias}._root_authority_lambda_functions([function])"
    new_call = f"{alias}._root_authority_lambda_functions(_LambdaClient([function]))"
    if old_call in source:
        source = source.replace(old_call, new_call)
        changed = True
    elif new_call not in source:
        raise RuntimeError(f"missing root-authority assertion marker in {path}")

    if changed:
        target.write_text(source, encoding="utf-8")
        print(f"{path}: Lambda client fixture corrected")
    else:
        print(f"{path}: Lambda client fixture already current")
    return changed


def main() -> int:
    repair()
    _patch_test(
        "tests/unit/test_mlb_deploy_identity_isolated_v3.py",
        "from scripts import verify_mlb_deploy_identity as verifier\n",
        "verifier",
    )
    _patch_test(
        "tests/unit/test_mlb_isolated_authority_boundary.py",
        "from scripts import verify_mlb_deploy_identity as deploy_identity\n",
        "deploy_identity",
    )
    print("MLB production repair and verifier fixtures are ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
