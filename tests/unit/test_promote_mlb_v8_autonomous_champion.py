import copy
import hashlib
import json

from botocore.exceptions import ClientError

from hello_world import mlb_v8_autonomy_v1 as autonomy
from hello_world import mlb_v8_model_runtime as runtime
import promote_mlb_v8_autonomous_champion as promotion


class FakeTable:
    def __init__(self, items=None):
        self.items = copy.deepcopy(items or {})

    def get_item(self, *, Key, **_kwargs):
        value = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(value)} if value else {}

    def put_item(
        self,
        *,
        Item,
        ConditionExpression=None,
        ExpressionAttributeValues=None,
        **_kwargs,
    ):
        key = (Item["PK"], Item["SK"])
        current = self.items.get(key)
        if ConditionExpression == "attribute_not_exists(PK)":
            assert current is None
        if ConditionExpression == "#revision = :expected":
            expected = int((ExpressionAttributeValues or {})[":expected"])
            assert int((current or {}).get("revision") or 0) == expected
        self.items[key] = copy.deepcopy(Item)
        return {}

    def delete_item(
        self,
        *,
        Key,
        ExpressionAttributeValues=None,
        **_kwargs,
    ):
        key = (Key["PK"], Key["SK"])
        current = self.items.get(key)
        expected = int((ExpressionAttributeValues or {})[":expected"])
        assert int((current or {}).get("revision") or 0) == expected
        self.items.pop(key, None)
        return {}


class FakeS3:
    def __init__(self, *, bad_head=False):
        self.objects = {}
        self.bad_head = bad_head

    def put_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.objects:
            raise ClientError(
                {
                    "Error": {
                        "Code": "PreconditionFailed",
                        "Message": "already exists",
                    },
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.objects[key] = {
            "Body": kwargs["Body"],
            "Metadata": dict(kwargs.get("Metadata") or {}),
        }
        return {"VersionId": "v1"}

    def head_object(self, *, Bucket, Key):
        value = self.objects[(Bucket, Key)]
        metadata = dict(value["Metadata"])
        if self.bad_head:
            metadata["sha256"] = "tampered"
        return {"Metadata": metadata, "VersionId": "v1"}


def _model(*, baseline=False):
    value = {
        "featureCompilerVersion": "compiler-v8",
        "featureGroup": "market_baseline" if baseline else "market_temporal_team",
        "trainingSteps": 0 if baseline else 700,
        "weights": [] if baseline else [0.1, -0.2],
        "intercept": 0.0,
        "standardizer": {
            "featureNames": [] if baseline else ["a", "b"],
            "means": [] if baseline else [0.0, 0.0],
            "scales": [] if baseline else [1.0, 1.0],
        },
        "calibrator": {"identity": True},
    }
    value["modelDigest"] = hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    return value


def _training(*, baseline=False):
    model = _model(baseline=baseline)
    candidate_digest = "prospective-candidate-digest"
    value = {
        "ok": True,
        "createdAtUtc": "2026-08-05T05:00:00+00:00",
        "architecture": {"probabilityBounds": [0.05, 0.95]},
        "model": model,
        "selection": {"selectedFeatureGroup": model["featureGroup"]},
        "selectionObjective": {},
        "metrics": {},
        "partitions": {
            "train": {"lastDate": "2026-01-01"},
            "walkForward": {"lastDate": "2026-01-02"},
            "untouchedAudit": {"lastDate": "2026-01-03"},
        },
        "learningExecution": {
            "learningExecuted": True,
            "learnedCandidateSelected": not baseline,
            "marketBaselineRetainedByGuard": baseline,
        },
        "promotionGate": {"passed": not baseline},
        "freshProspectiveAuditRequired": True,
        "productionPromotionEligible": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
    }
    value["resultDigest"] = autonomy._sha(value)
    if baseline:
        value.update(
            {
                "freshProspectiveAuditRequired": False,
                "productionPromotionEligible": True,
                "prospectiveCandidateDigest": candidate_digest,
                "prospectiveAudit": {
                    "candidateDigest": candidate_digest,
                    "modelDigest": model["modelDigest"],
                    "auditDigest": "audit-digest",
                    "prospectiveEvidenceComplete": True,
                    "prospectiveAuditPassed": True,
                    "prospectiveAuditRejected": False,
                    "modelRefitDuringProspectiveAudit": False,
                    "selectionUsedProspectiveOutcomes": False,
                },
            }
        )
        value["resultDigest"] = autonomy._sha(
            {key: item for key, item in value.items() if key != "resultDigest"}
        )
        return value

    frozen = runtime.build_bundle(value)
    value.update(
        {
            "freshProspectiveAuditRequired": False,
            "productionPromotionEligible": True,
            "prospectiveCandidateDigest": candidate_digest,
            "prospectiveAuditDigest": "audit-digest",
            "prospectiveAudit": {
                "candidateDigest": candidate_digest,
                "modelDigest": frozen["modelDigest"],
                "sourceModelDigest": frozen.get("sourceModelDigest"),
                "auditDigest": "audit-digest",
                "prospectiveEvidenceComplete": True,
                "prospectiveAuditPassed": True,
                "prospectiveAuditRejected": False,
                "modelRefitDuringProspectiveAudit": False,
                "selectionUsedProspectiveOutcomes": False,
            },
            "frozenModelBundle": copy.deepcopy(frozen),
            "frozenModelBundleDigest": frozen["modelDigest"],
        }
    )
    value["resultDigest"] = autonomy._sha(
        {key: item for key, item in value.items() if key != "resultDigest"}
    )
    return value


def _prospective_pointer(training, *, status="PASSED", revision=2):
    bundle = training["frozenModelBundle"]
    return {
        "PK": promotion.PROSPECTIVE_PK,
        "SK": promotion.PROSPECTIVE_SK,
        "record_type": promotion.PROSPECTIVE_RECORD_TYPE,
        "revision": revision,
        "data": {
            "status": status,
            "candidateDigest": training["prospectiveCandidateDigest"],
            "modelDigest": bundle["modelDigest"],
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
        },
    }


def _key(pk, sk):
    return (pk, sk)


def test_gate_passing_learned_model_promotes_exact_frozen_bundle_atomically():
    training = _training()
    table = FakeTable(
        {
            _key(promotion.PROSPECTIVE_PK, promotion.PROSPECTIVE_SK): _prospective_pointer(
                training
            )
        }
    )
    s3 = FakeS3()

    result = promotion.promote(
        training, bucket="bucket", table=table, s3=s3
    )

    champion = table.items[_key(promotion.POINTER_PK, promotion.POINTER_SK)]
    prospective = table.items[
        _key(promotion.PROSPECTIVE_PK, promotion.PROSPECTIVE_SK)
    ]
    artifact = s3.objects[
        (result["artifact"]["bucket"], result["artifact"]["key"])
    ]
    stored = json.loads(artifact["Body"].decode("utf-8"))

    assert result["ok"] is True
    assert result["promoted"] is True
    assert result["alreadyActive"] is False
    assert result["rolledBack"] is False
    assert champion["record_type"] == promotion.RECORD_TYPE
    assert champion["data"]["automaticPromotion"] is True
    assert champion["data"]["automaticWagerAllowed"] is False
    assert champion["data"]["modelDigest"] == training[
        "frozenModelBundleDigest"
    ]
    assert stored["modelBundle"] == training["frozenModelBundle"]
    assert prospective["data"]["status"] == "PROMOTED"
    assert prospective["data"]["championArtifact"]["sha256"] == result[
        "artifact"
    ]["sha256"]
    assert result["productionAuthorityChanged"] is False


def test_market_baseline_is_never_promoted():
    table = FakeTable()
    result = promotion.promote(
        _training(baseline=True),
        bucket="bucket",
        table=table,
        s3=FakeS3(),
    )

    assert result["ok"] is False
    assert result["promoted"] is False
    assert "market_baseline_cannot_be_promoted" in result["validation"][
        "errors"
    ]
    assert not table.items


def test_failed_readback_rolls_both_pointers_back():
    training = _training()
    previous_champion = {
        "PK": promotion.POINTER_PK,
        "SK": promotion.POINTER_SK,
        "record_type": promotion.RECORD_TYPE,
        "revision": 3,
        "data": {"sha256": "previous", "modelDigest": "previous-model"},
    }
    previous_prospective = _prospective_pointer(training, revision=7)
    table = FakeTable(
        {
            _key(promotion.POINTER_PK, promotion.POINTER_SK): previous_champion,
            _key(
                promotion.PROSPECTIVE_PK, promotion.PROSPECTIVE_SK
            ): previous_prospective,
        }
    )

    result = promotion.promote(
        training,
        bucket="bucket",
        table=table,
        s3=FakeS3(bad_head=True),
    )

    assert result["ok"] is False
    assert result["rolledBack"] is True
    assert table.items[
        _key(promotion.POINTER_PK, promotion.POINTER_SK)
    ] == previous_champion
    assert table.items[
        _key(promotion.PROSPECTIVE_PK, promotion.PROSPECTIVE_SK)
    ] == previous_prospective


def test_repeated_same_champion_is_idempotent():
    training = _training()
    table = FakeTable(
        {
            _key(promotion.PROSPECTIVE_PK, promotion.PROSPECTIVE_SK): _prospective_pointer(
                training
            )
        }
    )
    s3 = FakeS3()

    first = promotion.promote(
        training, bucket="bucket", table=table, s3=s3
    )
    first_revision = table.items[
        _key(promotion.POINTER_PK, promotion.POINTER_SK)
    ]["revision"]
    second = promotion.promote(
        training, bucket="bucket", table=table, s3=s3
    )

    assert first["promoted"] is True
    assert second["ok"] is True
    assert second["promoted"] is True
    assert second["alreadyActive"] is True
    assert second["rolledBack"] is False
    assert table.items[
        _key(promotion.POINTER_PK, promotion.POINTER_SK)
    ]["revision"] == first_revision
    assert table.items[
        _key(promotion.PROSPECTIVE_PK, promotion.PROSPECTIVE_SK)
    ]["data"]["status"] == "PROMOTED"
