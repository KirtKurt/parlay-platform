"""Verified loader for the pure-Python CatBoost component of MLB V15.4."""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

_RESOURCE = Path(__file__).with_name("mlb_v15_4_catboost_model_impl.py.gz.b64")
_EXPECTED_RESOURCE_SHA256 = "661d3e0dabb7c9a89e190b1d8c932714e602ed1050b249f12f94cb79de515414"
_EXPECTED_SOURCE_SHA256 = "2c04d08a3a437bfa99ed42e16503ca825109a9d6f327ab7863b34884e5a52082"

_encoded = _RESOURCE.read_bytes()
if hashlib.sha256(_encoded).hexdigest() != _EXPECTED_RESOURCE_SHA256:
    raise RuntimeError("MLB_V15_4_CATBOOST_RESOURCE_CHECKSUM_MISMATCH")
_source = gzip.decompress(base64.b64decode(_encoded))
if hashlib.sha256(_source).hexdigest() != _EXPECTED_SOURCE_SHA256:
    raise RuntimeError("MLB_V15_4_CATBOOST_SOURCE_CHECKSUM_MISMATCH")
exec(compile(_source, str(_RESOURCE) + "::<verified-source>", "exec"), globals(), globals())
