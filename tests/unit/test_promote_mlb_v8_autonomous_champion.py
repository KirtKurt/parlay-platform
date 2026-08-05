import copy
import hashlib
import json

from hello_world import mlb_v8_autonomy_v1 as autonomy
import promote_mlb_v8_autonomous_champion as promotion


class FakeTable:
    def __init__(self, item=None):
        self.item = copy.deepcopy(item)

    def get_item(self, **_kwargs):
        return {"Item": copy.deepcopy(self.item)} if self.item else {}

    def put_item(self, *, Item, ConditionExpression=None, ExpressionAttributeValues=None, **_kwargs):
        if ConditionExpression == "attribute_not_exists(PK)" and self.item is not None:
            raise AssertionError("conditional create failed")
        if ConditionExpression == "#revision = :expected":
            expected = int((ExpressionAttributeValues or {})[":expected"])
            current = int((self.item or {}).get("revision") or 0)
            assert current == expected
        self.item = copy.deepcopy(Item)
        return {}

    def delete_item(self, *, ExpressionAttributeValues=None, **_kwargs):
        expected = int((ExpressionAttributeValues or {})[":expected"])
        assert int((self.item or {}).get("revision") or 0) == expected
        self.item = None
        return {}


class FakeS3:
    def __init__(self, *, bad_head=False):
        self.objects = {}
        self.bad_head = bad_head

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = {
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


def _training(*, baseline=False):
    model = {
        "featureGroup": "market_baseline" if baseline else "market_temporal_team",
        "trainingSteps": 0 if baseline else 700,
        "weights": [] if baseline else [0.1, -0.2],
        "intercept": 0.0,
        "standardizer": {
            "featureNames": [] if baseline else ["a", "b"],
            "means": [] if baseline else [0.0, 0.0],
            "scales": [] if baseline else [1.0, 1.0],
        },
    }
    model["modelDigest"] = hashlib.sha256(
        json.dumps(
            model,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    value = {
        "ok": True,
        "model": model,
        "selection": {"selectedFeatureGroup": model["featureGroup"]},
        "selectionObjective": {},
        "metrics": {},
        "partitions": {},
        "learningExecution": {
            "learningExecuted": True,
            "learnedCandidateSelected": not baseline,
            "marketBaselineRetainedByGuard": baseline,
        },
        "promotionGate": {"passed": True},
        "freshProspectiveAuditRequired": False,
        "productionPromotionEligible": True,
        "automaticWagerAllowed": False,
    }
    value["resultDigest"] = autonomy._sha(value)
    return value


def test_gate_passing_learned_model_promotes_atomically():
    table = FakeTable()
    s3 = FakeS3()

    result = promotion.promote(
        _training(), bucket="bucket", table=table, s3=s3
    )

    assert result["ok"] is True
    assert result["promoted"] is True
    assert result["rolledBack"] is False
    assert table.item["record_type"] == promotion.RECORD_TYPE
    assert table.item["data"]["automaticPromotion"] is True
    assert table.item["data"]["automaticWagerAllowed"] is False
    assert result["productionAuthorityChanged"] is False


def test_market_baseline_is_never_promoted():
    table = FakeTable()
    result = promotion.promote(
        _training(baseline=True), bucket="bucket", table=table, s3=FakeS3()
    )

    assert result["ok"] is False
    assert result["promoted"] is False
    assert "market_baseline_cannot_be_promoted" in result["validation"]["errors"]
    assert table.item is None


def test_failed_readback_rolls_pointer_back_to_previous_revision():
    previous = {
        "PK": promotion.POINTER_PK,
        "SK": promotion.POINTER_SK,
        "record_type": promotion.RECORD_TYPE,
        "revision": 3,
        "data": {"sha256": "previous"},
    }
    table = FakeTable(previous)

    result = promotion.promote(
        _training(),
        bucket="bucket",
        table=table,
        s3=FakeS3(bad_head=True),
    )

    assert result["ok"] is False
    assert result["rolledBack"] is True
    assert table.item == previous
