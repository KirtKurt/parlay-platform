"""Verified loader for the V15.10 ranked MLB production selector.

The implementation is stored as a gzip/base64 resource so the exact reviewed
source can be content-addressed in GitHub without committing a binary model.

A separately versioned historical daily-slate champion is installed only after
V15.10's selection wrapper has completed.  The historical wrapper is fail-closed
and remains inert until its immutable 1,000-training-game plus validation/audit
promotion contract passes.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE = Path(__file__).with_name("mlb_ranked_primary_v15_10_impl.py.gz.b64")
_EXPECTED_RESOURCE_SHA256 = "49c42a09e4f003b9e99ddd95b7a5e3e00fbc38839d17f64a99ca405b141c9871"
_EXPECTED_SOURCE_SHA256 = "55883336a56d72ea3fb281584f3742b054e58c04d379b7d9dbd97607328fa126"

_encoded = _RESOURCE.read_bytes()
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("MLB_RANKED_V15_10_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("MLB_RANKED_V15_10_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(_RESOURCE) + "::<verified-source>", "exec"), globals(), globals())

# Capture the verified implementation before adding the dynamic, gated outer
# authority.  V15.10 remains the incumbent and rollback diagnostic until a
# historical champion passes every runtime validation invariant.
_VERIFIED_APPLY_SELECTION_AUTHORITY = apply_selection_authority


def apply_selection_authority(engine_module):
    result = _VERIFIED_APPLY_SELECTION_AUTHORITY(engine_module)
    # This guard must load even before first promotion. Swallowing an import or
    # install error could revive V15.10 after the write-once cutover was active.
    import mlb_historical_policy_v1

    mlb_historical_policy_v1.apply_runtime_authority(engine_module)
    return result
