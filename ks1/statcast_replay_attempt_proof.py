"""Freeze every retained Statcast S3 read attempt for deterministic replay.

This module is development-only provenance plumbing. It never calls a provider or
writes AWS state. Successful reads are bound to the exact S3 VersionId and body
SHA; true missing reads are retained explicitly; unknown or non-replayable reads
fail closed rather than falling through to current objects.
"""
from __future__ import annotations

import hashlib
import threading

from botocore.exceptions import ClientError

_MISSING_CODES = {"NoSuchKey", "NoSuchVersion", "404", "NotFound"}


def _error_code(exc):
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    return str(error.get("Code")) if isinstance(error, dict) and error.get("Code") else None


class _RecordingBody:
    def __init__(self, body, complete, failed):
        self._body = body
        self._complete = complete
        self._failed = failed
        self._hasher = hashlib.sha256()
        self._done = False

    def read(self, amt=None):
        try:
            data = self._body.read() if amt is None else self._body.read(amt)
        except Exception as exc:
            if not self._done:
                self._failed(exc)
                self._done = True
            raise
        self._hasher.update(data)
        if not self._done and (amt is None or amt == -1 or not data):
            self._complete(self._hasher.hexdigest())
            self._done = True
        return data

    def __getattr__(self, name):
        return getattr(self._body, name)


class RecordingS3:
    """Read-through S3 adapter retaining deterministic get_object attempt proof."""

    def __init__(self, s3):
        self._s3 = s3
        self._attempts = []
        self._lock = threading.Lock()

    def _record(self, value):
        with self._lock:
            self._attempts.append(dict(value))

    def get_object(self, **kwargs):
        bucket = str(kwargs.get("Bucket") or "")
        key = str(kwargs.get("Key") or "")
        requested = kwargs.get("VersionId")
        base = {"bucket": bucket, "key": key}
        if requested not in (None, "", "null"):
            base["requestedVersion"] = str(requested)
        try:
            response = self._s3.get_object(**kwargs)
        except Exception as exc:
            code = _error_code(exc)
            if code in _MISSING_CODES or isinstance(exc, KeyError):
                self._record({**base, "state": "absent"})
            else:
                item = {**base, "state": "error", "errorType": type(exc).__name__}
                if code:
                    item["errorCode"] = code
                self._record(item)
            raise

        response = dict(response)
        version = response.get("VersionId")

        def complete(sha256):
            item = {**base, "state": "read", "versionId": version, "sha256": sha256}
            self._record(item)

        def failed(exc):
            item = {**base, "state": "error", "errorType": type(exc).__name__}
            code = _error_code(exc)
            if code:
                item["errorCode"] = code
            self._record(item)

        response["Body"] = _RecordingBody(response["Body"], complete, failed)
        return response

    def frozen_attempts(self):
        """Return one deterministic claim per bucket/key, failing on drift."""
        with self._lock:
            values = list(self._attempts)
        frozen = {}
        for candidate in values:
            if not isinstance(candidate, dict):
                raise ValueError("statcast replay attempt invalid")
            bucket = str(candidate.get("bucket") or "")
            key = str(candidate.get("key") or "")
            state = candidate.get("state")
            if not bucket or not key or state not in {"read", "absent", "error"}:
                raise ValueError("statcast replay attempt invalid")
            item = {"bucket": bucket, "key": key, "state": state}
            requested = candidate.get("requestedVersion")
            if requested not in (None, "", "null"):
                item["requestedVersion"] = str(requested)
            if state == "read":
                version = candidate.get("versionId")
                sha256 = candidate.get("sha256")
                if version in (None, "", "null") or not isinstance(sha256, str) or len(sha256) != 64:
                    raise ValueError("statcast replay successful read is unbound")
                if item.get("requestedVersion") and item["requestedVersion"] != str(version):
                    raise ValueError("statcast replay requested version mismatch")
                item.update(versionId=str(version), sha256=sha256)
            elif state == "error":
                item["errorType"] = str(candidate.get("errorType") or "UnknownError")
                if candidate.get("errorCode"):
                    item["errorCode"] = str(candidate["errorCode"])
            location = (bucket, key)
            previous = frozen.get(location)
            if previous is not None and previous != item:
                raise ValueError("statcast replay attempt changed during proof creation")
            frozen[location] = item
        return [frozen[key] for key in sorted(frozen)]

    def __getattr__(self, name):
        return getattr(self._s3, name)


class ProofBoundS3:
    """Replay only the exact read/absence inventory frozen by RecordingS3."""

    def __init__(self, s3, attempts):
        self._s3 = s3
        self._attempts = {}
        for candidate in attempts or []:
            if not isinstance(candidate, dict):
                raise ValueError("statcast replay attempt proof invalid")
            bucket = str(candidate.get("bucket") or "")
            key = str(candidate.get("key") or "")
            state = candidate.get("state")
            if not bucket or not key or state not in {"read", "absent", "error"}:
                raise ValueError("statcast replay attempt proof invalid")
            location = (bucket, key)
            normalized = dict(candidate)
            prior = self._attempts.get(location)
            if prior is not None and prior != normalized:
                raise ValueError("statcast replay attempt proof ambiguous")
            if state == "read":
                if (candidate.get("versionId") in (None, "", "null")
                        or not isinstance(candidate.get("sha256"), str)
                        or len(candidate["sha256"]) != 64):
                    raise ValueError("statcast replay read proof unbound")
            self._attempts[location] = normalized

    def get_object(self, **kwargs):
        bucket = str(kwargs.get("Bucket") or "")
        key = str(kwargs.get("Key") or "")
        location = (bucket, key)
        attempt = self._attempts.get(location)
        if attempt is None:
            raise ValueError("statcast replay key was never attempted in input proof")
        requested = kwargs.get("VersionId")
        frozen_requested = attempt.get("requestedVersion")
        if requested not in (None, "", "null"):
            if frozen_requested and str(requested) != str(frozen_requested):
                raise ValueError("statcast replay requested version differs from input proof")
            if attempt["state"] == "read" and str(requested) != str(attempt["versionId"]):
                raise ValueError("statcast replay requested version differs from frozen read")
        elif frozen_requested:
            raise ValueError("statcast replay omitted historically explicit version")

        if attempt["state"] == "absent":
            code = "NoSuchVersion" if frozen_requested else "NoSuchKey"
            raise ClientError({"Error": {"Code": code, "Message": "frozen Statcast replay absence"}}, "GetObject")
        if attempt["state"] == "error":
            raise ValueError("statcast replay input proof contains non-replayable read error")

        request = dict(kwargs)
        request["VersionId"] = attempt["versionId"]
        response = dict(self._s3.get_object(**request))
        if str(response.get("VersionId") or "") != str(attempt["versionId"]):
            raise ValueError("statcast replay returned wrong frozen version")
        metadata = dict(response.get("Metadata") or {})
        metadata["sha256"] = attempt["sha256"]
        response["Metadata"] = metadata
        return response

    def __getattr__(self, name):
        return getattr(self._s3, name)
