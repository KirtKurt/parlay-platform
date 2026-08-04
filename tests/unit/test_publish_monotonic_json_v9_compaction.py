from __future__ import annotations

import json
from pathlib import Path

from scripts import publish_monotonic_json as publisher


def _diagnostic_view(count: int, *, source_run: str | None = None) -> dict:
    value = {
        "version": "MLB-V9-DIAGNOSTIC-SELECTED-PICK-BANDS-v1",
        "gameCount": count,
        "untruncatedRowCount": count,
        "selectedGameCount": count // 2,
        "correct": count // 4,
        "bandThresholds": {
            "MLB_STRONG": {"minimumSelectedProbability": 0.65},
            "MLB_LEAN": {"minimumSelectedProbability": 0.55},
            "PASS": {"maximumExclusive": 0.55},
        },
        "byBand": {
            "MLB_STRONG": {"gameCount": count // 3},
            "MLB_LEAN": {"gameCount": count // 3},
            "PASS": {"gameCount": count - 2 * (count // 3)},
        },
        "dailySelectedPickBands": [
            {
                "slateDateEt": "2026-08-03",
                "selectedGameCount": 8,
                "correct": 5,
                "accuracy": 0.625,
            }
        ],
        "games": [
            {
                "gameId": str(index),
                "slateDateEt": "2026-08-03",
                "selectedPickBand": "MLB_LEAN",
            }
            for index in range(count)
        ],
    }
    if source_run is not None:
        value["fullGameRowsSourceRunId"] = source_run
    return value


def _candidate(run_id: str = "400") -> dict:
    return {
        "ok": True,
        "proofType": "MLB_HISTORICAL_V7_V9_SHADOW_EVALUATION",
        "createdAtUtc": "2026-08-04T20:30:00+00:00",
        "runId": run_id,
        "productionAuthorityChanged": False,
        "diagnosticSelectedPickBands": {
            "allCanonicalGames": _diagnostic_view(250),
            "training": _diagnostic_view(180),
            "walkForward": _diagnostic_view(80),
            "untouchedHoldout": _diagnostic_view(70),
        },
        "canonicalCandidateHandoff": {
            "artifactType": "FROZEN_SHADOW_MODEL",
            "promotionAuthority": False,
        },
    }


def test_v9_repository_pointer_is_compacted_but_full_candidate_is_unchanged(
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "candidate.json"
    output_path = tmp_path / publisher.V9_LATEST_POINTER_NAME
    candidate = _candidate()
    candidate_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")

    result = publisher.publish(candidate_path, output_path, output_path)

    assert result["published"] is True
    assert result["compactedForLatestPointer"] is True
    durable = json.loads(output_path.read_text(encoding="utf-8"))
    full = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert len(full["diagnosticSelectedPickBands"]["allCanonicalGames"]["games"]) == 250
    assert len(durable["diagnosticSelectedPickBands"]["allCanonicalGames"]["games"]) == 100
    assert len(durable["diagnosticSelectedPickBands"]["walkForward"]["games"]) == 80
    view = durable["diagnosticSelectedPickBands"]["allCanonicalGames"]
    assert view["publishedGamesTruncated"] is True
    assert view["fullGameRowsAvailableInWorkflowArtifact"] is True
    assert view["fullGameRowsSourceRunId"] == "400"
    assert view["dailySelectedPickBands"] == candidate[
        "diagnosticSelectedPickBands"
    ]["allCanonicalGames"]["dailySelectedPickBands"]
    assert durable["canonicalCandidateHandoff"] == candidate["canonicalCandidateHandoff"]
    compaction = durable["latestPointerCompaction"]
    assert compaction["version"] == publisher.V9_POINTER_COMPACTION_VERSION
    assert compaction["untruncatedDiagnosticGameRowCount"] == 580
    assert compaction["publishedDiagnosticGameRowCount"] == 350
    assert compaction["aggregateMetricsPreserved"] is True
    assert compaction["dailySummariesPreserved"] is True
    assert compaction["productionAuthorityChanged"] is False
    assert compaction["fullEvidenceArtifactNames"] == ["mlb-historical-v7-v9-400"]


def test_recompaction_preserves_original_full_evidence_source_run() -> None:
    candidate = _candidate(run_id="401")
    for view in candidate["diagnosticSelectedPickBands"].values():
        view["games"] = view["games"][:10]
        view["fullGameRowsSourceRunId"] = "399"

    compacted = publisher.compact_latest_pointer(
        candidate,
        Path(publisher.V9_LATEST_POINTER_NAME),
    )

    assert compacted["latestPointerCompaction"]["fullEvidenceArtifactNames"] == [
        "mlb-historical-v7-v9-399"
    ]
    assert all(
        view["fullGameRowsSourceRunId"] == "399"
        for view in compacted["diagnosticSelectedPickBands"].values()
    )


def test_non_v9_pointer_is_not_compacted(tmp_path: Path) -> None:
    candidate_path = tmp_path / "candidate.json"
    output_path = tmp_path / "other_latest.json"
    candidate = _candidate()
    candidate_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")

    result = publisher.publish(candidate_path, output_path, output_path)

    assert result["published"] is True
    assert result["compactedForLatestPointer"] is False
    durable = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(durable["diagnosticSelectedPickBands"]["allCanonicalGames"]["games"]) == 250
    assert "latestPointerCompaction" not in durable
