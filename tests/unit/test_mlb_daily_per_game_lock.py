from __future__ import annotations

import copy
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mlb_daily_per_game_lock_patch as patch
import mlb_daily_lock_coverage_patch as coverage_patch
import mlb_daily_lock_audit_fallback_patch as audit_fallback
import inqsi_pull_history as history_contract
import mlb_ml_clean_cohort_v1 as cohort
from scripts.mlb_ml_feature_test_fixtures import attach_lock_safe_features


SLATE = "2026-07-13"


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


class ConditionalCollision(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
        super().__init__("conditional collision")


class FakeTable:
    def __init__(self):
        self.items = {}
        self.put_calls = []
        self.put_requests = []
        self.diagnostic_write_failures_remaining = 0

    def get_item(self, Key, ConsistentRead=False):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item is not None else {}

    def put_item(self, Item, ConditionExpression=None):
        if (
            Item.get("record_type") in {
                patch.ATTEMPT_RECORD_TYPE,
                patch.ATTEMPT_OUTCOME_RECORD_TYPE,
            }
            and self.diagnostic_write_failures_remaining > 0
        ):
            self.diagnostic_write_failures_remaining -= 1
            raise RuntimeError("injected diagnostic write failure")
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and key in self.items:
            raise ConditionalCollision()
        self.items[key] = copy.deepcopy(Item)
        self.put_calls.append(key)
        self.put_requests.append({
            "key": key,
            "condition": ConditionExpression,
        })
        return {}

    def query(
        self,
        KeyConditionExpression=None,
        ExpressionAttributeValues=None,
        ConsistentRead=False,
        ScanIndexForward=True,
        Limit=None,
    ):
        values = ExpressionAttributeValues or {}
        pk = values.get(":pk")
        prefix = str(values.get(":prefix") or "")
        matches = [
            copy.deepcopy(item)
            for (item_pk, item_sk), item in self.items.items()
            if item_pk == pk and str(item_sk).startswith(prefix)
        ]
        matches.sort(key=lambda item: str(item.get("SK") or ""), reverse=not ScanIndexForward)
        truncated = bool(Limit is not None and len(matches) > Limit)
        selected = matches[:Limit] if Limit is not None else matches
        response = {"Items": selected}
        if truncated and selected:
            response["LastEvaluatedKey"] = {
                "PK": selected[-1]["PK"],
                "SK": selected[-1]["SK"],
            }
        return response

    def delete(self, pk: str, sk: str):
        self.items.pop((pk, sk), None)


class FakeHistory:
    def __init__(self, table: FakeTable, pulls):
        self.PULLS = table
        self.pulls = list(pulls)
        self.persisted_manifest_keys = set()
        self.provider_manifest_read_count = 0

    def _persist_provider_manifest_once(self, pull):
        manifest = pull.get("provider_schedule_manifest")
        if not isinstance(manifest, dict):
            return
        manifest_key = history_contract._provider_manifest_key(manifest)
        item_key = (manifest_key["PK"], manifest_key["SK"])
        if item_key in self.persisted_manifest_keys:
            return
        self.PULLS.items.setdefault(
            item_key,
            {
                **manifest_key,
                "record_type": history_contract.PROVIDER_MANIFEST_RECORD_TYPE,
                "sport": "mlb",
                "slate_date": pull["slate_date"],
                "pulled_at": pull["pulled_at"],
                "pull_id": pull["pull_id"],
                "manifest_version": history_contract.PROVIDER_MANIFEST_VERSION,
                "manifest_fingerprint": manifest["fingerprint"],
                "write_once": True,
                "data": copy.deepcopy(manifest),
                "created_at": pull["pulled_at"],
            },
        )
        self.persisted_manifest_keys.add(item_key)

    def query_pulls(self, sport, date=None, limit=500):
        assert sport == "mlb"
        assert date == SLATE
        for pull in self.pulls[:limit]:
            self._persist_provider_manifest_once(pull)
            key = (
                f"PULLS#mlb#{pull['slate_date']}",
                f"PULL#{pull['pulled_at']}#{pull['pull_id']}",
            )
            if key not in self.PULLS.items:
                self.PULLS.items[key] = {
                    "PK": key[0],
                    "SK": key[1],
                    "record_type": "pull_run",
                    "sport": "mlb",
                    "slate_date": pull["slate_date"],
                    "pulled_at": pull["pulled_at"],
                    "pull_id": pull["pull_id"],
                    "data": copy.deepcopy(pull),
                }
        return copy.deepcopy(self.pulls[:limit])

    def provider_manifest_authority_for_lock(self, pull, slate, expected_games):
        original_table = history_contract.PULLS
        history_contract.PULLS = self.PULLS
        try:
            return history_contract.provider_manifest_authority_for_lock(
                pull,
                slate,
                expected_games,
            )
        finally:
            history_contract.PULLS = original_table

    def provider_manifest_games_for_lock(self, pull, slate):
        self.provider_manifest_read_count += 1
        original_table = history_contract.PULLS
        history_contract.PULLS = self.PULLS
        try:
            return history_contract.provider_manifest_games_for_lock(pull, slate)
        finally:
            history_contract.PULLS = original_table

    @staticmethod
    def validate_provider_schedule_manifest(
        pull,
        slate,
        *,
        verify_immutable_storage=False,
    ):
        return history_contract.validate_provider_schedule_manifest(
            pull,
            slate,
            verify_immutable_storage=verify_immutable_storage,
        )

    @staticmethod
    def ddb_safe(value):
        return copy.deepcopy(value)


class FakeEngine:
    MODEL_VERSION = "fake-engine-v1"

    def __init__(self, history: FakeHistory, *, vectorless: bool = False, tampered_provenance: bool = False):
        self.history = history
        self.vectorless = vectorless
        self.tampered_provenance = tampered_provenance
        self.canonical_new_writes = 0
        self.canonical_calls = 0
        self.canonical_failures_remaining = 0
        self.pull_to_add_during_prediction = None
        self.prediction_calls = 0

    def prediction_row(self, game, source):
        slate = source.get("slate_date") or SLATE
        game = copy.deepcopy(game)
        start = dt(game["commence_time"])
        lock_at = start - timedelta(minutes=45)
        selected_odds = game["books"]["fanduel"]["ml"]["home"]
        identity = game.get("game_id") or patch.game_identity(game)
        row = {
            "sport": "mlb",
            "slate_date": slate,
            "slateDateEt": slate,
            "gameId": identity,
            "gameIdentity": identity,
            "gameKey": game.get("game_key"),
            "commenceTime": game["commence_time"],
            "homeTeam": game["home_team"],
            "awayTeam": game["away_team"],
            "predictedWinner": game["home_team"],
            "predictedSide": "home",
            "opponent": game["away_team"],
            "americanOdds": selected_odds,
            "lockedAmericanOdds": selected_odds,
            "priceBook": "fanduel",
            "priceSource": "real_book",
            "score": 64.0,
            "winProbability": 0.56,
            "winProbabilityPct": 56.0,
            "teamWinProbabilityPct": 56.0,
            "winProbabilityMeaning": "estimated_probability_selected_team_wins_game",
            "probabilitySemanticsFixed": True,
            "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1-test",
            "promoted": True,
            "promotionStatus": "PROMOTED",
            "homeSignal": {
                "probLatest": 0.56,
                "probStart": 0.53,
                "delta": 0.03,
                "score": 64.0,
                "americanOdds": selected_odds,
                "priceBook": "fanduel",
                "priceSource": "real_book",
                "bookDivergence": 0.01,
                "reversalCount": 0,
                "tags": ["BOOK_AGREEMENT"],
            },
            "awaySignal": {
                "probLatest": 0.44,
                "probStart": 0.47,
                "delta": -0.03,
                "score": 48.0,
                "americanOdds": game["books"]["fanduel"]["ml"]["away"],
                "priceBook": "fanduel",
                "priceSource": "real_book",
                "bookDivergence": 0.01,
                "reversalCount": 0,
                "tags": ["BOOK_AGREEMENT"],
            },
            "slatePredictionLock": {
                "locked": True,
                "finalLocked": True,
                "phase": "SLATE_LOCKED",
                "lockAtUtc": lock_at.isoformat(),
                "latestScoringPullAt": source["pulled_at"],
            },
            "lockedAtUtc": lock_at.isoformat(),
            "predictionSourcePullAt": source["pulled_at"],
            "predictionSourcePullId": source["pull_id"],
            "lockedPrediction": True,
            "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
            "slateCoverage": {"coverageComplete": True},
            "lockedCardAudit": {
                "lockedFlag": True,
                "lockAtUtc": lock_at.isoformat(),
                "explicitSourceAtUtc": source["pulled_at"],
                "preventsLateRows": True,
            },
            "featureVectorFrozenAtLock": True,
            "tags": ["FINAL_LOCKED", "SLATE_LOCKED"],
        }
        attach_lock_safe_features(row)
        if self.tampered_provenance:
            after_source = (dt(source["pulled_at"]) + timedelta(minutes=1)).isoformat()
            row["homeSignal"]["temporalFeatures"]["asOfUtc"] = after_source
            row["fundamentalsSnapshot"]["asOfUtc"] = after_source
        if not self.vectorless:
            vector = cohort.freeze_feature_snapshot(row)
            row["frozenFeatureVector"] = vector
            row["frozenFeatureVectorVersion"] = vector["version"]
            row["mlFeatureFreeze"] = {
                "applied": True,
                "completeSlateCoverage": True,
                "exactVectorCreated": True,
                "trainingEligible": True,
                "trainingExclusionReasons": [],
            }
        row["createdAt"] = source["pulled_at"]
        return row

    def predict_all(self, slate, store=False, limit=500):
        self.prediction_calls += 1
        raise AssertionError("the lock path must never run prediction scoring")

    def _store_prediction(self, row):
        self.canonical_calls += 1
        if self.canonical_failures_remaining:
            self.canonical_failures_remaining -= 1
            raise RuntimeError("injected canonical write failure")
        import mlb_daily_lock_ml_vector_preservation_patch as contract

        errors = contract.validate_exact_locked_row(row)
        if errors:
            raise RuntimeError("invalid canonical row: " + ",".join(errors))
        key = (
            f"GAME_WINNERS#mlb#{row['slate_date']}",
            f"LOCKED#GAME#{row['commenceTime']}#{row['gameIdentity']}",
        )
        existing = self.history.PULLS.items.get(key)
        if existing:
            current = existing["data"]
            if current["frozenFeatureVector"]["fingerprint"] != row["frozenFeatureVector"]["fingerprint"]:
                raise RuntimeError("immutable vector collision")
            created = False
        else:
            self.history.PULLS.items[key] = {
                "PK": key[0],
                "SK": key[1],
                "record_type": "mlb_immutable_locked_single_game_prediction",
                "data": copy.deepcopy(row),
            }
            self.canonical_new_writes += 1
            created = True
        return {
            "ok": True,
            "pk": key[0],
            "sk": key[1],
            "storageClass": "LOCKED_IMMUTABLE",
            "writeOnce": True,
            "exactVectorVerified": True,
            "created": created,
        }


class FakeModule:
    LOCK_MINUTES = 45
    MIN_PULLS_PER_GAME_FOR_LOCK = 1
    MAX_LATEST_PULL_AGE_MINUTES = 20
    EASTERN = timezone.utc
    timedelta = timedelta

    def __init__(self, pulls, now: str, *, vectorless: bool = False, tampered_provenance: bool = False):
        self.TABLE = FakeTable()
        self.history = FakeHistory(self.TABLE, pulls)
        self.mlb_game_winner_engine = FakeEngine(
            self.history,
            vectorless=vectorless,
            tampered_provenance=tampered_provenance,
        )
        self.now = dt(now)
        self.MODEL_VERSION = "legacy"
        self.LOCK_POLICY = "legacy"

    @staticmethod
    def _parse_dt(value):
        try:
            return dt(str(value)) if value else None
        except Exception:
            return None

    @staticmethod
    def _lock_pk(slate):
        return f"LOCKED_PICKS#mlb#{slate}"

    @staticmethod
    def _lock_sk():
        return "DAILY_LOCK#TMINUS45"

    def _get_lock_item(self, slate):
        return self.TABLE.get_item(Key={"PK": self._lock_pk(slate), "SK": self._lock_sk()}, ConsistentRead=True).get("Item")

    @staticmethod
    def _lock_response(item):
        if not item:
            return None
        return {
            "locked": True,
            "gameCount": item.get("game_count"),
            "predictionCount": item.get("prediction_count"),
            "picks": (item.get("data") or {}).get("picks") or [],
        }

    def _pulls_for_date(self, slate):
        return self.history.query_pulls("mlb", slate, 500)

    def _latest_games_for_date(self, slate, pulls):
        latest = {}
        for pull in pulls:
            for game in pull.get("games") or []:
                latest[patch.game_identity(game)] = copy.deepcopy(game)
        return sorted(latest.values(), key=lambda game: game["commence_time"])

    @staticmethod
    def _game_date_et(game):
        return str(game.get("commence_time") or game.get("commenceTime") or "")[:10]

    @staticmethod
    def _sort_picks(rows):
        return sorted(rows, key=lambda row: row["commenceTime"])

    @staticmethod
    def _compact_pick(row):
        return copy.deepcopy(row)

    def _now_utc(self):
        return self.now

    @staticmethod
    def _today_et():
        return SLATE


def game(game_id: str, start: str):
    return {
        "game_id": game_id,
        "game_key": f"mlb-{game_id}",
        "commence_time": start,
        "home_team": f"Home {game_id}",
        "away_team": f"Away {game_id}",
        "books": {"fanduel": {"ml": {"home": -125, "away": 115}}},
    }


G1 = game("g1", "2026-07-13T18:00:00+00:00")
G2 = game("g2", "2026-07-13T20:00:00+00:00")
G3 = game("g3", "2026-07-13T22:00:00+00:00")


def pull(at: str, games, suffix: str):
    pull_id = f"pull-{suffix}"
    games = copy.deepcopy(games)
    manifest = history_contract._build_provider_schedule_manifest(
        sport="mlb",
        slate=SLATE,
        pulled_at=at,
        pull_id=pull_id,
        source="test_provider",
        games=games,
    )
    # Preserve the shared game-key fallback exercised by the legacy no-ID
    # regression. Odds API production rows carry provider IDs.
    for index, source_game in enumerate(games):
        if source_game.get("game_id") or source_game.get("id"):
            continue
        manifest["games"][index]["game_id"] = None
        manifest["games"][index]["id"] = None
    manifest["gameIdentities"] = [
        history_contract.provider_game_identity("mlb", game)
        for game in manifest["games"]
    ]
    manifest["fingerprint"] = history_contract.provider_manifest_fingerprint(manifest)
    key = history_contract._provider_manifest_key(manifest)
    return {
        "pull_id": pull_id,
        "sport": "mlb",
        "source": "test_provider",
        "pulled_at": at,
        "slate_date": SLATE,
        "games": games,
        "provider_schedule_manifest": manifest,
        "provider_manifest_binding": {
            "version": history_contract.PROVIDER_MANIFEST_VERSION,
            "fingerprint": manifest["fingerprint"],
            "gameCount": manifest["gameCount"],
            "pk": key["PK"],
            "sk": key["SK"],
            "immutable": True,
            "fullProviderSchedule": True,
        },
    }


EARLY_PULLS = [
    pull("2026-07-13T17:00:00+00:00", [G1, G2], "1700"),
    pull("2026-07-13T17:15:00+00:00", [G1, G2], "1715"),
]
LATE_PULLS = [
    pull("2026-07-13T19:00:00+00:00", [G2], "1900"),
    pull("2026-07-13T19:15:00+00:00", [G2], "1915"),
]


def staged_items(module):
    return [item for item in module.TABLE.items.values() if item.get("record_type") == patch.STAGE_RECORD_TYPE]


def diagnostic_items(module):
    return [
        item
        for item in module.TABLE.items.values()
        if item.get("record_type") in {
            patch.ATTEMPT_RECORD_TYPE,
            patch.ATTEMPT_OUTCOME_RECORD_TYPE,
        }
    ]


def daily_item(module):
    return module.TABLE.items.get((module._lock_pk(SLATE), module._lock_sk()))


def persist_candidate(module, game_row, source_pull, mutate=None, persisted_at=None):
    row = module.mlb_game_winner_engine.prediction_row(game_row, source_pull)
    if mutate is not None:
        mutate(row)
    identity = row["gameIdentity"]
    created_at = row["createdAt"]
    persisted_at = persisted_at or created_at
    digest = patch._payload_fingerprint({
        "identity": identity,
        "createdAt": created_at,
        "source": row["predictionSourcePullAt"],
        "winner": row["predictedWinner"],
    })[:20]
    pk = f"GAME_WINNERS#mlb#{SLATE}"
    snapshot = {
        "PK": pk,
        "SK": f"PREGAME#GAME#{identity}#PERSISTED#{persisted_at}#CREATED#{created_at}#{digest}",
        "record_type": patch.PREGAME_SNAPSHOT_RECORD_TYPE,
        "snapshot_version": patch.PREGAME_SNAPSHOT_VERSION,
        "slate_date": SLATE,
        "game_id": row["gameId"],
        "game_identity": identity,
        "commence_time": row["commenceTime"],
        "prediction_created_at_utc": created_at,
        "prediction_persisted_at_utc": persisted_at,
        "prediction_persistence_proof_type": patch.PREGAME_PERSISTENCE_PROOF_TYPE,
        "prediction_persistence_write_pk": pk,
        "prediction_persistence_write_sk": f"GAME#{row['commenceTime']}#{identity}",
        "prediction_payload_fingerprint_version": patch.PAYLOAD_FINGERPRINT_VERSION,
        "prediction_payload_fingerprint": patch._payload_fingerprint(row),
        "prediction_source_pull_at_utc": row["predictionSourcePullAt"],
        "prediction_source_pull_id": row["predictionSourcePullId"],
        "immutable_pregame": True,
        "write_once": True,
        "data": copy.deepcopy(row),
        "created_at": created_at,
    }
    module.TABLE.put_item(
        Item=snapshot,
        ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
    )
    module.TABLE.put_item(Item={
        "PK": pk,
        "SK": f"GAME#{row['commenceTime']}#{identity}",
        "record_type": patch.LIVE_PREDICTION_RECORD_TYPE,
        "slate_date": SLATE,
        "game_id": row["gameId"],
        "game_identity": identity,
        "created_at": created_at,
        "data": copy.deepcopy(row),
    })
    return row


def persist_candidate_with_production_writer(module, game_row, source_pull):
    import mlb_game_winner_engine as production_writer

    row = module.mlb_game_winner_engine.prediction_row(game_row, source_pull)
    snapshot = production_writer._pregame_snapshot_item(
        row,
        persisted_at=row["createdAt"],
    )
    module.TABLE.put_item(
        Item=snapshot,
        ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
    )
    return row, snapshot


def persist_latest_prelock_candidates(module, pulls):
    latest = {}
    for source in pulls:
        source_at = dt(source["pulled_at"])
        for game_row in source.get("games") or []:
            cutoff = dt(game_row["commence_time"]) - timedelta(minutes=45)
            if source_at <= cutoff:
                latest[game_row["game_id"]] = (game_row, source)
    return {
        game_id: persist_candidate(module, game_row, source)
        for game_id, (game_row, source) in latest.items()
    }


def build_module(pulls, now, *, vectorless=False, tampered_provenance=False, seed=True):
    module = FakeModule(
        pulls,
        now,
        vectorless=vectorless,
        tampered_provenance=tampered_provenance,
    )
    patch.apply(module)
    if seed:
        persist_latest_prelock_candidates(module, pulls)
    return module


def test_complete_slate_union_reads_back_only_full_authority_and_latest_feed():
    historical = [
        pull(
            (dt("2026-07-13T16:00:00+00:00") + timedelta(minutes=index)).isoformat(),
            [G1, G2],
            f"history-{index}",
        )
        for index in range(92)
    ]
    historical.append(
        pull("2026-07-13T18:01:00+00:00", [G2], "latest-contracted")
    )
    module = FakeModule(historical, "2026-07-13T18:02:00+00:00")
    persisted = module._pulls_for_date(SLATE)

    games = coverage_patch._latest_games(module, SLATE, persisted)

    assert [game["game_id"] for game in games] == ["g1", "g2"]
    assert module.history.provider_manifest_read_count == 2


def test_manual_pull_timestamp_is_captured_after_provider_response():
    source = (HELLO_WORLD / "mlb_manual_pull.py").read_text(encoding="utf-8")
    start = source.index("def _fetch_odds_with_completion_timestamp")
    end = source.index("\n\ndef lambda_handler", start)
    helper = source[start:end]

    assert helper.index("_http_get_json(_odds_url())") < helper.index("_now_iso()")
    assert "raw_all, asof = _fetch_odds_with_completion_timestamp()" in source


def test_cutoff_waits_full_120_seconds_without_changing_scheduled_lock():
    module = build_module(EARLY_PULLS, "2026-07-13T17:16:59+00:00")

    result = module.run_lock(SLATE)

    assert result["reason"] == "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER"
    assert not staged_items(module)
    g1_status = next(row for row in result["perGameLockProgress"]["games"] if row["gameId"] == "g1")
    assert g1_status["state"] == "WAITING_FOR_CUTOFF_STABILIZATION"
    assert g1_status["scheduledLockAtUtc"] == "2026-07-13T17:15:00+00:00"
    assert g1_status["sourceWindowStableAtUtc"] == "2026-07-13T17:17:00+00:00"
    assert module.mlb_game_winner_engine.canonical_new_writes == 0


def test_newer_cutoff_pull_arriving_during_grace_wins_source_window():
    initial = [
        pull("2026-07-13T17:00:00+00:00", [G1], "1700-one"),
        pull("2026-07-13T17:14:00+00:00", [G1], "1714-one"),
    ]
    module = build_module(initial, "2026-07-13T17:16:00+00:00")
    waiting = module.run_lock(SLATE)
    assert waiting["perGameLockProgress"]["stabilizingCount"] == 1
    assert not staged_items(module)

    newest = pull("2026-07-13T17:15:00+00:00", [G1], "1715-one")
    module.history.pulls.append(newest)
    persist_candidate(module, G1, newest)
    module.now = dt("2026-07-13T17:17:00+00:00")
    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    assert stage["source_pull_id"] == "pull-1715-one"
    assert stage["source_pull_at_utc"] == "2026-07-13T17:15:00+00:00"


def test_response_completed_after_cutoff_is_not_in_bound_source_window():
    pulls = [
        pull("2026-07-13T17:14:00+00:00", [G1], "1714-before"),
        pull("2026-07-13T17:15:01+00:00", [G1], "171501-after"),
    ]
    module = build_module(pulls, "2026-07-13T17:17:00+00:00")

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    assert stage["source_pull_id"] == "pull-1714-before"
    assert [entry["pullId"] for entry in stage["source_window"]["pulls"]] == ["pull-1714-before"]


def test_newer_cutoff_pull_without_a_persisted_prediction_does_not_rescore():
    initial = [pull("2026-07-13T17:14:00+00:00", [G1], "1714-race")]
    module = build_module(initial, "2026-07-13T17:17:00+00:00")
    module.history.pulls.append(pull(
        "2026-07-13T17:15:00+00:00", [G1], "1715-race"
    ))

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    assert stage["source_pull_id"] == "pull-1714-race"
    assert stage["pull_depth"] == 1
    assert [entry["pullId"] for entry in stage["source_window"]["pulls"]] == [
        "pull-1714-race",
    ]
    assert module.mlb_game_winner_engine.prediction_calls == 0


def test_candidate_source_id_disambiguates_equal_timestamp_pulls():
    selected = pull("2026-07-13T17:15:00+00:00", [G1], "selected-source")
    other = pull("2026-07-13T17:15:00+00:00", [G1], "other-source")
    other["games"][0]["books"]["fanduel"]["ml"]["home"] = -999
    module = build_module(
        [selected, other],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    persist_candidate(module, G1, selected)

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    assert stage["source_pull_id"] == "pull-selected-source"
    assert stage["candidate_proof"]["predictionSourcePullId"] == "pull-selected-source"
    assert [entry["pullId"] for entry in stage["source_window"]["pulls"]] == [
        "pull-selected-source",
    ]


def test_exact_last_persisted_prelock_selection_becomes_final_lock():
    first = pull("2026-07-13T17:14:00+00:00", [G1], "first-candidate")
    module = build_module([first], "2026-07-13T17:17:00+00:00")
    last = pull("2026-07-13T17:15:00+00:00", [G1], "last-candidate")
    module.history.pulls.append(last)

    def select_away(row):
        row.update({
            "predictedWinner": G1["away_team"],
            "predictedSide": "away",
            "opponent": G1["home_team"],
            "americanOdds": G1["books"]["fanduel"]["ml"]["away"],
            "lockedAmericanOdds": G1["books"]["fanduel"]["ml"]["away"],
            "score": 77.25,
            "winProbability": 0.6125,
            "winProbabilityPct": 61.25,
            "teamWinProbabilityPct": 61.25,
            "edgeVsBook": 0.031,
            "expectedValue": 0.044,
        })

    candidate = persist_candidate(module, G1, last, mutate=select_away)
    expected_selection = patch._selection_material(candidate)

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    locked = stage["data"]["row"]
    assert patch._selection_material(locked) == expected_selection
    assert locked["predictedWinner"] == G1["away_team"]
    assert locked["predictedSide"] == "away"
    assert locked["americanOdds"] == 115
    assert locked["score"] == 77.25
    assert locked["modelOrSignalRecomputedAtLock"] is False
    assert stage["candidate_proof"]["predictionSourcePullId"] == "pull-last-candidate"
    assert stage["candidate_proof"]["candidateSelectionFingerprint"] == locked["lastPrelockSelectionFingerprint"]
    assert module.mlb_game_winner_engine.prediction_calls == 0


def test_persisted_probability_alias_is_promoted_without_rescore():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "legacy-probability-alias")
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )

    candidate = persist_candidate(
        module,
        G1,
        source,
        mutate=lambda row: row.pop("teamWinProbabilityPct", None),
    )
    expected_selection = patch._selection_material(candidate)

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    locked = stage["data"]["row"]
    assert locked["teamWinProbabilityPct"] == candidate["winProbabilityPct"]
    assert patch._selection_material(locked) == expected_selection
    assert locked["modelOrSignalRecomputedAtLock"] is False
    assert module.mlb_game_winner_engine.prediction_calls == 0


def test_production_ddb_normalized_candidate_promotes_without_cutoff_rescore():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "production-ddb")
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    candidate, snapshot = persist_candidate_with_production_writer(module, G1, source)

    # The actual writer converts integral floats to Decimal before persisting.
    # This is the representation that triggered the production hash mismatch.
    assert snapshot["data"]["score"] == Decimal("64.0")
    assert snapshot["prediction_payload_fingerprint_version"] == (
        patch.PAYLOAD_FINGERPRINT_VERSION
    )

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    locked = stage["data"]["row"]
    assert locked["predictedWinner"] == candidate["predictedWinner"]
    assert locked["predictedSide"] == candidate["predictedSide"]
    assert stage["source_pull_id"] == source["pull_id"]
    assert stage["source_pull_at_utc"] == source["pulled_at"]
    assert stage["candidate_proof"]["predictionPayloadFingerprintVersion"] == (
        patch.PAYLOAD_FINGERPRINT_VERSION
    )
    assert module.mlb_game_winner_engine.prediction_calls == 0
    assert module.mlb_game_winner_engine.canonical_new_writes == 1


def test_ddb_normalized_candidate_payload_tamper_fails_closed():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "production-ddb-tamper")
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    _, snapshot = persist_candidate_with_production_writer(module, G1, source)
    stored_snapshot = module.TABLE.items[(snapshot["PK"], snapshot["SK"])]
    stored_snapshot["data"]["americanOdds"] = Decimal("-124")

    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert "persisted_prelock_payload_fingerprint_mismatch" in str(result["failures"])
    assert not staged_items(module)
    assert module.mlb_game_winner_engine.canonical_new_writes == 0


def test_unknown_candidate_payload_fingerprint_version_fails_closed():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "unknown-hash-version")
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    _, snapshot = persist_candidate_with_production_writer(module, G1, source)
    stored_snapshot = module.TABLE.items[(snapshot["PK"], snapshot["SK"])]
    stored_snapshot["prediction_payload_fingerprint_version"] = "UNKNOWN-FINGERPRINT-v99"

    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert "persisted_prelock_payload_fingerprint_version_unsupported" in str(
        result["failures"]
    )
    assert not staged_items(module)


def test_falsy_invalid_candidate_payload_fingerprint_version_fails_closed():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "falsy-hash-version")
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    _, snapshot = persist_candidate_with_production_writer(module, G1, source)
    stored_snapshot = module.TABLE.items[(snapshot["PK"], snapshot["SK"])]
    stored_snapshot["prediction_payload_fingerprint_version"] = 0

    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert "persisted_prelock_payload_fingerprint_version_unsupported" in str(
        result["failures"]
    )
    assert not staged_items(module)


def test_legacy_unversioned_candidate_with_matching_persisted_hash_remains_valid():
    import inqsi_pull_history as history_contract

    source = pull("2026-07-13T17:15:00+00:00", [G1], "legacy-unversioned")
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    persist_candidate(module, G1, source)
    snapshot = next(
        item
        for item in module.TABLE.items.values()
        if item.get("record_type") == patch.PREGAME_SNAPSHOT_RECORD_TYPE
    )
    snapshot.pop("prediction_payload_fingerprint_version")
    snapshot["prediction_payload_fingerprint"] = (
        history_contract.legacy_payload_fingerprint(snapshot["data"])
    )

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    assert staged_items(module)[0]["candidate_proof"].get(
        "predictionPayloadFingerprintVersion"
    ) is None


def test_missing_or_post_cutoff_candidate_fails_closed_without_stage():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "source-only")
    missing = build_module([source], "2026-07-13T17:17:00+00:00", seed=False)

    missing_result = missing.run_lock(SLATE)

    assert missing_result["ok"] is False
    assert "no_persisted_prelock_prediction" in str(missing_result["failures"])
    assert not staged_items(missing)

    after = pull("2026-07-13T17:15:01+00:00", [G1], "after-cutoff")
    post_cutoff = build_module(
        [source, after],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    persist_candidate(post_cutoff, G1, after)

    after_result = post_cutoff.run_lock(SLATE)

    assert after_result["ok"] is False
    assert "no_persisted_prelock_prediction" in str(after_result["failures"])
    assert not staged_items(post_cutoff)
    assert post_cutoff.mlb_game_winner_engine.prediction_calls == 0


def test_backdated_prediction_persisted_after_cutoff_is_not_authoritative():
    source = pull("2026-07-13T17:14:00+00:00", [G1], "backdated-source")
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    persist_candidate(
        module,
        G1,
        source,
        persisted_at="2026-07-13T17:15:01+00:00",
    )

    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert "no_persisted_prelock_prediction" in str(result["failures"])


def test_client_timestamp_without_post_write_ack_proof_is_not_authoritative():
    source = pull("2026-07-13T17:14:00+00:00", [G1], "unproven-client-time")
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    persist_candidate(module, G1, source)
    snapshot = next(
        item for item in module.TABLE.items.values()
        if item.get("record_type") == patch.PREGAME_SNAPSHOT_RECORD_TYPE
    )
    snapshot.pop("prediction_persistence_proof_type")

    result = module.run_lock(SLATE)

    assert result["locked"] is False
    assert "persisted_prelock_post_write_ack_missing" in str(result["failures"])
    assert not staged_items(module)
    assert not staged_items(module)


def test_newest_invalid_candidate_falls_back_to_last_valid_candidate():
    valid_pull = pull("2026-07-13T17:14:00+00:00", [G1], "valid-candidate")
    invalid_pull = pull("2026-07-13T17:15:00+00:00", [G1], "invalid-candidate")
    module = build_module(
        [valid_pull, invalid_pull],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    valid = persist_candidate(module, G1, valid_pull)

    def remove_price(row):
        row["americanOdds"] = None
        row["lockedAmericanOdds"] = None
        row["homeSignal"]["americanOdds"] = None
        row["priceBook"] = None
        row["homeSignal"]["priceBook"] = None

    persist_candidate(module, G1, invalid_pull, mutate=remove_price)

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    assert stage["source_pull_id"] == "pull-valid-candidate"
    assert stage["data"]["row"]["predictedWinner"] == valid["predictedWinner"]
    proof = stage["candidate_proof"]
    assert proof["rejectedNewerCandidateCount"] == 1
    assert "persisted_prelock_selected_side_real_book_price_missing" in (
        proof["rejectedNewerCandidates"][0]["errors"]
    )


def test_no_provider_id_uses_shared_fallback_identity_for_candidate_lookup():
    game = copy.deepcopy(G1)
    game.pop("game_id", None)
    game["game_key"] = "mlb|fallback-doubleheader-safe"
    source = pull("2026-07-13T17:15:00+00:00", [game], "fallback-identity")
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
        seed=False,
    )
    candidate = persist_candidate(module, game, source)

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    assert stage["game_identity"].startswith("key:")
    assert stage["data"]["row"]["gameIdentity"] == candidate["gameIdentity"]
    assert stage["source_pull_id"] == "pull-fallback-identity"


def test_schedule_shift_promotes_same_game_candidate_from_old_commence_key():
    old = copy.deepcopy(G1)
    shifted = copy.deepcopy(G1)
    shifted["commence_time"] = "2026-07-13T18:10:00+00:00"
    old_pull = pull("2026-07-13T17:15:00+00:00", [old], "old-start")
    shifted_pull = pull("2026-07-13T17:20:00+00:00", [shifted], "shifted-start")
    module = build_module(
        [old_pull, shifted_pull],
        "2026-07-13T17:27:00+00:00",
        seed=False,
    )
    candidate = persist_candidate(module, old, old_pull)

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    stage = staged_items(module)[0]
    assert stage["candidate_proof"]["sk"].find(old["commence_time"]) == -1
    assert stage["data"]["row"]["commenceTime"] == shifted["commence_time"]
    assert stage["data"]["row"]["predictedWinner"] == candidate["predictedWinner"]
    assert stage["source_pull_id"] == "pull-old-start"
    assert module.mlb_game_winner_engine.prediction_calls == 0


def test_late_backfill_does_not_invalidate_stage_or_block_canonical_retry():
    initial = [pull("2026-07-13T17:14:00+00:00", [G1], "1714-retry")]
    module = build_module(initial, "2026-07-13T17:17:00+00:00")
    module.mlb_game_winner_engine.canonical_failures_remaining = 2

    first = module.run_lock(SLATE)
    assert first["ok"] is False
    assert first["reason"] == "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL"
    assert len(staged_items(module)) == 1
    assert module.mlb_game_winner_engine.canonical_new_writes == 0

    module.history.pulls.append(pull("2026-07-13T17:15:00+00:00", [G1], "1715-late-backfill"))
    second = module.run_lock(SLATE)

    assert second["locked"] is True
    assert module.mlb_game_winner_engine.canonical_new_writes == 1
    status = second["perGameLockProgress"]["games"][0]
    assert status["state"] == "LOCKED_CANONICAL"
    assert status["lateBackfillDetected"] is True
    assert status["lateBackfillPullCount"] == 1
    stage = staged_items(module)[0]
    assert stage["source_pull_id"] == "pull-1714-retry"


def test_bound_pull_mutation_invalidates_stage_fail_closed():
    module = build_module(EARLY_PULLS, "2026-07-13T17:17:00+00:00")
    module.run_lock(SLATE)
    assert len(staged_items(module)) == 1

    module.history.pulls[-1]["games"][0]["books"]["fanduel"]["ml"]["home"] = -999
    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["reason"] == "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL"
    errors = result["perGameLockProgress"]["games"][0]["errors"]
    assert "bound_source_window_pull_missing_or_changed" in errors
    assert daily_item(module) is None


def test_first_game_canonical_at_own_cutoff_while_later_game_pending():
    module = build_module(EARLY_PULLS, "2026-07-13T17:17:00+00:00")

    result = module.run_lock(SLATE)

    assert result["reason"] == "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER"
    assert result["perGameLockProgress"]["stagedCount"] == 1
    assert result["perGameLockProgress"]["canonicalCount"] == 1
    assert module.mlb_game_winner_engine.canonical_new_writes == 1
    stage = staged_items(module)[0]
    assert stage["game_id"] == "g1"
    assert stage["scheduled_lock_at_utc"] == "2026-07-13T17:15:00+00:00"
    assert stage["staged_at_utc"] == "2026-07-13T17:17:00+00:00"
    assert stage["source_pull_at_utc"] == "2026-07-13T17:15:00+00:00"
    assert stage["source_window"]["stabilizationSeconds"] == 120
    assert stage["source_window"]["scheduledCutoffAtUtc"] == "2026-07-13T17:15:00+00:00"
    assert stage["source_window"]["pulls"][-1]["pullId"] == "pull-1715"
    assert daily_item(module) is None
    diagnostics = diagnostic_items(module)
    assert len(diagnostics) == 2
    assert {item["diagnostic_event"] for item in diagnostics} == {
        "ATTEMPT_STARTED",
        "ATTEMPT_OUTCOME",
    }
    outcome = next(item for item in diagnostics if item["diagnostic_event"] == "ATTEMPT_OUTCOME")
    assert outcome["outcome"] == "LOCKED_CANONICAL"
    assert outcome["canonical_proven_after_attempt"] is True
    assert result["perGameLockAttemptDiagnostics"]["attemptedGameCount"] == 1
    assert result["perGameLockAttemptDiagnostics"]["attempts"][0]["outcome"] == "LOCKED_CANONICAL"
    diagnostic_keys = {(item["PK"], item["SK"]) for item in diagnostics}
    diagnostic_puts = [
        request for request in module.TABLE.put_requests
        if request["key"] in diagnostic_keys
    ]
    assert len(diagnostic_puts) == 2
    assert all(
        request["condition"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"
        for request in diagnostic_puts
    )


def test_later_game_uses_newer_own_cutoff_pull_and_only_then_finalizes_daily_card():
    module = build_module(EARLY_PULLS, "2026-07-13T17:17:00+00:00")
    module.run_lock(SLATE)
    assert daily_item(module) is None

    module.history.pulls.extend(LATE_PULLS)
    persist_latest_prelock_candidates(module, LATE_PULLS)
    module.now = dt("2026-07-13T19:17:00+00:00")
    result = module.run_lock(SLATE)

    assert result["locked"] is True
    assert module.mlb_game_winner_engine.canonical_new_writes == 2
    stages = {item["game_id"]: item for item in staged_items(module)}
    assert stages["g1"]["source_pull_at_utc"] == "2026-07-13T17:15:00+00:00"
    assert stages["g2"]["source_pull_at_utc"] == "2026-07-13T19:15:00+00:00"
    assert stages["g2"]["scheduled_lock_at_utc"] == "2026-07-13T19:15:00+00:00"
    card = daily_item(module)
    assert card["per_game_lock"] is True
    assert card["canonical_immutable_game_row_count"] == 2
    assert len(card["data"]["perGameLockProof"]) == 2


def _fifteen_game_contracted_feed_slate():
    games = [game("contracted-01", "2026-07-13T18:00:00+00:00")]
    games.extend(
        game(f"contracted-{index:02d}", "2026-07-13T20:00:00+00:00")
        for index in range(2, 16)
    )
    return games


def _manifest_item_key(provider_pull):
    key = history_contract._provider_manifest_key(
        provider_pull["provider_schedule_manifest"]
    )
    return key["PK"], key["SK"]


def test_stage_membership_uses_immutable_manifest_order_for_same_start_games():
    first = game("same-start-a", "2026-07-13T18:00:00+00:00")
    second = game("same-start-b", "2026-07-13T18:00:00+00:00")
    # Discovery order is intentionally opposite the immutable provider
    # manifest's (commence, provider-id) order.
    source = pull(
        "2026-07-13T17:15:00+00:00",
        [second, first],
        "same-start-shuffled",
    )
    module = build_module(
        [source],
        "2026-07-13T17:17:00+00:00",
    )

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    expected = [
        patch.game_identity(entry)
        for entry in source["provider_schedule_manifest"]["games"]
    ]
    assert expected == ["provider:same-start-a", "provider:same-start-b"]
    for stage in staged_items(module):
        assert stage["provider_manifest_authority"]["canonicalGameIdentities"] == expected
        assert stage["data"]["manifestGameIdentities"] == expected
        assert patch.persisted_stage_authority_errors(module.TABLE, stage) == []


def test_contracted_latest_feed_selects_prior_full_manifest_authority():
    full_slate = _fifteen_game_contracted_feed_slate()
    full_pull = pull(
        "2026-07-13T17:15:00+00:00",
        full_slate,
        "contracted-full-1715",
    )
    module = build_module(
        [full_pull],
        "2026-07-13T17:17:00+00:00",
    )

    first = module.run_lock(SLATE)

    assert first["reason"] == "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER"
    assert first["perGameLockProgress"]["stagedCount"] == 1
    assert first["perGameLockProgress"]["canonicalCount"] == 1
    first_stage = copy.deepcopy(staged_items(module)[0])
    full_manifest_item = copy.deepcopy(
        module.TABLE.items[_manifest_item_key(full_pull)]
    )

    # The provider stops returning a game after it starts.  This later response
    # is valid scoring evidence for the remaining games, but it must not replace
    # the already persisted 15-game full-slate authority.
    remaining_pull = pull(
        "2026-07-13T19:15:00+00:00",
        full_slate[1:],
        "contracted-remaining-1915",
    )
    module.history.pulls.append(remaining_pull)
    persist_latest_prelock_candidates(module, [remaining_pull])
    module.now = dt("2026-07-13T19:17:00+00:00")

    result = module.run_lock(SLATE)

    assert result["locked"] is True
    assert result["perGameLockProgress"]["stagedCount"] == 15
    assert result["perGameLockProgress"]["canonicalCount"] == 15
    assert module.mlb_game_winner_engine.canonical_new_writes == 15
    stages = {item["game_id"]: item for item in staged_items(module)}
    assert len(stages) == 15
    assert stages["contracted-01"] == first_stage
    expected_fingerprint = full_pull["provider_schedule_manifest"]["fingerprint"]
    expected_raw_ids = list(
        full_pull["provider_schedule_manifest"]["gameIdentities"]
    )
    expected_canonical_ids = [patch.game_identity(entry) for entry in full_slate]
    for stage in stages.values():
        authority = stage["provider_manifest_authority"]
        assert authority["fingerprint"] == expected_fingerprint
        assert authority["pk"] == full_pull["provider_manifest_binding"]["pk"]
        assert authority["sk"] == full_pull["provider_manifest_binding"]["sk"]
        assert authority["pullId"] == full_pull["pull_id"]
        assert authority["observedAtUtc"] == full_pull["pulled_at"]
        assert authority["gameCount"] == 15
        assert authority["gameIdentities"] == expected_raw_ids
        assert authority["canonicalGameIdentities"] == expected_canonical_ids
        assert stage["manifest_game_count"] == 15
        assert stage["data"]["manifestGameIdentities"] == expected_canonical_ids

    later_stage = stages["contracted-02"]
    later_row = later_stage["data"]["row"]
    assert later_stage["source_pull_id"] == remaining_pull["pull_id"]
    assert later_stage["source_pull_at_utc"] == remaining_pull["pulled_at"]
    assert later_stage["candidate_proof"]["predictionSourcePullId"] == remaining_pull["pull_id"]
    assert later_stage["candidate_proof"]["predictionSourcePullAtUtc"] == remaining_pull["pulled_at"]
    assert later_stage["source_window"]["pulls"][-1]["pullId"] == remaining_pull["pull_id"]
    assert later_row["predictionSourcePullId"] == remaining_pull["pull_id"]
    assert later_row["predictionSourcePullAt"] == remaining_pull["pulled_at"]
    assert later_row["frozenFeatureVector"]["sourcePullAtUtc"] == remaining_pull["pulled_at"]
    assert later_row["modelOrSignalRecomputedAtLock"] is False
    assert module.mlb_game_winner_engine.prediction_calls == 0

    card = daily_item(module)
    assert card["manifest_game_count"] == 15
    assert card["canonical_immutable_game_row_count"] == 15
    assert card["data"]["manifestGameIdentities"] == expected_canonical_ids
    assert len(card["data"]["perGameLockProof"]) == 15
    assert module.TABLE.items[_manifest_item_key(full_pull)] == full_manifest_item


def test_contracted_feed_future_game_omission_fails_closed_without_shrinking_slate():
    full_slate = _fifteen_game_contracted_feed_slate()
    full_pull = pull(
        "2026-07-13T17:15:00+00:00",
        full_slate,
        "contracted-full-future-omission",
    )
    module = build_module(
        [full_pull],
        "2026-07-13T17:17:00+00:00",
    )
    module.run_lock(SLATE)

    # contracted-01 has started, so its omission is expected. contracted-15 is
    # still future and its omission must remain visible as a missing fresh
    # cutoff source instead of silently contracting the official slate to 14.
    incomplete_remaining = full_slate[1:-1]
    remaining_pull = pull(
        "2026-07-13T19:15:00+00:00",
        incomplete_remaining,
        "contracted-future-omitted-1915",
    )
    module.history.pulls.append(remaining_pull)
    persist_latest_prelock_candidates(module, [remaining_pull])
    module.now = dt("2026-07-13T19:17:00+00:00")

    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["locked"] is False
    assert result["reason"] == "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL"
    assert len(result["perGameLockProgress"]["games"]) == 15
    # The contracted pull itself is rejected as manifest authority because it
    # omitted a still-future game. No game from that pull may advance, even
    # though its other 13 due games are present and fresh.
    assert result["perGameLockProgress"]["stagedCount"] == 1
    assert result["perGameLockProgress"]["canonicalCount"] == 1
    assert result["perGameLockProgress"]["dueMissingCount"] == 14
    missing = next(
        row
        for row in result["perGameLockProgress"]["games"]
        if row["gameId"] == "contracted-15"
    )
    assert missing["state"] == "DUE_NOT_STAGED"
    assert not any(
        item["game_id"] == "contracted-15" for item in staged_items(module)
    )
    assert not any(
        item["game_id"] == "contracted-02" for item in staged_items(module)
    )
    assert any(
        failure.get("gameIdentity") == patch.game_identity(full_slate[1])
        and failure.get("reason") == "PROVIDER_MANIFEST_AUTHORITY_NOT_STAGED"
        for failure in result["failures"]
    )
    assert any(
        failure.get("gameIdentity") == patch.game_identity(full_slate[-1])
        and failure.get("reason") == "STALE_OR_MISSING_CUTOFF_PULL_NOT_STAGED"
        for failure in result["failures"]
    )
    assert daily_item(module) is None


def test_missing_full_manifest_authority_blocks_later_contracted_feed():
    full_slate = _fifteen_game_contracted_feed_slate()
    full_pull = pull(
        "2026-07-13T17:15:00+00:00",
        full_slate,
        "contracted-full-missing-authority",
    )
    module = build_module(
        [full_pull],
        "2026-07-13T17:17:00+00:00",
    )
    module.run_lock(SLATE)
    first_stage = copy.deepcopy(staged_items(module)[0])
    module.TABLE.delete(*_manifest_item_key(full_pull))

    remaining_pull = pull(
        "2026-07-13T19:15:00+00:00",
        full_slate[1:],
        "contracted-remaining-missing-authority",
    )
    module.history.pulls.append(remaining_pull)
    persist_latest_prelock_candidates(module, [remaining_pull])
    module.now = dt("2026-07-13T19:17:00+00:00")

    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["locked"] is False
    assert "immutable_provider_manifest" in str(result)
    assert staged_items(module) == [first_stage]
    assert module.mlb_game_winner_engine.canonical_new_writes == 1
    assert daily_item(module) is None


def test_tampered_full_manifest_authority_blocks_later_contracted_feed():
    full_slate = _fifteen_game_contracted_feed_slate()
    full_pull = pull(
        "2026-07-13T17:15:00+00:00",
        full_slate,
        "contracted-full-tampered-authority",
    )
    module = build_module(
        [full_pull],
        "2026-07-13T17:17:00+00:00",
    )
    module.run_lock(SLATE)
    first_stage = copy.deepcopy(staged_items(module)[0])
    manifest_item = module.TABLE.items[_manifest_item_key(full_pull)]
    manifest_item["data"]["games"][0]["home_team"] = "Tampered Home"

    remaining_pull = pull(
        "2026-07-13T19:15:00+00:00",
        full_slate[1:],
        "contracted-remaining-tampered-authority",
    )
    module.history.pulls.append(remaining_pull)
    persist_latest_prelock_candidates(module, [remaining_pull])
    module.now = dt("2026-07-13T19:17:00+00:00")

    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["locked"] is False
    assert "immutable_provider_manifest_readback_mismatch" in str(result)
    assert staged_items(module) == [first_stage]
    assert module.mlb_game_winner_engine.canonical_new_writes == 1
    assert daily_item(module) is None


def test_no_stage_or_canonical_write_before_due_or_after_game_start():
    before = build_module(EARLY_PULLS, "2026-07-13T17:14:00+00:00")
    result = before.run_lock(SLATE)
    assert result["reason"] == "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER"
    assert not staged_items(before)
    assert before.mlb_game_winner_engine.canonical_new_writes == 0

    missed = build_module([pull("2026-07-13T17:15:00+00:00", [G1], "only")], "2026-07-13T18:01:00+00:00")
    result = missed.run_lock(SLATE, force=True)
    assert result["reason"] == "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"
    assert result["forceIgnoredForSafety"] is True
    assert not staged_items(missed)
    assert missed.mlb_game_winner_engine.canonical_new_writes == 0
    diagnostics = diagnostic_items(missed)
    assert len(diagnostics) == 2
    outcome = next(item for item in diagnostics if item["diagnostic_event"] == "ATTEMPT_OUTCOME")
    assert outcome["outcome"] == "MISSED_NOT_BACKFILLED"
    assert outcome["state_at_attempt"] == "MISSED_NOT_BACKFILLED"
    assert outcome["state_after_attempt"] == "MISSED_NOT_BACKFILLED"
    assert outcome["force_requested"] is True

    first_diagnostic_keys = {(item["PK"], item["SK"]) for item in diagnostics}
    repeated = missed.run_lock(SLATE, force=True)
    assert repeated["reason"] == "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"
    assert {(item["PK"], item["SK"]) for item in diagnostic_items(missed)} == first_diagnostic_keys
    assert repeated["perGameLockAttemptDiagnostics"]["attemptedGameCount"] == 0


def test_manifest_drift_after_first_stage_fails_closed_without_daily_card():
    module = build_module(EARLY_PULLS, "2026-07-13T17:17:00+00:00")
    module.run_lock(SLATE)
    assert module.mlb_game_winner_engine.canonical_new_writes == 1

    module.history.pulls.append(pull("2026-07-13T18:30:00+00:00", [G2, G3], "drift"))
    module.now = dt("2026-07-13T19:17:00+00:00")
    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["reason"] == "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL"
    assert any("manifest_changed_after_game_lock" in row.get("errors", []) for row in result["perGameLockProgress"]["games"])
    assert daily_item(module) is None
    assert module.mlb_game_winner_engine.canonical_new_writes == 1


def test_vectorless_prelock_candidate_is_frozen_at_lock_without_rescoring():
    pulls = [pull("2026-07-13T17:15:00+00:00", [G1], "vectorless")]
    module = build_module(pulls, "2026-07-13T17:17:00+00:00", vectorless=True)
    result = module.run_lock(SLATE)

    assert result["locked"] is True
    row = staged_items(module)[0]["data"]["row"]
    vector = row["frozenFeatureVector"]
    assert vector["fingerprint"] == cohort.fingerprint_for_vector(vector)
    assert vector["labels"] == {"homeWon": None, "pickCorrect": None}
    assert module.mlb_game_winner_engine.prediction_calls == 0


def test_invalid_persisted_candidate_records_failed_attempt_then_can_retry():
    pulls = [pull("2026-07-13T17:15:00+00:00", [G1], "invalid")]
    module = build_module(
        pulls,
        "2026-07-13T17:17:00+00:00",
        tampered_provenance=True,
    )
    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["reason"] == "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL"
    assert not staged_items(module)
    assert module.mlb_game_winner_engine.canonical_new_writes == 0
    assert daily_item(module) is None
    first_records = diagnostic_items(module)
    assert len(first_records) == 2
    first_outcome = next(
        item for item in first_records
        if item["diagnostic_event"] == "ATTEMPT_OUTCOME"
    )
    assert first_outcome["outcome"] == "FAILED"
    assert first_outcome["reason"] == "PER_GAME_STAGE_VALIDATION_FAILED"
    assert first_outcome["failure_details"][0]["reason"] == "PER_GAME_STAGE_VALIDATION_FAILED"
    assert "temporal" in str(first_outcome["failure_details"][0]["errors"])
    assert all(item["diagnostic_fingerprint"] for item in first_records)

    records_before_status = {
        item["SK"]: copy.deepcopy(item)
        for item in first_records
    }
    puts_before_status = len(module.TABLE.put_calls)
    status = module._status_payload(SLATE)
    assert len(module.TABLE.put_calls) == puts_before_status
    assert {
        item["SK"]: item for item in diagnostic_items(module)
    } == records_before_status
    latest = status["perGameStatus"][0]["attemptDiagnostics"]["latestAttempt"]
    assert latest["outcome"] == "FAILED"
    assert latest["reason"] == "PER_GAME_STAGE_VALIDATION_FAILED"
    assert latest["startObserved"] is True
    assert latest["outcomeObserved"] is True

    module.mlb_game_winner_engine.tampered_provenance = False
    for key, item in list(module.TABLE.items.items()):
        if item.get("record_type") in {
            patch.PREGAME_SNAPSHOT_RECORD_TYPE,
            patch.LIVE_PREDICTION_RECORD_TYPE,
        }:
            module.TABLE.items.pop(key)
    persist_latest_prelock_candidates(module, pulls)
    retry = module.run_lock(SLATE)

    assert retry["locked"] is True
    all_records = diagnostic_items(module)
    assert len(all_records) == 4
    assert {
        sk: module.TABLE.items[(module._lock_pk(SLATE), sk)]
        for sk in records_before_status
    } == records_before_status
    outcomes = sorted(
        item["outcome"]
        for item in all_records
        if item["diagnostic_event"] == "ATTEMPT_OUTCOME"
    )
    assert outcomes == ["FAILED", "LOCKED_CANONICAL"]


def test_post_source_temporal_and_fundamental_provenance_writes_nothing():
    module = build_module(
        EARLY_PULLS,
        "2026-07-13T17:17:00+00:00",
        tampered_provenance=True,
    )
    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["reason"] == "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL"
    failure_text = str(result.get("failures") or [])
    assert "temporal" in failure_text
    assert "fundamental" in failure_text
    assert not staged_items(module)
    assert module.mlb_game_winner_engine.canonical_new_writes == 0
    assert daily_item(module) is None


def test_status_exposes_newer_orphaned_start_after_older_completed_attempt():
    pulls = [pull("2026-07-13T17:15:00+00:00", [G1], "orphan")]
    module = build_module(pulls, "2026-07-13T17:17:00+00:00", vectorless=True)
    module.run_lock(SLATE)

    orphan_at = dt("2026-07-13T17:18:00+00:00")
    orphan_id = "orphaned-timeout-attempt"
    write = patch._put_diagnostic(module, {
        "PK": module._lock_pk(SLATE),
        "SK": patch._diagnostic_sk(module, G1, orphan_at, orphan_id, "START"),
        "record_type": patch.ATTEMPT_RECORD_TYPE,
        "diagnostics_version": patch.ATTEMPT_DIAGNOSTICS_VERSION,
        "diagnostic_event": "ATTEMPT_STARTED",
        "attempt_id": orphan_id,
        "attempted_at_utc": orphan_at.isoformat(),
        "game_identity": "g1",
        "created_at": orphan_at.isoformat(),
        "write_once": True,
    })
    assert write["ok"] is True
    puts_before_status = len(module.TABLE.put_calls)

    status = module._status_payload(SLATE)

    assert len(module.TABLE.put_calls) == puts_before_status
    latest = status["perGameStatus"][0]["attemptDiagnostics"]["latestAttempt"]
    assert latest["attemptId"] == orphan_id
    assert latest["startObserved"] is True
    assert latest["outcomeObserved"] is False
    assert latest["outcome"] is None


def test_failed_lock_raises_if_durable_outcome_diagnostic_cannot_persist():
    pulls = [pull("2026-07-13T17:15:00+00:00", [G1], "diagnostic-failure")]
    module = build_module(
        pulls,
        "2026-07-13T17:17:00+00:00",
        tampered_provenance=True,
    )
    module.TABLE.diagnostic_write_failures_remaining = 10

    try:
        module.run_lock(SLATE)
    except RuntimeError as exc:
        assert "LOCK_ATTEMPT_DIAGNOSTIC_PERSIST_FAILED" in str(exc)
    else:
        raise AssertionError("failed lock returned without durable outcome diagnostics")

    assert diagnostic_items(module) == []
    assert staged_items(module) == []
    assert daily_item(module) is None


def test_stage_and_canonical_collisions_are_idempotent():
    module = build_module(EARLY_PULLS, "2026-07-13T17:17:00+00:00")
    module.run_lock(SLATE)
    stage_count = len(staged_items(module))
    canonical_writes = module.mlb_game_winner_engine.canonical_new_writes

    result = module.run_lock(SLATE)

    assert result["reason"] == "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER"
    assert len(staged_items(module)) == stage_count == 1
    assert module.mlb_game_winner_engine.canonical_new_writes == canonical_writes == 1


def test_legacy_daily_lock_row_cannot_short_circuit_per_game_authority():
    pulls = [pull("2026-07-13T17:15:00+00:00", [G1], "legacy-daily")]
    module = build_module(pulls, "2026-07-13T17:17:00+00:00")
    module.TABLE.put_item(Item={
        "PK": module._lock_pk(SLATE),
        "SK": module._lock_sk(),
        "record_type": "mlb_daily_locked_moneyline_picks",
        "sport": "mlb",
        "slate_date": SLATE,
        "model_version": "legacy-slate-recompute-v1",
        "locked": True,
        "game_count": 1,
        "prediction_count": 1,
        "data": {"picks": [{"gameIdentity": "provider:g1"}]},
    })

    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["locked"] is False
    assert result["failClosed"] is True
    assert result["reason"] == "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY"
    assert "daily_lock_record_type_not_per_game_authority" in result["dailyLockAuthorityErrors"]
    assert "daily_lock_model_or_manifest_version_mismatch" in result["dailyLockAuthorityErrors"]
    assert not staged_items(module)
    assert module.mlb_game_winner_engine.canonical_new_writes == 0
    assert module.mlb_game_winner_engine.prediction_calls == 0

    status = module._status_payload(SLATE)
    assert status["locked"] is False
    assert status["invalidExistingDailyLock"] is True
    assert "daily_lock_record_type_not_per_game_authority" in status["dailyLockAuthorityErrors"]


def test_current_daily_lock_missing_per_game_proofs_is_not_accepted():
    pulls = [pull("2026-07-13T17:15:00+00:00", [G1], "proof-tamper")]
    module = build_module(pulls, "2026-07-13T17:17:00+00:00")
    first = module.run_lock(SLATE)
    assert first["locked"] is True
    canonical_writes = module.mlb_game_winner_engine.canonical_new_writes

    stored = daily_item(module)
    stored["data"]["perGameLockProof"] = []
    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["locked"] is False
    assert result["reason"] == "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY"
    assert "daily_lock_per_game_proof_count_mismatch" in result["dailyLockAuthorityErrors"]
    assert module.mlb_game_winner_engine.canonical_new_writes == canonical_writes == 1


def test_current_daily_lock_with_live_stage_and_canonical_proofs_is_idempotent():
    pulls = [pull("2026-07-13T17:15:00+00:00", [G1], "current-daily")]
    module = build_module(pulls, "2026-07-13T17:17:00+00:00")
    first = module.run_lock(SLATE)
    stage_count = len(staged_items(module))
    canonical_writes = module.mlb_game_winner_engine.canonical_new_writes

    second = module.run_lock(SLATE)

    assert first["locked"] is True
    assert second["ok"] is True
    assert second["locked"] is True
    assert second["alreadyLocked"] is True
    assert second["lock"]["perGameLock"] is True
    assert len(staged_items(module)) == stage_count == 1
    assert module.mlb_game_winner_engine.canonical_new_writes == canonical_writes == 1
    status = module._status_payload(SLATE)
    assert status["locked"] is True
    assert status["invalidExistingDailyLock"] is False
    assert status["dailyLockAuthorityErrors"] == []


def test_status_is_read_only_and_does_not_repair_missing_canonical_row():
    module = build_module(EARLY_PULLS, "2026-07-13T17:17:00+00:00")
    module.run_lock(SLATE)
    stage = staged_items(module)[0]
    row = stage["data"]["row"]
    module.TABLE.delete(
        f"GAME_WINNERS#mlb#{SLATE}",
        f"LOCKED#GAME#{row['commenceTime']}#{row['gameIdentity']}",
    )
    calls_before = module.mlb_game_winner_engine.canonical_calls

    status = module._status_payload(SLATE)

    assert status["canonicalImmutableGameRowCount"] == 0
    assert module.mlb_game_winner_engine.canonical_calls == calls_before


def test_dynamic_template_keeps_one_minute_lock_check_and_invariant():
    patcher = (ROOT / "scripts" / "patch_template_mlb_v1.py").read_text()
    invariant = (ROOT / "scripts" / "verify_mlb_schedule_invariants.py").read_text()
    assert "MLBDailyPickLockEveryMinute" in patcher
    assert "Schedule: rate(1 minute)" in patcher
    assert "MLBDailyPickLockEveryMinute" in invariant


def test_per_game_daily_card_remains_authoritative_for_settlement_fallback():
    module = build_module(EARLY_PULLS, "2026-07-13T17:17:00+00:00")
    module.run_lock(SLATE)
    module.history.pulls.extend(LATE_PULLS)
    persist_latest_prelock_candidates(module, LATE_PULLS)
    module.now = dt("2026-07-13T19:17:00+00:00")
    module.run_lock(SLATE)

    assert audit_fallback._authoritative(daily_item(module)) is True
