#!/usr/bin/env python3
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

try:
    import boto3  # noqa: F401
except ModuleNotFoundError:
    history_stub = ModuleType("inqsi_pull_history")
    history_stub.PULLS = None
    history_stub.ddb_safe = lambda value: copy.deepcopy(value)
    sys.modules["inqsi_pull_history"] = history_stub

import mlb_yesterday_audit as audit


SLATE = "2026-07-12"


class FakeTable:
    def __init__(self, items=None):
        self.items = copy.deepcopy(items or {})
        self.get_calls = []
        self.put_calls = []

    def get_item(self, Key, ConsistentRead=False):
        self.get_calls.append((copy.deepcopy(Key), ConsistentRead))
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item is not None else {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and key in self.items:
            raise RuntimeError("conditional collision")
        self.items[key] = copy.deepcopy(Item)
        self.put_calls.append((key, ConditionExpression))
        return {}


def pick(game_id, home, away, winner, side, *, official_marker="missing", fingerprint=None):
    row = {
        "gameId": game_id,
        "gameIdentity": game_id,
        "commenceTime": f"2026-07-12T1{len(game_id)}:00:00+00:00",
        "homeTeam": home,
        "awayTeam": away,
        "predictedWinner": winner,
        "predictedSide": side,
        "score": 55.0,
        "tags": ["FINAL_LOCKED"],
    }
    if official_marker != "missing":
        row["officialPick"] = official_marker
    if fingerprint:
        row["frozenFeatureVector"] = {
            "version": "test-vector",
            "fingerprint": fingerprint,
            "labels": {"homeWon": None, "pickCorrect": None},
        }
    return row


def locked_card(rows, *, per_game=False):
    card = {
        "PK": f"LOCKED_PICKS#mlb#{SLATE}",
        "SK": audit.DAILY_LOCK_SK,
        "record_type": "mlb_daily_locked_individual_game_moneyline_picks",
        "slate_date": SLATE,
        "locked": True,
        "all_games_predicted": True,
        "game_count": len(rows),
        "prediction_count": len(rows),
        "created_at": "2026-07-12T16:00:00+00:00",
        "locked_at": "2026-07-12T16:00:00+00:00",
        "latest_pull_at": "2026-07-12T15:55:00+00:00",
        "first_game_start_utc": "2026-07-12T17:00:00+00:00",
        "model_version": "locked-test-model",
        "data": {"picks": copy.deepcopy(rows)},
    }
    if per_game:
        card.update({
            "per_game_lock": True,
            "lock_policy": "each_mlb_game_minus_45_minutes",
            "coverage_complete": True,
            "canonical_immutable_game_row_count": len(rows),
        })
        card["data"]["perGameLockProof"] = [
            {
                "gameIdentity": row["gameIdentity"],
                "writeOnce": True,
                "canonicalImmutableGameRow": True,
            }
            for row in rows
        ]
    return card


def final(game_id, home, away, winner, home_score, away_score, *, commence_time=None):
    row = {
        "id": game_id,
        "homeTeam": home,
        "awayTeam": away,
        "matchup": f"{away} at {home}",
        "commenceTime": commence_time or f"2026-07-12T1{len(game_id)}:00:00+00:00",
        "homeScore": home_score,
        "awayScore": away_score,
        "winner": winner,
        "completed": True,
    }
    return row


def score_report(finals):
    by_matchup = {}
    for row in finals:
        key = f"{audit.normalize_team(row['awayTeam'])}|{audit.normalize_team(row['homeTeam'])}"
        by_matchup.setdefault(key, []).append(row)
    return {
        "ok": True,
        "slate_date": SLATE,
        "finalScoreCount": len(finals),
        "finalScores": copy.deepcopy(finals),
        "byId": {row["id"]: copy.deepcopy(row) for row in finals},
        "byMatchup": by_matchup,
    }


def official_schedule(total_games):
    return {
        "ok": True,
        "source": "official_mlb_stats_api",
        "requestedDate": SLATE,
        "exactDateVerified": True,
        "totalGames": total_games,
        "gamePks": [str(index + 1) for index in range(total_games)],
        "gameStates": [],
    }


def verify_legacy_card_build_and_supersession():
    first = pick("g1", "Home One", "Away One", "Home One", "home", official_marker=False, fingerprint="fp-1")
    second = pick("g22", "Home Two", "Away Two", "Away Two", "away")
    card = locked_card([first, second])
    previous_latest = {
        "PK": "MLB_DAILY_AUDIT#LATEST",
        "SK": "LATEST",
        "slate_date": SLATE,
        "created_at": "2026-07-13T11:49:57+00:00",
        "data": {"proofType": "MLB_YESTERDAY_GAME_AUDIT", "slate_date": SLATE},
    }
    previous_run = {
        "PK": f"MLB_DAILY_AUDIT#{SLATE}",
        "SK": "AUDIT#2026-07-13T11:49:57+00:00",
        "data": {"historical": True},
    }
    table = FakeTable({
        (card["PK"], card["SK"]): card,
        (previous_latest["PK"], previous_latest["SK"]): previous_latest,
        (previous_run["PK"], previous_run["SK"]): previous_run,
    })
    finals = [
        # Final providers may replace a scheduled start with actual first pitch.
        # Exact provider ID + teams remain the immutable game identity.
        final(
            "g1",
            "Home One",
            "Away One",
            "Home One",
            4,
            2,
            commence_time="2026-07-12T12:03:00+00:00",
        ),
        final("g22", "Home Two", "Away Two", "Home Two", 5, 1),
    ]
    old_table = audit.history.PULLS
    old_ddb_safe = audit.history.ddb_safe
    old_scores = audit.pull_final_scores
    old_schedule = audit.pull_official_schedule
    old_clean = audit._clean_validation_errors
    old_now = audit.now_iso
    try:
        audit.history.PULLS = table
        audit.history.ddb_safe = lambda value: copy.deepcopy(value)
        audit.pull_final_scores = lambda slate_date, days_from=3: score_report(finals)
        audit.pull_official_schedule = lambda slate_date: official_schedule(2)
        audit._clean_validation_errors = lambda row: [] if row.get("gameId") == "g1" else ["missing_frozen_feature_vector"]
        audit.now_iso = lambda: "2026-07-13T14:00:00+00:00"
        report = audit.build(slate_date=SLATE, store=True, write_file=False)
    finally:
        audit.history.PULLS = old_table
        audit.history.ddb_safe = old_ddb_safe
        audit.pull_final_scores = old_scores
        audit.pull_official_schedule = old_schedule
        audit._clean_validation_errors = old_clean
        audit.now_iso = old_now

    assert report["ok"] is True, report
    assert report["historicalPredictionsRecomputed"] is False
    assert report["immutableLockAuthority"]["authorityClass"] == "WRITE_ONCE_IMMUTABLE_DAILY_LOCK_CARD_LEGACY"
    assert report["summary"]["officialCardPickCount"] == 2
    assert report["summary"]["officialCardAccuracyPct"] == 50.0
    assert report["summary"]["officialCardAuthorityTargetPct"] == 90.0
    assert report["summary"]["cleanRowCount"] == 1
    assert report["summary"]["quarantinedRowCount"] == 1
    assert audit._clean_validation_errors(second) == ["missing_frozen_feature_vector"]
    assert report["rows"][0]["predictedWinner"] == first["predictedWinner"]
    assert report["rows"][0]["officialPick"] is False
    assert report["rows"][1]["officialPick"] is None
    assert all(row["officialCardPrediction"] is True for row in report["rows"])
    assert report["rows"][0]["frozenFeatureVector"]["labels"] == {"homeWon": None, "pickCorrect": None}
    assert report["rows"][0]["outcomeJoin"]["pickCorrect"] is True
    assert report["rows"][0]["outcomeJoin"]["joinedOutsideFrozenFeatureVector"] is True
    assert report["finalOutcomeIdentityJoin"]["identityContextFieldsVerified"] == [
        "providerGameId",
        "homeTeam",
        "awayTeam",
    ]
    assert report["finalOutcomeIdentityJoin"]["commenceTimeIsMutableScheduleMetadata"] is True
    assert report["finalOutcomeIdentityJoin"]["commenceTimeDifferenceCount"] == 1
    assert report["finalOutcomeIdentityJoin"]["commenceTimeDifferences"][0]["differenceSeconds"] == 180
    assert report["supersedes"]["createdAt"] == previous_latest["created_at"]
    assert table.items[(previous_run["PK"], previous_run["SK"])] == previous_run
    run_puts = [call for call in table.put_calls if call[0][0] == f"MLB_DAILY_AUDIT#{SLATE}"]
    assert len(run_puts) == 1 and run_puts[0][1] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    assert any(call[0] == ("MLB_DAILY_AUDIT#LATEST", "LATEST") and call[1] is None for call in table.put_calls)
    assert all(consistent is True for _, consistent in table.get_calls)


def verify_per_game_canonical_consistency_and_mismatch_block():
    daily = pick("canonical-1", "Home", "Away", "Home", "home", official_marker=True, fingerprint="same-fingerprint")
    card = locked_card([daily], per_game=True)
    canonical = copy.deepcopy(daily)
    canonical["lockedPrediction"] = True
    canonical_item = {
        "PK": f"GAME_WINNERS#mlb#{SLATE}",
        "SK": f"LOCKED#GAME#{daily['commenceTime']}#{daily['gameIdentity']}",
        "immutable_locked": True,
        "data": canonical,
    }
    table = FakeTable({
        (card["PK"], card["SK"]): card,
        (canonical_item["PK"], canonical_item["SK"]): canonical_item,
    })
    old_table = audit.history.PULLS
    try:
        audit.history.PULLS = table
        loaded = audit.load_locked_predictions(SLATE)
        assert loaded["authority"]["canonicalSingleGameRowsVerified"] is True
        assert loaded["rows"][0]["lockedPrediction"] is True
        assert all(consistent is True for _, consistent in table.get_calls)

        table.items[(canonical_item["PK"], canonical_item["SK"])]["data"]["predictedWinner"] = "Away"
        try:
            audit.load_locked_predictions(SLATE)
        except audit.LockedEvidenceUnavailable as exc:
            assert "DAILY_CARD_CANONICAL_ROW_MISMATCH" in str(exc)
        else:
            raise AssertionError("canonical mismatch did not fail closed")
    finally:
        audit.history.PULLS = old_table


def verify_missing_locked_evidence_fails_closed():
    table = FakeTable()
    one_final = [final("missing", "Home", "Away", "Home", 2, 1)]
    old_table = audit.history.PULLS
    old_scores = audit.pull_final_scores
    old_schedule = audit.pull_official_schedule
    try:
        audit.history.PULLS = table
        audit.pull_final_scores = lambda slate_date, days_from=3: score_report(one_final)
        audit.pull_official_schedule = lambda slate_date: official_schedule(1)
        report = audit.build(slate_date=SLATE, store=True, write_file=False)
    finally:
        audit.history.PULLS = old_table
        audit.pull_final_scores = old_scores
        audit.pull_official_schedule = old_schedule
    assert report["ok"] is False
    assert report["failClosed"] is True
    assert report["status"] == "LOCKED_EVIDENCE_UNAVAILABLE"
    assert report["historicalPredictionsRecomputed"] is False
    assert not table.put_calls


def verify_empty_provider_requires_verified_zero_game_schedule():
    empty = score_report([])
    old_scores = audit.pull_final_scores
    old_schedule = audit.pull_official_schedule
    old_table = audit.history.PULLS
    try:
        audit.history.PULLS = FakeTable()
        audit.pull_final_scores = lambda slate_date, days_from=3: copy.deepcopy(empty)

        audit.pull_official_schedule = lambda slate_date: official_schedule(1)
        nonempty = audit.build(slate_date=SLATE, store=True, write_file=False)
        assert nonempty["ok"] is False
        assert nonempty["status"] == "FINAL_OUTCOMES_INCOMPLETE"
        assert nonempty["stored"] is False

        def unavailable(_slate_date):
            raise audit.OfficialScheduleUnverified("injected schedule outage")

        audit.pull_official_schedule = unavailable
        unverified = audit.build(slate_date=SLATE, store=True, write_file=False)
        assert unverified["ok"] is False
        assert unverified["status"] == "OFFICIAL_SCHEDULE_UNVERIFIED"
        assert unverified["stored"] is False

        audit.pull_official_schedule = lambda slate_date: official_schedule(0)
        verified_empty = audit.build(slate_date=SLATE, store=True, write_file=False)
        assert verified_empty["ok"] is True
        assert verified_empty["officialScheduleZeroGamesVerified"] is True
        assert verified_empty["stored"] is False
        assert not audit.history.PULLS.put_calls
    finally:
        audit.pull_final_scores = old_scores
        audit.pull_official_schedule = old_schedule
        audit.history.PULLS = old_table


def verify_duplicate_locked_identity_and_non_bijective_finals_fail_closed():
    first = pick("g1", "Home One", "Away One", "Home One", "home")
    duplicate = copy.deepcopy(first)
    duplicate["homeTeam"] = "Another Home"
    duplicate_card = locked_card([first, duplicate])
    duplicate_table = FakeTable({(duplicate_card["PK"], duplicate_card["SK"]): duplicate_card})
    old_table = audit.history.PULLS
    try:
        audit.history.PULLS = duplicate_table
        try:
            audit.load_locked_predictions(SLATE)
        except audit.LockedEvidenceUnavailable as exc:
            assert "DUPLICATE_GAME_IDENTITY" in str(exc)
        else:
            raise AssertionError("duplicate locked identity did not fail closed")
    finally:
        audit.history.PULLS = old_table

    second = pick("g22", "Home Two", "Away Two", "Away Two", "away")
    card = locked_card([first, second])
    valid_items = {(card["PK"], card["SK"]): card}
    final_one = final("g1", "Home One", "Away One", "Home One", 4, 2)
    reused = final("g1", "Home Two", "Away Two", "Home Two", 5, 1)
    mismatched = final("g333", "Home Two", "Away Two", "Home Two", 5, 1)
    wrong_context_one = final("g1", "Home Two", "Away Two", "Home Two", 5, 1)
    wrong_context_two = final("g22", "Home One", "Away One", "Home One", 4, 2)
    old_scores = audit.pull_final_scores
    old_schedule = audit.pull_official_schedule
    old_table = audit.history.PULLS
    try:
        audit.pull_official_schedule = lambda slate_date: official_schedule(2)
        for finals in ([final_one, reused], [final_one, mismatched], [wrong_context_one, wrong_context_two]):
            table = FakeTable(valid_items)
            audit.history.PULLS = table
            audit.pull_final_scores = lambda slate_date, days_from=3, rows=finals: score_report(rows)
            report = audit.build(slate_date=SLATE, store=True, write_file=False)
            assert report["ok"] is False
            assert report["status"] == "FINAL_OUTCOME_IDENTITY_MISMATCH"
            assert report["stored"] is False
            assert not table.put_calls
    finally:
        audit.pull_final_scores = old_scores
        audit.pull_official_schedule = old_schedule
        audit.history.PULLS = old_table


def verify_source_cannot_recompute_historical_picks():
    source = (HELLO_WORLD / "mlb_yesterday_audit.py").read_text(encoding="utf-8")
    forbidden = ("predict_all(", "mlb_game_winner_engine", "mlb_b10_engine", "official75Target", "75% product target")
    assert not [value for value in forbidden if value in source]


def main() -> int:
    verify_source_cannot_recompute_historical_picks()
    verify_legacy_card_build_and_supersession()
    verify_per_game_canonical_consistency_and_mismatch_block()
    verify_missing_locked_evidence_fails_closed()
    verify_empty_provider_requires_verified_zero_game_schedule()
    verify_duplicate_locked_identity_and_non_bijective_finals_fail_closed()
    print(
        "MLB yesterday audit verified: immutable locked predictions only, canonical per-game readback, "
        "external FINAL joins, 90% official-card reporting, clean quarantine, and write-once audit history."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
