#!/usr/bin/env python3
"""Bind the MLB repair to the deployed wrapper and account memory ceiling."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts/run_mlb_r7_stable_feature_repair.py"

OLD_BEFORE = '''    pull_before = lambda_client.get_function_configuration(FunctionName=pull_fn)
    trainer_before = lambda_client.get_function_configuration(FunctionName=trainer_fn)
    _assert_active(pull_before, handler=PULL_HANDLER)
    _assert_active(trainer_before, handler=TRAINER_HANDLER)
    _write(evidence / "pull-before.json", pull_before)
    _write(evidence / "trainer-before.json", trainer_before)
'''
NEW_BEFORE = '''    pull_before = lambda_client.get_function_configuration(FunctionName=pull_fn)
    trainer_before = lambda_client.get_function_configuration(FunctionName=trainer_fn)
    pull_handler = str(pull_before.get("Handler") or "").strip()
    if "MLBAuditedPullFunction" not in pull_fn:
        raise RepairError("resolved_pull_function_not_audited_pull")
    if not pull_handler.endswith(".lambda_handler"):
        raise RepairError(f"deployed_pull_handler_invalid:{pull_handler}")
    _assert_active(pull_before, handler=pull_handler)
    _assert_active(trainer_before, handler=TRAINER_HANDLER)
    _write(evidence / "pull-before.json", pull_before)
    _write(evidence / "trainer-before.json", trainer_before)
'''
OLD_AFTER = '''    _assert_active(pull_after, handler=PULL_HANDLER)
    if pull_after.get("CodeSha256") != desired_digest:
'''
NEW_AFTER = '''    _assert_active(pull_after, handler=pull_handler)
    if pull_after.get("CodeSha256") != desired_digest:
'''


def patch(source: str) -> str:
    out = source
    if NEW_BEFORE not in out:
        if out.count(OLD_BEFORE) != 1:
            raise RuntimeError("pull-before handler anchor drifted")
        out = out.replace(OLD_BEFORE, NEW_BEFORE, 1)
    if NEW_AFTER not in out:
        if out.count(OLD_AFTER) != 1:
            raise RuntimeError("pull-after handler anchor drifted")
        out = out.replace(OLD_AFTER, NEW_AFTER, 1)
    # Account 735707987003 currently enforces the legacy 3008 MB Lambda ceiling.
    # Use the maximum accepted allocation rather than repeatedly requesting 4096.
    out = out.replace("4096", "3008")
    return out


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    patched = patch(source)
    TARGET.write_text(patched, encoding="utf-8")
    if "MemorySize=3008" not in patched or "trainer_memory_not_3008" not in patched:
        raise RuntimeError("trainer memory ceiling patch did not bind")
    print(f"patched={patched != source}; target={TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
