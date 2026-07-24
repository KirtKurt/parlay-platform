"""Checksum-verified loader for test_mlb_historical_daily_optimizer_v1.

The reviewed implementation is stored as deterministic gzip/base64 text so the
exact source is content-addressed while remaining deployable in GitHub and AWS.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE_PARTS = [
    "test_mlb_historical_daily_optimizer_v1_impl.py.gz.b64.part000",
    "test_mlb_historical_daily_optimizer_v1_impl.py.gz.b64.part001",
]
_EXPECTED_RESOURCE_SHA256 = "4c4e037c5d2aef3789a297cf6d9a14a02ff2a89dbdc2e2589727a5b78c9f51a0"
_EXPECTED_SOURCE_SHA256 = "ac8a3a1a3acc687ac916389e156abba6f9675f07a1b590b01023d80727bb2910"

_encoded = b"".join(Path(__file__).with_name(name).read_bytes() for name in _RESOURCE_PARTS)
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("TEST_MLB_HISTORICAL_DAILY_OPTIMIZER_V1_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("TEST_MLB_HISTORICAL_DAILY_OPTIMIZER_V1_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(Path(__file__).with_name(_RESOURCE_PARTS[0])) + "::<verified-source>", "exec"), globals(), globals())
