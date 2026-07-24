"""Checksum-verified loader for test_mlb_historical_policy_v1.

The reviewed implementation is stored as deterministic gzip/base64 text so the
exact source is content-addressed while remaining deployable in GitHub and AWS.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE_PARTS = [
    "test_mlb_historical_policy_v1_impl.py.gz.b64.part000",
    "test_mlb_historical_policy_v1_impl.py.gz.b64.part001",
]
_EXPECTED_RESOURCE_SHA256 = "c17e9621adc716d9327ea73aa29c44739f3a8b45946a01628fd479f7d2878668"
_EXPECTED_SOURCE_SHA256 = "98aa78eb1c1a8c0eb4ba5d7c3ea1517e99373e1f44e058eaa1ef1d7104713bc4"

_encoded = b"".join(Path(__file__).with_name(name).read_bytes() for name in _RESOURCE_PARTS)
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("TEST_MLB_HISTORICAL_POLICY_V1_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("TEST_MLB_HISTORICAL_POLICY_V1_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(Path(__file__).with_name(_RESOURCE_PARTS[0])) + "::<verified-source>", "exec"), globals(), globals())
