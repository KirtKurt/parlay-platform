from __future__ import annotations

import json
from pathlib import Path

import run_mlb_v8_observational_audit as runner


def test_runner_uses_exact_training_report_and_cannot_change_authority(tmp_path, monkeypatch):
    training_path = tmp_path / "training.json"
    output_path = tmp_path / "observational.json"
    training_path.write_text(
        json.dumps(
            {
                "ok": True,
                "resultDigest": "training-digest",
                "learningExecution": {"learningExecuted": True},
            }
        )
    )
    table = object()
    s3 = object()
    monkeypatch.setattr(
        runner.prospective_runner,
        "_load_runtime_records",
        lambda **_kwargs: (
            [{"row": 1}],
            table,
            s3,
            "bucket",
            {"functionName": "historical-function"},
        ),
    )

    def advance(**kwargs):
        assert kwargs["table"] is table
        assert kwargs["s3"] is s3
        assert kwargs["bucket"] == "bucket"
        assert kwargs["training"]["resultDigest"] == "training-digest"
        return {
            "proofType": "MLB_V8_FROZEN_OBSERVATIONAL_AUDIT",
            "status": "OBSERVATIONAL_COLLECTING",
            "candidateDigest": "candidate",
            "modelDigest": "model",
            "sampleSize": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "voids": 0,
            "overallAccuracy": None,
            "selectedPickAccuracy": None,
            "calibrationEce": None,
            "confidenceBands": {},
            "promotionEligible": False,
            "promotionRequested": False,
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
        }

    monkeypatch.setattr(runner.observational, "advance", advance)

    result = runner.run(
        region="us-east-1",
        stack_name="historical-stack",
        table_name="snapshots",
        training_report=training_path,
        output=output_path,
        created_at="2026-08-10T20:00:00+00:00",
    )

    persisted = json.loads(output_path.read_text())
    assert result == persisted
    assert persisted["sourceTrainingReport"] == str(training_path)
    assert persisted["sourceTrainingResultDigest"] == "training-digest"
    assert persisted["recordCountLoaded"] == 1
    assert persisted["promotionEligible"] is False
    assert persisted["promotionRequested"] is False
    assert persisted["automaticWagerAllowed"] is False
    assert persisted["productionAuthorityChanged"] is False
    assert persisted["reportDigest"]


def test_runner_rejects_blank_or_unhealthy_training_pointer(tmp_path):
    training_path = tmp_path / "training.json"
    training_path.write_text("{}\n")

    try:
        runner.run(
            region="us-east-1",
            stack_name="historical-stack",
            table_name="snapshots",
            training_report=training_path,
            output=tmp_path / "out.json",
        )
    except RuntimeError as exc:
        assert "unavailable or unhealthy" in str(exc)
    else:
        raise AssertionError("blank latest-training pointer must fail closed")
