"""Checksum-verified loader for mlb_ml_runtime_install_v3.

The reviewed implementation is stored as deterministic gzip/base64 text so the
exact source is content-addressed while remaining deployable in GitHub and AWS.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE = Path(__file__).with_name("mlb_ml_runtime_install_v3_impl.py.gz.b64")
_EXPECTED_RESOURCE_SHA256 = "ac5ba77c42f17eee11b4b91484a08e7c94aa01196853382616a34957900c40f5"
_EXPECTED_SOURCE_SHA256 = "967a6e5a3a0bb02bd39a7b2fc19ba75f8ab3bb443eb75d93ea7105e364d21d09"

_encoded = _RESOURCE.read_bytes()
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("MLB_ML_RUNTIME_INSTALL_V3_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("MLB_ML_RUNTIME_INSTALL_V3_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(_RESOURCE) + "::<verified-source>", "exec"), globals(), globals())
