"""Checksum-verified loader for mlb_historical_optimizer_handler.

The reviewed implementation is stored as deterministic gzip/base64 text so the
exact source is content-addressed while remaining deployable in GitHub and AWS.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE_PARTS = [
    "mlb_historical_optimizer_handler_impl.py.gz.b64.part000",
    "mlb_historical_optimizer_handler_impl.py.gz.b64.part001",
    "mlb_historical_optimizer_handler_impl.py.gz.b64.part002",
    "mlb_historical_optimizer_handler_impl.py.gz.b64.part003",
    "mlb_historical_optimizer_handler_impl.py.gz.b64.part004",
]
_EXPECTED_RESOURCE_SHA256 = "1569801b4c765ec3ede7afd6f1b72737a06870d3df09d4e3aceed96c089d90bd"
_EXPECTED_SOURCE_SHA256 = "b003aa39f049875a614ae27d7bffb689af484488253002040803d01fc1f419c2"

_encoded = b"".join(Path(__file__).with_name(name).read_bytes() for name in _RESOURCE_PARTS)
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("MLB_HISTORICAL_OPTIMIZER_HANDLER_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("MLB_HISTORICAL_OPTIMIZER_HANDLER_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(Path(__file__).with_name(_RESOURCE_PARTS[0])) + "::<verified-source>", "exec"), globals(), globals())
