"""Pin retained Statcast replay reads to exact versions already frozen by input proof.

The historical development table is built from a checksum/version-bound source proof.
A later development-only enrichment must replay *that same source view*, even if a
retained daily Statcast object or recovery pointer advanced after the table was built.
This adapter is deliberately narrow: only retained Statcast daily/revision/recovery
namespaces are pinned. Other AWS reads keep their existing behavior and are independently
proof-bound by their callers.
"""
from __future__ import annotations

from ks1.inventory import RESEARCH

_PREFIXES = (
    RESEARCH + "sources/statcast-v2/",
    RESEARCH + "sources/statcast-v2-revisions/",
    RESEARCH + "sources/statcast-recovery-v1/",
)


class NoSuchKey(Exception):
    """Reproduce a source that was absent when the input proof was frozen."""


def _identity(receipt):
    if not isinstance(receipt, dict):
        return None
    bucket = str(receipt.get("bucket") or "")
    key = str(receipt.get("key") or "")
    version = str(receipt.get("versionId") or receipt.get("version_id") or "")
    sha256 = str(receipt.get("sha256") or "")
    if not bucket or not key or not version or len(sha256) != 64:
        return None
    return bucket, key, version, sha256


def _target(key):
    return isinstance(key, str) and key.startswith(_PREFIXES)


class ProofBoundStatcastReplayS3:
    """Read exact proof versions for replay; never discover newer retained objects."""

    def __init__(self, s3, proof):
        self._s3 = s3
        claims = {}
        for candidate in proof.get("source_receipts", []):
            identity = _identity(candidate)
            if identity is None:
                continue
            bucket, key, version, sha256 = identity
            if not _target(key):
                continue
            token = (bucket, key)
            claim = (version, sha256)
            previous = claims.get(token)
            if previous is not None and previous != claim:
                raise ValueError("proof_bound_statcast_replay_receipt_conflict")
            claims[token] = claim
        self._claims = claims

    def get_object(self, **kwargs):
        bucket = str(kwargs.get("Bucket") or "")
        key = str(kwargs.get("Key") or "")
        if not _target(key):
            return self._s3.get_object(**kwargs)
        claim = self._claims.get((bucket, key))
        if claim is None:
            # load_training_statcast records the exception class in its retained
            # report. Preserve the historical NoSuchKey state rather than seeing
            # an object that appeared after input-proof creation.
            raise NoSuchKey("retained Statcast object absent from input proof")
        version, _ = claim
        requested = kwargs.get("VersionId")
        if requested not in (None, "", version):
            raise ValueError("proof_bound_statcast_replay_version_unbound")
        exact = dict(kwargs)
        exact["VersionId"] = version
        return self._s3.get_object(**exact)

    def __getattr__(self, name):
        return getattr(self._s3, name)


def proof_bound_statcast_replay_s3(s3, proof):
    """Return the narrow exact-version replay adapter for a frozen input proof."""
    return ProofBoundStatcastReplayS3(s3, proof)
