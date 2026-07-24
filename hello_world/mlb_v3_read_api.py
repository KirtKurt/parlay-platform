"""Checksum-verified loader for mlb_v3_read_api.

The reviewed implementation is stored as deterministic gzip/base64 text so the
exact source is content-addressed while remaining deployable in GitHub and AWS.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE = Path(__file__).with_name("mlb_v3_read_api_impl.py.gz.b64")
_EXPECTED_RESOURCE_SHA256 = "72a7e40d942ede0da42431aae6afd9f2ccfc819d5592dd31e9c04c94bcc09518"
_EXPECTED_SOURCE_SHA256 = "ed6856092a1c6e670f2eb5185a4df47a2c2856e670f54f4339e8badced9a9d9a"

_encoded = _RESOURCE.read_bytes()
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("MLB_V3_READ_API_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("MLB_V3_READ_API_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(_RESOURCE) + "::<verified-source>", "exec"), globals(), globals())
