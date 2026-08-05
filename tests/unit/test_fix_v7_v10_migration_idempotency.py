from pathlib import Path

import fix_v7_v10_migration_idempotency as fixer


def test_bootstrap_marks_feature_aware_v8_entrypoint_as_already_migrated(
    tmp_path, monkeypatch
):
    path = tmp_path / "migrate_v7_v10_stall_fixes.py"
    path.write_text(
        fixer.NEW_HELPER
        + "\n"
        + fixer.NEW_TEST_PATCH
        + "\n"
        + fixer.OLD_V8_ENTRYPOINT_PATCH
        + "        'legacy', 'replacement'\n    )\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fixer, "PATH", path)

    assert fixer.main() == 0
    first = path.read_text(encoding="utf-8")
    assert fixer.NEW_V8_ENTRYPOINT_PATCH in first
    assert "replayFromStartApplied" in first

    assert fixer.main() == 0
    second = path.read_text(encoding="utf-8")
    assert second == first


def test_bootstrap_accepts_current_semantic_test_patch(tmp_path, monkeypatch):
    path = tmp_path / "migrate_v7_v10_stall_fixes.py"
    semantic_test_patch = "\n".join(fixer.CURRENT_TEST_PATCH_MARKERS)
    path.write_text(
        fixer.NEW_HELPER
        + "\n"
        + semantic_test_patch
        + "\n"
        + fixer.NEW_V8_ENTRYPOINT_PATCH
        + "        'legacy', 'replacement'\n    )\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fixer, "PATH", path)

    assert fixer.main() == 0
    first = path.read_text(encoding="utf-8")
    assert semantic_test_patch in first

    assert fixer.main() == 0
    assert path.read_text(encoding="utf-8") == first


def test_replace_or_verify_rejects_unknown_migration_shape():
    try:
        fixer._replace_or_verify("different", "old", "new", "shape")
    except RuntimeError as exc:
        assert "migration idempotency marker missing:shape" in str(exc)
    else:
        raise AssertionError("unknown migration source must fail closed")
