"""Checksum-verified loader for test_mlb_historical_optimizer_handler.

The reviewed implementation is stored as deterministic gzip/base64 text so the
exact source is content-addressed while remaining deployable in GitHub and AWS.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE_PARTS = [
    "test_mlb_historical_optimizer_handler_impl.py.gz.b64.part000",
    "test_mlb_historical_optimizer_handler_impl.py.gz.b64.part001",
]
_EXPECTED_RESOURCE_SHA256 = "b5d157d4ea2e09a562365804004bb5ce756ed0044088a4de64b5e006c3298528"
_EXPECTED_SOURCE_SHA256 = "903b77effd233d2167aa3514df96c24eb2b6960dd9b7fe25756602ea2a384adc"

_encoded = b"".join(
    Path(__file__).with_name(name).read_bytes().strip()
    for name in _RESOURCE_PARTS
) + b"\n"
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("TEST_MLB_HISTORICAL_OPTIMIZER_HANDLER_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("TEST_MLB_HISTORICAL_OPTIMIZER_HANDLER_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(Path(__file__).with_name(_RESOURCE_PARTS[0])) + "::<verified-source>", "exec"), globals(), globals())
