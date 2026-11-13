import io

import pytest

from ks1.inventory import RESEARCH, Reader
from ks1.proof_bound_statcast_replay import (
    NoSuchKey,
    ProofBoundStatcastReplayS3,
    proof_bound_statcast_replay_s3,
)


class FakeS3:
    def __init__(self):
        self.calls = []

    def get_object(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "Body": io.BytesIO(b"{}"),
            "VersionId": kwargs.get("VersionId"),
        }

    def get_paginator(self, name):
        return ("paginator", name)


def receipt(key, version="v1", sha="a" * 64, bucket="b"):
    return {
        "bucket": bucket,
        "key": key,
        "versionId": version,
        "sha256": sha,
    }


def test_unversioned_daily_read_is_pinned_to_input_proof_version():
    key = RESEARCH + "sources/statcast-v2/2026-09-18.json"
    raw = FakeS3()
    wrapped = proof_bound_statcast_replay_s3(
        raw, {"source_receipts": [receipt(key, version="frozen-v7")]})
    reader = Reader(wrapped, "b")
    assert reader.read(key) == {}
    assert raw.calls == [{"Bucket": "b", "Key": key, "VersionId": "frozen-v7"}]
    assert reader.receipts[-1]["versionId"] == "frozen-v7"


def test_object_created_after_input_proof_stays_historically_absent():
    key = RESEARCH + "sources/statcast-v2/2026-09-19.json"
    wrapped = ProofBoundStatcastReplayS3(FakeS3(), {"source_receipts": []})
    with pytest.raises(NoSuchKey):
        wrapped.get_object(Bucket="b", Key=key)
    try:
        wrapped.get_object(Bucket="b", Key=key)
    except Exception as exc:
        assert type(exc).__name__ == "NoSuchKey"


def test_revision_and_recovery_reads_are_version_pinned():
    revision = RESEARCH + "sources/statcast-v2-revisions/2026-09-18/hash.json"
    recovery = RESEARCH + "sources/statcast-recovery-v1/2026-09-18/latest.json"
    raw = FakeS3()
    wrapped = ProofBoundStatcastReplayS3(raw, {"source_receipts": [
        receipt(revision, version="revision-v2"),
        receipt(recovery, version="recovery-v3"),
    ]})
    wrapped.get_object(Bucket="b", Key=revision)
    wrapped.get_object(Bucket="b", Key=recovery, VersionId="recovery-v3")
    assert raw.calls == [
        {"Bucket": "b", "Key": revision, "VersionId": "revision-v2"},
        {"Bucket": "b", "Key": recovery, "VersionId": "recovery-v3"},
    ]
    with pytest.raises(ValueError, match="version_unbound"):
        wrapped.get_object(Bucket="b", Key=recovery, VersionId="advanced-v4")


def test_conflicting_versions_for_same_replay_key_fail_closed():
    key = RESEARCH + "sources/statcast-v2/2026-09-18.json"
    with pytest.raises(ValueError, match="receipt_conflict"):
        ProofBoundStatcastReplayS3(FakeS3(), {"source_receipts": [
            receipt(key, version="v1", sha="a" * 64),
            receipt(key, version="v2", sha="b" * 64),
        ]})


def test_duplicate_identical_receipts_are_allowed():
    key = RESEARCH + "sources/statcast-v2/2026-09-18.json"
    frozen = receipt(key, version="v1")
    wrapped = ProofBoundStatcastReplayS3(
        FakeS3(), {"source_receipts": [dict(frozen), dict(frozen)]})
    wrapped.get_object(Bucket="b", Key=key)


def test_non_replay_sources_keep_existing_s3_behavior_and_paginators():
    key = RESEARCH + "prior-games.json"
    raw = FakeS3()
    wrapped = ProofBoundStatcastReplayS3(raw, {"source_receipts": []})
    wrapped.get_object(Bucket="b", Key=key, VersionId="caller-owned-version")
    assert raw.calls == [{
        "Bucket": "b", "Key": key, "VersionId": "caller-owned-version"}]
    assert wrapped.get_paginator("list_objects_v2") == ("paginator", "list_objects_v2")


def test_proof_receipts_outside_statcast_replay_namespace_do_not_grant_access():
    replay_key = RESEARCH + "sources/statcast-v2/2026-09-18.json"
    other = RESEARCH + "prior-games.json"
    wrapped = ProofBoundStatcastReplayS3(
        FakeS3(), {"source_receipts": [receipt(other, version="official-v1")]})
    with pytest.raises(NoSuchKey):
        wrapped.get_object(Bucket="b", Key=replay_key)
