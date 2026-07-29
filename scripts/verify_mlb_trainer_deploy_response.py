#!/usr/bin/env python3
"""Verify trainer deployment evidence against the installed shadow-only contract.

The core verifier is pinned byte-for-byte so this adapter changes only the
runtime-authority assertions that diverged from the deployed V8 status schema.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


_CORE_PATH = Path(__file__).with_name("_verify_mlb_trainer_deploy_response_core.py")
_CORE_GIT_BLOB_SHA1 = "6366c6c0fa36f66654e396c78e6655acfe7728bd"


def _git_blob_sha1(body: bytes) -> str:
    header = f"blob {len(body)}\0".encode("ascii")
    return hashlib.sha1(header + body).hexdigest()


def _load_core() -> Any:
    body = _CORE_PATH.read_bytes()
    actual = _git_blob_sha1(body)
    if actual != _CORE_GIT_BLOB_SHA1:
        raise RuntimeError(
            "MLB trainer deploy verifier core digest mismatch: "
            f"expected {_CORE_GIT_BLOB_SHA1}, found {actual}"
        )
    spec = importlib.util.spec_from_file_location(
        "_mlb_trainer_deploy_response_core",
        _CORE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("MLB trainer deploy verifier core could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_core = _load_core()
for _name in dir(_core):
    if not _name.startswith("__") and _name not in {"verify", "main"}:
        globals()[_name] = getattr(_core, _name)

_core_verify = _core.verify


def verify(
    *,
    training: Dict[str, Any],
    selection_capture: Dict[str, Any],
    status_after: Dict[str, Any],
    invocation_metadata: Iterable[Dict[str, Any]],
    run_started_at: str,
    expected_git_sha: str,
    expected_template_sha256: str,
) -> List[str]:
    errors = set(
        _core_verify(
            training=training,
            selection_capture=selection_capture,
            status_after=status_after,
            invocation_metadata=invocation_metadata,
            run_started_at=run_started_at,
            expected_git_sha=expected_git_sha,
            expected_template_sha256=expected_template_sha256,
        )
    )

    # The deployed status contract intentionally keeps V8 shadow-only. The old
    # verifier incorrectly demanded that runtime authority activation be
    # available, which made a safe deployment fail after all AWS checks passed.
    errors.discard("runtime_authority_activation_not_available")
    if status_after.get("manualReviewCreatesShadowApprovalOnly") is not True:
        errors.add("manual_review_shadow_only_contract_missing")
    if status_after.get("runtimeAuthorityActivationAvailable") is not False:
        errors.add("runtime_authority_activation_must_remain_unavailable")

    return sorted(errors)


def main() -> int:
    # The core CLI resolves ``verify`` from its own module globals.
    _core.verify = verify
    return int(_core.main())


if __name__ == "__main__":
    raise SystemExit(main())
