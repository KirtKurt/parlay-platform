"""Read-only release identity for the primary ARB Lambda health response.

This identifies Python source bytes, not dependencies, IAM policy, or a signed
attestation. Missing/corrupt manifests are explicitly unverified. No network,
credentials, or environment-supplied revision is trusted by the runtime reader.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

MANIFEST_NAME = "arb_release_manifest.json"
SHA1 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}")
FIELDS = (
    "schema_version", "revision", "python_source_sha256", "python_source_files",
    "workflow_run_id", "workflow_run_attempt", "built_at",
)


def source_fingerprint(source: Path) -> tuple[str, int]:
    """Hash sorted, length-prefixed relative paths and bytes of Python sources."""
    digest = hashlib.sha256()
    entries = [p for p in source.rglob("*") if "__pycache__" not in p.parts]
    if any(p.is_symlink() for p in entries):
        raise ValueError("SOURCE_SYMLINK")
    files = sorted(p for p in entries if p.suffix == ".py" and p.is_file())
    if not files:
        raise ValueError("NO_PYTHON_SOURCES")
    for path in files:
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != source.parent):
            raise ValueError("SOURCE_SYMLINK")
        relative = path.relative_to(source).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest(), len(files)


def read_identity(source: Path) -> dict[str, Any]:
    manifest = source / MANIFEST_NAME
    if not manifest.exists():
        return {"status": "unverified", "reason": "RELEASE_MANIFEST_MISSING"}
    try:
        if manifest.is_symlink() or manifest.stat().st_size > 16384:
            raise ValueError("INVALID_MANIFEST_FILE")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("INVALID_SCHEMA")
        for key, pattern in (("revision", SHA1), ("python_source_sha256", SHA256),
                             ("workflow_run_id", RUN_ID), ("workflow_run_attempt", RUN_ID)):
            if not isinstance(data.get(key), str) or not pattern.fullmatch(data[key]):
                raise ValueError("INVALID_FIELD")
        if type(data.get("python_source_files")) is not int or data["python_source_files"] < 1:
            raise ValueError("INVALID_FILE_COUNT")
        built_at = datetime.fromisoformat(data["built_at"].replace("Z", "+00:00"))
        if built_at.tzinfo is None or built_at.utcoffset() is None:
            raise ValueError("NAIVE_BUILD_TIMESTAMP")
        actual_hash, actual_count = source_fingerprint(source)
        if (actual_hash, actual_count) != (data["python_source_sha256"], data["python_source_files"]):
            return {"status": "unverified", "reason": "RELEASE_SOURCE_MISMATCH"}
        # Do not reflect arbitrary JSON keys, paths, or credentials into health.
        return {"status": "verified", **{key: data[key] for key in FIELDS}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return {"status": "unverified", "reason": "RELEASE_MANIFEST_INVALID"}


@lru_cache(maxsize=1)
def runtime_identity() -> dict[str, Any]:
    # Lambda deployment files are immutable during an execution environment's life.
    return read_identity(Path(__file__).resolve().parent)


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    # Preserve every application response except additive health metadata.
    import app
    result = app.lambda_handler(event, context)
    request = event or {}
    if app._method(request) == "GET" and app._path(request) == "/v1/arb/health" and result.get("statusCode") == 200:
        body = json.loads(result["body"])
        body["release"] = runtime_identity()
        result = {**result, "body": json.dumps(body)}
    return result
