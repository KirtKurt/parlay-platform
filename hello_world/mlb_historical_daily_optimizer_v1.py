"""Checksum-verified loader for mlb_historical_daily_optimizer_v1.

The reviewed implementation is stored as deterministic gzip/base64 text so the
exact source is content-addressed while remaining deployable in GitHub and AWS.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE_PARTS = [
    "mlb_historical_daily_optimizer_v1_impl.py.gz.b64.part000",
    "mlb_historical_daily_optimizer_v1_impl.py.gz.b64.part001",
    "mlb_historical_daily_optimizer_v1_impl.py.gz.b64.part002",
    "mlb_historical_daily_optimizer_v1_impl.py.gz.b64.part003",
    "mlb_historical_daily_optimizer_v1_impl.py.gz.b64.part004",
    "mlb_historical_daily_optimizer_v1_impl.py.gz.b64.part005",
]
_EXPECTED_RESOURCE_SHA256 = "ee54607886d7441ef7f5f9f27758a69d9dcb20485dacc4ff3e4ab2d0eec8256f"
_EXPECTED_SOURCE_SHA256 = "01a24fa2f8c03d88b8f8fbb261b270d0f09ddea9bc92c29c0454644cbcabe516"

# Git content APIs may preserve or append one trailing newline per text part.
# Base64 contains no meaningful whitespace, so normalize boundaries before the
# digest check while retaining the original single final newline.
_encoded = b"".join(
    Path(__file__).with_name(name).read_bytes().strip()
    for name in _RESOURCE_PARTS
) + b"\n"
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("MLB_HISTORICAL_DAILY_OPTIMIZER_V1_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("MLB_HISTORICAL_DAILY_OPTIMIZER_V1_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(Path(__file__).with_name(_RESOURCE_PARTS[0])) + "::<verified-source>", "exec"), globals(), globals())
