from __future__ import annotations

import copy
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import inqsi_pull_history as history

SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
REPORT_PATH = "runtime_reports/mlb_yesterday_audit_latest.json"
VERSION = "MLB-YESTERDAY-AUDIT-v2.1-immutable-lock-identity"
OFFICIAL_CARD_AUTHORITY_TARGET_PCT = 90.0
DAILY_LOCK_SK = "DAILY_LOCK#TMINUS45"
MLB_STATS_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


class LockedEvidenceUnavailable(RuntimeError):
    """The audit cannot prove a historical pick from immutable storage."""


class OfficialScheduleUnverified(RuntimeError):
    """The exact-date official MLB schedule could not be proven."""


class FinalOutcomeJoinUnavailable(RuntimeError):
    """FINAL provider rows do not map one-to-one to locked predictions."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def yesterday_et() -> str:
    return (datetime.now(SLATE_TZ).date() - timedelta(days=1)).isoformat()


def normalize_team(name: Optional[str]) -> str:
    return " ".join((name or "").lower().strip().split())


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def slate_date_from_commence(value: Any) -> Optional[str]:
    dt = parse_dt(value)
    return dt.astimezone(SLATE_TZ).date().isoformat() if dt else None


def scores_url(days_from: int = 3) -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    query = urllib.parse.urlencode({"apiKey": ODDS_API_KEY, "daysFrom": days_from, "dateFormat": "iso"})
    return "https://api.the-odds-api.com/v4/sports/baseball_mlb/scores/?" + query


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def pull_official_schedule(slate_date: str) -> Dict[str, Any]:
    """Verify the exact-date game count against MLB's official schedule API."""
    query = urllib.parse.urlencode({"sportId": 1, "date": slate_date, "hydrate": "team"})
    try:
        payload = http_get_json(f"{MLB_STATS_SCHEDULE_URL}?{query}")
    except Exception as exc:
        raise OfficialScheduleUnverified(f"OFFICIAL_MLB_SCHEDULE_REQUEST_FAILED:{exc}") from exc
    if not isinstance(payload, dict):
        raise OfficialScheduleUnverified("OFFICIAL_MLB_SCHEDULE_MALFORMED_PAYLOAD")
    total_games = payload.get("totalGames")
    if isinstance(total_games, bool) or not isinstance(total_games, int) or total_games < 0:
        raise OfficialScheduleUnverified("OFFICIAL_MLB_SCHEDULE_TOTAL_GAMES_INVALID")
    dates = payload.get("dates")
    if not isinstance(dates, list):
        raise OfficialScheduleUnverified("OFFICIAL_MLB_SCHEDULE_DATES_INVALID")
    games: List[Dict[str, Any]] = []
    for date_entry in dates:
        if not isinstance(date_entry, dict) or str(date_entry.get("date") or "") != str(slate_date):
            raise OfficialScheduleUnverified("OFFICIAL_MLB_SCHEDULE_NOT_EXACT_DATE")
        date_games = date_entry.get("games")
        if not isinstance(date_games, list):
            raise OfficialScheduleUnverified("OFFICIAL_MLB_SCHEDULE_GAMES_INVALID")
        games.extend(date_games)
    if len(games) != total_games:
        raise OfficialScheduleUnverified("OFFICIAL_MLB_SCHEDULE_GAME_COUNT_MISMATCH")
    game_pks = [str(game.get("gamePk") or "") for game in games if isinstance(game, dict)]
    if len(game_pks) != total_games or any(not value for value in game_pks) or len(set(game_pks)) != len(game_pks):
        raise OfficialScheduleUnverified("OFFICIAL_MLB_SCHEDULE_GAME_IDENTITIES_INVALID")
    if any(
        str(game.get("officialDate") or "") != str(slate_date)
        for game in games
        if isinstance(game, dict)
    ):
        raise OfficialScheduleUnverified("OFFICIAL_MLB_SCHEDULE_GAME_DATE_MISMATCH")
    return {
        "ok": True,
        "source": "official_mlb_stats_api",
        "requestedDate": slate_date,
        "exactDateVerified": True,
        "totalGames": total_games,
        "gamePks": game_pks,
        "gameStates": [
            {
                "gamePk": str(game.get("gamePk")),
                "abstractGameState": ((game.get("status") or {}).get("abstractGameState")),
                "detailedState": ((game.get("status") or {}).get("detailedState")),
            }
            for game in games
        ],
    }


def pull_final_scores(slate_date: str, days_from: int = 3) -> Dict[str, Any]:
    raw = http_get_json(scores_url(days_from=days_from))
    finals = []
    by_id: Dict[str, Dict[str, Any]] = {}
    by_matchup: Dict[str, List[Dict[str, Any]]] = {}
    for game in raw or []:
        if not game.get("completed"):
            continue
        if slate_date_from_commence(game.get("commence_time")) != slate_date:
            continue
        home = game.get("home_team")
        away = game.get("away_team")
        home_score = away_score = None
        for score in game.get("scores") or []:
            if score.get("name") == home:
                home_score = int(score.get("score"))
            if score.get("name") == away:
                away_score = int(score.get("score"))
        if home_score is None or away_score is None:
            continue
        winner = home if home_score > away_score else away if away_score > home_score else "TIE"
        row = {
            "id": game.get("id"),
            "homeTeam": home,
            "awayTeam": away,
            "matchup": f"{away} at {home}",
            "commenceTime": game.get("commence_time"),
            "homeScore": home_score,
            "awayScore": away_score,
            "winner": winner,
            "margin": abs(home_score - away_score),
            "totalRuns": home_score + away_score,
            "completed": True,
        }
        finals.append(row)
        if row.get("id"):
            by_id[str(row["id"])] = row
        by_matchup.setdefault(f"{normalize_team(away)}|{normalize_team(home)}", []).append(row)
    return {
        "ok": True,
        "slate_date": slate_date,
        "finalScoreCount": len(finals),
        "finalScores": finals,
        "byId": by_id,
        "byMatchup": by_matchup,
    }


def outcome_for(row: Dict[str, Any], score_report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    game_id = _game_id(row)
    return (score_report.get("byId") or {}).get(game_id) if game_id else None


def validate_one_to_one_final_join(locked_rows: List[Dict[str, Any]], score_report: Dict[str, Any]) -> Dict[str, Any]:
    locked_ids = [_game_id(row) for row in locked_rows]
    finals = score_report.get("finalScores") if isinstance(score_report.get("finalScores"), list) else []
    final_ids = [str(row.get("id") or "") for row in finals if isinstance(row, dict)]
    by_id = score_report.get("byId") if isinstance(score_report.get("byId"), dict) else {}
    if (
        len(locked_ids) != len(set(locked_ids))
        or any(not value for value in locked_ids)
        or len(final_ids) != len(finals)
        or any(not value for value in final_ids)
        or len(final_ids) != len(set(final_ids))
        or set(by_id) != set(final_ids)
        or set(locked_ids) != set(final_ids)
        or any(row.get("completed") is not True for row in finals if isinstance(row, dict))
    ):
        raise FinalOutcomeJoinUnavailable("LOCKED_AND_FINAL_GAME_IDENTITIES_NOT_ONE_TO_ONE")
    locked_by_id = {_game_id(row): row for row in locked_rows}
    commence_time_differences: List[Dict[str, Any]] = []
    for game_id, locked in locked_by_id.items():
        outcome = by_id.get(game_id)
        if not isinstance(outcome, dict) or (
            normalize_team(locked.get("homeTeam")) != normalize_team(outcome.get("homeTeam"))
            or normalize_team(locked.get("awayTeam")) != normalize_team(outcome.get("awayTeam"))
        ):
            raise FinalOutcomeJoinUnavailable(f"LOCKED_AND_FINAL_GAME_CONTEXT_MISMATCH:{game_id}")
        locked_commence = parse_dt(locked.get("commenceTime"))
        final_commence = parse_dt(outcome.get("commenceTime"))
        if not locked_commence or not final_commence:
            raise FinalOutcomeJoinUnavailable(f"LOCKED_OR_FINAL_COMMENCE_TIME_INVALID:{game_id}")
        if locked_commence != final_commence:
            commence_time_differences.append({
                "gameId": game_id,
                "lockedCommenceTime": locked_commence.isoformat(),
                "finalProviderCommenceTime": final_commence.isoformat(),
                "differenceSeconds": int((final_commence - locked_commence).total_seconds()),
                "acceptedBecauseExactProviderGameIdAndTeamsMatch": True,
            })
    return {
        "ok": True,
        "lockedGameIds": locked_ids,
        "finalGameIds": final_ids,
        "uniqueLockedGameIds": True,
        "uniqueFinalGameIds": True,
        "oneToOneIdentityJoin": True,
        "identityContextFieldsVerified": ["providerGameId", "homeTeam", "awayTeam"],
        "commenceTimeIsMutableScheduleMetadata": True,
        "commenceTimeDifferenceCount": len(commence_time_differences),
        "commenceTimeDifferences": commence_time_differences,
    }


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _identity(row: Dict[str, Any]) -> str:
    return str(row.get("gameIdentity") or row.get("gameId") or row.get("game_id") or row.get("id") or "")


def _game_id(row: Dict[str, Any]) -> str:
    value = (
        row.get("providerEventId")
        or row.get("provider_event_id")
        or row.get("providerGameId")
        or row.get("provider_game_id")
        or row.get("gameId")
        or row.get("game_id")
        or row.get("id")
        or ""
    )
    text = str(value)
    return text[len("provider:"):] if text.startswith("provider:") else text


def _commence(row: Dict[str, Any]) -> str:
    return str(row.get("commenceTime") or row.get("commence_time") or "")


def _vector_fingerprint(row: Dict[str, Any]) -> str:
    vector = row.get("frozenFeatureVector") if isinstance(row.get("frozenFeatureVector"), dict) else {}
    return str(vector.get("fingerprint") or "")


def _prediction_identity(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "gameId": _game_id(row),
        "gameIdentity": _identity(row),
        "commenceTime": _commence(row),
        "homeTeam": normalize_team(row.get("homeTeam")),
        "awayTeam": normalize_team(row.get("awayTeam")),
        "predictedWinner": normalize_team(row.get("predictedWinner")),
        "predictedSide": str(row.get("predictedSide") or ""),
        "fingerprint": _vector_fingerprint(row),
    }


def _canonical_row(slate_date: str, daily_pick: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, str]]:
    identity = _identity(daily_pick)
    commence = _commence(daily_pick)
    if not identity or not commence:
        raise LockedEvidenceUnavailable("CANONICAL_LOCKED_IDENTITY_MISSING")
    key = {
        "PK": f"GAME_WINNERS#mlb#{slate_date}",
        "SK": f"LOCKED#GAME#{commence}#{identity}",
    }
    response = history.PULLS.get_item(Key=key, ConsistentRead=True)
    item = response.get("Item") if isinstance(response, dict) else None
    stored = (item or {}).get("data") if isinstance((item or {}).get("data"), dict) else None
    if not isinstance(item, dict) or item.get("immutable_locked") is not True or not isinstance(stored, dict):
        raise LockedEvidenceUnavailable(f"CANONICAL_LOCKED_GAME_ROW_UNAVAILABLE:{identity}")
    daily_identity = _prediction_identity(daily_pick)
    canonical_identity = _prediction_identity(stored)
    if daily_identity != canonical_identity:
        raise LockedEvidenceUnavailable(f"DAILY_CARD_CANONICAL_ROW_MISMATCH:{identity}")
    return copy.deepcopy(stored), key


def load_locked_predictions(slate_date: str) -> Dict[str, Any]:
    """Read only immutable, write-once lock evidence for a completed slate.

    A per-game lock card is a manifest: every referenced canonical game row is
    read consistently and must match the card exactly. Legacy slate-wide cards
    remain historical authority, but their rows are expected to be quarantined
    by the exact-vector validator.
    """
    if history.PULLS is None:
        raise LockedEvidenceUnavailable("LOCKED_EVIDENCE_TABLE_UNAVAILABLE")
    card_key = {"PK": f"LOCKED_PICKS#mlb#{slate_date}", "SK": DAILY_LOCK_SK}
    response = history.PULLS.get_item(Key=card_key, ConsistentRead=True)
    item = response.get("Item") if isinstance(response, dict) else None
    if not isinstance(item, dict):
        raise LockedEvidenceUnavailable("IMMUTABLE_DAILY_LOCK_CARD_UNAVAILABLE")
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    daily_picks = data.get("picks") if isinstance(data.get("picks"), list) else []
    expected = _as_int(item.get("game_count"))
    prediction_count = _as_int(item.get("prediction_count"))
    if not (
        item.get("record_type") == "mlb_daily_locked_individual_game_moneyline_picks"
        and str(item.get("slate_date") or "") == str(slate_date)
        and item.get("locked") is True
        and item.get("all_games_predicted") is True
        and expected > 0
        and expected == prediction_count == len(daily_picks)
    ):
        raise LockedEvidenceUnavailable("IMMUTABLE_DAILY_LOCK_CARD_INCOMPLETE")
    if any(
        not isinstance(row, dict)
        or not _game_id(row)
        or not _identity(row)
        or not _commence(row)
        or not row.get("homeTeam")
        or not row.get("awayTeam")
        or not row.get("predictedWinner")
        for row in daily_picks
    ):
        raise LockedEvidenceUnavailable("IMMUTABLE_DAILY_LOCK_PICK_IDENTITY_INCOMPLETE")
    game_ids = [_game_id(row) for row in daily_picks]
    identities = [_identity(row) for row in daily_picks]
    if len(set(game_ids)) != len(game_ids) or len(set(identities)) != len(identities):
        raise LockedEvidenceUnavailable("IMMUTABLE_DAILY_LOCK_DUPLICATE_GAME_IDENTITY")

    per_game = bool(item.get("per_game_lock") is True or item.get("lock_policy") == "each_mlb_game_minus_45_minutes")
    if not per_game:
        locked_at = parse_dt(item.get("locked_at") or item.get("created_at"))
        latest_pull_at = parse_dt(item.get("latest_pull_at"))
        first_start_at = parse_dt(item.get("first_game_start_utc"))
        if not (
            locked_at
            and latest_pull_at
            and latest_pull_at <= locked_at
            and (first_start_at is None or locked_at < first_start_at)
        ):
            raise LockedEvidenceUnavailable("LEGACY_DAILY_LOCK_TIME_AUTHORITY_NOT_PROVEN")
    rows: List[Dict[str, Any]] = []
    canonical_keys: List[Dict[str, str]] = []
    if per_game:
        proof = data.get("perGameLockProof") if isinstance(data.get("perGameLockProof"), list) else []
        proof_identities = {
            str(entry.get("gameIdentity") or entry.get("gameId") or "")
            for entry in proof
            if isinstance(entry, dict)
        }
        daily_identities = {_identity(row) for row in daily_picks}
        if not (
            item.get("coverage_complete") is True
            and _as_int(item.get("canonical_immutable_game_row_count")) == expected
            and len(proof) == expected
            and all(
                isinstance(entry, dict)
                and entry.get("writeOnce") is True
                and entry.get("canonicalImmutableGameRow") is True
                for entry in proof
            )
            and proof_identities == daily_identities
        ):
            raise LockedEvidenceUnavailable("PER_GAME_CANONICAL_LOCK_PROOF_INCOMPLETE")
        for daily_pick in daily_picks:
            row, key = _canonical_row(slate_date, daily_pick)
            rows.append(row)
            canonical_keys.append(key)
        authority_class = "CANONICAL_IMMUTABLE_PER_GAME_ROWS"
    else:
        rows = [copy.deepcopy(row) for row in daily_picks]
        authority_class = "WRITE_ONCE_IMMUTABLE_DAILY_LOCK_CARD_LEGACY"

    return {
        "rows": rows,
        "dailyPicks": copy.deepcopy(daily_picks),
        "authority": {
            "version": VERSION,
            "authorityClass": authority_class,
            "historicalPredictionsRecomputed": False,
            "consistentRead": True,
            "writeOnce": True,
            "cardPk": card_key["PK"],
            "cardSk": card_key["SK"],
            "cardCreatedAtUtc": item.get("created_at") or item.get("locked_at"),
            "cardModelVersion": item.get("model_version"),
            "perGameLock": per_game,
            "canonicalSingleGameRowsVerified": per_game,
            "canonicalKeys": canonical_keys,
            "predictionCount": len(rows),
        },
    }


def _clean_validation_errors(row: Dict[str, Any]) -> List[str]:
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as exact_contract

        return sorted(set(exact_contract.validate_exact_locked_row(copy.deepcopy(row))))
    except Exception as exc:
        return [f"exact_vector_validator_unavailable:{exc}"]


def audit_rows(locked_rows: List[Dict[str, Any]], score_report: Dict[str, Any], authority: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for pred in locked_rows:
        outcome = outcome_for(pred, score_report)
        clean_errors = _clean_validation_errors(pred)
        vector = pred.get("frozenFeatureVector") if isinstance(pred.get("frozenFeatureVector"), dict) else {}
        stored_official = {
            "officialPick": copy.deepcopy(pred.get("officialPick")) if "officialPick" in pred else None,
            "officialPrediction": copy.deepcopy(pred.get("officialPrediction")) if "officialPrediction" in pred else None,
            "officialPredictionStatus": copy.deepcopy(pred.get("officialPredictionStatus")),
            "lockedPrediction": copy.deepcopy(pred.get("lockedPrediction")) if "lockedPrediction" in pred else None,
        }
        base = {
            "gameId": pred.get("gameId"),
            "gameIdentity": pred.get("gameIdentity"),
            "commenceTime": pred.get("commenceTime"),
            "homeTeam": pred.get("homeTeam"),
            "awayTeam": pred.get("awayTeam"),
            "matchup": f"{pred.get('awayTeam')} at {pred.get('homeTeam')}",
            "predictedWinner": pred.get("predictedWinner"),
            "predictedSide": pred.get("predictedSide"),
            "gameWinnerScore": pred.get("score"),
            "officialPick": stored_official["officialPick"],
            "officialPrediction": stored_official["officialPrediction"],
            "officialPredictionStatus": stored_official["officialPredictionStatus"],
            "lockedPrediction": stored_official["lockedPrediction"],
            "storedOfficialPickFields": stored_official,
            "officialCardPrediction": True,
            "officialCardAuthority": authority.get("authorityClass"),
            "accuracyTargetEligible": pred.get("accuracyTargetEligible"),
            "actionability": pred.get("actionability"),
            "actionabilityReason": pred.get("actionabilityReason"),
            "gameWinnerConfidenceTier": pred.get("confidenceTier"),
            "gameWinnerTags": pred.get("tags") or [],
            "frozenFeatureVector": copy.deepcopy(vector) if vector else None,
            "frozenFeatureVectorVersion": vector.get("version"),
            "frozenFeatureVectorFingerprint": vector.get("fingerprint"),
            "pregameVectorLabels": copy.deepcopy(vector.get("labels")) if isinstance(vector.get("labels"), dict) else None,
            "cleanCohortStatus": "CLEAN" if not clean_errors else "QUARANTINED",
            "cleanCohortEligible": not clean_errors,
            "cleanCohortQuarantineReasons": clean_errors,
        }
        if not outcome:
            rows.append({
                **base,
                "status": "MISSING_FINAL_SCORE",
                "outcomeJoin": {
                    "status": "MISSING_FINAL_SCORE",
                    "source": "the_odds_api_final_scores",
                    "joinedOutsideFrozenFeatureVector": True,
                },
            })
            continue
        actual = outcome.get("winner")
        gw_correct = normalize_team(pred.get("predictedWinner")) == normalize_team(actual)
        home_won = normalize_team(actual) == normalize_team(pred.get("homeTeam"))
        rows.append({
            **base,
            "matchup": outcome.get("matchup"),
            "status": "FINAL",
            "homeScore": outcome.get("homeScore"),
            "awayScore": outcome.get("awayScore"),
            "actualWinner": actual,
            "gameWinnerCorrect": gw_correct,
            "officialCardPickCorrect": gw_correct,
            "outcomeJoin": {
                "status": "FINAL",
                "source": "the_odds_api_final_scores",
                "joinedOutsideFrozenFeatureVector": True,
                "actualWinner": actual,
                "homeScore": outcome.get("homeScore"),
                "awayScore": outcome.get("awayScore"),
                "homeWon": home_won,
                "pickCorrect": gw_correct,
            },
        })
    return rows


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    final_rows = [r for r in rows if r.get("status") == "FINAL"]
    gw_correct = [r for r in final_rows if r.get("gameWinnerCorrect") is True]
    official = [r for r in final_rows if r.get("officialCardPrediction") is True]
    official_correct = [r for r in official if r.get("officialCardPickCorrect") is True]
    clean = [r for r in final_rows if r.get("cleanCohortEligible") is True]
    quarantined = [r for r in rows if r.get("cleanCohortEligible") is not True]
    official_accuracy = round(len(official_correct) / len(official) * 100, 2) if official else None
    return {
        "auditedGameCount": len(rows),
        "finalGameCount": len(final_rows),
        "missingFinalScoreCount": len(rows) - len(final_rows),
        "gameWinnerCorrect": len(gw_correct),
        "gameWinnerWrong": len(final_rows) - len(gw_correct),
        "gameWinnerAccuracyPct": round(len(gw_correct) / len(final_rows) * 100, 2) if final_rows else None,
        "officialCardPickCount": len(official),
        "officialCardCorrect": len(official_correct),
        "officialCardWrong": len(official) - len(official_correct),
        "officialCardAccuracyPct": official_accuracy,
        "officialCardAuthorityTargetPct": OFFICIAL_CARD_AUTHORITY_TARGET_PCT,
        "officialCardAuthorityTargetMet": (
            official_accuracy >= OFFICIAL_CARD_AUTHORITY_TARGET_PCT
            if official_accuracy is not None
            else None
        ),
        "cleanRowCount": len(clean),
        "quarantinedRowCount": len(quarantined),
        "allLockedRowsCountTowardOfficialCardAccuracy": True,
        "cleanStatusDoesNotChangeOfficialCardDenominator": True,
    }


def learning_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def bucket_add(bucket: Dict[str, Dict[str, Any]], key: str, correct: bool):
        row = bucket.setdefault(key, {"count": 0, "correct": 0, "accuracyPct": None})
        row["count"] += 1
        if correct:
            row["correct"] += 1

    def finish(bucket: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        for row in bucket.values():
            row["accuracyPct"] = round(row["correct"] / row["count"] * 100, 2) if row["count"] else None
        return bucket

    tag_stats: Dict[str, Dict[str, Any]] = {}
    confidence_stats: Dict[str, Dict[str, Any]] = {}
    actionability_stats: Dict[str, Dict[str, Any]] = {}
    quarantine_reason_counts: Dict[str, int] = {}
    for row in rows:
        for reason in row.get("cleanCohortQuarantineReasons") or []:
            quarantine_reason_counts[str(reason)] = quarantine_reason_counts.get(str(reason), 0) + 1
        if row.get("status") != "FINAL":
            continue
        for tag in row.get("gameWinnerTags") or []:
            bucket_add(tag_stats, f"GW:{tag}", bool(row.get("gameWinnerCorrect")))
        if row.get("gameWinnerConfidenceTier"):
            bucket_add(confidence_stats, str(row.get("gameWinnerConfidenceTier")), bool(row.get("gameWinnerCorrect")))
        if row.get("actionability"):
            bucket_add(actionability_stats, str(row.get("actionability")), bool(row.get("gameWinnerCorrect")))
    return {
        "tagStats": finish(tag_stats),
        "confidenceStats": finish(confidence_stats),
        "actionabilityStats": finish(actionability_stats),
        "quarantineReasonCounts": quarantine_reason_counts,
        "usage": (
            "Official-card accuracy grades every immutable locked winner. Only CLEAN rows may enter ML training; "
            "quarantined rows remain diagnostic evidence. FINAL labels are joined outside the frozen pregame vector."
        ),
    }


def store_audit(report: Dict[str, Any]) -> Dict[str, Any]:
    if history.PULLS is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    slate = report.get("slate_date")
    previous = history.PULLS.get_item(
        Key={"PK": "MLB_DAILY_AUDIT#LATEST", "SK": "LATEST"},
        ConsistentRead=True,
    ).get("Item")
    if isinstance(previous, dict):
        previous_data = previous.get("data") if isinstance(previous.get("data"), dict) else {}
        report["supersedes"] = {
            "pk": previous.get("PK"),
            "sk": previous.get("SK"),
            "slateDateEt": previous.get("slate_date") or previous_data.get("slate_date"),
            "createdAt": previous.get("created_at") or previous_data.get("createdAt"),
            "proofType": previous_data.get("proofType"),
            "historicalRunPreserved": True,
        }
    item = history.ddb_safe({
        "PK": f"MLB_DAILY_AUDIT#{slate}",
        "SK": f"AUDIT#{report.get('createdAt')}",
        "record_type": "mlb_yesterday_immutable_lock_audit",
        "sport": "mlb",
        "slate_date": slate,
        "created_at": report.get("createdAt"),
        "data": report,
    })
    latest = history.ddb_safe({
        "PK": "MLB_DAILY_AUDIT#LATEST",
        "SK": "LATEST",
        "record_type": "mlb_yesterday_immutable_lock_audit_latest",
        "sport": "mlb",
        "slate_date": slate,
        "created_at": report.get("createdAt"),
        "data": report,
    })
    history.PULLS.put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
    )
    history.PULLS.put_item(Item=latest)
    return {
        "ok": True,
        "pk": item["PK"],
        "sk": item["SK"],
        "historicalRunWriteOnce": True,
        "latestPointerUpdated": True,
        "supersededLatest": copy.deepcopy(report.get("supersedes")),
    }


def _write_report(report: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(REPORT_PATH) or ".", exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2, default=str)
        report_file.write("\n")


def _failed_report(
    slate: str,
    created_at: str,
    status: str,
    reason: str,
    final_count: int,
    official_schedule: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "proofType": "MLB_YESTERDAY_IMMUTABLE_LOCK_AUDIT",
        "version": VERSION,
        "createdAt": created_at,
        "sport": "mlb",
        "slate_date": slate,
        "status": status,
        "reason": reason,
        "finalScoreCount": final_count,
        "officialSchedule": copy.deepcopy(official_schedule),
        "stored": False,
        "failClosed": True,
        "historicalPredictionsRecomputed": False,
        "policy": (
            "The audit requires an independently verified exact-date official MLB schedule, complete FINAL provider "
            "coverage, and one-to-one immutable locked game identities. Any uncertainty fails closed without changing LATEST."
        ),
    }


def build(slate_date: Optional[str] = None, days_from: int = 3, store: bool = True, write_file: bool = True) -> Dict[str, Any]:
    slate = slate_date or yesterday_et()
    score_report = pull_final_scores(slate, days_from=days_from)
    created_at = now_iso()
    final_count = _as_int(score_report.get("finalScoreCount"))
    try:
        official_schedule = pull_official_schedule(slate)
        if not (
            isinstance(official_schedule, dict)
            and official_schedule.get("ok") is True
            and official_schedule.get("exactDateVerified") is True
            and str(official_schedule.get("requestedDate") or "") == str(slate)
            and isinstance(official_schedule.get("totalGames"), int)
            and not isinstance(official_schedule.get("totalGames"), bool)
            and official_schedule.get("totalGames") >= 0
        ):
            raise OfficialScheduleUnverified("OFFICIAL_MLB_SCHEDULE_VERIFICATION_INVALID")
    except Exception as exc:
        report = _failed_report(
            slate,
            created_at,
            "OFFICIAL_SCHEDULE_UNVERIFIED",
            str(exc),
            final_count,
        )
        if write_file:
            _write_report(report)
        return report

    scheduled_game_count = _as_int(official_schedule.get("totalGames"))
    if score_report.get("ok") is not True or final_count != scheduled_game_count:
        report = _failed_report(
            slate,
            created_at,
            "FINAL_OUTCOMES_INCOMPLETE",
            f"OFFICIAL_SCHEDULE_GAMES_{scheduled_game_count}_FINAL_OUTCOMES_{final_count}",
            final_count,
            official_schedule,
        )
        if write_file:
            _write_report(report)
        return report

    if scheduled_game_count == 0:
        report = {
            "ok": True,
            "proofType": "MLB_YESTERDAY_IMMUTABLE_LOCK_AUDIT",
            "version": VERSION,
            "createdAt": created_at,
            "sport": "mlb",
            "slate_date": slate,
            "status": "NO_FINAL_GAMES_NO_AUDIT_REQUIRED",
            "finalScoreCount": 0,
            "officialSchedule": official_schedule,
            "officialScheduleZeroGamesVerified": True,
            "stored": False,
            "historicalPredictionsRecomputed": False,
            "policy": (
                "The exact-date official MLB Stats API schedule independently verified totalGames=0. No historical pick "
                "was reconstructed and LATEST was not changed."
            ),
        }
        if write_file:
            _write_report(report)
        return report

    try:
        locked = load_locked_predictions(slate)
    except LockedEvidenceUnavailable as exc:
        report = _failed_report(
            slate,
            created_at,
            "LOCKED_EVIDENCE_UNAVAILABLE",
            str(exc),
            final_count,
            official_schedule,
        )
        if write_file:
            _write_report(report)
        return report

    try:
        final_join = validate_one_to_one_final_join(locked["rows"], score_report)
    except FinalOutcomeJoinUnavailable as exc:
        report = _failed_report(
            slate,
            created_at,
            "FINAL_OUTCOME_IDENTITY_MISMATCH",
            str(exc),
            final_count,
            official_schedule,
        )
        report["immutableLockAuthority"] = locked["authority"]
        if write_file:
            _write_report(report)
        return report

    rows = audit_rows(locked["rows"], score_report, locked["authority"])
    summary = summarize(rows)
    complete = bool(
        len(rows) == final_count
        and summary.get("missingFinalScoreCount") == 0
        and summary.get("officialCardPickCount") == len(rows)
    )
    report = {
        "ok": complete,
        "proofType": "MLB_YESTERDAY_IMMUTABLE_LOCK_AUDIT",
        "version": VERSION,
        "createdAt": created_at,
        "sport": "mlb",
        "slate_date": slate,
        "status": "COMPLETE" if complete else "FINAL_JOIN_INCOMPLETE",
        "failClosed": not complete,
        "finalScoreCount": final_count,
        "officialSchedule": official_schedule,
        "lockedPredictionCount": len(locked["rows"]),
        "immutableLockAuthority": locked["authority"],
        "finalOutcomeIdentityJoin": final_join,
        "historicalPredictionsRecomputed": False,
        "finalOutcomeLabelsJoinedOutsideFrozenVector": True,
        "summary": summary,
        "learningSummary": learning_summary(rows),
        "rows": rows,
        "finalScores": score_report.get("finalScores") or [],
        "policy": (
            "Audit every immutable locked MLB winner against FINAL outcomes joined outside the frozen vector. Every locked "
            "winner remains an official-card prediction regardless of playability. The rolling official-card authority target "
            "is 90%; 50/60/70/80 are reporting-only milestones. Clean-vector status controls ML training eligibility, not "
            "the official-card accuracy denominator. ROI is not a promotion gate."
        ),
        "officialCardAuthorityTargetPct": OFFICIAL_CARD_AUTHORITY_TARGET_PCT,
        "progressMilestonesPct": [50.0, 60.0, 70.0, 80.0],
        "progressMilestonesReportingOnly": True,
        "roiPromotionGate": False,
    }
    if store and report["ok"] is True:
        try:
            report["stored"] = store_audit(report)
        except Exception as exc:
            report["storeError"] = str(exc)
            report["stored"] = False
            report["ok"] = False
            report["status"] = "AUDIT_STORAGE_FAILED"
            report["failClosed"] = True
    else:
        report["stored"] = False
    if write_file:
        _write_report(report)
    return report


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
