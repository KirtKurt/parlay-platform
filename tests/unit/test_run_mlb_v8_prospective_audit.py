import copy
from types import SimpleNamespace

from hello_world import mlb_v8_autonomy_v1 as autonomy
import run_mlb_v8_prospective_audit as runner


class Body:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        assert key not in self.objects
        self.objects[key] = {
            "Body": kwargs["Body"],
            "Metadata": copy.deepcopy(kwargs.get("Metadata") or {}),
        }
        return {"VersionId": "v1"}

    def get_object(self, *, Bucket, Key):
        return {"Body": Body(self.objects[(Bucket, Key)]["Body"])}

    def head_object(self, *, Bucket, Key):
        return {
            "Metadata": copy.deepcopy(
                self.objects[(Bucket, Key)]["Metadata"]
            ),
            "VersionId": "v1",
        }


class FakeTable:
    def __init__(self):
        self.item = None

    def get_item(self, **_kwargs):
        return {"Item": copy.deepcopy(self.item)} if self.item else {}

    def put_item(
        self,
        *,
        Item,
        ConditionExpression=None,
        ExpressionAttributeValues=None,
        **_kwargs,
    ):
        if ConditionExpression == "attribute_not_exists(PK)":
            assert self.item is None
        if ConditionExpression == "#revision = :expected":
            expected = int((ExpressionAttributeValues or {})[":expected"])
            assert int((self.item or {}).get("revision") or 0) == expected
        self.item = copy.deepcopy(Item)
        return {}


def _training(*, eligible=True):
    group = "market_temporal_team" if eligible else "market_baseline"
    value = {
        "ok": True,
        "createdAtUtc": "2026-08-05T05:00:00+00:00",
        "architecture": {"probabilityBounds": [0.05, 0.95]},
        "selection": {"selectedFeatureGroup": group},
        "model": {
            "featureCompilerVersion": "compiler-v8",
            "featureGroup": group,
            "standardizer": {
                "featureNames": ["x"] if eligible else [],
                "means": [0.0] if eligible else [],
                "scales": [1.0] if eligible else [],
            },
            "weights": [2.5] if eligible else [],
            "intercept": 0.0,
            "trainingSteps": 700 if eligible else 0,
            "calibrator": {"identity": True},
            "modelDigest": "source-model-digest",
        },
        "learningExecution": {
            "learningExecuted": True,
            "learnedCandidateSelected": eligible,
            "marketBaselineRetainedByGuard": not eligible,
            "learnedCandidateCount": 10,
            "totalOptimizationSteps": 1000,
            "selectedFeatureGroup": group,
        },
        "promotionGate": {"passed": eligible, "errors": []},
        "partitions": {
            "train": {"lastDate": "2026-01-01"},
            "walkForward": {"lastDate": "2026-01-02"},
            "untouchedAudit": {"lastDate": "2026-01-03"},
        },
        "retrospectiveArchitectureEvaluation": True,
        "freshProspectiveAuditRequired": True,
        "productionPromotionEligible": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
    }
    value["resultDigest"] = autonomy._sha(value)
    return value


def _examples(count):
    return [
        SimpleNamespace(
            day=f"2026-02-{1 + index % 15:02d}",
            game_id=str(index),
            outcome=1,
            market_probability=0.60,
            features={"x": 1.0},
        )
        for index in range(count)
    ]


def test_ineligible_current_model_waits_without_writing_pointer():
    table = FakeTable()
    lifecycle, effective = runner.advance(
        training=_training(eligible=False),
        records=[],
        table=table,
        s3=FakeS3(),
        bucket="bucket",
        created_at="2026-08-05T05:00:00+00:00",
    )

    assert lifecycle["status"] == "WAITING_FOR_RETROSPECTIVE_GATE"
    assert lifecycle["action"] == "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH"
    assert table.item is None
    assert effective["productionPromotionEligible"] is False


def test_candidate_freezes_then_passes_on_later_settled_slates(monkeypatch):
    table = FakeTable()
    s3 = FakeS3()
    examples = _examples(20)
    monkeypatch.setattr(
        runner.prospective.features,
        "prepare_examples",
        lambda _records: examples,
    )

    first, first_effective = runner.advance(
        training=_training(),
        records=[{}],
        table=table,
        s3=s3,
        bucket="bucket",
        created_at="2026-08-05T05:00:00+00:00",
    )

    assert first["status"] == "COLLECTING"
    assert first["action"] == "COLLECT_AUTONOMOUS_PROSPECTIVE_AUDIT"
    assert first["candidateDigest"]
    assert table.item["revision"] == 1
    assert table.item["data"]["status"] == "COLLECTING"
    assert first_effective["productionPromotionEligible"] is False

    examples[:] = _examples(200)
    second, effective = runner.advance(
        training=_training(),
        records=[{}],
        table=table,
        s3=s3,
        bucket="bucket",
        created_at="2026-08-06T05:00:00+00:00",
    )

    assert second["status"] == "PASSED"
    assert second["prospectiveAuditPassed"] is True
    assert second["action"] == "AUTO_PROMOTE_GUARDED_CHAMPION"
    assert table.item["revision"] == 2
    assert table.item["data"]["status"] == "PASSED"
    assert effective["freshProspectiveAuditRequired"] is False
    assert effective["productionPromotionEligible"] is True
    assert effective["autonomyDecision"] == "AUTO_PROMOTE_GUARDED_CHAMPION"
    assert effective["automaticWagerAllowed"] is False


def test_passed_pointer_rehydrates_frozen_training_without_refit(monkeypatch):
    table = FakeTable()
    s3 = FakeS3()
    examples = _examples(20)
    monkeypatch.setattr(
        runner.prospective.features,
        "prepare_examples",
        lambda _records: examples,
    )
    runner.advance(
        training=_training(),
        records=[{}],
        table=table,
        s3=s3,
        bucket="bucket",
        created_at="2026-08-05T05:00:00+00:00",
    )
    examples[:] = _examples(200)
    runner.advance(
        training=_training(),
        records=[{}],
        table=table,
        s3=s3,
        bucket="bucket",
        created_at="2026-08-06T05:00:00+00:00",
    )

    lifecycle, effective = runner.advance(
        training=_training(eligible=False),
        records=[],
        table=table,
        s3=s3,
        bucket="bucket",
        created_at="2026-08-07T05:00:00+00:00",
    )

    assert lifecycle["status"] == "PASSED"
    assert lifecycle["action"] == "AUTO_PROMOTE_GUARDED_CHAMPION"
    assert effective["model"]["featureGroup"] == "market_temporal_team"
    assert effective["prospectiveAudit"]["modelRefitDuringProspectiveAudit"] is False
