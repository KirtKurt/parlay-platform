from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import mlb_v8_observational_audit_v1 as audit
import mlb_v8_model_runtime as runtime


class FakeModel:
    feature_group = "learned"

    def raw_probability(self, row):
        return 0.72 if row.features["x"] >= 0 else 0.28

    def to_dict(self):
        return {
            "featureGroup": "learned",
            "standardizer": {
                "featureNames": ["x"],
                "means": [0.0],
                "scales": [1.0],
            },
            "weights": [1.25],
            "intercept": 0.0,
            "l2": 0.2,
            "trainingSteps": audit.FINAL_FIT_STEPS,
            "seed": audit.SEED,
        }


class FakeCalibrator:
    def to_dict(self):
        return {"identity": True, "slope": 1.0, "intercept": 0.0}


class FakeFeatures:
    VERSION = "fixture-features-v1"

    def __init__(self, examples):
        self.examples = examples

    def prepare_examples(self, _records):
        return list(self.examples)


class FakeModelModule:
    features = SimpleNamespace(
        FEATURE_GROUPS={"market_baseline": (), "learned": ("x",)}
    )
    _INQSI_MLB_IDENTITY_CALIBRATOR_ONCE = False

    @staticmethod
    def chronological_partitions(_examples):
        return {
            "train": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "walkForward": ["2026-01-04"],
            "untouchedAudit": ["2026-01-05"],
        }

    @staticmethod
    def inner_expanding_folds(_train_days):
        return [
            (["2026-01-01"], ["2026-01-02"]),
            (["2026-01-01", "2026-01-02"], ["2026-01-03"]),
        ]

    @staticmethod
    def _subset(examples, days):
        return [row for row in examples if row.day in set(days)]

    @staticmethod
    def fit_residual_logistic(*_args, **_kwargs):
        return FakeModel()

    @staticmethod
    def fit_platt(_probabilities, _outcomes):
        return FakeCalibrator()

    @staticmethod
    def evaluate_probabilities(examples, probabilities):
        correct = sum(
            int((probability >= 0.5) == (row.outcome == 1))
            for row, probability in zip(examples, probabilities)
        )
        count = len(examples)
        return {
            "gameCount": count,
            "dayCount": len({row.day for row in examples}),
            "correct": correct,
            "overallAccuracy": correct / count if count else None,
            "meanDailyAccuracy": correct / count if count else None,
            "minimumDailyAccuracy": correct / count if count else None,
            "logLoss": 0.5 if count else None,
            "brierScore": 0.2 if count else None,
            "expectedCalibrationError": 0.03 if count else None,
        }

    @classmethod
    def _market_metrics(cls, examples):
        return cls.evaluate_probabilities(
            examples, [row.market_probability for row in examples]
        )

    @staticmethod
    def _sha(value):
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()


def _example(day, game_id, outcome, x, market=0.60):
    return SimpleNamespace(
        day=day,
        game_id=game_id,
        outcome=outcome,
        market_probability=market,
        features={"x": x},
        home_team="Home",
        away_team="Away",
    )


def _training():
    return {
        "ok": True,
        "createdAtUtc": "2026-01-06T00:00:00+00:00",
        "recordCountLoaded": 5,
        "architecture": {"probabilityBounds": [0.05, 0.95]},
        "learningExecution": {"learningExecuted": True},
        "selection": {
            "selectionGuard": {"version": "guard-v1"},
            "ablation": {
                "market_baseline": {},
                "learned": {
                    "l2": 0.2,
                    "guard": {
                        "eligible": False,
                        "errors": ["aggregate_accuracy_uplift_below_floor"],
                        "stability": {
                            "positiveFoldCount": 2,
                            "overallAccuracyUplift": 0.004,
                            "meanDailyAccuracyUplift": 0.005,
                        },
                    },
                    "oofMetrics": {
                        "overallAccuracy": 0.562,
                        "meanDailyAccuracy": 0.558,
                        "minimumDailyAccuracy": 0.20,
                    },
                    "oofMarketBaseline": {"overallAccuracy": 0.558},
                    "folds": [],
                },
            },
        },
        "model": {"featureCompilerVersion": "fixture-features-v1"},
        "partitions": {
            "train": {"lastDate": "2026-01-03"},
            "walkForward": {"lastDate": "2026-01-04"},
            "untouchedAudit": {"lastDate": "2026-01-05"},
        },
        "resultDigest": "training-result",
    }


def _training_examples():
    return [
        _example("2026-01-01", "1", 1, 1.0),
        _example("2026-01-02", "2", 0, -1.0),
        _example("2026-01-03", "3", 1, 1.0),
        _example("2026-01-04", "4", 0, -1.0),
        _example("2026-01-05", "5", 1, 1.0),
    ]


def test_best_ineligible_learned_candidate_is_frozen_for_observation_only():
    candidate = audit.build_candidate(
        _training(),
        [{}],
        model_module=FakeModelModule,
        feature_module=FakeFeatures(_training_examples()),
        runtime_module=runtime,
    )
    audit.verify_candidate(candidate)

    assert candidate["featureGroup"] == "learned"
    assert candidate["retrospectiveGuardEligible"] is False
    assert candidate["retrospectiveGuardErrors"] == [
        "aggregate_accuracy_uplift_below_floor"
    ]
    assert candidate["observationalOnly"] is True
    assert candidate["promotionEligible"] is False
    assert candidate["promotionRequested"] is False
    assert candidate["automaticWagerAllowed"] is False
    assert candidate["productionAuthorityChanged"] is False
    assert candidate["modelFitUsedProspectiveOutcomes"] is False
    assert candidate["frozenCorpusLastDate"] == "2026-01-05"
    assert candidate["modelBundle"]["authority"] == "SHADOW_ONLY"


def test_observational_grading_records_model_and_same_time_market_rows():
    candidate = audit.build_candidate(
        _training(),
        [{}],
        model_module=FakeModelModule,
        feature_module=FakeFeatures(_training_examples()),
        runtime_module=runtime,
    )
    future = [
        _example("2026-01-06", "6", 1, 1.0, 0.55),
        _example("2026-01-07", "7", 0, -1.0, 0.60),
        _example("2026-01-08", "8", 0, 1.0, 0.40),
    ]

    result = audit.evaluate_candidate(
        candidate,
        [{}],
        feature_module=FakeFeatures(future),
        model_module=FakeModelModule,
        runtime_module=runtime,
    )

    assert result["sampleSize"] == 3
    assert result["wins"] == 2
    assert result["losses"] == 1
    assert result["pushes"] == 0
    assert result["voids"] == 0
    assert result["overallAccuracy"] == 2 / 3
    assert result["marketWins"] == 2
    assert result["marketLosses"] == 1
    assert result["selectedPickSampleSize"] == 3
    assert result["selectedPickAccuracy"] == 2 / 3
    assert len(result["gradedRows"]) == 3
    assert all(row["candidateDigest"] == candidate["candidateDigest"] for row in result["gradedRows"])
    assert all(row["promotionEligible"] is False for row in result["gradedRows"])
    assert result["promotionEligible"] is False
    assert result["productionAuthorityChanged"] is False


def test_observational_candidate_tampering_fails_closed():
    candidate = audit.build_candidate(
        _training(),
        [{}],
        model_module=FakeModelModule,
        feature_module=FakeFeatures(_training_examples()),
        runtime_module=runtime,
    )
    candidate["promotionEligible"] = True

    try:
        audit.verify_candidate(candidate)
    except ValueError as exc:
        assert "authority" in str(exc)
    else:
        raise AssertionError("tampered observational authority must be rejected")


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
            "Metadata": copy.deepcopy(self.objects[(Bucket, Key)]["Metadata"]),
            "VersionId": "v1",
        }


class FakeTable:
    def __init__(self):
        self.item = None

    def get_item(self, **_kwargs):
        return {"Item": copy.deepcopy(self.item)} if self.item else {}

    def put_item(self, *, Item, ConditionExpression=None, ExpressionAttributeValues=None, **_kwargs):
        if ConditionExpression == "attribute_not_exists(PK)":
            assert self.item is None
        if ConditionExpression == "#revision = :expected":
            expected = int((ExpressionAttributeValues or {})[":expected"])
            assert int((self.item or {}).get("revision") or 0) == expected
        self.item = copy.deepcopy(Item)
        return {}


def test_advance_uses_separate_pointer_and_persists_immutable_grade_rows(monkeypatch):
    candidate = audit.build_candidate(
        _training(),
        [{}],
        model_module=FakeModelModule,
        feature_module=FakeFeatures(_training_examples()),
        runtime_module=runtime,
    )
    future = [
        _example("2026-01-06", "6", 1, 1.0),
        _example("2026-01-07", "7", 0, -1.0),
    ]
    evaluation = audit.evaluate_candidate(
        candidate,
        [{}],
        feature_module=FakeFeatures(future),
        model_module=FakeModelModule,
        runtime_module=runtime,
    )
    monkeypatch.setattr(audit, "build_candidate", lambda *_args, **_kwargs: copy.deepcopy(candidate))
    monkeypatch.setattr(audit, "evaluate_candidate", lambda *_args, **_kwargs: copy.deepcopy(evaluation))
    table = FakeTable()
    s3 = FakeS3()

    report = audit.advance(
        training=_training(),
        records=[{}],
        table=table,
        s3=s3,
        bucket="bucket",
        created_at="2026-01-08T00:00:00+00:00",
    )

    assert table.item["PK"] == audit.POINTER_PK
    assert table.item["PK"] != "MLB_V8_PROSPECTIVE_AUDIT#V1"
    assert report["sampleSize"] == 2
    assert report["gradeArtifactCount"] == 2
    assert report["promotionEligible"] is False
    assert report["promotionRequested"] is False
    assert report["automaticWagerAllowed"] is False
    assert report["productionAuthorityChanged"] is False
    grade_keys = [
        key for (_bucket, key) in s3.objects
        if key.startswith("mlb/v8/observational-grades/")
    ]
    assert len(grade_keys) == 2
