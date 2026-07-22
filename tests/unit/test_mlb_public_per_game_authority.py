from __future__ import annotations

import copy
import importlib
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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


def _provider_pull(
    manifest_games=None,
    raw_games=None,
    pull_id="pull-1",
    pulled_at="2026-07-17T20:00:00Z",
):
    manifest_games = copy.deepcopy(manifest_games if manifest_games is not None else [G1, G2])
    raw_games = copy.deepcopy(raw_games if raw_games is not None else manifest_games)
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
    start = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
    lock_at = (start - timedelta(minutes=45)).astimezone(timezone.utc).isoformat()
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


def _playability_item(
    game,
    canonical,
    checkpoint,
    evaluated_at,
    *,
    playable,
    reasons=None,
):
    locked = canonical["data"]
    digest = coverage._status_digest(game)
    item = {
        "PK": f"LOCKED_PICKS#mlb#{SLATE}",
        "SK": f"PER_GAME_PLAYABILITY#{checkpoint}#{digest}",
        "record_type": coverage.PLAYABILITY_RECORD_TYPE,
        "version": coverage.PLAYABILITY_VERSION,
        "sport": "mlb",
        "slate_date": SLATE,
        "game_identity": coverage.game_identity(game),
        "game_id": game["game_id"],
        "commence_time": game["commence_time"],
        "checkpoint": checkpoint,
        "checkpoint_timing_status": "ON_TIME",
        "evaluated_at_utc": evaluated_at,
        "canonical_selection_fingerprint": locked["lastPrelockSelectionFingerprint"],
        "canonical_predicted_winner": locked["predictedWinner"],
        "canonical_predicted_side": locked["predictedSide"],
        "canonical_probability_pct": locked.get(
            "teamWinProbabilityPct",
            locked.get("winProbabilityPct"),
        ),
        "selection_rewrite_allowed": False,
        "playable": playable,
        "blocked": not playable,
        "status": "PLAYABLE" if playable else "BLOCKED",
        "reasons": list(reasons or []),
        "write_once": True,
        "created_at": evaluated_at,
    }
    item["assessment_fingerprint"] = coverage._record_fingerprint(
        item,
        "assessment_fingerprint",
    )
    return item


def _terminal_outcome_item(game, *, reasons=None):
    authority_pull = PULLS[0]
    manifest = authority_pull["provider_schedule_manifest"]
    binding = authority_pull["provider_manifest_binding"]
    digest = coverage._status_digest(game)
    item = {
        "PK": f"LOCKED_PICKS#mlb#{SLATE}",
        "SK": f"PER_GAME_LOCK_OUTCOME#TMINUS45#{digest}",
        "record_type": coverage.LOCK_OUTCOME_RECORD_TYPE,
        "version": coverage.LOCK_OUTCOME_VERSION,
        "sport": "mlb",
        "slate_date": SLATE,
        "game_identity": coverage.game_identity(game),
        "game_id": game["game_id"],
        "commence_time": game["commence_time"],
        "lock_status": "LOCKED_NO_PREDICTION_DATA",
        "lock_outcome_recorded": True,
        "locked_prediction": False,
        "canonical": False,
        "official_prediction": False,
        "playable": False,
        "blocked": True,
        "playability_block_reasons": ["NO_VALID_PREGAME_PREDICTION"],
        "training_eligible": False,
        "training_exclusion_reasons": ["missing_immutable_prediction"],
        "reasons": list(reasons or ["no_valid_user_visible_platform_prelock_prediction"]),
        "provider_manifest_fingerprint": manifest["fingerprint"],
        "provider_manifest_pk": binding["pk"],
        "provider_manifest_sk": binding["sk"],
        "manifest_game_count": manifest["gameCount"],
        "write_once": True,
        "created_at": "2026-07-18T01:15:00+00:00",
    }
    item["lock_outcome_fingerprint"] = coverage._record_fingerprint(
        item,
        "lock_outcome_fingerprint",
    )
    return item


class _Table:
    def __init__(self, items, pulls=None, status_items=None):
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
        for raw in status_items or []:
            item = copy.deepcopy(raw)
            self.by_key[(item["PK"], item["SK"])] = item

    def query(self, **kwargs):
        return {"Items": copy.deepcopy(self.items)}

    def get_item(self, *, Key, ConsistentRead=False):
        item = self.by_key.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item else {}


def _engine(items, *, current=None, status_items=None):
    if current is None:
        current = [
            _live(G1, G1["away_team"], "away"),
            _live(G2, G2["away_team"], "away"),
        ]

    class History:
        PULLS = _Table(items, PULLS, status_items)

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


def _fallback_manifest_game():
    return {
        "game_id": "mlb_statsapi:880002",
        "game_key": "mlb|statsapi|880002",
        "official_game_pk": "880002",
        "official_game_id": "mlb_statsapi:880002",
        "canonical_start_time_source": "MLB_STATS_API_EXACT_DATE",
        "commence_time": "2026-07-17T23:00:00Z",
        "away_team": "Alias Away",
        "home_team": "Alias Home",
    }


def _fallback_manifest_authority():
    return {
        "providerManifestValidated": True,
        "providerManifestFingerprint": "official-fallback-fixture",
        "providerManifestPk": "PROVIDER_SCHEDULE#mlb#2026-07-17",
        "providerManifestSk": "MANIFEST#fixture",
        "verifiedFullSlateGameCount": 1,
        "officialScheduleBacked": True,
        "officialScheduleAuthorityVersion": "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date",
        "officialScheduleAuthoritySource": "MLB_STATS_API_EXACT_DATE",
        "officialScheduleGameCount": 1,
        "officialScheduleAuthoritativeStartTimes": True,
    }


def test_public_prelock_provider_alias_is_bound_to_durable_stats_identity(monkeypatch):
    manifest_game = _fallback_manifest_game()
    provider_row = {
        **_live(
            {
                "game_id": "late-provider-880002",
                "game_key": "mlb|provider|880002",
                "commence_time": "2026-07-17T23:00:00Z",
                "away_team": "Alias Away",
                "home_team": "Alias Home",
            },
            "Alias Home",
            "home",
        ),
        "officialGamePk": "880002",
        "officialGameId": "mlb_statsapi:880002",
        "providerEventId": "late-provider-880002",
        "providerCommenceTime": "2026-07-17T23:01:00Z",
        "providerStartDriftSeconds": 60,
        # An upstream actionability wrapper must not make a pre-lock row count
        # toward immutable playable-pick accuracy.
        "playableAccuracyEligible": True,
    }
    engine = _engine([], current=[provider_row])
    _install(engine)
    monkeypatch.setattr(
        coverage,
        "_provider_manifest_for_public",
        lambda module, lock_module, pulls, slate: (
            [copy.deepcopy(manifest_game)],
            _fallback_manifest_authority(),
        ),
    )
    monkeypatch.setattr(
        coverage,
        "_now_utc",
        lambda: datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc),
    )

    result = engine.predict_all(SLATE, store=False)

    assert result["gameCount"] == 1
    assert result["lifecycleDisplayCount"] == 1
    assert result["operationalDefect"] is False
    assert result["slateCoverage"]["missingLifecycleDisplayGameIdentities"] == []
    row = result["predictions"][0]
    assert row["gameId"] == "mlb_statsapi:880002"
    assert row["sourcePredictionGameId"] == "late-provider-880002"
    assert row["providerEventId"] == "late-provider-880002"
    assert row["officialGamePk"] == "880002"
    assert row["lockedPrediction"] is False
    assert row["playableAccuracyEligible"] is False


def test_public_lifecycle_synthesizes_manifest_row_without_current_prediction(monkeypatch):
    manifest_game = _fallback_manifest_game()
    engine = _engine([], current=[])
    _install(engine)
    monkeypatch.setattr(
        coverage,
        "_provider_manifest_for_public",
        lambda module, lock_module, pulls, slate: (
            [copy.deepcopy(manifest_game)],
            _fallback_manifest_authority(),
        ),
    )
    monkeypatch.setattr(
        coverage,
        "_now_utc",
        lambda: datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc),
    )

    result = engine.predict_all(SLATE, store=False)

    assert result["gameCount"] == result["lifecycleDisplayCount"] == 1
    assert result["displayStatusCoverageComplete"] is True
    assert result["predictionCoverageComplete"] is False
    assert result["operationalDefect"] is False
    row = result["predictions"][0]
    assert row["gameId"] == "mlb_statsapi:880002"
    assert row["officialGamePk"] == "880002"
    assert row["predictedWinner"] is None
    assert row["lockStatus"] == "OPEN_PRE_LOCK"


def test_partial_canonical_overlay_preserves_lock_and_clears_legacy_official_flags(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    # This test isolates public overlay behavior; persisted stage-chain
    # enforcement has dedicated storage-authority tests and verifiers.
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda table, row: [])
    canonical = _canonical_item(G1, G1["home_team"], "home", 71)
    engine = _engine([canonical])
    _install(engine)
    monkeypatch.setattr(
        coverage,
        "_now_utc",
        lambda: datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc),
    )

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
    assert "invalid_vector_not_explicitly_unverified" in (
        result["slatePredictionLock"]["invalidCanonicalRows"]["provider:game-1"]
    )
    assert all(row["lockedPrediction"] is False for row in result["predictions"])
    assert result["operationalDefect"] is True


def test_explicit_vector_exclusion_does_not_hide_canonical_selection(monkeypatch):
    vector_errors = ["missing_frozen_feature_vector"]
    monkeypatch.setattr(
        exact_contract,
        "validate_exact_locked_row",
        lambda row: vector_errors,
    )
    monkeypatch.setattr(
        immutable_storage,
        "validate_canonical_stage_authority",
        lambda table, row: [],
    )
    canonical = _canonical_item(G1, G1["home_team"], "home", 71)
    canonical["data"] = exact_contract.apply_exact_vector_training_status(
        canonical["data"],
        vector_errors,
    )
    engine = _engine([canonical])
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    row = next(item for item in result["predictions"] if item["gameId"] == "game-1")
    assert row["lockedPrediction"] is True
    assert row["officialPrediction"] is True
    assert row["exactVectorVerified"] is False
    assert row["trainingEligible"] is False
    card = next(item for item in result["officialPredictionDisplay"] if item["gameId"] == "game-1")
    assert card["exactVectorVerified"] is False
    assert card["trainingEligible"] is False


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

    result = engine.read_persisted_predictions(SLATE, store=False)

    assert result["locked"] is False
    assert result["operationalDefect"] is True
    assert result["slatePredictionLock"]["canonicalReadOperational"] is False
    assert "ddb unavailable" in result["slatePredictionLock"]["canonicalReadError"]
    assert result["officialPredictionCount"] == 0
    assert result["officialPredictionDisplay"] == []
    assert all(row["lockedPrediction"] is False for row in result["predictions"])


def test_runtime_version_exposes_last_prelock_promotion_authority_marker():
    import mlb_ml_runtime_install_v3

    assert "verified-stage-promotion-authority" in mlb_ml_runtime_install_v3.VERSION
    assert coverage.AUTHORITY_VERSION == "MLB-LAST-PRELOCK-PROMOTION-AUTHORITY-v1-canonical-read-overlay"


def test_persisted_reader_uses_one_read_scope_without_scoping_writer(monkeypatch):
    engine = _engine([])
    table = engine.history.PULLS
    table.name = "parlay_platform_snapshots"

    class BatchResource:
        def __init__(self):
            self.calls = 0

        def batch_get_item(self, *, RequestItems):
            self.calls += 1
            request = RequestItems[table.name]
            rows = []
            for key in request.get("Keys") or []:
                item = table.by_key.get((key["PK"], key["SK"]))
                if item is not None:
                    rows.append(copy.deepcopy(item))
            return {
                "Responses": {table.name: rows},
                "UnprocessedKeys": {},
            }

    resource = BatchResource()
    engine.history.DDB = resource
    _install(engine)
    events = []
    original_scope = per_game._status_read_scope

    @contextmanager
    def read_scope():
        events.append("enter")
        try:
            with original_scope() as state:
                yield state
        finally:
            events.append("exit")

    monkeypatch.setattr(per_game, "_status_read_scope", read_scope)

    uncached = engine.predict_all(SLATE, store=False)
    cached = engine.read_persisted_predictions(SLATE, store=False)

    assert cached == uncached
    assert events == ["enter", "exit"]
    assert per_game._STATUS_READ_CACHE.get() is None
    assert resource.calls > 0

    events.clear()
    read_calls = resource.calls
    engine.predict_all(SLATE, store=True)
    engine.read_persisted_predictions(SLATE, store=True)
    assert events == []
    assert resource.calls == read_calls


def test_legacy_immutable_lock_suppresses_ambiguous_probability_authority(monkeypatch):
    canonical = _canonical_item(G1, G1["home_team"], "home", 71)
    canonical["data"].update(
        {
            "playable": True,
            "trainingEligible": True,
            "teamWinProbabilityPct": 97.0,
            "modelWinProbability": 0.97,
        }
    )
    engine = _engine([canonical])
    monkeypatch.setattr(
        immutable_storage, "validate_canonical_stage_authority", lambda table, row: []
    )
    monkeypatch.setattr(
        exact_contract, "validate_selection_lock_vector_status", lambda row: []
    )

    row, errors = coverage._canonical_row(engine, canonical, SLATE, G1)

    assert errors == []
    assert row["predictedWinner"] == G1["home_team"]
    assert row["probabilityAuthoritySuppressed"] is True
    assert row["playable"] is False
    assert row["trainingEligible"] is False
    assert "teamWinProbabilityPct" not in row
    assert "modelWinProbability" not in row
    assert "legacy_probability_contract_missing" in row["trainingExclusionReasons"]


def test_persisted_public_reader_carries_probability_contract_attestation(monkeypatch):
    captured = {}

    class Engine:
        history = object()
        _INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED = True

    class LockModule:
        LOCK_MINUTES = 45
        _parse_dt = staticmethod(coverage._parse_dt)

    monkeypatch.setattr(
        per_game,
        "_scoring_pulls",
        lambda adapter, pulls, game, at_or_before=None: list(pulls),
    )

    def candidate(adapter, slate, game, scoring, at_or_before=None):
        captured["engine"] = adapter.mlb_game_winner_engine
        required = getattr(
            adapter.mlb_game_winner_engine,
            "_INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED",
            False,
        )
        return (
            None,
            None,
            [],
            ["probability_contract_version_missing_or_wrong"] if required else [],
        )

    monkeypatch.setattr(per_game, "_last_prelock_candidate", candidate)

    current, invalid = coverage._persisted_prelock_by_identity(
        Engine,
        LockModule,
        PULLS,
        [G1],
        SLATE,
        datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc),
    )

    assert captured["engine"] is Engine
    assert current == {}
    assert invalid["provider:game-1"] == [
        "probability_contract_version_missing_or_wrong"
    ]


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


def test_public_authority_retains_full_roster_after_started_game_contracts(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda table, row: [])
    full = _provider_pull(
        pull_id="full-prestart",
        pulled_at="2026-07-17T20:00:00Z",
    )
    contracted = _provider_pull(
        manifest_games=[G2],
        raw_games=[G2],
        pull_id="contracted-after-game-one-start",
        pulled_at="2026-07-17T23:30:00Z",
    )
    monkeypatch.setattr(sys.modules[__name__], "PULLS", [full, contracted])
    engine = _engine([
        _canonical_item(G1, G1["home_team"], "home", 71),
        _canonical_item(G2, G2["home_team"], "home", 69),
    ])
    _install(engine)

    result = engine.predict_all(SLATE, store=False)

    assert result["locked"] is True
    assert result["gameCount"] == 2
    assert result["officialPredictionCount"] == 2
    assert result["slateCoverage"]["verifiedFullSlateGameCount"] == 2
    assert result["slateCoverage"]["latestProviderFeedGameCount"] == 1
    assert result["slateCoverage"]["latestProviderFeedContracted"] is True


def test_latest_due_playability_assessment_controls_release_without_rewriting_winner(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda table, row: [])
    canonical = _canonical_item(G1, G1["home_team"], "home", 71)
    t30 = _playability_item(
        G1,
        canonical,
        "T_MINUS_30",
        "2026-07-17T22:30:05+00:00",
        playable=True,
    )
    t15 = _playability_item(
        G1,
        canonical,
        "T_MINUS_15",
        "2026-07-17T22:45:05+00:00",
        playable=False,
        reasons=["CONFIRMED_IMPACT_PLAYER_ABSENCE"],
    )
    engine = _engine([canonical], status_items=[t30, t15])
    _install(engine)
    monkeypatch.setattr(
        coverage,
        "_now_utc",
        lambda: datetime(2026, 7, 17, 22, 46, tzinfo=timezone.utc),
    )

    result = engine.predict_all(SLATE, store=False)
    row = next(item for item in result["predictions"] if item["gameId"] == "game-1")

    assert row["predictedWinner"] == G1["home_team"]
    assert row["predictedSide"] == "home"
    assert row["lockedPrediction"] is True
    assert row["officialPrediction"] is True
    assert row["playable"] is False
    assert row["blocked"] is True
    assert row["wagerReleaseBlocked"] is True
    assert row["requiredPlayabilityCheckpoint"] == "T_MINUS_15"
    assert row["playabilityAssessment"]["checkpoint"] == "T_MINUS_15"
    assert row["playabilityAssessmentValidationErrors"] == []
    assert "CONFIRMED_IMPACT_PLAYER_ABSENCE" in row["playabilityBlockReasons"]
    assert result["operationalDefect"] is False


def test_missing_latest_due_playability_assessment_fails_release_closed(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda table, row: [])
    canonical = _canonical_item(G1, G1["home_team"], "home", 71)
    t30 = _playability_item(
        G1,
        canonical,
        "T_MINUS_30",
        "2026-07-17T22:30:05+00:00",
        playable=True,
    )
    engine = _engine([canonical], status_items=[t30])
    _install(engine)
    monkeypatch.setattr(
        coverage,
        "_now_utc",
        lambda: datetime(2026, 7, 17, 22, 46, tzinfo=timezone.utc),
    )

    result = engine.predict_all(SLATE, store=False)
    row = next(item for item in result["predictions"] if item["gameId"] == "game-1")

    assert row["predictedWinner"] == G1["home_team"]
    assert row["predictedSide"] == "home"
    assert row["lockedPrediction"] is True
    assert row["playable"] is False
    assert row["blocked"] is True
    assert row["playabilityAssessment"] is None
    assert row["playabilityAssessmentValidationErrors"] == [
        "T_MINUS_15:required_assessment_missing"
    ]
    assert (
        "PLAYABILITY_ASSESSMENT_INVALID:T_MINUS_15:required_assessment_missing"
        in row["playabilityBlockReasons"]
    )
    assert result["operationalDefect"] is True


def test_terminal_no_prediction_status_displays_without_current_prediction_row(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda table, row: [])
    canonical = _canonical_item(G1, G1["home_team"], "home", 71)
    assessment = _playability_item(
        G1,
        canonical,
        "T_MINUS_15",
        "2026-07-17T22:45:05+00:00",
        playable=True,
    )
    terminal = _terminal_outcome_item(G2)
    engine = _engine(
        [canonical],
        current=[_live(G1, G1["away_team"], "away")],
        status_items=[assessment, terminal],
    )
    _install(engine)
    monkeypatch.setattr(
        coverage,
        "_now_utc",
        lambda: datetime(2026, 7, 18, 2, 1, tzinfo=timezone.utc),
    )

    result = engine.predict_all(SLATE, store=False)
    by_id = {row["gameId"]: row for row in result["predictions"]}
    terminal_row = by_id["game-2"]

    assert result["gameCount"] == 2
    assert result["displayPredictionCount"] == 2
    assert result["lifecycleDisplayCount"] == 2
    assert result["lockedPredictionCount"] == 1
    assert result["officialPredictionCount"] == 1
    assert result["lockedStatusCount"] == 2
    assert result["noPredictionDataCount"] == 1
    assert result["lockStatusComplete"] is True
    assert result["canonicalPredictionComplete"] is False
    assert result["allGamesPredicted"] is False
    assert result["predictionCoverageComplete"] is False
    assert result["displayStatusCoverageComplete"] is True
    assert result["operationalDefect"] is False
    assert len(result["requiredWinnerPredictionDisplay"]) == 1
    assert len(result["requiredGameLifecycleDisplay"]) == 2
    assert result["slateCoverage"]["missingWinnerPredictionGameIdentities"] == [
        "provider:game-2"
    ]

    assert terminal_row["lockStatus"] == "LOCKED_NO_PREDICTION_DATA"
    assert terminal_row["lockOutcomeRecorded"] is True
    assert terminal_row["lockedPrediction"] is False
    assert terminal_row["officialPrediction"] is False
    assert terminal_row["predictedWinner"] is None
    assert terminal_row["predictedSide"] is None
    assert terminal_row["playable"] is False
    assert terminal_row["blocked"] is True
    assert terminal_row["trainingEligible"] is False


def test_public_doubleheader_game2_pending_then_final_event_changes_release_only(monkeypatch):
    monkeypatch.setattr(exact_contract, "validate_exact_locked_row", lambda row: [])
    monkeypatch.setattr(immutable_storage, "validate_canonical_stage_authority", lambda table, row: [])
    dh1 = _game("dh-1", "2026-07-17T20:00:00Z", "Same Away", "Same Home")
    dh2 = _game("dh-2", "2026-07-17T22:00:00Z", "Same Away", "Same Home")
    pull = _provider_pull(
        [dh1, dh2],
        [dh1, dh2],
        pull_id="doubleheader-full",
        pulled_at="2026-07-17T19:00:00Z",
    )
    monkeypatch.setattr(sys.modules[__name__], "PULLS", [pull])

    canonical_one = _canonical_item(dh1, dh1["home_team"], "home", 70)
    canonical_two = _canonical_item(dh2, dh2["home_team"], "home", 68)
    game_one_t15 = _playability_item(
        dh1,
        canonical_one,
        "T_MINUS_15",
        "2026-07-17T19:45:05+00:00",
        playable=True,
    )
    pending = _playability_item(
        dh2,
        canonical_two,
        "EVENT_GAME1_PENDING",
        "2026-07-17T21:15:05+00:00",
        playable=False,
        reasons=["DOUBLEHEADER_GAME1_NOT_FINAL"],
    )
    engine = _engine(
        [canonical_one, canonical_two],
        current=[
            _live(dh1, dh1["away_team"], "away"),
            _live(dh2, dh2["away_team"], "away"),
        ],
        status_items=[game_one_t15, pending],
    )
    _install(engine)
    monkeypatch.setattr(
        coverage,
        "_now_utc",
        lambda: datetime(2026, 7, 17, 21, 16, tzinfo=timezone.utc),
    )

    pending_result = engine.predict_all(SLATE, store=False)
    pending_row = next(
        row for row in pending_result["predictions"] if row["gameId"] == "dh-2"
    )
    immutable_selection = (
        pending_row["predictedWinner"],
        pending_row["predictedSide"],
        pending_row["lastPrelockSelectionFingerprint"],
    )

    assert pending_row["lockedPrediction"] is True
    assert pending_row["selectionFingerprint"] == pending_row["lastPrelockSelectionFingerprint"]
    assert pending_row["eventPlayabilityAssessmentRequired"] is True
    assert pending_row["requiredPlayabilityCheckpoint"] is None
    assert pending_row["playabilityAssessment"]["checkpoint"] == "EVENT_GAME1_PENDING"
    assert pending_row["playable"] is False
    assert pending_row["blocked"] is True
    assert "DOUBLEHEADER_GAME1_NOT_FINAL" in pending_row["playabilityBlockReasons"]
    assert pending_result["operationalDefect"] is False

    t30 = _playability_item(
        dh2,
        canonical_two,
        "T_MINUS_30",
        "2026-07-17T21:30:05+00:00",
        playable=False,
        reasons=["DOUBLEHEADER_GAME1_NOT_FINAL"],
    )
    final = _playability_item(
        dh2,
        canonical_two,
        "EVENT_GAME1_FINAL",
        "2026-07-17T21:31:05+00:00",
        playable=True,
    )
    for item in (t30, final):
        engine.history.PULLS.by_key[(item["PK"], item["SK"])] = copy.deepcopy(item)
    monkeypatch.setattr(
        coverage,
        "_now_utc",
        lambda: datetime(2026, 7, 17, 21, 31, 5, tzinfo=timezone.utc),
    )

    final_result = engine.predict_all(SLATE, store=False)
    final_row = next(
        row for row in final_result["predictions"] if row["gameId"] == "dh-2"
    )

    assert final_row["playabilityAssessment"]["checkpoint"] == "EVENT_GAME1_FINAL"
    assert final_row["requiredPlayabilityCheckpoint"] == "T_MINUS_30"
    assert final_row["playabilityAssessmentValidationErrors"] == []
    assert final_row["playable"] is True
    assert final_row["blocked"] is False
    assert final_row["selectionFingerprint"] == final_row["lastPrelockSelectionFingerprint"]
    assert (
        final_row["predictedWinner"],
        final_row["predictedSide"],
        final_row["lastPrelockSelectionFingerprint"],
    ) == immutable_selection
    assert final_result["operationalDefect"] is False
