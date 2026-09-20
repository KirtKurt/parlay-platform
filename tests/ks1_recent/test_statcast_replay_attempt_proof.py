import hashlib
import io
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from ks1.inventory import Reader, encode
from ks1.statcast_history import load_training_statcast
from ks1.statcast_replay_attempt_proof import ProofBoundS3, RecordingS3
from tests.ks1.test_statcast_history import RetainedS3, fixture


class VersionedS3:
    def __init__(self):
        self.objects = {}
        self.current = {}

    def put(self, bucket, key, version, payload, *, current=True):
        self.objects[(bucket, key, version)] = encode(payload)
        if current:
            self.current[(bucket, key)] = version

    def get_object(self, Bucket, Key, VersionId=None):
        version = VersionId or self.current.get((Bucket, Key))
        body = self.objects.get((Bucket, Key, version)) if version else None
        if body is None:
            code = "NoSuchVersion" if VersionId else "NoSuchKey"
            raise ClientError({"Error": {"Code": code, "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(body), "VersionId": version,
                "Metadata": {"sha256": hashlib.sha256(body).hexdigest()}}


class NoSuchKey(ClientError):
    pass


class ModeledMissingS3(VersionedS3):
    class exceptions:
        @staticmethod
        def from_code(code):
            return NoSuchKey if code == "NoSuchKey" else ClientError

    def get_object(self, Bucket, Key, VersionId=None):
        response = {"Error": {"Code": "NoSuchKey", "Message": "missing"}}
        raise NoSuchKey(response, "GetObject")


def test_successful_rejected_read_is_frozen_without_becoming_admitted_receipt():
    bundle, payload, key = fixture()
    payload["rows"].pop()
    recorder = RecordingS3(RetainedS3({key: (payload, "v1", None)}))
    load_training_statcast(bundle, recorder, "b")
    attempts = recorder.frozen_attempts()
    attempt = next(item for item in attempts if item["key"] == key)
    assert attempt["state"] == "read"
    assert attempt["versionId"] == "v1"
    assert len(attempt["sha256"]) == 64
    assert bundle["source_receipts"] == []


def test_successful_read_replays_exact_version_after_current_object_advances():
    s3 = VersionedS3()
    s3.put("b", "k", "v1", {"value": 1})
    recorder = RecordingS3(s3)
    assert Reader(recorder, "b").read("k") == {"value": 1}
    attempts = recorder.frozen_attempts()
    s3.put("b", "k", "v2", {"value": 2})
    reader = Reader(ProofBoundS3(s3, attempts), "b")
    assert reader.read("k") == {"value": 1}
    assert reader.receipts[-1]["versionId"] == "v1"


def test_historical_absence_stays_absent_even_if_key_is_later_created():
    s3 = VersionedS3()
    recorder = RecordingS3(s3)
    with pytest.raises(ClientError):
        Reader(recorder, "b").read("missing")
    attempts = recorder.frozen_attempts()
    assert attempts == [{"bucket": "b", "key": "missing", "state": "absent",
                         "errorType": "ClientError", "errorCode": "NoSuchKey"}]
    s3.put("b", "missing", "v1", {"now": "present"})
    with pytest.raises(ClientError):
        Reader(ProofBoundS3(s3, attempts), "b").read("missing")


def test_modeled_missing_read_replays_the_exact_exception_class():
    s3 = ModeledMissingS3()
    recorder = RecordingS3(s3)
    with pytest.raises(NoSuchKey):
        Reader(recorder, "b").read("missing")
    attempts = recorder.frozen_attempts()
    assert attempts == [{"bucket": "b", "key": "missing", "state": "absent",
                         "errorType": "NoSuchKey", "errorCode": "NoSuchKey"}]
    with pytest.raises(NoSuchKey):
        Reader(ProofBoundS3(s3, attempts), "b").read("missing")


def test_never_attempted_key_fails_closed_instead_of_reading_current_s3():
    s3 = VersionedS3()
    s3.put("b", "other", "v1", {"value": 1})
    with pytest.raises(ValueError, match="never attempted"):
        Reader(ProofBoundS3(s3, []), "b").read("other")


def test_seven_day_workflow_uses_attempt_proof_wrappers():
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/ks1-retrain-recent.yml").read_text()
    assert "python -m ks1.retrain_recent_attempt_proof" in workflow
    assert "python -m ks1.forensic_derived_development_attempt_proof" in workflow
