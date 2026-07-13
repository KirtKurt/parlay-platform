from __future__ import annotations

import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mlb_daily_per_game_lock_patch as patch
import mlb_daily_lock_audit_fallback_patch as audit_fallback
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

    def get_item(self, Key, ConsistentRead=False):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item is not None else {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and key in self.items:
            raise ConditionalCollision()
        self.items[key] = copy.deepcopy(Item)
        self.put_calls.append(key)
        return {}

    def delete(self, pk: str, sk: str):
        self.items.pop((pk, sk), None)


class FakeHistory:
    def __init__(self, table: FakeTable, pulls):
        self.PULLS = table
        self.pulls = list(pulls)

    def query_pulls(self, sport, date=None, limit=500):
        assert sport == "mlb"
        assert date == SLATE
        return copy.deepcopy(self.pulls[:limit])

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

    def predict_all(self, slate, store=False, limit=500):
        pulls = self.history.query_pulls("mlb", slate, limit)
        assert pulls
        game = copy.deepcopy(pulls[-1]["games"][0])
        source = pulls[-1]
        start = dt(game["commence_time"])
        lock_at = start - timedelta(minutes=45)
        selected_odds = game["books"]["fanduel"]["ml"]["home"]
        row = {
            "sport": "mlb",
            "slate_date": slate,
            "slateDateEt": slate,
            "gameId": game["game_id"],
            "gameIdentity": game["game_id"],
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
        return {"ok": True, "gameCount": 1, "count": 1, "predictions": [row]}

    def _store_prediction(self, row):
        self.canonical_calls += 1
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
    return {"pull_id": f"pull-{suffix}", "pulled_at": at, "slate_date": SLATE, "games": copy.deepcopy(games)}


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


def daily_item(module):
    return module.TABLE.items.get((module._lock_pk(SLATE), module._lock_sk()))


def build_module(pulls, now, *, vectorless=False, tampered_provenance=False):
    module = FakeModule(
        pulls,
        now,
        vectorless=vectorless,
        tampered_provenance=tampered_provenance,
    )
    patch.apply(module)
    return module


def test_first_game_canonical_at_own_cutoff_while_later_game_pending():
    module = build_module(EARLY_PULLS, "2026-07-13T17:16:00+00:00")

    result = module.run_lock(SLATE)

    assert result["reason"] == "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER"
    assert result["perGameLockProgress"]["stagedCount"] == 1
    assert result["perGameLockProgress"]["canonicalCount"] == 1
    assert module.mlb_game_winner_engine.canonical_new_writes == 1
    stage = staged_items(module)[0]
    assert stage["game_id"] == "g1"
    assert stage["scheduled_lock_at_utc"] == "2026-07-13T17:15:00+00:00"
    assert stage["staged_at_utc"] == "2026-07-13T17:16:00+00:00"
    assert stage["source_pull_at_utc"] == "2026-07-13T17:15:00+00:00"
    assert daily_item(module) is None


def test_later_game_uses_newer_own_cutoff_pull_and_only_then_finalizes_daily_card():
    module = build_module(EARLY_PULLS, "2026-07-13T17:16:00+00:00")
    module.run_lock(SLATE)
    assert daily_item(module) is None

    module.history.pulls.extend(LATE_PULLS)
    module.now = dt("2026-07-13T19:16:00+00:00")
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


def test_manifest_drift_after_first_stage_fails_closed_without_daily_card():
    module = build_module(EARLY_PULLS, "2026-07-13T17:16:00+00:00")
    module.run_lock(SLATE)
    assert module.mlb_game_winner_engine.canonical_new_writes == 1

    module.history.pulls.append(pull("2026-07-13T18:30:00+00:00", [G2, G3], "drift"))
    module.now = dt("2026-07-13T19:16:00+00:00")
    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["reason"] == "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL"
    assert any("manifest_changed_after_game_lock" in row.get("errors", []) for row in result["perGameLockProgress"]["games"])
    assert daily_item(module) is None
    assert module.mlb_game_winner_engine.canonical_new_writes == 1


def test_vectorless_or_tampered_generation_writes_nothing_canonical():
    module = build_module(EARLY_PULLS, "2026-07-13T17:16:00+00:00", vectorless=True)
    result = module.run_lock(SLATE)

    assert result["ok"] is False
    assert result["reason"] == "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL"
    assert not staged_items(module)
    assert module.mlb_game_winner_engine.canonical_new_writes == 0
    assert daily_item(module) is None


def test_post_source_temporal_and_fundamental_provenance_writes_nothing():
    module = build_module(
        EARLY_PULLS,
        "2026-07-13T17:16:00+00:00",
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


def test_stage_and_canonical_collisions_are_idempotent():
    module = build_module(EARLY_PULLS, "2026-07-13T17:16:00+00:00")
    module.run_lock(SLATE)
    stage_count = len(staged_items(module))
    canonical_writes = module.mlb_game_winner_engine.canonical_new_writes

    result = module.run_lock(SLATE)

    assert result["reason"] == "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER"
    assert len(staged_items(module)) == stage_count == 1
    assert module.mlb_game_winner_engine.canonical_new_writes == canonical_writes == 1


def test_status_is_read_only_and_does_not_repair_missing_canonical_row():
    module = build_module(EARLY_PULLS, "2026-07-13T17:16:00+00:00")
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
    module = build_module(EARLY_PULLS, "2026-07-13T17:16:00+00:00")
    module.run_lock(SLATE)
    module.history.pulls.extend(LATE_PULLS)
    module.now = dt("2026-07-13T19:16:00+00:00")
    module.run_lock(SLATE)

    assert audit_fallback._authoritative(daily_item(module)) is True
