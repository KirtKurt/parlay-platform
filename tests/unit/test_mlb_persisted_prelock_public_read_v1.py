from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import inqsi_pull_history as history
import mlb_persisted_prelock_public_read_v1 as subject
import mlb_prediction_probability_contract_v1 as probability
from mlb_slate_coverage_patch import AUTHORITY_VERSION


SLATE = "2026-08-04"
COMMENCE = "2026-08-04T20:00:00+00:00"
CREATED = "2026-08-04T18:00:00+00:00"
PERSISTED = "2026-08-04T18:05:00+00:00"
NOW = datetime(2026, 8, 4, 18, 30, tzinfo=timezone.utc)


class Table:
    def __init__(self, live_item, snapshot):
        self.responses = [
            {"Items": [copy.deepcopy(live_item)]},
            {"Items": [copy.deepcopy(snapshot)]},
        ]
        self.calls = []
        self.write_count = 0

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0) if self.responses else {"Items": []}

    def put_item(self, **_kwargs):
        self.write_count += 1
        raise AssertionError("public read must not write")


def _row():
    source_slot = {
        "version": history.PULL_SLOT_VERSION,
        "canonicalPullFingerprint": "canonical-slot-fingerprint",
    }
    base = {
        "slate_date": SLATE,
        "slateDateEt": SLATE,
        "gameId": "provider:evt-123",
        "gameIdentity": "evt-123",
        "eventId": "evt-123",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "commenceTime": COMMENCE,
        "predictionSourcePullAt": CREATED,
        "predictionSourcePullId": "pull-123",
        "predictionSourceCanonicalSlot": source_slot,
        "homeSignal": {
            "modelWinProbability": 0.61,
            "marketProbability": 0.58,
            "americanOdds": -125,
            "averageAmericanOdds": -125,
            "priceBook": "book-a",
            "priceSource": "real_book",
            "marketSide": "home",
            "score": 63.0,
        },
        "awaySignal": {
            "modelWinProbability": 0.39,
            "marketProbability": 0.42,
            "americanOdds": 110,
            "averageAmericanOdds": 110,
            "priceBook": "book-b",
            "priceSource": "real_book",
            "marketSide": "away",
            "score": 37.0,
        },
    }
    row = probability.normalize_row(base)
    row.update(
        {
            "lockedPrediction": False,
            "officialPrediction": False,
            "displayPrediction": True,
            "officialPredictionStatus": subject.DISPLAY_STATUS,
            "displayGroup": "pre_lock_prediction",
            "recommendationStatus": "PRE_LOCK_PREDICTION",
            "createdAt": CREATED,
            "perGameCanonicalLock": {
                "authorityVersion": AUTHORITY_VERSION,
                "status": "OPEN_PRE_LOCK",
                "canonical": False,
            },
            "tags": sorted(set(row.get("tags") or []) | {"PRE_LOCK_PREDICTION"}),
        }
    )
    assert probability.validation_errors(row) == []
    return row


def _items():
    row = _row()
    ddb_row = history.ddb_safe(row)
    pk = f"GAME_WINNERS#mlb#{SLATE}"
    live_sk = "GAME#2026-08-04T20:00:00Z#evt-123"
    fingerprint = history.canonical_payload_fingerprint(ddb_row)
    live = {
        "PK": pk,
        "SK": live_sk,
        "record_type": subject.LIVE_RECORD_TYPE,
        "data": copy.deepcopy(ddb_row),
    }
    snapshot = {
        "PK": pk,
        "SK": "PREGAME#GAME#evt-123#PERSISTED#2026-08-04T18:05:00Z#CREATED#2026-08-04T18:00:00Z#digest",
        "record_type": subject.SNAPSHOT_RECORD_TYPE,
        "snapshot_version": subject.SNAPSHOT_VERSION,
        "snapshot_role": subject.SNAPSHOT_ROLE,
        "slate_date": SLATE,
        "game_identity": "evt-123",
        "prediction_created_at_utc": CREATED,
        "prediction_persisted_at_utc": PERSISTED,
        "prediction_persistence_proof_type": subject.PERSISTENCE_PROOF_TYPE,
        "prediction_persistence_write_pk": pk,
        "prediction_persistence_write_sk": live_sk,
        "prediction_payload_fingerprint": fingerprint,
        "prediction_payload_fingerprint_version": subject.PAYLOAD_FINGERPRINT_VERSION,
        "public_authority_version": AUTHORITY_VERSION,
        "display_status": subject.DISPLAY_STATUS,
        "display_surface": subject.DISPLAY_SURFACE,
        "user_visible": True,
        "display_prediction": True,
        "immutable_pregame": True,
        "write_once": True,
        "data": copy.deepcopy(ddb_row),
    }
    return live, snapshot


def _engine(table):
    return SimpleNamespace(history=SimpleNamespace(PULLS=table))


def _placeholder(**extra):
    value = {
        "gameId": "provider:evt-123",
        "gameIdentity": "evt-123",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "commenceTime": COMMENCE,
        "lockedPrediction": False,
        "canonical": False,
        "predictedWinner": None,
        "lockStatus": "OPEN_PRE_LOCK",
    }
    value.update(extra)
    return value


def test_reads_exact_decimal_backed_snapshot_and_replaces_only_placeholder():
    live, snapshot = _items()
    table = Table(live, snapshot)
    result = subject.merge_into_payload(
        _engine(table),
        SLATE,
        {"gameCount": 1, "predictions": [_placeholder()]},
        now=NOW,
    )

    assert len(table.calls) == 2
    assert table.calls[0]["ConsistentRead"] is True
    assert table.calls[1]["ScanIndexForward"] is False
    assert table.write_count == 0
    assert result["count"] == 1
    assert result["allGamesPredicted"] is True
    assert result["allGamesHaveDisplayedWinnerPrediction"] is True
    row = result["predictions"][0]
    assert row["predictedWinner"] == "Home Club"
    assert row["persistedPrelockPublicRead"] is True
    assert row["persistedPrelockPublicReadVersion"] == subject.VERSION
    assert row["lockedPrediction"] is False
    assert row["officialPrediction"] is False
    proof = result["persistedPrelockPublicRead"]
    assert proof["coverageComplete"] is True
    assert proof["validatedPredictionCount"] == 1
    assert proof["recomputed"] is False
    assert proof["productionAuthorityChanged"] is False


def test_raw_provider_prefix_identity_is_normalized_for_snapshot_lookup():
    assert subject._snapshot_identity({"gameId": "provider:evt-123"}) == "evt-123"
    assert subject._snapshot_identity({"gameIdentity": "evt-123"}) == "evt-123"


def test_mutated_live_row_is_rejected_against_write_once_snapshot():
    live, snapshot = _items()
    live["data"]["modelWinProbability"] = history.ddb_safe(0.75)
    result = subject.merge_into_payload(
        _engine(Table(live, snapshot)),
        SLATE,
        {"gameCount": 1, "predictions": [_placeholder()]},
        now=NOW,
    )

    assert result["count"] == 0
    assert result["predictions"][0]["predictedWinner"] is None
    invalid = result["persistedPrelockPublicRead"]["invalidRows"]
    assert "mutable_live_row_changed_after_snapshot" in next(iter(invalid.values()))


def test_snapshot_is_not_publicly_reused_at_or_after_t_minus_45():
    live, snapshot = _items()
    result = subject.merge_into_payload(
        _engine(Table(live, snapshot)),
        SLATE,
        {"gameCount": 1, "predictions": [_placeholder()]},
        now=datetime(2026, 8, 4, 19, 15, tzinfo=timezone.utc),
    )

    assert result["count"] == 0
    invalid = result["persistedPrelockPublicRead"]["invalidRows"]
    assert "prelock_public_window_closed" in next(iter(invalid.values()))


def test_existing_locked_canonical_row_is_never_overwritten():
    live, snapshot = _items()
    locked = _placeholder(
        lockedPrediction=True,
        canonical=True,
        predictedWinner="Away Club",
        officialPrediction=True,
    )
    result = subject.merge_into_payload(
        _engine(Table(live, snapshot)),
        SLATE,
        {"gameCount": 1, "predictions": [locked]},
        now=NOW,
    )

    assert result["predictions"][0]["predictedWinner"] == "Away Club"
    assert result["predictions"][0]["lockedPrediction"] is True
    assert result["persistedPrelockPublicRead"]["placeholderReplacementCount"] == 0


def test_public_api_invokes_exact_persisted_reader_before_lifecycle_counts():
    source = Path("hello_world/mlb_v3_read_api.py").read_text(encoding="utf-8")
    merge = source.index("persisted_prelock_read.merge_into_payload")
    reconcile = source.index("lifecycle_counts.reconcile_payload")

    assert merge < reconcile
    assert "predictionCoverageComplete" in source
    assert "persistedPrelockPublicReadVersion" in source
    assert "store=False" in source
