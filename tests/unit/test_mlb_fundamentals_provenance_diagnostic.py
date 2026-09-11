from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from hello_world import mlb_fundamentals_snapshot_v2 as snapshot_v2


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "mlb_fundamentals_provenance_diagnostic",
    SCRIPTS / "mlb_fundamentals_provenance_diagnostic.py",
)
assert SPEC and SPEC.loader
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)


def _row(*, lock_at: str, retrieved_at: str) -> dict:
    context = {
        context_name: {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "reason": "fixture source unavailable",
        }
        for _output, context_name, _fields in snapshot_v2.GROUP_SPECS
    }
    context["travel_rest"] = {
        "source_status": "CONNECTED",
        "home_rest_days": 2,
        "away_rest_days": 1,
        "sourceProvenance": {
            "provider": "fixture",
            "dataset": "rest",
            "retrievedAtUtc": retrieved_at,
            "sourceEffectiveAtUtc": "2026-07-22T11:58:00+00:00",
            "payloadFingerprint": "rest-fixture",
        },
    }
    row = {
        "gameId": "fixture-game",
        "homeTeam": "Home",
        "awayTeam": "Away",
        "commenceTime": "2026-07-22T17:05:00+00:00",
        "predictionSourcePullAt": "2026-07-22T12:00:00+00:00",
        "predictionSourcePullId": "fixture-pull",
        "lockedAtUtc": lock_at,
        "advanced_context": context,
    }
    row["fundamentalsSnapshotV2"] = snapshot_v2.build(
        row,
        captured_at_utc="2026-07-22T12:00:00+00:00",
    )
    snapshot_v2.enhance_row(row)
    return row


def test_valid_provenance_boundary_reports_safe_without_changing_contract() -> None:
    result = SUBJECT.diagnose_row(
        _row(
            lock_at="2026-07-22T16:20:00+00:00",
            retrieved_at="2026-07-22T11:59:30+00:00",
        ),
        persisted_at="2026-07-22T12:01:00+00:00",
    )

    assert result["contractSafe"] is True
    assert result["violations"] == []


def test_persistence_after_lock_is_reason_coded_but_not_relaxed() -> None:
    result = SUBJECT.diagnose_row(
        _row(
            lock_at="2026-07-22T12:00:30+00:00",
            retrieved_at="2026-07-22T11:59:30+00:00",
        ),
        persisted_at="2026-07-22T12:01:00+00:00",
    )

    assert result["contractSafe"] is False
    assert "prediction_persisted_after_lock" in result["violations"]


def test_source_retrieved_after_persistence_is_reason_coded_and_stays_blocked() -> None:
    result = SUBJECT.diagnose_row(
        _row(
            lock_at="2026-07-22T16:20:00+00:00",
            retrieved_at="2026-07-22T12:02:00+00:00",
        ),
        persisted_at="2026-07-22T12:01:00+00:00",
    )

    assert result["contractSafe"] is False
    assert (
        "group:travel_rest:retrieved_after_prediction_persistence"
        in result["violations"]
    )


# Acceptance regressions: a source snapshot is not proof that the current
# prediction was immutably persisted. These tests expose the current gap.
# No runtime, scoring, or chronology validator is replaced or relaxed.
import copy
from types import SimpleNamespace

import pytest


def _record_pair():
    post = SUBJECT.post
    data = _row(lock_at="2026-07-22T16:20:00+00:00", retrieved_at="2026-07-22T11:59:30+00:00")
    data = post.history_contract.ddb_safe(data)
    live = {
        "PK": "GAME_WINNERS#mlb#2026-07-22", "SK": "GAME#fixture-game",
        "record_type": post.base.PREDICTION_RECORD_TYPE, "data": data,
    }
    proof = {
        "PK": live["PK"], "SK": "PREGAME#GAME#fixture-game#fixture",
        "record_type": post.PREGAME_RECORD_TYPE,
        "snapshot_version": post.PREGAME_SNAPSHOT_VERSION,
        "prediction_persistence_proof_type": post.PERSISTENCE_PROOF_TYPE,
        "prediction_persistence_write_pk": live["PK"],
        "prediction_persistence_write_sk": live["SK"],
        "prediction_payload_fingerprint_version": post.PAYLOAD_FINGERPRINT_VERSION,
        "prediction_payload_fingerprint": post.history_contract.canonical_payload_fingerprint(data),
        "prediction_created_at_utc": "2026-07-22T12:00:45+00:00",
        "prediction_persisted_at_utc": "2026-07-22T12:01:00+00:00",
        "immutable_pregame": True, "write_once": True, "data": copy.deepcopy(data),
    }
    return live, proof


def _read_report(monkeypatch, live, proof):
    table = object()
    resource = SimpleNamespace(Table=lambda _name: table)
    monkeypatch.setattr(SUBJECT.post.base.boto3, "resource", lambda *_a, **_k: resource)
    calls = []
    def query(actual_table, partition, prefix):
        assert actual_table is table
        assert partition == "GAME_WINNERS#mlb#2026-07-22"
        calls.append(prefix)
        return copy.deepcopy([live] if prefix == "GAME#" else ([proof] if proof is not None else []))
    monkeypatch.setattr(SUBJECT.post.base, "_query_partition", query)
    report = SUBJECT.build_live_report(
        slate_date="2026-07-22", region="us-east-1", snapshots_table="offline-fixture",
    )
    assert calls == ["GAME#", "PREGAME#GAME#"]
    assert report["readOnly"] is True
    for key in ("mutatedPersistence", "productionAuthorityChanged", "modelPromotionAllowed",
                "automaticWagerAllowed", "immutablePredictionRewriteAllowed"):
        assert report[key] is False
    return report


def test_bound_valid_proof_retains_source_chronology_result_and_input_immutability(monkeypatch):
    live, proof = _record_pair()
    original = copy.deepcopy((live, proof))
    report = _read_report(monkeypatch, live, proof)
    assert report["contractSafeGameCount"] == 1
    assert report["contractBlockedGameCount"] == 0
    assert (live, proof) == original


@pytest.mark.parametrize("field,value", [
    ("write_once", False), ("immutable_pregame", False),
    ("snapshot_version", "untrusted"), ("prediction_persistence_proof_type", "untrusted"),
    ("prediction_payload_fingerprint_version", "untrusted"),
    ("prediction_payload_fingerprint", "tampered"),
    ("prediction_persistence_write_pk", "other-partition"),
    ("prediction_persistence_write_sk", "other-game"),
    ("prediction_created_at_utc", "2026-07-22T12:02:00+00:00"),
])
def test_invalid_persistence_metadata_cannot_be_counted_as_contract_safe(monkeypatch, field, value):
    live, proof = _record_pair()
    proof[field] = value
    report = _read_report(monkeypatch, live, proof)
    assert report["contractSafeGameCount"] == 0
    assert report["contractBlockedGameCount"] == 1


def test_other_prediction_payload_cannot_borrow_an_old_valid_source_snapshot(monkeypatch):
    live, proof = _record_pair()
    live["data"]["homeTeam"] = "Different current prediction"
    report = _read_report(monkeypatch, live, proof)
    assert report["contractSafeGameCount"] == 0
    assert report["contractBlockedGameCount"] == 1


def test_absent_proof_stays_blocked_without_manufacturing_timestamps(monkeypatch):
    live, _ = _record_pair()
    report = _read_report(monkeypatch, live, None)
    assert report["contractSafeGameCount"] == 0 and report["contractBlockedGameCount"] == 1
    game = report["games"][0]
    assert game["predictionPersistedAtUtc"] is None and game["lockAtUtc"] is None
    assert game["violations"] == ["write_once_pregame_persistence_proof_missing"]
