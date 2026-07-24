"""Checksum-verified loader for mlb_historical_policy_v1.

The reviewed implementation is stored as deterministic gzip/base64 text so the
exact source is content-addressed while remaining deployable in GitHub and AWS.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE_PARTS = [
    "mlb_historical_policy_v1_impl.py.gz.b64.part000",
    "mlb_historical_policy_v1_impl.py.gz.b64.part001",
    "mlb_historical_policy_v1_impl.py.gz.b64.part002",
    "mlb_historical_policy_v1_impl.py.gz.b64.part003",
    "mlb_historical_policy_v1_impl.py.gz.b64.part004",
]
_EXPECTED_RESOURCE_SHA256 = "c95052a45023c22c18754acb366cf17604835e1fc5a1efc444ea7cbc229fcd79"
_EXPECTED_SOURCE_SHA256 = "034f032eefa87dd287784b2fc407eed42fe230cd3ce7d274f9cfa2200d198db6"

_encoded = b"".join(Path(__file__).with_name(name).read_bytes() for name in _RESOURCE_PARTS)
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("MLB_HISTORICAL_POLICY_V1_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("MLB_HISTORICAL_POLICY_V1_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(Path(__file__).with_name(_RESOURCE_PARTS[0])) + "::<verified-source>", "exec"), globals(), globals())
