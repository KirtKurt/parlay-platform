from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_daily_lock_ml_vector_preservation_patch as exact_contract
import mlb_ml_clean_cohort_v1 as cohort
import mlb_real_world_accuracy_patch as accuracy
import mlb_rolling_24h_audit as audit
from scripts.mlb_ml_feature_test_fixtures import attach_lock_safe_features


SLATE = "2026-07-18"


def _row(game_id: str = "game-1", winner: str = "Seattle Mariners"):
    source_at = "2026-07-18T22:20:00+00:00"
    lock_at = "2026-07-18T22:25:00+00:00"
    row = {
        "slate_date": SLATE,
        "slateDateEt": SLATE,
        "gameId": game_id,
        "gameIdentity": game_id,
        "commenceTime": "2026-07-18T23:10:00Z",
        "awayTeam": "San Francisco Giants",
        "homeTeam": "Seattle Mariners",
        "predictedWinner": winner,
        "predictedSide": "home" if winner == "Seattle Mariners" else "away",
        "createdAt": "2026-07-18T22:25:00Z",
        "lockedAtUtc": lock_at,
        "predictionSourcePullAt": source_at,
        "lockedPrediction": True,
        "officialPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "immutableLockedStorage": True,
        "immutableLockedStorageVersion": immutable_storage.VERSION,
        "immutableLockedStorageKeyspace": "LOCKED#GAME",
        "americanOdds": -115,
        "lockedAmericanOdds": -115,
        "priceBook": "FanDuel",
        "priceSource": "real_book",
        "teamWinProbabilityPct": 55.0,
        "winProbabilityPct": 55.0,
        "winProbabilityMeaning": "estimated_probability_selected_team_wins_game",
        "probabilitySemanticsFixed": True,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1-test",
        "featureVectorFrozenAtLock": True,
        "mlFeatureFreeze": {
            "exactVectorApplied": True,
            "exactVectorCreated": True,
            "completeSlateCoverage": True,
            "trainingEligible": True,
        },
        "slateCoverage": {"coverageComplete": True},
        "slatePredictionLock": {
            "locked": True,
            "lockAtUtc": lock_at,
            "latestScoringPullAt": source_at,
        },
        "canonicalPerGameStageAuthority": {
            "version": immutable_storage.AUTHORITY_VERSION,
            "verified": True,
            "stageFingerprint": "stage-fingerprint",
        },
        "tags": ["BOOK_AGREEMENT"],
    }
    attach_lock_safe_features(row)
    vector = cohort.freeze_feature_snapshot(row)
    row["frozenFeatureVector"] = vector
    row["frozenFeatureVectorVersion"] = vector["version"]
    return row


def _item(row=None):
    row = copy.deepcopy(row or _row())
    return {
        "PK": f"GAME_WINNERS#mlb#{SLATE}",
        "SK": f"LOCKED#GAME#{row['commenceTime']}#{row['gameIdentity']}",
        "record_type": audit.CANONICAL_LOCK_RECORD_TYPE,
        "sport": "mlb",
        "slate_date": SLATE,
        "game_id": row["gameId"],
        "game_identity": row["gameIdentity"],
        "immutable_locked": True,
        "stage_authority_verified": True,
        "stage_authority_version": immutable_storage.AUTHORITY_VERSION,
        "stage_fingerprint": "stage-fingerprint",
        "immutable_locked_storage_version": immutable_storage.VERSION,
        "data": row,
    }


class _Table:
    def __init__(self, items):
        self.items = copy.deepcopy(items)
        self.query_calls = []

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return {"Items": copy.deepcopy(self.items)}


def _final(game_id="game-1", away="San Francisco Giants", home="Seattle Mariners"):
    return {
        "id": game_id,
        "gameKeyBase": "san francisco giants|seattle mariners",
        "awayTeam": away,
        "homeTeam": home,
        "matchup": f"{away} at {home}",
        "commenceTime": "2026-07-18T23:12:00Z",
        "slateDateEt": SLATE,
        "winner": "Seattle Mariners",
        "completed": True,
    }


def _install_table(monkeypatch, items):
    table = _Table(items)
    monkeypatch.setattr(audit.history, "PULLS", table)
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda _table, _row: [])
    return table


def test_query_rejects_mutable_and_spoofed_lock_rows(monkeypatch):
    canonical = _item()
    later_mutable = {
        "PK": f"GAME_WINNERS#mlb#{SLATE}",
        "SK": "PREDICTION#later",
        "record_type": "mlb_game_winner_prediction",
        "data": {**_row(winner="San Francisco Giants"), "createdAt": "2026-07-18T23:59:00Z"},
    }
    spoofed = _item(_row(game_id="game-2"))
    spoofed["immutable_locked"] = False
    table = _install_table(monkeypatch, [later_mutable, spoofed, canonical])

    rows = audit._query_predictions_for_slate(SLATE)

    assert len(rows) == 1
    assert rows[0]["predictedWinner"] == "Seattle Mariners"
    authority = rows[0]["canonicalLockAuthority"]
    assert authority["sourceSk"].startswith("LOCKED#GAME#")
    assert authority["immutableLocked"] is True
    assert authority["stageAuthorityVerified"] is True
    assert authority["exactProviderIdentityMatched"] is False
    assert table.query_calls[0]["ConsistentRead"] is True


def test_mutable_row_without_lock_is_missing_not_invalid(monkeypatch):
    mutable = {
        "PK": f"GAME_WINNERS#mlb#{SLATE}",
        "SK": "GAME#2026-07-18T23:10:00Z#game-1",
        "record_type": "mlb_single_game_moneyline_prediction",
        "data": {
            **_row(),
            "lockedPrediction": False,
            "officialPrediction": False,
            "officialPredictionStatus": "PRE_LOCK_PLATFORM_PREDICTION",
            "immutableLockedStorage": False,
        },
    }
    _install_table(monkeypatch, [mutable])

    audited = audit.audit_rows([_final()])[0]

    assert audited["status"] == "MISSING_CANONICAL_LOCK"
    assert audited["canonicalLockEvidenceStatus"] == "MISSING"
    assert audited["canonicalLockValidationErrors"] == []


def test_query_rejects_missing_or_tampered_exact_lock_vectors(monkeypatch):
    valid = _item()
    missing_vector = _item(_row(game_id="game-missing-vector"))
    missing_vector["data"].pop("frozenFeatureVector")
    tampered_vector = _item(_row(game_id="game-tampered-vector"))
    tampered_vector["data"]["frozenFeatureVector"]["labels"]["homeWon"] = True
    _install_table(monkeypatch, [missing_vector, tampered_vector, valid])

    assert exact_contract.validate_exact_locked_row(valid["data"]) == []
    assert "missing_frozen_feature_vector" in exact_contract.validate_exact_locked_row(missing_vector["data"])
    assert "pregame_vector_contains_outcome_label" in exact_contract.validate_exact_locked_row(tampered_vector["data"])

    rows = audit._query_predictions_for_slate(SLATE)

    assert len(rows) == 1
    assert rows[0]["gameId"] == "game-1"
    rejected = audit.audit_rows([_final(game_id="game-tampered-vector")])[0]
    assert rejected["status"] == "INVALID_CANONICAL_LOCK"
    assert rejected["canonicalLockEvidenceStatus"] == "INVALID"
    assert rejected["canonicalLockValidationErrors"]
    assert "pregame_vector_contains_outcome_label" in rejected["canonicalLockAuthority"]["rejectionReasons"]


def test_query_rejects_wrong_storage_version_and_stage_fingerprint_binding(monkeypatch):
    wrong_storage_version = _item(_row(game_id="wrong-storage-version"))
    wrong_storage_version["immutable_locked_storage_version"] = "legacy-version"
    wrong_stage_binding = _item(_row(game_id="wrong-stage-binding"))
    wrong_stage_binding["stage_fingerprint"] = "different-stage-fingerprint"
    _install_table(monkeypatch, [wrong_storage_version, wrong_stage_binding])

    rows = audit._query_predictions_for_slate(SLATE)

    assert rows == []
    storage_rejection = audit.audit_rows(
        [_final(game_id="wrong-storage-version")]
    )[0]
    assert storage_rejection["status"] == "INVALID_CANONICAL_LOCK"
    assert "immutable_locked_envelope_version_mismatch" in (
        storage_rejection["canonicalLockValidationErrors"]
    )
    stage_rejection = audit.audit_rows(
        [_final(game_id="wrong-stage-binding")]
    )[0]
    assert stage_rejection["status"] == "INVALID_CANONICAL_LOCK"
    assert "stage_fingerprint_envelope_payload_mismatch" in (
        stage_rejection["canonicalLockValidationErrors"]
    )


def test_official_audit_requires_exact_provider_id_and_teams(monkeypatch):
    _install_table(monkeypatch, [_item()])

    exact, wrong_id, wrong_teams = audit.audit_rows([
        _final(),
        _final(game_id="different-provider-id"),
        _final(away="Wrong Away", home="Wrong Home"),
    ])

    assert exact["status"] == "GRADED"
    assert exact["correct"] is True
    assert audit._is_canonical_graded_row(exact) is True
    assert exact["canonicalLockAuthority"]["matchMethod"] == "exact_provider_game_id_and_teams"
    assert wrong_id["status"] == "MISSING_CANONICAL_LOCK"
    assert wrong_teams["status"] in {"MISSING_CANONICAL_LOCK", "CANONICAL_LOCK_IDENTITY_MISMATCH"}
    assert wrong_teams["status"] != "GRADED"


def test_learning_excludes_legacy_graded_rows_without_canonical_authority(monkeypatch):
    _install_table(monkeypatch, [_item()])
    canonical = audit.audit_rows([_final()])[0]
    legacy = {
        **canonical,
        "id": "legacy-game",
        "tags": ["LEGACY_SIGNAL"],
    }
    legacy.pop("canonicalLockAuthority")

    deduped = audit._dedupe_rows([legacy, canonical])
    learning = audit.score_learning([legacy, canonical], historical_rows=[legacy])

    assert deduped == [canonical]
    assert learning["multiWindowStats"]["season"]["rowCount"] == 1
    assert "LEGACY_SIGNAL" not in learning["multiWindowStats"]["season"]["tagStats"]
    assert learning["historicalStats"]["historicalRowsUsed"] == 0


def test_accuracy_ledger_uses_new_namespace_and_rejects_legacy_rows(monkeypatch):
    _install_table(monkeypatch, [_item()])
    canonical = audit.audit_rows([_final()])[0]
    legacy = dict(canonical)
    legacy.pop("canonicalLockAuthority")

    assert accuracy.LEDGER_PK == "MLB_CANONICAL_LOCK_ACCURACY#LEDGER#v2"
    assert accuracy._has_canonical_lock_authority(canonical) is True
    assert accuracy._has_canonical_lock_authority(legacy) is False

    wrong_authority_version = copy.deepcopy(canonical)
    wrong_authority_version["canonicalLockAuthority"]["version"] = "lookalike-v0"
    assert accuracy._has_canonical_lock_authority(wrong_authority_version) is False
