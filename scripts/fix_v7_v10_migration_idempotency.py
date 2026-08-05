#!/usr/bin/env python3
"""One-shot bootstrap for the V7-V10 migration idempotency guard."""
from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).with_name("migrate_v7_v10_stall_fixes.py")

OLD_HELPER = '''def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
'''
NEW_HELPER = '''def _replace_once(text: str, old: str, new: str, label: str) -> str:
    # Check the full replacement first because many migration anchors are a
    # strict substring of the replacement block itself.
    if new in text:
        return text
    if old in text:
        return text.replace(old, new, 1)
'''

OLD_TEST_PATCH = '''    text = text.replace(
        'module.cadence_state.decide_cadence = decide\\n    module.cadence_state.report_anchor_fields = anchors',
        'module.cadence_state.decide_cadence = decide\\n    module.cadence_state.report_anchor_fields = anchors\\n    module.cadence_v3.decide_cadence = decide\\n    module.cadence_v3.report_anchor_fields = anchors',
    )
'''
NEW_TEST_PATCH = '''    text = _replace_once(
        text,
        'module.cadence_state.decide_cadence = decide\\n    module.cadence_state.report_anchor_fields = anchors',
        'module.cadence_state.decide_cadence = decide\\n    module.cadence_state.report_anchor_fields = anchors\\n    module.cadence_v3.decide_cadence = decide\\n    module.cadence_v3.report_anchor_fields = anchors',
        "feature-aware test cadence v3 monkeypatch",
    )
'''

OLD_V8_ENTRYPOINT_PATCH = '''def patch_v8_entrypoint(text: str) -> str:
    text = text.replace(
'''
NEW_V8_ENTRYPOINT_PATCH = '''def patch_v8_entrypoint(text: str) -> str:
    # The feature-aware replay contract supersedes the old one-shot pointer
    # migration. Treat it as an already-migrated state instead of searching for
    # source anchors that were intentionally removed by the newer repair.
    if (
        'eligibilityPolicyVersion": eligibility.VERSION' in text
        and 'materializerVersion": eligibility.MATERIALIZER_VERSION' in text
        and 'replayFromStartApplied' in text
    ):
        return text
    text = text.replace(
'''


def _replace_or_verify(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if new in text:
        return text, False
    if old not in text:
        raise RuntimeError(f"V7-V10 migration idempotency marker missing:{label}")
    return text.replace(old, new, 1), True


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    changed = False
    text, helper_changed = _replace_or_verify(
        text, OLD_HELPER, NEW_HELPER, "replacement helper"
    )
    changed = changed or helper_changed
    text, test_patch_changed = _replace_or_verify(
        text, OLD_TEST_PATCH, NEW_TEST_PATCH, "feature-aware test monkeypatch"
    )
    changed = changed or test_patch_changed
    text, v8_patch_changed = _replace_or_verify(
        text,
        OLD_V8_ENTRYPOINT_PATCH,
        NEW_V8_ENTRYPOINT_PATCH,
        "feature-aware V8 entrypoint",
    )
    changed = changed or v8_patch_changed
    if changed:
        PATH.write_text(text, encoding="utf-8")
        print("Fixed V7-V10 migration idempotency guards")
    else:
        print("V7-V10 migration idempotency guards already fixed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
