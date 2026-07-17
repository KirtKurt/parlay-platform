from __future__ import annotations

import copy
import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_daily_lock_ml_vector_preservation_patch as exact_contract
import inqsi_pull_history
import mlb_daily_per_game_lock_patch as per_game
import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_slate_coverage_patch as coverage
import mlb_slate_prediction_lock as slate_lock


SLATE = "2026-07-17"


def _game(game_id: str, start: str, away: str, home: str):
    return {
        "game_id": game_id,
        "game_key": f"mlb|{game_id}",
        "commence_time": start,
        "away_team": away,
        "home_team": home,
    }


G1 = _game("game-1", "2026-07-17T23:00:00Z", "Away One", "Home One")
G2 = _game("game-2", "2026-07-18T02:00:00Z", "Away Two", "Home Two")
G1["books"] = {"fanduel": {"ml": {"home": -110, "away": 100}}}


def _provider_pull(manifest_games=None, raw_games=None, pull_id="pull-1"):
    manifest_games = copy.deepcopy(manifest_games if manifest_games is not None else [G1, G2])
    raw_games = copy.deepcopy(raw_games if raw_games is not None else manifest_games)
    pulled_at = "2026-07-17T20:00:00Z"
    manifest = inqsi_pull_history._build_provider_schedule_manifest(
        sport="mlb",
        slate=SLATE,
        pulled_at=pulled_at,
        pull_id=pull_id,
        source="the_odds_api",
        games=manifest_games,
    )
    key = inqsi_pull_history._provider_manifest_key(manifest)
    return {
        "sport": "mlb",
        "source": "the_odds_api",
        "slate_date": SLATE,
        "pulled_at": pulled_at,
        "pull_id": pull_id,
        "games": raw_games,
        "provider_schedule_manifest": manifest,
        "provider_manifest_binding": {
            "version": inqsi_pull_history.PROVIDER_MANIFEST_VERSION,
            "fingerprint": manifest["fingerprint"],
            "gameCount": manifest["gameCount"],
            "pk": key["PK"],
            "sk": key["SK"],
            "immutable": True,
            "fullProviderSchedule": True,
        },
    }


PULLS = [_provider_pull()]


def _live(game, winner, side):
    return {
        "slate_date": SLATE,
        "gameId": game["game_id"],
        "gameIdentity": game["game_id"],
        "gameKey": game["game_key"],
        "commenceTime": game["commence_time"],
        "awayTeam": game["away_team"],
        "homeTeam": game["home_team"],
        "predictedWinner": winner,
        "predictedSide": side,
        "score": 1,
        "winProbability": 0.01,
        # Simulate legacy wrappers having incorrectly declared this row final.
        "lockedPrediction": True,
        "officialPrediction": True,
        "officialPick": True,
        "tags": ["FINAL_LOCKED", "SLATE_LOCKED", "SLATE_WIDE_45_MIN_LOCK_POLICY"],
    }


def _canonical_item(game, winner, side, score):
    lock_at = "2026-07-17T22:15:00+00:00" if game["game_id"] == "game-1" else "2026-07-18T01:15:00+00:00"
    row = {
        "slate_date": SLATE,
        "gameId": game["game_id"],
        "gameIdentity": game["game_id"],
        "gameKey": game["game_key"],
        "commenceTime": game["commence_time"],
        "awayTeam": game["away_team"],
        "homeTeam": game["home_team"],
        "predictedWinner": winner,
        "predictedSide": side,
        "score": score,
        "winProbability": 0.61,
        "lockedPrediction": True,
        "officialPrediction": True,
        "officialPick": True,
        "lockedAtUtc": lock_at,
        "predictionSourcePullAt": "2026-07-17T20:00:00+00:00",
        "frozenFeatureVector": {"lockAtUtc": lock_at, "fingerprint": f"fingerprint-{game['game_id']}"},
        "tags": ["FINAL_LOCKED", "OFFICIAL_LOCKED_PREDICTION"],
        "immutablePerGameStage": True,
        "lastPrelockSelectionFingerprint": f"selection-{game['game_id']}",
        "lastPrelockPromotionVersion": per_game.PROMOTION_POLICY_VERSION,
        "modelOrSignalRecomputedAtLock": False,
        "slatePredictionLock": {"lockAtUtc": lock_at, "locked": True},
    }
    stage = {
        **immutable_storage._stage_key(row),
        "record_type": per_game.STAGE_RECORD_TYPE,
        "slate_date": SLATE,
        "game_identity": coverage.game_identity(row),
        "commence_time": game["commence_time"],
        "scheduled_lock_at_utc": lock_at,
        "source_pull_at_utc": "2026-07-17T20:00:00+00:00",
        "staged_at_utc": lock_at,
        "promotion_policy_version": per_game.PROMOTION_POLICY_VERSION,
        "immutable_staged": True,
        "write_once": True,
        "candidate_proof": {
            "version": per_game.PROMOTION_POLICY_VERSION,
            "predictionSourcePullAtUtc": "2026-07-17T20:00:00+00:00",
            "predictionCreatedAtUtc": "2026-07-17T20:01:00+00:00",
            "predictionPersistedAtUtc": "2026-07-17T20:02:00+00:00",
            "sourceAtOrBeforeCutoff": True,
            "createdAtOrBeforeCutoff": True,
            "persistedAtOrBeforeCutoff": True,
            "candidateSelectionFingerprint": f"selection-{game['game_id']}",
            "modelOrSignalRecomputedAtLock": False,
        },
        "data": {"row": copy.deepcopy(row)},
    }
    stage["stage_fingerprint"] = per_game._stage_fingerprint(stage)
    canonical_row = copy.deepcopy(row)
    canonical_row.update({
        "immutableLockedStorage": True,
        "immutableLockedStorageVersion": immutable_storage.VERSION,
        "immutableLockedStorageKeyspace": "LOCKED#GAME",
        "canonicalPerGameStageAuthority": immutable_storage._authority_proof(stage),
    })
    return {
        "PK": f"GAME_WINNERS#mlb#{SLATE}",
        "SK": f"LOCKED#GAME#{game['commence_time']}#{game['game_id']}",
        "record_type": coverage.CANONICAL_RECORD_TYPE,
        "immutable_locked": True,
        "stage_authority_verified": True,
        "stage_authority_version": immutable_storage.AUTHORITY_VERSION,
        "stage_fingerprint": stage["stage_fingerprint"],
        "data": canonical_row,
        "_stage": stage,
    }


class _Table:
    def __init__(self, items, pulls=None):
        self.items = []
        self.by_key = {}
        for raw in items:
            item = copy.deepcopy(raw)
            stage = item.pop("_stage", None)
            self.items.append(item)
            self.by_key[(item["PK"], item["SK"])] = item
            if stage:
                self.by_key[(stage["PK"], stage["SK"])] = stage
        for pull in pulls or []:
            manifest = pull.get("provider_schedule_manifest")
            if not isinstance(manifest, dict):
                continue
            key = inqsi_pull_history._provider_manifest_key(manifest)
            stored = {
                **key,
                "record_type": inqsi_pull_history.PROVIDER_MANIFEST_RECORD_TYPE,
                "manifest_fingerprint": manifest.get("fingerprint"),
                "data": copy.deepcopy(manifest),
            }
            self.by_key[(key["PK"], key["SK"])] = stored

    def query(self, **kwargs):
        return {"Items": copy.deepcopy(self.items)}

    def get_item(self, *, Key, ConsistentRead=False):
        item = self.by_key.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item else {}


def _engine(items):
    current = [
        _live(G1, G1["away_team"], "away"),
        _live(G2, G2["away_team"], "away"),
    ]

    class History:
        PULLS = _Table(items, PULLS)

        @staticmethod
        def query_pulls(sport, slate, limit):
            assert (sport, slate) == ("mlb", SLATE)
            return PULLS

        @staticmethod
        def provider_manifest_games_for_lock(pull, slate):
            return inqsi_pull_history.provider_manifest_games_for_lock(pull, slate)

    class Engine:
        history = History()
        calls = 0

        @staticmethod
        def predict_all(game_date=None, store=False, limit=500):
            Engine.calls += 1
            return {"ok": True, "sport": "mlb", "slate_date": game_date, "predictions": current}

        @staticmethod
        def _prediction_for_game(*args, **kwargs):
            raise AssertionError("public canonical authority must not rescore at lock")

    return Engine


def _install(engine):
    # Reload the two stateful patch modules so each test gets a fresh wrapper.
    global coverage, slate_lock
    slate_lock = importlib.reload(slate_lock)
    coverage = importlib.reload(coverage)
    inqsi_pull_history.PULLS = engine.history.PULLS
    coverage.apply(slate_lock)
    slate_lock.apply(engine)
    coverage.install_public_authority(engine, slate_lock)


def test_partial_canonical_overlay_preserves_lock_and_clears_legacy_official_flags(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    # This test isolates public overlay behavior; persisted stage-chain
    # enforcement has dedicated storage-authority tests and verifiers.
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda table, row: [])
    canonical = _canonical_item(G1, G1["home_team"], "home", 71)
    engine = _engine([canonical])
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    assert engine.calls == 1
    assert result["locked"] is False
    assert result["slatePredictionLock"]["slateWideLock"] is False
    assert result["slatePredictionLock"]["canonicalLockedGameCount"] == 1
    assert result["slatePredictionLock"]["lockStatus"] == "PARTIAL_PER_GAME_CANONICAL"
    assert result["publicPerGameAuthority"]["recomputedLockedPredictions"] is False

    by_id = {row["gameId"]: row for row in result["predictions"]}
    assert by_id["game-1"]["predictedWinner"] == G1["home_team"]
    assert by_id["game-1"]["score"] == 71
    assert by_id["game-1"]["lockedPrediction"] is True
    assert by_id["game-1"]["officialPrediction"] is True
    assert by_id["game-1"]["frozenFeatureVector"]["fingerprint"] == "fingerprint-game-1"

    assert by_id["game-2"]["predictedWinner"] == G2["away_team"]
    assert by_id["game-2"]["lockedPrediction"] is False
    assert by_id["game-2"]["officialPrediction"] is False
    assert by_id["game-2"]["officialPick"] is False
    assert "FINAL_LOCKED" not in by_id["game-2"]["tags"]
    assert "SLATE_LOCKED" not in by_id["game-2"]["tags"]
    assert by_id["game-2"]["perGameCanonicalLock"]["lockAtUtc"] == "2026-07-18T01:15:00+00:00"


def test_result_becomes_locked_only_when_every_manifest_game_is_canonical(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda table, row: [])
    items = [
        _canonical_item(G1, G1["home_team"], "home", 71),
        _canonical_item(G2, G2["home_team"], "home", 69),
    ]
    engine = _engine(items)
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    assert engine.calls == 1
    assert result["locked"] is True
    assert result["slatePredictionLock"]["locked"] is True
    assert result["slatePredictionLock"]["lockStatus"] == "COMPLETE_MANIFEST_ALL_CANONICAL"
    assert result["slateCoverage"]["canonicalCoverageComplete"] is True
    assert result["slateCoverage"]["providerManifestValidated"] is True
    assert result["slateCoverage"]["providerManifestFullProviderSchedule"] is True
    # G2 has no books/odds, but the immutable provider schedule still requires
    # it for public completeness and canonical lock coverage.
    assert result["slatePredictionLock"]["manifestGameIdentities"] == [
        "provider:game-1",
        "provider:game-2",
    ]
    assert result["officialPredictionCount"] == 2
    assert all(row["lockedPrediction"] is True for row in result["predictions"])
    assert {row["predictedWinner"] for row in result["predictions"]} == {G1["home_team"], G2["home_team"]}


def test_invalid_canonical_row_is_ignored_and_live_row_remains_prelock(monkeypatch):
    monkeypatch.setattr(
        exact_contract,
        "validate_exact_locked_row",
        lambda row: ["fingerprint_mismatch"] if row.get("gameId") == "game-1" else [],
    )
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda table, row: [])
    engine = _engine([_canonical_item(G1, G1["home_team"], "home", 71)])
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    assert result["locked"] is False
    assert result["officialPredictionCount"] == 0
    assert result["slatePredictionLock"]["canonicalLockedGameCount"] == 0
    assert result["slatePredictionLock"]["invalidCanonicalRows"]["provider:game-1"] == ["fingerprint_mismatch"]
    assert all(row["lockedPrediction"] is False for row in result["predictions"])
    assert result["operationalDefect"] is True


def test_canonical_query_failure_keeps_every_live_row_prelock(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])

    engine = _engine([])
    manifest_table = engine.history.PULLS

    class BrokenTable:
        @staticmethod
        def query(**kwargs):
            raise RuntimeError("ddb unavailable")

        @staticmethod
        def get_item(*, Key, ConsistentRead=False):
            return manifest_table.get_item(Key=Key, ConsistentRead=ConsistentRead)

    engine.history.PULLS = BrokenTable()
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    assert result["locked"] is False
    assert result["slatePredictionLock"]["canonicalReadOperational"] is False
    assert "ddb unavailable" in result["slatePredictionLock"]["canonicalReadError"]
    assert result["officialPredictionCount"] == 0
    assert result["officialPredictionDisplay"] == []
    assert all(row["lockedPrediction"] is False for row in result["predictions"])


def test_runtime_version_exposes_last_prelock_promotion_authority_marker():
    import mlb_ml_runtime_install_v3

    assert "verified-stage-promotion-authority" in mlb_ml_runtime_install_v3.VERSION
    assert coverage.AUTHORITY_VERSION == "MLB-LAST-PRELOCK-PROMOTION-AUTHORITY-v1-canonical-read-overlay"


def test_schedule_drift_invalidates_old_canonical_row(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    shifted = copy.deepcopy(G1)
    shifted["commence_time"] = "2026-07-17T23:30:00Z"
    monkeypatch.setattr(
        sys.modules[__name__],
        "PULLS",
        [_provider_pull([shifted, G2], pull_id="pull-shifted")],
    )
    engine = _engine([_canonical_item(G1, G1["home_team"], "home", 71)])
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    errors = result["slatePredictionLock"]["invalidCanonicalRows"]["provider:game-1"]
    assert "manifest_commence_time_mismatch" in errors
    assert "manifest_tminus45_cutoff_mismatch" in errors
    assert result["officialPredictionCount"] == 0
    assert result["operationalDefect"] is True


def test_missing_canonical_at_cutoff_is_failure_not_prelock(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    engine = _engine([])
    _install(engine)
    monkeypatch.setattr(
        coverage,
        "_now_utc",
        lambda: datetime(2026, 7, 17, 22, 30, tzinfo=timezone.utc),
    )

    result = engine.predict_all(SLATE, store=False)

    assert result["slatePredictionLock"]["lockStatus"] == "LOCK_DUE_CANONICAL_MISSING"
    assert result["slatePredictionLock"]["lockDueCanonicalMissingCount"] == 1
    by_id = {row["gameId"]: row for row in result["predictions"]}
    assert by_id["game-1"]["officialPredictionStatus"] == "LOCK_DUE_CANONICAL_MISSING"
    assert by_id["game-1"]["perGameCanonicalLock"]["status"] == "LOCK_DUE_CANONICAL_MISSING"
    assert by_id["game-1"]["lockedPrediction"] is False
    assert result["operationalDefect"] is True


def test_missing_provider_manifest_fails_closed_and_never_appears_complete(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    missing = copy.deepcopy(_provider_pull())
    missing.pop("provider_schedule_manifest")
    missing.pop("provider_manifest_binding")
    monkeypatch.setattr(sys.modules[__name__], "PULLS", [missing])
    engine = _engine([
        _canonical_item(G1, G1["home_team"], "home", 71),
        _canonical_item(G2, G2["home_team"], "home", 69),
    ])
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    assert result["locked"] is False
    assert result["allGamesPredicted"] is False
    assert result["officialPredictionCount"] == 0
    assert result["slateCoverage"]["coverageComplete"] is False
    assert result["slateCoverage"]["providerManifestValidated"] is False
    assert "PROVIDER_SCHEDULE_MANIFEST" in result["slateCoverage"]["error"]


def test_tampered_provider_manifest_fails_closed_and_never_appears_complete(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    tampered = _provider_pull()
    tampered["provider_schedule_manifest"]["games"][0]["home_team"] = "Tampered Team"
    monkeypatch.setattr(sys.modules[__name__], "PULLS", [tampered])
    engine = _engine([
        _canonical_item(G1, G1["home_team"], "home", 71),
        _canonical_item(G2, G2["home_team"], "home", 69),
    ])
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    assert result["locked"] is False
    assert result["officialPredictionCount"] == 0
    assert result["slateCoverage"]["coverageComplete"] is False
    assert "provider_manifest_fingerprint_mismatch" in result["slateCoverage"]["error"]


def test_subset_pull_cannot_shrink_full_provider_manifest_or_appear_complete(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    subset = _provider_pull(manifest_games=[G1, G2], raw_games=[G1])
    monkeypatch.setattr(sys.modules[__name__], "PULLS", [subset])
    engine = _engine([_canonical_item(G1, G1["home_team"], "home", 71)])
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    assert result["locked"] is False
    assert result["officialPredictionCount"] == 0
    assert result["slateCoverage"]["coverageComplete"] is False
    assert "provider_manifest_pull_membership_mismatch" in result["slateCoverage"]["error"]
