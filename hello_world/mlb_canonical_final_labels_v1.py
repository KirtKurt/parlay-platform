from __future__ import annotations

import copy
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import boto3

import inqsi_pull_history as history
import mlb_daily_per_game_lock_patch as per_game_lock
import mlb_doubleheader_safe_audit_patch as doubleheader_audit
import mlb_official_schedule_authority as official_schedule
import mlb_rolling_24h_audit as rolling_audit
import mlb_slate_coverage_patch as slate_coverage


VERSION = "MLB-CANONICAL-FINAL-LABEL-v1-official-game-pk-write-once"
PROOF_VERSION = "MLB-CANONICAL-SETTLEMENT-PROOF-v1"
RECORD_TYPE = "mlb_canonical_official_final_label"
SOURCE = "MLB Stats API exact-date official FINAL"
SOURCE_URL = "https://statsapi.mlb.com/api/v1/schedule"
LABEL_PK_PREFIX = "MLB_CANONICAL_FINAL_LABEL#"
LABEL_SK_PREFIX = "GAME_PK#"
SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))


dynamodb = boto3.resource("dynamodb")
OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE", "")
outcomes_tbl = dynamodb.Table(OUTCOMES_TABLE) if OUTCOMES_TABLE else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _slate_date_et() -> str:
    return _now().astimezone(SLATE_TZ).date().isoformat()


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
    except Exception:
        pass
    return value


def _conditional_collision(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    return str((response.get("Error") or {}).get("Code") or "") == (
        "ConditionalCheckFailedException"
    )


def _ordered_teams(row: Dict[str, Any]) -> Tuple[str, str]:
    return (
        official_schedule.normalize_team(row.get("awayTeam") or row.get("away_team")),
        official_schedule.normalize_team(row.get("homeTeam") or row.get("home_team")),
    )


def _official_game_pk_values(row: Dict[str, Any]) -> Set[str]:
    values: Set[str] = set()
    containers = [
        row,
        row.get("canonicalLockAuthority") or {},
        (row.get("canonicalLockAuthority") or {}).get("providerAliasCrosswalk") or {},
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("officialGamePk", "official_game_pk"):
            value = container.get(key)
            if value not in (None, ""):
                values.add(str(value).strip())
        for key in (
            "officialGameId",
            "official_game_id",
            "gameId",
            "game_id",
            "gameIdentity",
            "canonicalLockedGameId",
        ):
            value = str(container.get(key) or "").strip()
            if value.startswith("mlb_statsapi:"):
                values.add(value.split(":", 1)[1])
    return {value for value in values if value}


def _provider_event_id(row: Dict[str, Any]) -> Optional[str]:
    authority = row.get("canonicalLockAuthority") or {}
    proof = authority.get("providerAliasCrosswalk") or {}
    values: Set[str] = set()
    for container in (row, authority, proof):
        if not isinstance(container, dict):
            continue
        for key in (
            "providerEventId",
            "provider_event_id",
            "providerGameId",
            "provider_game_id",
        ):
            value = str(container.get(key) or "").strip()
            if value and not value.startswith("mlb_statsapi:"):
                values.add(value[len("provider:") :] if value.startswith("provider:") else value)
    return next(iter(values)) if len(values) == 1 else None


def _vector_fingerprint(row: Dict[str, Any]) -> Optional[str]:
    vector = row.get("frozenFeatureVector") or {}
    value = str(vector.get("fingerprint") or "").strip() if isinstance(vector, dict) else ""
    return value or None


def _training_verdict(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    authority = row.get("canonicalLockAuthority") or {}
    freeze = row.get("mlFeatureFreeze") or {}
    reasons = {
        str(reason)
        for source in (
            authority.get("trainingExclusionReasons") or [],
            row.get("trainingExclusionReasons") or [],
            freeze.get("trainingExclusionReasons") or [],
        )
        for reason in source
        if str(reason)
    }
    eligible = bool(
        authority.get("learningEligible") is True
        and _vector_fingerprint(row)
        and row.get("trainingEligible", freeze.get("trainingEligible")) is True
    )
    snapshot = row.get("fundamentalsSnapshotV2")
    if not isinstance(snapshot, dict) or not snapshot:
        reasons.add("fundamentals_v2_missing")
    else:
        try:
            import mlb_fundamentals_snapshot_v2 as snapshot_v2

            errors = snapshot_v2.validate(snapshot)
            reasons.update(errors)
            lock_at = (
                row.get("lockedAtUtc")
                or (row.get("slatePredictionLock") or {}).get("lockAtUtc")
                or (row.get("frozenFeatureVector") or {}).get("lockAtUtc")
            )
            persisted_at = row.get("predictionPersistedAtUtc")
            if not persisted_at:
                reasons.add("fundamentals_v2_prediction_persistence_proof_missing")
            elif not errors and not snapshot_v2.provenance_is_lock_safe(
                snapshot,
                prediction_persisted_at=persisted_at,
                lock_at=lock_at,
            ):
                reasons.add("fundamentals_v2_evidence_not_lock_safe")
            if snapshot.get("trainingEligibleAtCapture") is not True:
                reasons.update(
                    snapshot.get("trainingExclusionReasons")
                    or ["fundamentals_v2_pregame_sources_incomplete"]
                )
        except Exception as exc:
            reasons.add(
                f"fundamentals_v2_validation_unavailable:{type(exc).__name__}"
            )
    eligible = bool(eligible and not reasons)
    if not eligible and not reasons:
        reasons.add("canonical_lock_not_learning_eligible")
    return eligible, sorted(reasons)


def _canonical_lock_payload_fingerprint(row: Dict[str, Any]) -> str:
    material = copy.deepcopy(row)
    # This authority envelope is derived after the consistent read. It is not
    # part of the immutable stored lock payload and must not change its hash.
    authority = material.pop("canonicalLockAuthority", None) or {}
    method = authority.get("providerIdentityMatchMethod") or authority.get("matchMethod")
    if method == rolling_audit.VERIFIED_PROVIDER_ALIAS_MATCH_METHOD:
        # The rolling authority adds this alias to a copy after reading the
        # immutable fallback lock. Do not misrepresent it as stored lock data.
        for key in (
            "providerEventId",
            "provider_event_id",
            "providerGameId",
            "provider_game_id",
        ):
            material.pop(key, None)
    return history.canonical_payload_fingerprint(material)


def official_finals_url(slate_date: str) -> str:
    query = urllib.parse.urlencode(
        {
            "sportId": "1",
            "startDate": slate_date,
            "endDate": slate_date,
            "hydrate": "linescore",
        }
    )
    return f"{SOURCE_URL}?{query}"


def _http_get_json(url: str, timeout: int = 15) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-mlb-canonical-settlement/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _score(side: Dict[str, Any]) -> Optional[int]:
    value = side.get("score")
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _official_final_evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    return history.ddb_safe({
        "officialGamePk": row.get("officialGamePk"),
        "officialDate": row.get("officialDate"),
        "gameDate": row.get("gameDate"),
        "awayTeam": row.get("awayTeam"),
        "homeTeam": row.get("homeTeam"),
        "awayScore": row.get("awayScore"),
        "homeScore": row.get("homeScore"),
        "winner": row.get("winner"),
        "officialStatus": copy.deepcopy(row.get("officialStatus") or {}),
    })


def validate_official_schedule_payload(payload: Any, slate_date: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("MLB_OFFICIAL_FINAL_PAYLOAD_NOT_OBJECT")
    total_games = payload.get("totalGames")
    if isinstance(total_games, bool) or not isinstance(total_games, int) or total_games < 0:
        raise RuntimeError("MLB_OFFICIAL_FINAL_TOTAL_GAMES_INVALID")
    dates = payload.get("dates")
    if not isinstance(dates, list):
        raise RuntimeError("MLB_OFFICIAL_FINAL_DATES_INVALID")

    raw_games: List[Dict[str, Any]] = []
    for date_row in dates:
        if not isinstance(date_row, dict) or str(date_row.get("date") or "") != slate_date:
            raise RuntimeError("MLB_OFFICIAL_FINAL_NOT_EXACT_DATE")
        games = date_row.get("games")
        if not isinstance(games, list):
            raise RuntimeError("MLB_OFFICIAL_FINAL_GAMES_INVALID")
        raw_games.extend(games)
    if len(raw_games) != total_games:
        raise RuntimeError("MLB_OFFICIAL_FINAL_GAME_COUNT_MISMATCH")

    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for raw in raw_games:
        if not isinstance(raw, dict):
            raise RuntimeError("MLB_OFFICIAL_FINAL_GAME_ROW_INVALID")
        official_pk = str(raw.get("gamePk") or "").strip()
        if not official_pk or official_pk in seen:
            raise RuntimeError("MLB_OFFICIAL_FINAL_GAME_PK_INVALID_OR_DUPLICATE")
        seen.add(official_pk)
        if str(raw.get("officialDate") or slate_date) != slate_date:
            raise RuntimeError(f"MLB_OFFICIAL_FINAL_GAME_DATE_MISMATCH:{official_pk}")
        teams = raw.get("teams") or {}
        away_side = teams.get("away") or {}
        home_side = teams.get("home") or {}
        away = ((away_side.get("team") or {}).get("name"))
        home = ((home_side.get("team") or {}).get("name"))
        if not away or not home:
            raise RuntimeError(f"MLB_OFFICIAL_FINAL_TEAM_IDENTITY_MISSING:{official_pk}")
        status = raw.get("status") or {}
        abstract_state = str(status.get("abstractGameState") or "")
        is_final = abstract_state.upper() == "FINAL"
        away_score = _score(away_side)
        home_score = _score(home_side)
        winner: Optional[str] = None
        if is_final:
            if away_score is None or home_score is None:
                raise RuntimeError(f"MLB_OFFICIAL_FINAL_SCORE_MISSING:{official_pk}")
            if away_score == home_score:
                raise RuntimeError(f"MLB_OFFICIAL_FINAL_TIED_SCORE_UNSUPPORTED:{official_pk}")
            winner = home if home_score > away_score else away
        row = {
            "officialGamePk": official_pk,
            "officialDate": slate_date,
            "gameDate": raw.get("gameDate"),
            "awayTeam": str(away),
            "homeTeam": str(home),
            "awayScore": away_score,
            "homeScore": home_score,
            "winner": winner,
            "completed": is_final,
            "officialStatus": {
                "abstractGameState": status.get("abstractGameState"),
                "codedGameState": status.get("codedGameState"),
                "statusCode": status.get("statusCode"),
                "detailedState": status.get("detailedState"),
            },
        }
        row["sourcePayloadFingerprint"] = history.canonical_payload_fingerprint(
            _official_final_evidence(row)
        )
        rows.append(row)
    return {
        "ok": True,
        "source": SOURCE,
        "sourceUrl": official_finals_url(slate_date),
        "slateDateEt": slate_date,
        "officialGameCount": len(rows),
        "officialFinalCount": sum(row.get("completed") is True for row in rows),
        "games": sorted(rows, key=lambda row: str(row.get("officialGamePk") or "")),
    }


def fetch_official_schedule(
    slate_date: str,
    *,
    timeout: int = 15,
    http_get: Optional[Callable[[str, int], Any]] = None,
) -> Dict[str, Any]:
    getter = http_get or (lambda url, seconds: _http_get_json(url, seconds))
    return validate_official_schedule_payload(
        getter(official_finals_url(slate_date), timeout),
        slate_date,
    )


def _query_partition(table: Any, pk: str, sk_prefix: str) -> List[Dict[str, Any]]:
    if table is None:
        raise RuntimeError("SNAPSHOTS_TABLE_NOT_CONFIGURED")
    items: List[Dict[str, Any]] = []
    start_key = None
    while True:
        args: Dict[str, Any] = {
            "KeyConditionExpression": (
                history.Key("PK").eq(pk) & history.Key("SK").begins_with(sk_prefix)
            ),
            "ConsistentRead": True,
        }
        if start_key:
            args["ExclusiveStartKey"] = start_key
        response = table.query(**args)
        items.extend(
            copy.deepcopy(item)
            for item in response.get("Items") or []
            if isinstance(item, dict)
        )
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def _validated_canonical_locks(
    slate_date: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    items = _query_partition(
        history.PULLS,
        f"GAME_WINNERS#mlb#{slate_date}",
        "LOCKED#GAME#",
    )
    rows: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for item in items:
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        errors = rolling_audit._canonical_lock_item_errors(item, slate_date)
        if errors:
            rejected.append(
                {
                    "sourcePk": item.get("PK"),
                    "sourceSk": item.get("SK"),
                    "officialGamePks": sorted(_official_game_pk_values(data)),
                    "errors": errors,
                }
            )
            continue
        row = copy.deepcopy(data)
        row["canonicalLockAuthority"] = rolling_audit._canonical_lock_authority(
            item,
            slate_date,
        )
        rows.append(row)

    rows = rolling_audit._apply_verified_provider_aliases(rows, slate_date)
    valid: List[Dict[str, Any]] = []
    for row in rows:
        errors: List[str] = []
        authority = row.get("canonicalLockAuthority") or {}
        if not doubleheader_audit._canonical_authority(
            row,
            rolling_audit.CANONICAL_LOCK_AUTHORITY_VERSION,
        ):
            errors.append("canonical_lock_authority_invalid")
        if not str(authority.get("stageFingerprint") or ""):
            errors.append("canonical_stage_fingerprint_missing")
        official_values = _official_game_pk_values(row)
        if len(official_values) != 1:
            errors.append("official_game_pk_missing_or_conflicting")
        if not all(_ordered_teams(row)):
            errors.append("ordered_team_identity_missing")
        method = authority.get("providerIdentityMatchMethod") or authority.get("matchMethod")
        if method == rolling_audit.VERIFIED_PROVIDER_ALIAS_MATCH_METHOD and not (
            doubleheader_audit._verified_provider_alias_authority(row)
        ):
            errors.append("verified_provider_alias_authority_invalid")
        if errors:
            rejected.append(
                {
                    "sourcePk": authority.get("sourcePk"),
                    "sourceSk": authority.get("sourceSk"),
                    "officialGamePks": sorted(official_values),
                    "errors": sorted(set(errors)),
                }
            )
            continue
        valid.append(row)
    return valid, rejected


def _terminal_outcome_errors(item: Dict[str, Any], slate_date: str) -> List[str]:
    errors: List[str] = []
    if item.get("record_type") != per_game_lock.LOCK_OUTCOME_RECORD_TYPE:
        errors.append("terminal_record_type_mismatch")
    if item.get("version") != per_game_lock.LOCK_OUTCOME_VERSION:
        errors.append("terminal_version_mismatch")
    if str(item.get("slate_date") or "") != slate_date:
        errors.append("terminal_slate_mismatch")
    if item.get("lock_status") != "LOCKED_NO_PREDICTION_DATA":
        errors.append("terminal_status_mismatch")
    if item.get("lock_outcome_recorded") is not True or item.get("write_once") is not True:
        errors.append("terminal_write_once_proof_missing")
    if item.get("locked_prediction") is not False or item.get("training_eligible") is not False:
        errors.append("terminal_prediction_or_training_exclusion_missing")
    expected = slate_coverage._record_fingerprint(item, "lock_outcome_fingerprint")
    if str(item.get("lock_outcome_fingerprint") or "") != expected:
        errors.append("terminal_fingerprint_mismatch")
    try:
        errors.extend(
            per_game_lock._provider_manifest_authority_errors(history.PULLS, item)
        )
    except Exception as exc:
        errors.append(
            f"terminal_manifest_authority_validation_failed:{type(exc).__name__}"
        )
    return sorted(set(errors))


def _terminal_official_game_pk(
    item: Dict[str, Any],
    crosswalk: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    values = _official_game_pk_values(item)
    game_id = str(item.get("game_id") or "").strip()
    if game_id.startswith("mlb_statsapi:"):
        values.add(game_id.split(":", 1)[1])
    if len(values) == 1:
        return next(iter(values))
    if values:
        return None
    away, home = _ordered_teams((item.get("data") or {}).get("row") or item)
    provider_id = game_id[len("provider:") :] if game_id.startswith("provider:") else game_id
    candidates = [
        official_pk
        for official_pk, proof in crosswalk.items()
        if str(proof.get("providerEventId") or "") == provider_id
        and (
            str(proof.get("awayTeamNormalized") or ""),
            str(proof.get("homeTeamNormalized") or ""),
        )
        == (away, home)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _validated_terminal_outcomes(
    slate_date: str,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    items = _query_partition(
        history.PULLS,
        f"LOCKED_PICKS#mlb#{slate_date}",
        "PER_GAME_LOCK_OUTCOME#TMINUS45#",
    )
    crosswalk = rolling_audit._verified_provider_alias_crosswalk(slate_date)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    rejected: List[Dict[str, Any]] = []
    for item in items:
        errors = _terminal_outcome_errors(item, slate_date)
        official_pk = _terminal_official_game_pk(item, crosswalk)
        if not official_pk:
            errors.append("terminal_official_game_pk_unresolved")
        if errors:
            rejected.append(
                {
                    "sourcePk": item.get("PK"),
                    "sourceSk": item.get("SK"),
                    "officialGamePk": official_pk,
                    "errors": sorted(set(errors)),
                }
            )
            continue
        grouped.setdefault(str(official_pk), []).append(item)
    valid: Dict[str, Dict[str, Any]] = {}
    for official_pk, candidates in grouped.items():
        if len(candidates) != 1:
            rejected.append(
                {
                    "officialGamePk": official_pk,
                    "errors": ["duplicate_terminal_official_game_pk"],
                    "candidateCount": len(candidates),
                }
            )
            continue
        valid[official_pk] = candidates[0]
    return valid, rejected


def _lock_index(
    rows: Iterable[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        official_values = _official_game_pk_values(row)
        if len(official_values) == 1:
            grouped.setdefault(next(iter(official_values)), []).append(row)
    valid: Dict[str, Dict[str, Any]] = {}
    rejected: List[Dict[str, Any]] = []
    for official_pk, candidates in grouped.items():
        if len(candidates) != 1:
            rejected.append(
                {
                    "officialGamePk": official_pk,
                    "errors": ["duplicate_canonical_locks_for_official_game_pk"],
                    "candidateCount": len(candidates),
                }
            )
            continue
        valid[official_pk] = candidates[0]
    return valid, rejected


def _lock_integrity_snapshot(
    rows: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    return {
        official_pk: {
            "payloadFingerprint": _canonical_lock_payload_fingerprint(row),
            "vectorFingerprint": _vector_fingerprint(row),
            "sourcePk": (row.get("canonicalLockAuthority") or {}).get("sourcePk"),
            "sourceSk": (row.get("canonicalLockAuthority") or {}).get("sourceSk"),
            "stageFingerprint": (row.get("canonicalLockAuthority") or {}).get(
                "stageFingerprint"
            ),
        }
        for official_pk, row in rows.items()
    }


def _label_key(slate_date: str, official_game_pk: str) -> Dict[str, str]:
    return {
        "PK": f"{LABEL_PK_PREFIX}{slate_date}",
        "SK": f"{LABEL_SK_PREFIX}{official_game_pk}",
    }


def _settlement_material(item: Dict[str, Any]) -> Dict[str, Any]:
    fields = (
        "version",
        "record_type",
        "sport",
        "slate_date",
        "official_game_pk",
        "provider_event_id",
        "provider_identity_match_method",
        "provider_alias_crosswalk",
        "away_team",
        "home_team",
        "game_date_utc",
        "away_score",
        "home_score",
        "winner",
        "home_won",
        "predicted_winner",
        "predicted_side",
        "correct",
        "canonical_lock_pk",
        "canonical_lock_sk",
        "canonical_lock_authority_version",
        "canonical_lock_official_audit_eligible",
        "canonical_lock_learning_eligible",
        "exact_lock_vector_validated",
        "canonical_stage_fingerprint",
        "canonical_lock_payload_fingerprint",
        "frozen_feature_vector_fingerprint",
        "fundamentals_snapshot_v2_version",
        "fundamentals_snapshot_v2_fingerprint",
        "source",
        "source_url",
        "source_payload_fingerprint",
        "official_status",
        "accuracy_eligible",
        "training_eligible",
        "training_exclusion_reasons",
    )
    return {field: copy.deepcopy(item.get(field)) for field in fields}


def _record_fingerprint(item: Dict[str, Any]) -> str:
    return history.canonical_payload_fingerprint(
        {key: value for key, value in item.items() if key != "record_fingerprint"}
    )


def _label_record_errors(item: Any, slate_date: str, official_game_pk: str) -> List[str]:
    if not isinstance(item, dict):
        return ["canonical_label_record_missing"]
    errors: List[str] = []
    expected_key = _label_key(slate_date, official_game_pk)
    if item.get("PK") != expected_key["PK"] or item.get("SK") != expected_key["SK"]:
        errors.append("canonical_label_key_mismatch")
    if item.get("record_type") != RECORD_TYPE or item.get("version") != VERSION:
        errors.append("canonical_label_contract_mismatch")
    if item.get("write_once") is not True or item.get("completed") is not True:
        errors.append("canonical_label_write_once_or_final_flag_missing")
    if str(item.get("official_game_pk") or "") != official_game_pk:
        errors.append("canonical_label_official_game_pk_mismatch")
    if str(item.get("settlement_fingerprint") or "") != history.canonical_payload_fingerprint(
        _settlement_material(item)
    ):
        errors.append("canonical_label_settlement_fingerprint_mismatch")
    if str(item.get("record_fingerprint") or "") != _record_fingerprint(item):
        errors.append("canonical_label_record_fingerprint_mismatch")
    final_evidence = history.ddb_safe({
        "officialGamePk": item.get("official_game_pk"),
        "officialDate": item.get("slate_date"),
        "gameDate": item.get("game_date_utc"),
        "awayTeam": item.get("away_team"),
        "homeTeam": item.get("home_team"),
        "awayScore": item.get("away_score"),
        "homeScore": item.get("home_score"),
        "winner": item.get("winner"),
        "officialStatus": copy.deepcopy(item.get("official_status") or {}),
    })
    if str(item.get("source_payload_fingerprint") or "") != (
        history.canonical_payload_fingerprint(final_evidence)
    ):
        errors.append("canonical_label_official_source_payload_fingerprint_mismatch")
    try:
        away_score = int(item.get("away_score"))
        home_score = int(item.get("home_score"))
        expected_winner = (
            item.get("home_team") if home_score > away_score else item.get("away_team")
        )
        if home_score == away_score or official_schedule.normalize_team(
            item.get("winner")
        ) != official_schedule.normalize_team(expected_winner):
            errors.append("canonical_label_winner_score_mismatch")
    except Exception:
        errors.append("canonical_label_final_score_invalid")
    expected_correct = official_schedule.normalize_team(
        item.get("predicted_winner")
    ) == official_schedule.normalize_team(item.get("winner"))
    if not isinstance(item.get("correct"), bool) or item.get("correct") != expected_correct:
        errors.append("canonical_label_pick_correct_mismatch")
    return sorted(set(errors))


def _build_label(
    slate_date: str,
    final: Dict[str, Any],
    locked: Dict[str, Any],
    observed_at_utc: str,
) -> Dict[str, Any]:
    official_pk = str(final["officialGamePk"])
    authority = copy.deepcopy(locked.get("canonicalLockAuthority") or {})
    provider_method = authority.get("providerIdentityMatchMethod") or authority.get(
        "matchMethod"
    )
    predicted_winner = str(locked.get("predictedWinner") or "")
    winner = str(final.get("winner") or "")
    if not predicted_winner or not winner:
        raise RuntimeError("CANONICAL_LABEL_WINNER_IDENTITY_MISSING")
    home_won = official_schedule.normalize_team(winner) == official_schedule.normalize_team(
        final.get("homeTeam")
    )
    correct = official_schedule.normalize_team(predicted_winner) == official_schedule.normalize_team(
        winner
    )
    vector_fingerprint = _vector_fingerprint(locked)
    training_eligible, training_exclusions = _training_verdict(locked)
    fundamentals_v2 = locked.get("fundamentalsSnapshotV2") or {}
    item: Dict[str, Any] = {
        **_label_key(slate_date, official_pk),
        "record_type": RECORD_TYPE,
        "version": VERSION,
        "sport": "mlb",
        "slate_date": slate_date,
        "official_game_pk": official_pk,
        "provider_event_id": _provider_event_id(locked),
        "provider_identity_match_method": (
            provider_method or "exact_official_game_pk_and_ordered_teams"
        ),
        "provider_alias_crosswalk": (
            copy.deepcopy(authority.get("providerAliasCrosswalk") or {})
            if provider_method == rolling_audit.VERIFIED_PROVIDER_ALIAS_MATCH_METHOD
            else None
        ),
        "away_team": final.get("awayTeam"),
        "home_team": final.get("homeTeam"),
        "game_date_utc": final.get("gameDate"),
        "away_score": final.get("awayScore"),
        "home_score": final.get("homeScore"),
        "winner": winner,
        "home_won": home_won,
        "predicted_winner": predicted_winner,
        "predicted_side": locked.get("predictedSide"),
        "correct": correct,
        "status": "GRADED",
        "completed": True,
        "canonical_lock_pk": authority.get("sourcePk"),
        "canonical_lock_sk": authority.get("sourceSk"),
        "canonical_lock_authority_version": authority.get("version"),
        "canonical_lock_official_audit_eligible": authority.get(
            "officialAuditEligible"
        )
        is True,
        "canonical_lock_learning_eligible": authority.get("learningEligible") is True,
        "exact_lock_vector_validated": authority.get("exactLockVectorValidated") is True,
        "canonical_stage_fingerprint": authority.get("stageFingerprint"),
        "canonical_lock_payload_fingerprint": _canonical_lock_payload_fingerprint(locked),
        "frozen_feature_vector_fingerprint": vector_fingerprint,
        "fundamentals_snapshot_v2_version": fundamentals_v2.get("version"),
        "fundamentals_snapshot_v2_fingerprint": fundamentals_v2.get("fingerprint"),
        "source": SOURCE,
        "source_url": official_finals_url(slate_date),
        "source_payload_fingerprint": final.get("sourcePayloadFingerprint"),
        "official_status": copy.deepcopy(final.get("officialStatus") or {}),
        "observed_at_utc": observed_at_utc,
        "created_at": observed_at_utc,
        "write_once": True,
        "lock_and_vector_mutation_allowed": False,
        "labels_joined_outside_immutable_pregame_vector": True,
        "accuracy_eligible": True,
        "training_eligible": training_eligible,
        "training_exclusion_reasons": sorted(set(training_exclusions)),
    }
    prepared = history.ddb_safe(item)
    prepared["settlement_fingerprint"] = history.canonical_payload_fingerprint(
        _settlement_material(prepared)
    )
    prepared["record_fingerprint"] = _record_fingerprint(prepared)
    return prepared


def _write_label(item: Dict[str, Any]) -> Dict[str, Any]:
    if outcomes_tbl is None:
        return {
            "ok": False,
            "status": "WRITE_FAILED",
            "officialGamePk": item.get("official_game_pk"),
            "error": "OUTCOMES_TABLE_NOT_CONFIGURED",
        }
    official_pk = str(item.get("official_game_pk") or "")
    slate_date = str(item.get("slate_date") or "")
    key = _label_key(slate_date, official_pk)
    try:
        outcomes_tbl.put_item(
            Item=copy.deepcopy(item),
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return {
            "ok": True,
            "status": "CREATED",
            "officialGamePk": official_pk,
            "pk": key["PK"],
            "sk": key["SK"],
            "settlementFingerprint": item.get("settlement_fingerprint"),
        }
    except Exception as exc:
        if not _conditional_collision(exc):
            return {
                "ok": False,
                "status": "WRITE_FAILED",
                "officialGamePk": official_pk,
                "error": f"{type(exc).__name__}:{exc}",
            }
    try:
        existing = outcomes_tbl.get_item(Key=key, ConsistentRead=True).get("Item")
    except Exception as exc:
        return {
            "ok": False,
            "status": "CONFLICT_READ_FAILED",
            "officialGamePk": official_pk,
            "error": f"{type(exc).__name__}:{exc}",
        }
    errors = _label_record_errors(existing, slate_date, official_pk)
    if errors:
        return {
            "ok": False,
            "status": "IMMUTABLE_EXISTING_INVALID",
            "officialGamePk": official_pk,
            "errors": errors,
        }
    if str(existing.get("settlement_fingerprint") or "") == str(
        item.get("settlement_fingerprint") or ""
    ):
        return {
            "ok": True,
            "status": "IDEMPOTENT_EXISTING",
            "officialGamePk": official_pk,
            "pk": key["PK"],
            "sk": key["SK"],
            "settlementFingerprint": existing.get("settlement_fingerprint"),
        }
    return {
        "ok": False,
        "status": "OFFICIAL_FINAL_CORRECTION_CONFLICT",
        "officialGamePk": official_pk,
        "existingSettlementFingerprint": existing.get("settlement_fingerprint"),
        "proposedSettlementFingerprint": item.get("settlement_fingerprint"),
        "policy": "Write-once canonical labels never overwrite a differing official FINAL; correction conflicts require manual review.",
    }


def _labels_for_slate(slate_date: str) -> List[Dict[str, Any]]:
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE_NOT_CONFIGURED")
    rows: List[Dict[str, Any]] = []
    start_key = None
    while True:
        args: Dict[str, Any] = {
            "KeyConditionExpression": history.Key("PK").eq(
                f"{LABEL_PK_PREFIX}{slate_date}"
            ),
            "ConsistentRead": True,
        }
        if start_key:
            args["ExclusiveStartKey"] = start_key
        response = outcomes_tbl.query(**args)
        rows.extend(
            copy.deepcopy(item)
            for item in response.get("Items") or []
            if isinstance(item, dict)
            and str(item.get("SK") or "").startswith(LABEL_SK_PREFIX)
        )
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            break
    return sorted(rows, key=lambda item: str(item.get("official_game_pk") or ""))


def _proof_from_stored(
    slate_date: str,
    current_locks: Dict[str, Dict[str, Any]],
    terminal_outcomes: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    labels = _labels_for_slate(slate_date)
    invalid: List[Dict[str, Any]] = []
    valid: List[Dict[str, Any]] = []
    for item in labels:
        official_pk = str(item.get("official_game_pk") or "")
        errors = _label_record_errors(item, slate_date, official_pk)
        locked = current_locks.get(official_pk)
        if locked and official_pk in terminal_outcomes:
            errors.append("canonical_label_current_lock_terminal_conflict")
        if not locked:
            errors.append(
                "canonical_label_current_lock_missing"
                if official_pk not in terminal_outcomes
                else "canonical_label_conflicts_with_no_prediction_terminal"
            )
        else:
            authority = locked.get("canonicalLockAuthority") or {}
            comparisons = {
                "canonical_lock_pk": authority.get("sourcePk"),
                "canonical_lock_sk": authority.get("sourceSk"),
                "canonical_stage_fingerprint": authority.get("stageFingerprint"),
                "canonical_lock_payload_fingerprint": (
                    _canonical_lock_payload_fingerprint(locked)
                ),
                "frozen_feature_vector_fingerprint": _vector_fingerprint(locked),
                "provider_event_id": _provider_event_id(locked),
            }
            for field, expected in comparisons.items():
                if item.get(field) != expected:
                    errors.append(f"canonical_label_current_{field}_mismatch")
            if _ordered_teams(item) != _ordered_teams(locked):
                errors.append("canonical_label_current_ordered_teams_mismatch")
            if official_schedule.normalize_team(item.get("predicted_winner")) != (
                official_schedule.normalize_team(locked.get("predictedWinner"))
            ):
                errors.append("canonical_label_current_predicted_winner_mismatch")
            if item.get("training_eligible") is True and authority.get(
                "learningEligible"
            ) is not True:
                errors.append("canonical_label_current_training_authority_mismatch")
        if errors:
            invalid.append(
                {"officialGamePk": official_pk, "errors": sorted(set(errors))}
            )
        else:
            valid.append(item)
    verification_complete = bool(valid) and not invalid
    status = (
        "VERIFIED"
        if verification_complete
        else "WAITING_FOR_FIRST_CANONICAL_FINAL_LABEL"
        if not labels
        else "FAILED"
    )
    return {
        "ok": verification_complete,
        "version": PROOF_VERSION,
        "proofType": "MLB_CANONICAL_IMMUTABLE_LOCK_TO_OFFICIAL_FINAL_LABEL",
        "slateDateEt": slate_date,
        "status": status,
        "verificationComplete": verification_complete,
        "storedCanonicalLabelCount": len(valid),
        "invalidStoredCanonicalLabelCount": len(invalid),
        "invalidStoredCanonicalLabels": invalid,
        "labels": [_plain(item) for item in valid],
        "immutablePregameRowsMutated": False,
        "terminalNoPredictionRowsAccuracyEligible": False,
        "terminalNoPredictionRowsTrainingEligible": False,
    }


def _requested_slate_dates(
    slate_date: Optional[str] = None,
    slate_dates: Optional[Iterable[str]] = None,
) -> List[str]:
    values = [str(value) for value in (slate_dates or []) if str(value)]
    if slate_date:
        values.append(str(slate_date))
    return sorted(set(values or [_slate_date_et()]))


def _joined_training_row(
    slate_date: str,
    label: Dict[str, Any],
    locked: Dict[str, Any],
    *,
    slate_finalized: bool,
) -> Dict[str, Any]:
    vector = copy.deepcopy(locked.get("frozenFeatureVector") or {})
    fundamentals_v2 = copy.deepcopy(locked.get("fundamentalsSnapshotV2") or {})
    eligible_now, current_exclusions = _training_verdict(locked)
    exclusions = {
        str(reason)
        for source in (
            label.get("training_exclusion_reasons") or [],
            current_exclusions,
        )
        for reason in source
        if str(reason)
    }
    training_eligible = bool(
        slate_finalized
        and label.get("training_eligible") is True
        and eligible_now
        and not exclusions
    )
    return {
        "gameId": locked.get("gameId") or locked.get("gameIdentity"),
        "officialGamePk": label.get("official_game_pk"),
        "providerEventId": label.get("provider_event_id"),
        "slateDateEt": slate_date,
        "slateFinalized": slate_finalized,
        "commenceTime": locked.get("commenceTime"),
        "homeTeam": locked.get("homeTeam"),
        "awayTeam": locked.get("awayTeam"),
        "predictedWinner": locked.get("predictedWinner"),
        "predictedSide": locked.get("predictedSide"),
        "lockedAmericanOdds": locked.get("lockedAmericanOdds", locked.get("americanOdds")),
        "predictionPersistedAtUtc": locked.get("predictionPersistedAtUtc"),
        "trainingEligible": training_eligible,
        "trainingExclusionReasons": sorted(exclusions),
        "frozenFeatureVector": vector,
        "featureSnapshot": copy.deepcopy(vector),
        "fundamentalsSnapshotV2": fundamentals_v2,
        "fundamentalsSnapshotV2Ref": copy.deepcopy(
            locked.get("fundamentalsSnapshotV2Ref")
            or locked.get("fundamentalsSnapshotRefV2")
            or {}
        ),
        "winner": label.get("winner"),
        "homeWon": label.get("home_won"),
        "correct": label.get("correct"),
        "pickCorrect": label.get("correct"),
        "labelStatus": "FINAL",
        "labelFingerprint": label.get("settlement_fingerprint"),
        "labelRecordFingerprint": label.get("record_fingerprint"),
        "labelSource": label.get("source"),
        "labelSourcePayloadFingerprint": label.get("source_payload_fingerprint"),
        "labelRetrievedAtUtc": label.get("observed_at_utc"),
        "canonicalLockPk": label.get("canonical_lock_pk"),
        "canonicalLockSk": label.get("canonical_lock_sk"),
        "canonicalStageFingerprint": label.get("canonical_stage_fingerprint"),
        "immutablePregameVectorMutated": False,
    }


def load_canonical_training_rows(
    slate_date: Optional[str] = None,
    slate_dates: Optional[Iterable[str]] = None,
    *,
    official_fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Read exact lock + separate write-once FINAL label rows for AWS training.

    Partial dates never emit training rows. A date is finalized only when the
    exact official schedule is terminal and every official game has one valid
    immutable lock (or the explicit no-prediction terminal), with one valid
    write-once label for every completed lock.
    """
    fetcher = official_fetcher or fetch_official_schedule
    rows: List[Dict[str, Any]] = []
    finalized_dates: List[str] = []
    diagnostics: List[Dict[str, Any]] = []
    for slate in _requested_slate_dates(slate_date, slate_dates):
        try:
            locks, rejected_locks = _validated_canonical_locks(slate)
            lock_by_pk, duplicate_locks = _lock_index(locks)
            terminal_by_pk, rejected_terminal = _validated_terminal_outcomes(slate)
            proof = _proof_from_stored(slate, lock_by_pk, terminal_by_pk)
            labels = {
                str(label.get("official_game_pk") or ""): label
                for label in proof.get("labels") or []
            }
            official = fetcher(slate)
            official_games = official.get("games") or []
            official_pks = {
                str(game.get("officialGamePk") or "") for game in official_games
            }
            final_pks = {
                str(game.get("officialGamePk") or "")
                for game in official_games
                if game.get("completed") is True
            }
            all_official_terminal = bool(
                len(official_games) == int(official.get("officialGameCount") or 0)
                and all(game.get("completed") is True for game in official_games)
            )
            lock_terminal_conflicts = set(lock_by_pk) & set(terminal_by_pk)
            coverage_complete = official_pks == (set(lock_by_pk) | set(terminal_by_pk))
            labels_complete = set(labels) == (final_pks & set(lock_by_pk))
            finalized = bool(
                all_official_terminal
                and coverage_complete
                and labels_complete
                and not rejected_locks
                and not duplicate_locks
                and not rejected_terminal
                and not lock_terminal_conflicts
                and proof.get("ok") is True
            )
            if finalized:
                finalized_dates.append(slate)
                for official_pk in sorted(labels):
                    rows.append(
                        _joined_training_row(
                            slate,
                            labels[official_pk],
                            lock_by_pk[official_pk],
                            slate_finalized=True,
                        )
                    )
            diagnostics.append(
                {
                    "slateDateEt": slate,
                    "slateFinalized": finalized,
                    "officialGameCount": len(official_pks),
                    "officialFinalCount": len(final_pks),
                    "canonicalLockCount": len(lock_by_pk),
                    "terminalNoPredictionCount": len(terminal_by_pk),
                    "validLabelCount": len(labels),
                    "coverageComplete": coverage_complete,
                    "labelsComplete": labels_complete,
                    "rejectedCanonicalLockCount": len(rejected_locks) + len(duplicate_locks),
                    "rejectedTerminalCount": len(rejected_terminal),
                    "invalidLabelCount": int(proof.get("invalidStoredCanonicalLabelCount") or 0),
                }
            )
        except Exception as exc:
            diagnostics.append(
                {
                    "slateDateEt": slate,
                    "slateFinalized": False,
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
    requested = _requested_slate_dates(slate_date, slate_dates)
    return {
        "ok": all(item.get("slateFinalized") is True for item in diagnostics),
        "version": VERSION,
        "requestedSlateDates": requested,
        "finalizedSlateDates": sorted(finalized_dates),
        "rows": rows,
        "rowCount": len(rows),
        "slates": diagnostics,
        "policy": "Only full official-FINAL slates joined by official gamePk to current immutable LOCKED#GAME authority may enter the prospective cohort.",
    }


def load_canonical_locked_rows_without_labels(
    slate_date: Optional[str] = None,
    slate_dates: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Expose immutable pregame vectors for shadow selection, never outcomes."""
    rows: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for slate in _requested_slate_dates(slate_date, slate_dates):
        locks, lock_rejections = _validated_canonical_locks(slate)
        lock_by_pk, duplicates = _lock_index(locks)
        rejected.extend(lock_rejections)
        rejected.extend(duplicates)
        labels = {
            str(item.get("official_game_pk") or "")
            for item in _labels_for_slate(slate)
            if not _label_record_errors(
                item,
                slate,
                str(item.get("official_game_pk") or ""),
            )
        }
        for official_pk, locked in sorted(lock_by_pk.items()):
            vector = copy.deepcopy(locked.get("frozenFeatureVector") or {})
            forbidden = [
                key
                for key in ("winner", "correct", "success", "homeWon", "pickCorrect")
                if key in locked
            ]
            labels_at_lock = vector.get("labels") or {}
            if (
                official_pk in labels
                or forbidden
                or labels_at_lock.get("homeWon") is not None
                or labels_at_lock.get("pickCorrect") is not None
            ):
                rejected.append(
                    {
                        "slateDateEt": slate,
                        "officialGamePk": official_pk,
                        "errors": ["lock_not_unlabeled_pregame_shadow_candidate"],
                    }
                )
                continue
            rows.append(
                {
                    "gameId": locked.get("gameId") or locked.get("gameIdentity"),
                    "officialGamePk": official_pk,
                    "providerEventId": _provider_event_id(locked),
                    "slateDateEt": slate,
                    "slateFinalized": False,
                    "commenceTime": locked.get("commenceTime"),
                    "homeTeam": locked.get("homeTeam"),
                    "awayTeam": locked.get("awayTeam"),
                    "predictedWinner": locked.get("predictedWinner"),
                    "predictedSide": locked.get("predictedSide"),
                    "lockedAmericanOdds": locked.get("lockedAmericanOdds", locked.get("americanOdds")),
                    "predictionPersistedAtUtc": locked.get("predictionPersistedAtUtc"),
                    "frozenFeatureVector": vector,
                    "featureSnapshot": copy.deepcopy(vector),
                    "fundamentalsSnapshotV2": copy.deepcopy(locked.get("fundamentalsSnapshotV2") or {}),
                    "fundamentalsSnapshotV2Ref": copy.deepcopy(
                        locked.get("fundamentalsSnapshotV2Ref")
                        or locked.get("fundamentalsSnapshotRefV2")
                        or {}
                    ),
                    "labelStatus": "PREGAME_UNLABELED",
                    "outcomeFieldsPresent": False,
                    "canonicalLockAuthority": copy.deepcopy(
                        locked.get("canonicalLockAuthority") or {}
                    ),
                }
            )
    return {
        "ok": not rejected,
        "version": VERSION,
        "rows": rows,
        "rowCount": len(rows),
        "rejected": rejected,
        "policy": "Shadow selection may read immutable T-45 vectors before labels exist; it receives no outcome fields and cannot rewrite a lock.",
    }


def settle_mlb_slate(
    slate_date: Optional[str] = None,
    days_from: int = 3,
    fetch_scores: bool = True,
    store: bool = True,
) -> Dict[str, Any]:
    del days_from  # Kept for compatibility with the legacy route contract.
    slate = slate_date or _slate_date_et()
    created_at = _now_iso()
    try:
        locks, rejected_locks = _validated_canonical_locks(slate)
        lock_by_pk, duplicate_locks = _lock_index(locks)
        rejected_locks.extend(duplicate_locks)
        terminal_by_pk, rejected_terminal = _validated_terminal_outcomes(slate)
        lock_terminal_conflicts = sorted(set(lock_by_pk) & set(terminal_by_pk))
        if not fetch_scores:
            proof = _proof_from_stored(slate, lock_by_pk, terminal_by_pk)
            return {
                **proof,
                "createdAtUtc": created_at,
                "slate_date_et": slate,
                "settlement_version": VERSION,
                "proofReadMode": "STORED_CANONICAL_LABELS_ONLY",
                "canonicalLockCount": len(lock_by_pk),
                "rejectedCanonicalLocks": rejected_locks,
                "terminalNoPredictionCount": len(terminal_by_pk),
                "lockTerminalConflictCount": len(lock_terminal_conflicts),
                "lockTerminalConflictOfficialGamePks": lock_terminal_conflicts,
                "rejectedTerminalOutcomes": rejected_terminal,
                "authoritativeSettlement": True,
                "legacySettlementAuthority": False,
            }

        official = fetch_official_schedule(slate)
        writes: List[Dict[str, Any]] = []
        skipped_not_final: List[Dict[str, Any]] = []
        missing_locks: List[Dict[str, Any]] = []
        identity_rejections: List[Dict[str, Any]] = []
        terminal_exclusions: List[Dict[str, Any]] = []
        immutable_before = _lock_integrity_snapshot(lock_by_pk)

        for final in official.get("games") or []:
            official_pk = str(final.get("officialGamePk") or "")
            if final.get("completed") is not True:
                skipped_not_final.append(
                    {
                        "officialGamePk": official_pk,
                        "officialStatus": copy.deepcopy(final.get("officialStatus") or {}),
                    }
                )
                continue
            if official_pk in lock_terminal_conflicts:
                identity_rejections.append(
                    {
                        "officialGamePk": official_pk,
                        "reason": "CANONICAL_LOCK_AND_NO_PREDICTION_TERMINAL_CONFLICT",
                    }
                )
                continue
            locked = lock_by_pk.get(official_pk)
            if not locked:
                terminal = terminal_by_pk.get(official_pk)
                if terminal:
                    terminal_exclusions.append(
                        {
                            "officialGamePk": official_pk,
                            "status": "LOCKED_NO_PREDICTION_DATA",
                            "accuracyEligible": False,
                            "trainingEligible": False,
                            "sourcePk": terminal.get("PK"),
                            "sourceSk": terminal.get("SK"),
                        }
                    )
                else:
                    missing_locks.append(
                        {
                            "officialGamePk": official_pk,
                            "reason": "MISSING_VALID_CANONICAL_LOCK_OR_TERMINAL_OUTCOME",
                        }
                    )
                continue
            if _ordered_teams(locked) != _ordered_teams(final):
                identity_rejections.append(
                    {
                        "officialGamePk": official_pk,
                        "reason": "CANONICAL_LOCK_OFFICIAL_FINAL_ORDERED_TEAMS_MISMATCH",
                        "lockedTeams": _ordered_teams(locked),
                        "officialTeams": _ordered_teams(final),
                    }
                )
                continue
            label = _build_label(slate, final, locked, created_at)
            writes.append(
                _write_label(label)
                if store
                else {
                    "ok": True,
                    "status": "WOULD_CREATE",
                    "officialGamePk": official_pk,
                    "settlementFingerprint": label.get("settlement_fingerprint"),
                }
            )

        readback_errors: List[str] = []
        try:
            readback_rows, readback_rejections = _validated_canonical_locks(slate)
            readback_by_pk, readback_duplicates = _lock_index(readback_rows)
            immutable_readback = _lock_integrity_snapshot(readback_by_pk)
            mutation_detected = any(
                immutable_readback.get(official_pk) != proof
                for official_pk, proof in immutable_before.items()
            )
            if readback_rejections or readback_duplicates:
                readback_errors.append("canonical_lock_readback_contains_invalid_rows")
        except Exception as exc:
            mutation_detected = True
            readback_errors.append(
                f"canonical_lock_readback_failed:{type(exc).__name__}:{exc}"
            )
        failures = [row for row in writes if row.get("ok") is not True]
        failures.extend(identity_rejections)
        failures.extend(missing_locks)
        failures.extend(rejected_locks)
        ok = not failures and not mutation_detected and not readback_errors
        status = (
            "FAILED_CLOSED"
            if not ok
            else "PENDING_OFFICIAL_FINALS"
            if skipped_not_final
            else "CANONICAL_FINAL_LABELS_COMPLETE"
        )
        return {
            "ok": ok,
            "version": PROOF_VERSION,
            "settlement_version": VERSION,
            "proofType": "MLB_CANONICAL_IMMUTABLE_LOCK_TO_OFFICIAL_FINAL_LABEL",
            "createdAtUtc": created_at,
            "sport": "mlb",
            "slateDateEt": slate,
            "slate_date_et": slate,
            "status": status,
            "overall_status": status,
            "authoritativeSettlement": True,
            "legacySettlementAuthority": False,
            "officialSchedule": {
                key: value for key, value in official.items() if key != "games"
            },
            "officialGameCount": int(official.get("officialGameCount") or 0),
            "officialFinalCount": int(official.get("officialFinalCount") or 0),
            "canonicalLockCount": len(lock_by_pk),
            "rejectedCanonicalLockCount": len(rejected_locks),
            "rejectedCanonicalLocks": rejected_locks,
            "terminalNoPredictionCount": len(terminal_by_pk),
            "lockTerminalConflictCount": len(lock_terminal_conflicts),
            "lockTerminalConflictOfficialGamePks": lock_terminal_conflicts,
            "terminalNoPredictionExcludedCount": len(terminal_exclusions),
            "terminalNoPredictionExclusions": terminal_exclusions,
            "rejectedTerminalOutcomes": rejected_terminal,
            "skippedNotFinalCount": len(skipped_not_final),
            "skippedNotFinal": skipped_not_final,
            "missingCanonicalLockCount": len(missing_locks),
            "missingCanonicalLocks": missing_locks,
            "identityRejectionCount": len(identity_rejections),
            "identityRejections": identity_rejections,
            "labelWriteCount": len(writes),
            "labelCreatedCount": sum(row.get("status") == "CREATED" for row in writes),
            "labelIdempotentCount": sum(
                row.get("status") == "IDEMPOTENT_EXISTING" for row in writes
            ),
            "labelWouldCreateCount": sum(row.get("status") == "WOULD_CREATE" for row in writes),
            "labelConflictCount": sum(
                row.get("status") == "OFFICIAL_FINAL_CORRECTION_CONFLICT" for row in writes
            ),
            "labelWrites": writes,
            "immutablePregameRowsMutated": mutation_detected,
            "immutablePregameReadbackErrors": readback_errors,
            "immutablePregameVectorPolicy": (
                "Canonical FINAL labels are separate write-once records; LOCKED#GAME rows and frozen vectors are never updated."
            ),
            "terminalNoPredictionPolicy": (
                "LOCKED_NO_PREDICTION_DATA is a terminal lifecycle outcome excluded from accuracy and ML training."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "version": PROOF_VERSION,
            "settlement_version": VERSION,
            "proofType": "MLB_CANONICAL_IMMUTABLE_LOCK_TO_OFFICIAL_FINAL_LABEL",
            "createdAtUtc": created_at,
            "sport": "mlb",
            "slateDateEt": slate,
            "slate_date_et": slate,
            "status": "FAILED_CLOSED",
            "authoritativeSettlement": True,
            "legacySettlementAuthority": False,
            "error": f"{type(exc).__name__}:{exc}",
            "immutablePregameRowsMutated": False,
        }


def settle_recent_mlb_slates(
    *,
    days_from: int = 3,
    fetch_scores: bool = True,
    store: bool = True,
) -> Dict[str, Any]:
    """Settle the current ET slate and recent slates without crossing identities.

    The scheduled job can run after midnight ET while a western game from the
    previous slate has only just become FINAL.  Every date is therefore fetched
    and joined independently by official gamePk.  A historical gap is exposed
    in ``recentSlateFailures`` but does not hide the current slate's status or
    prevent other dates from being processed.
    """
    count = max(1, min(int(days_from or 1), 7))
    today = _now().astimezone(SLATE_TZ).date()
    dates = [(today - timedelta(days=offset)).isoformat() for offset in range(count)]
    reports = [
        settle_mlb_slate(
            slate_date=slate,
            fetch_scores=fetch_scores,
            store=store,
        )
        for slate in dates
    ]
    primary = copy.deepcopy(reports[0]) if reports else {
        "ok": False,
        "status": "FAILED_CLOSED",
        "slateDateEt": dates[0] if dates else _slate_date_et(),
    }
    failed = [
        {
            "slateDateEt": report.get("slateDateEt") or report.get("slate_date_et"),
            "status": report.get("status") or report.get("overall_status"),
            "error": report.get("error"),
        }
        for report in reports
        if report.get("ok") is not True
    ]
    primary.update(
        {
            "recentSlateSettlementApplied": True,
            "recentSlateDates": dates,
            "recentSlateReports": reports,
            "recentSlateFailureCount": len(failed),
            "recentSlateFailures": failed,
            "recentSlateAllOk": not failed,
            "recentSlatePolicy": (
                "Each ET slate is fetched and joined independently by exact official gamePk; "
                "a prior-slate failure cannot stop later dates from being attempted."
            ),
        }
    )
    return primary


def settlement_proof_report(
    slate_date: Optional[str] = None,
    days_from: int = 3,
    fetch_scores: bool = False,
) -> Dict[str, Any]:
    dry_run = (
        settle_mlb_slate(
            slate_date=slate_date,
            days_from=days_from,
            fetch_scores=True,
            store=False,
        )
        if fetch_scores
        else None
    )
    # Only a persisted write-once label, revalidated against the current lock,
    # can satisfy the proof. A requested official-FINAL dry run is diagnostic.
    report = settle_mlb_slate(
        slate_date=slate_date,
        days_from=days_from,
        fetch_scores=False,
        store=False,
    )
    report["readOnlyProof"] = True
    report["officialFinalDryRun"] = dry_run
    report["dryRunCanSatisfyProof"] = False
    report["proofPolicy"] = (
        "A proof is authoritative only when current canonical lock/stage validation, exact official gamePk and ordered teams, and a write-once official FINAL label all agree."
    )
    return report
