from __future__ import annotations

import copy
import hashlib
import io

import numpy as np
import pytest

from scripts import mlb_challenger_benchmark as subject


def rows(first_date, count=24):
    return [{"gameId": f"g{first_date}_{i}", "slateDateEt": f"2026-08-{first_date+i//4:02}",
             "homeWon": int(i % 3 != 0), "marketHomeProbability": 0.45 + (i % 5) * .04,
             "deltaGapHome": (i % 3 - 1) * .02, "bookAgreementGapHome": .0,
             "reversalGapHome": None, "homeAwayVelocityPpHr60mDiff": i % 2,
             "starterCompositeGapHome": None, "bullpenCompositeGapHome": None,
             "lineupWrcPlusGapHome": None, "fundamentalPitchingMissing": 1.,
             "fundamentalOffenseLineupMissing": 1.}
            for i in range(count)]


def test_market_is_preserved_when_there_is_no_adjustment():
    data = rows(1)
    assert np.allclose(subject.predict(data, None), [r["marketHomeProbability"] for r in data])


def test_training_statistics_never_use_evaluation_values():
    train = rows(1)
    before = copy.deepcopy(train)
    model = subject.fit_adjustment(train, subject.MOVEMENT + subject.BASEBALL, 1.)
    assert "starterCompositeGapHome" in model["inactiveFeatures"]
    assert "reversalGapHome" in model["inactiveFeatures"]
    assert "bookAgreementGapHome" in model["inactiveFeatures"]
    evaluation = [{**train[0], "deltaGapHome": 100000}]
    snapshot = copy.deepcopy(model)
    assert np.isfinite(subject.predict(evaluation, model)).all()
    assert model == snapshot
    assert train == before


def test_calibration_and_selection_keep_whole_slates_disjoint():
    calibration, selection = subject.split_validation(rows(1))
    assert max(r["slateDateEt"] for r in calibration) < min(r["slateDateEt"] for r in selection)
    with pytest.raises(ValueError):
        subject.split_validation(rows(1, 4))


def test_every_probability_is_counted_once_in_calibration_bins():
    data = rows(1, 10)
    report = subject.metrics(data, np.arange(1, 11) / 10)
    assert sum(b["count"] for b in report["calibrationBins"]) == len(data)


def test_source_artifact_requires_exact_version_and_checksums():
    body = subject.canonical_bytes({"rows": []})
    digest = hashlib.sha256(body).hexdigest()
    class S3:
        def get_object(self, **kwargs):
            assert kwargs == {"Bucket": "b", "Key": "k", "VersionId": "v"}
            return {"Body": io.BytesIO(body), "Metadata": {"sha256": digest}}
    pointer = {"bucket": "b", "key": "k", "versionId": "v", "sha256": digest}
    assert subject.verified_json(S3(), pointer, "b") == {"rows": []}
    with pytest.raises(ValueError, match="checksum"):
        subject.verified_json(S3(), {**pointer, "sha256": "a" * 64}, "b")
    with pytest.raises(ValueError, match="pointer"):
        subject.verified_json(S3(), {**pointer, "versionId": ""}, "b")
    with pytest.raises(ValueError, match="pointer"):
        subject.verified_json(S3(), pointer, "other-bucket")


def test_test_labels_cannot_select_the_configuration(monkeypatch):
    partitions = {"train": rows(1), "validation": rows(8), "prospectiveTest": rows(16)}
    monkeypatch.setattr(subject, "partition_records", lambda _: partitions)
    frozen = {"outcomeModel": {"ok": True, "bias": 0, "features": [], "weights": {}}}
    def run():
        expected = subject.metrics(partitions["prospectiveTest"], np.full(24, .5))["brier"]
        return subject.benchmark({"experimentId": "r8"}, frozen,
                                 {"prospectiveTest": {"outcome": {"brierScore": expected}}})
    before = run()
    for row in partitions["prospectiveTest"]:
        row["homeWon"] = 1 - row["homeWon"]
    after = run()
    assert before["chosenConfiguration"] == after["chosenConfiguration"]
    assert before["challenger"] == after["challenger"]
    assert after["promotionEligible"] is False
    assert after["prospectiveQualificationEvidence"] is False
    assert after["productionAuthorityChanged"] is False


@pytest.mark.parametrize("defect", ["overlap", "duplicate", "invalid_probability"])
def test_partition_integrity_fails_closed(monkeypatch, defect):
    monkeypatch.setattr(subject.dual, "records_from_clean_rows", lambda value: value)
    partitions = {"train": rows(1), "validation": rows(8), "prospectiveTest": rows(16)}
    if defect == "overlap":
        partitions["validation"][0]["slateDateEt"] = "2026-08-01"
    elif defect == "duplicate":
        partitions["train"][1] = dict(partitions["train"][0])
    else:
        partitions["train"][0]["marketHomeProbability"] = None
    with pytest.raises(ValueError):
        subject.partition_records({"partitions": partitions})


def test_feature_audit_does_not_turn_missing_values_into_measurements():
    report = subject.audit_features(rows(1))
    assert report["starterCompositeGapHome"]["observed"] == 0
    assert report["starterCompositeGapHome"]["coverage"] == 0
    assert report["bookAgreementGapHome"]["observed"] == 24
    assert report["bookAgreementGapHome"]["distinct"] == 1
