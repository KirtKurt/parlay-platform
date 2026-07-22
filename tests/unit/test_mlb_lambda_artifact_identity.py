from __future__ import annotations

import io
import warnings
import zipfile
from pathlib import Path

import pytest

from scripts import create_mlb_lambda_build_manifest as build_manifest
from scripts import verify_mlb_deploy_identity as deploy_identity
from scripts.mlb_lambda_artifact_identity import (
    MANIFEST_SCHEMA_VERSION,
    directory_content_manifest,
    lambda_code_sha256,
    zip_content_manifest,
)


GIT_SHA = "a" * 40
TEMPLATE_SHA = "b" * 64


def _zip_files(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            entry = zipfile.ZipInfo(name)
            entry.date_time = (2026, 7, 21, 0, 0, 0)
            archive.writestr(entry, content)
    return buffer.getvalue()


def test_directory_and_zip_manifests_bind_paths_and_contents(tmp_path: Path) -> None:
    build = tmp_path / "build"
    (build / "package").mkdir(parents=True)
    (build / "handler.py").write_bytes(b"def lambda_handler(event, context):\n    return event\n")
    (build / "package" / "dependency.py").write_bytes(b"VALUE = 1\n")
    artifact = _zip_files(
        {
            "package/dependency.py": b"VALUE = 1\n",
            "handler.py": b"def lambda_handler(event, context):\n    return event\n",
        }
    )

    assert zip_content_manifest(artifact) == directory_content_manifest(build)
    assert lambda_code_sha256(artifact)


def test_zip_manifest_rejects_duplicate_and_unsafe_paths() -> None:
    duplicate = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("handler.py", b"one")
            archive.writestr("handler.py", b"two")
    with pytest.raises(ValueError, match="duplicate path"):
        zip_content_manifest(duplicate.getvalue())

    unsafe = _zip_files({"../handler.py": b"unsafe"})
    with pytest.raises(ValueError, match="unsafe path"):
        zip_content_manifest(unsafe)


def test_build_manifest_requires_every_verified_lambda_directory(
    tmp_path: Path,
) -> None:
    assert set(build_manifest.FUNCTION_LOGICAL_IDS) == set(
        deploy_identity.FUNCTIONS
    )
    for logical_id in build_manifest.FUNCTION_LOGICAL_IDS:
        directory = tmp_path / logical_id
        directory.mkdir()
        (directory / "runtime.py").write_text(logical_id, encoding="utf-8")

    manifest = build_manifest.create_manifest(
        build_root=tmp_path,
        expected_git_sha=GIT_SHA,
        expected_template_sha256=TEMPLATE_SHA,
    )

    assert manifest["schemaVersion"] == MANIFEST_SCHEMA_VERSION
    assert manifest["expectedGitSha"] == GIT_SHA
    assert manifest["expectedTemplateSha256"] == TEMPLATE_SHA
    assert set(manifest["functions"]) == set(build_manifest.FUNCTION_LOGICAL_IDS)


def test_build_manifest_fails_closed_when_a_lambda_build_is_missing(
    tmp_path: Path,
) -> None:
    for logical_id in build_manifest.FUNCTION_LOGICAL_IDS[:-1]:
        directory = tmp_path / logical_id
        directory.mkdir()
        (directory / "runtime.py").write_text(logical_id, encoding="utf-8")

    with pytest.raises(ValueError, match="build directory is missing"):
        build_manifest.create_manifest(
            build_root=tmp_path,
            expected_git_sha=GIT_SHA,
            expected_template_sha256=TEMPLATE_SHA,
        )
