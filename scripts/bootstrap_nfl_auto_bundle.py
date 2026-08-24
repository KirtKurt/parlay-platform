#!/usr/bin/env python3
"""Materialize the audited NFL Auto source package for its first deployment."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil
import zlib

EXPECTED_SHA256 = 'f9054b2f0ce4660283e56c914dd4ee1887dd576853b342ce0b727eb84f03474f'
EXPECTED_PARTS = 8
MANIFEST = ['.github/workflows/deploy-nfl-auto.yml', 'docs/NFL_AUTO.md', 'nfl-auto-template.yaml', 'nfl_auto/README.md', 'nfl_auto/__init__.py', 'nfl_auto/config.py', 'nfl_auto/features.py', 'nfl_auto/handler.py', 'nfl_auto/llm_analyst.py', 'nfl_auto/model.py', 'nfl_auto/normalize.py', 'nfl_auto/provider_http.py', 'nfl_auto/providers.py', 'nfl_auto/requirements.txt', 'nfl_auto/storage.py', 'tests/nfl_auto/test_gates.py', 'tests/nfl_auto/test_model.py', 'tests/nfl_auto/test_normalize_features.py', 'tests/nfl_auto/test_providers.py', 'tests/nfl_auto/test_storage.py']
ALLOWED = (
    ".github/workflows/deploy-nfl-auto.yml",
    "docs/NFL_AUTO.md",
    "nfl-auto-template.yaml",
    "nfl_auto/",
    "tests/nfl_auto/",
)


def main() -> None:
    bundle_dir = Path("scripts/nfl_auto_bundle")
    part_paths = sorted(bundle_dir.glob("*.part"))
    if len(part_paths) != EXPECTED_PARTS:
        raise RuntimeError(f"Expected {EXPECTED_PARTS} bundle parts, found {len(part_paths)}")
    encoded = "".join(path.read_text(encoding="utf-8").strip() for path in part_paths)
    raw = zlib.decompress(base64.b64decode(encoded.encode("ascii")))
    actual = hashlib.sha256(raw).hexdigest()
    if actual != EXPECTED_SHA256:
        raise RuntimeError(f"NFL Auto bundle digest mismatch: {actual}")
    files = json.loads(raw.decode("utf-8"))
    if sorted(files) != sorted(MANIFEST):
        raise RuntimeError("NFL Auto bundle manifest does not match the audited file list")
    for relative, content in files.items():
        if not any(relative == prefix or relative.startswith(prefix) for prefix in ALLOWED):
            raise RuntimeError(f"Refusing unexpected bundle path: {relative}")
        path = Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    shutil.rmtree(bundle_dir)
    Path(__file__).unlink()
    print(f"materialized_nfl_auto_files={len(files)} digest={actual}")


if __name__ == "__main__":
    main()
