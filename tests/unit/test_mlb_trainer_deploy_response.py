from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any


_CORE_PATH = Path(__file__).with_name("_test_mlb_trainer_deploy_response_core.py")
_CORE_GIT_BLOB_SHA1 = "3f0a60ec4a47fcc71c611a04e29d60905e72d873"


def _git_blob_sha1(body: bytes) -> str:
    header = f"blob {len(body)}\0".encode("ascii")
    return hashlib.sha1(header + body).hexdigest()


def _load_core() -> Any:
    body = _CORE_PATH.read_bytes()
    actual = _git_blob_sha1(body)
    if actual != _CORE_GIT_BLOB_SHA1:
        raise RuntimeError(
            "trainer deploy response test core digest mismatch: "
            f"expected {_CORE_GIT_BLOB_SHA1}, found {actual}"
        )
    spec = importlib.util.spec_from_file_location(
        "_test_mlb_trainer_deploy_response_core",
        _CORE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("trainer deploy response test core could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_core = _load_core()
_original_payloads = _core._payloads


def _payloads():
    training, selection, after = _original_payloads()
    after["manualReviewCreatesShadowApprovalOnly"] = True
    after["runtimeAuthorityActivationAvailable"] = False
    return training, selection, after


# Existing test functions resolve helpers from the core module globals.
_core._payloads = _payloads
for _name in dir(_core):
    if _name.startswith("test_"):
        globals()[_name] = getattr(_core, _name)


def test_rejects_runtime_authority_activation_exposure() -> None:
    training, selection, after = _payloads()
    after["runtimeAuthorityActivationAvailable"] = True

    assert "runtime_authority_activation_must_remain_unavailable" in _core._verify(
        training,
        selection,
        after,
    )


def test_rejects_missing_shadow_only_manual_review_attestation() -> None:
    training, selection, after = _payloads()
    after["manualReviewCreatesShadowApprovalOnly"] = False

    assert "manual_review_shadow_only_contract_missing" in _core._verify(
        training,
        selection,
        after,
    )
