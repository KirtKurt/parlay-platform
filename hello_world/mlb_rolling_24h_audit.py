from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import inqsi_pull_history as history

SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
REPORT_PATH = "runtime_reports/mlb_rolling_24h_audit_latest.json"
WINDOW_HOURS = 24
TARGET_ACCURACY_PCT = 90.0
CANONICAL_LOCK_RECORD_TYPE = "mlb_immutable_locked_single_game_prediction"
CANONICAL_LOCK_AUTHORITY_VERSION = "MLB-ROLLING-AUDIT-CANONICAL-LOCK-AUTHORITY-v1"
EXACT_PROVIDER_MATCH_METHOD = "exact_provider_game_id_and_teams"
VERIFIED_PROVIDER_ALIAS_MATCH_METHOD = (
    "verified_immutable_pull_official_game_pk_provider_alias_and_teams"
)
_CANONICAL_REJECTIONS_BY_SLATE: Dict[str, Dict[str, List[str]]] = {}
HISTORICAL_AUDIT_WINDOW_DAYS = max(
    1,
    int(os.environ.get("INQSI_MLB_HISTORICAL_AUDIT_WINDOW_DAYS", "60")),
)
# A positive legacy run limit remains available as an explicit operational
# circuit breaker. The default is uncapped because 720 twice-hourly runs retained
# only about 15 days/~225 MLB games and made the 500-row promotion gate impossible.
HISTORICAL_AUDIT_RUN_LIMIT = max(
    0,
    int(os.environ.get("INQSI_MLB_HISTORICAL_AUDIT_RUN_LIMIT", "0")),
)
MIN_MULTI_WINDOW_SAMPLE = int(os.environ.get("INQSI_MLB_MIN_MULTI_WINDOW_SAMPLE", "6"))
MULTI_WINDOW_WEIGHTS = {
    "current24h": float(os.environ.get("INQSI_MLB_WEIGHT_CURRENT_24H", "0.50")),
    "sevenDay": float(os.environ.get("INQSI_MLB_WEIGHT_7D", "0.25")),
    "thirtyDay": float(os.environ.get("INQSI_MLB_WEIGHT_30D", "0.15")),
    "season": float(os.environ.get("INQSI_MLB_WEIGHT_SEASON", "0.10")),
}
MULTI_WINDOW_DAYS = {
    "current24h": 1,
    "sevenDay": 7,
    "thirtyDay": 30,
    "season": None,
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def normalize_team(name: Optional[str]) -> str:
    return " ".join((name or "").lower().strip().split())


def _provider_game_id(row: Dict[str, Any]) -> str:
    for key in (
        "providerEventId",
        "provider_event_id",
        "providerGameId",
        "provider_game_id",
        "gameId",
        "game_id",
        "id",
    ):
        value = row.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            return text[len("provider:"):] if text.startswith("provider:") else text
    identity = str(row.get("gameIdentity") or "").strip()
    return identity[len("provider:"):] if identity.startswith("provider:") else ""


def _canonical_game_id(row: Dict[str, Any]) -> str:
    for key in ("gameId", "game_id", "id"):
        value = row.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            return text[len("provider:"):] if text.startswith("provider:") else text
    identity = str(row.get("gameIdentity") or "").strip()
    return identity[len("provider:"):] if identity.startswith("provider:") else ""


def _explicit_provider_event_id(row: Dict[str, Any]) -> str:
    for key in (
        "providerEventId",
        "provider_event_id",
        "providerGameId",
        "provider_game_id",
    ):
        value = row.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            return text[len("provider:"):] if text.startswith("provider:") else text
    return ""


def _official_game_pk(row: Dict[str, Any]) -> str:
    for key in ("officialGamePk", "official_game_pk"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    for key in ("officialGameId", "official_game_id", "gameId", "game_id", "gameIdentity"):
        value = str(row.get(key) or "").strip()
        if value.startswith("mlb_statsapi:"):
            return value.split(":", 1)[1]
    return ""


def _ordered_teams(row: Dict[str, Any]) -> Tuple[str, str]:
    return (
        normalize_team(row.get("awayTeam") or row.get("away_team")),
        normalize_team(row.get("homeTeam") or row.get("home_team")),
    )


def _verified_provider_alias_crosswalk(slate_date: str) -> Dict[str, Dict[str, Any]]:
    """Build a one-to-one official-pk/provider-id map from immutable manifests.

    The settlement result must never supply this bridge.  Only same-slate pull
    manifests whose write-once copy and fingerprint validate are considered.
    Repeated identical evidence is allowed; any provider, official-pk, or team
    ambiguity removes the affected mapping entirely.
    """
    try:
        pulls = history.query_pulls("mlb", date=slate_date, limit=500)
    except Exception:
        return {}

    evidence: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for pull in pulls or []:
        if str(pull.get("slate_date") or slate_date) != str(slate_date):
            continue
        try:
            errors = history.validate_provider_schedule_manifest(
                pull,
                slate_date,
                verify_immutable_storage=True,
            )
        except Exception:
            continue
        if errors:
            continue
        manifest = pull.get("provider_schedule_manifest") or {}
        fingerprint = str(manifest.get("fingerprint") or "")
        if not fingerprint:
            continue
        for game in manifest.get("games") or []:
            if not isinstance(game, dict):
                continue
            official_pk = _official_game_pk(game)
            provider_id = _explicit_provider_event_id(game)
            away, home = _ordered_teams(game)
            if not official_pk or not provider_id or not away or not home:
                continue
            key = (official_pk, provider_id, away, home)
            proof = evidence.setdefault(
                key,
                {
                    "officialGamePk": official_pk,
                    "providerEventId": provider_id,
                    "awayTeamNormalized": away,
                    "homeTeamNormalized": home,
                    "manifestFingerprints": set(),
                },
            )
            proof["manifestFingerprints"].add(fingerprint)

    by_official: Dict[str, Set[Tuple[str, str, str]]] = {}
    by_provider: Dict[str, Set[Tuple[str, str, str]]] = {}
    for official_pk, provider_id, away, home in evidence:
        by_official.setdefault(official_pk, set()).add((provider_id, away, home))
        by_provider.setdefault(provider_id, set()).add((official_pk, away, home))

    verified: Dict[str, Dict[str, Any]] = {}
    for (official_pk, provider_id, away, home), proof in evidence.items():
        if by_official.get(official_pk) != {(provider_id, away, home)}:
            continue
        if by_provider.get(provider_id) != {(official_pk, away, home)}:
            continue
        verified[official_pk] = {
            **proof,
            "manifestFingerprints": sorted(proof["manifestFingerprints"]),
            "evidenceCount": len(proof["manifestFingerprints"]),
            "slateDateEt": slate_date,
            "immutableManifestValidated": True,
            "uniqueBidirectionalCrosswalk": True,
        }
    return verified


def _apply_verified_provider_aliases(
    predictions: List[Dict[str, Any]],
    slate_date: str,
) -> List[Dict[str, Any]]:
    crosswalk = _verified_provider_alias_crosswalk(slate_date)
    if not crosswalk:
        return predictions

    fallback_by_official: Dict[str, List[Dict[str, Any]]] = {}
    occupied_provider_ids: Set[str] = set()
    for pred in predictions:
        explicit_provider = _explicit_provider_event_id(pred)
        canonical_id = _canonical_game_id(pred)
        if explicit_provider:
            occupied_provider_ids.add(explicit_provider)
            continue
        if canonical_id and not canonical_id.startswith("mlb_statsapi:"):
            occupied_provider_ids.add(canonical_id)
            continue
        official_pk = _official_game_pk(pred)
        if official_pk:
            fallback_by_official.setdefault(official_pk, []).append(pred)

    aliases: Dict[str, Dict[str, Any]] = {}
    enriched_by_official: Dict[str, Dict[str, Any]] = {}
    for official_pk, candidates in fallback_by_official.items():
        proof = crosswalk.get(official_pk)
        if len(candidates) != 1 or not proof:
            continue
        provider_id = str(proof["providerEventId"])
        if provider_id in occupied_provider_ids or provider_id in aliases:
            continue
        pred = candidates[0]
        if _ordered_teams(pred) != (
            proof["awayTeamNormalized"],
            proof["homeTeamNormalized"],
        ):
            continue
        copied = dict(pred)
        copied["providerEventId"] = provider_id
        authority = dict(copied.get("canonicalLockAuthority") or {})
        authority.update({
            "providerGameId": provider_id,
            "canonicalLockedGameId": _canonical_game_id(pred),
            "officialGamePk": official_pk,
            "providerIdentityMatchMethod": VERIFIED_PROVIDER_ALIAS_MATCH_METHOD,
            "matchMethod": VERIFIED_PROVIDER_ALIAS_MATCH_METHOD,
            "exactProviderIdentityMatched": False,
            "verifiedProviderAliasCrosswalkMatched": True,
            "providerAliasCrosswalk": proof,
        })
        copied["canonicalLockAuthority"] = authority
        aliases[provider_id] = copied
        enriched_by_official[official_pk] = copied
        occupied_provider_ids.add(provider_id)

    return [enriched_by_official.get(_official_game_pk(pred), pred) for pred in predictions]


def _canonical_lock_item_errors(item: Dict[str, Any], slate_date: str) -> List[str]:
    """Validate the DynamoDB envelope and persisted stage proof for one lock row."""
    errors: List[str] = []
    data = item.get("data") if isinstance(item.get("data"), dict) else None
    expected_pk = f"GAME_WINNERS#mlb#{slate_date}"
    if item.get("PK") != expected_pk:
        errors.append("canonical_pk_mismatch")
    if not str(item.get("SK") or "").startswith("LOCKED#GAME#"):
        errors.append("canonical_sk_missing")
    if item.get("record_type") != CANONICAL_LOCK_RECORD_TYPE:
        errors.append("canonical_record_type_mismatch")
    if item.get("immutable_locked") is not True:
        errors.append("immutable_locked_envelope_flag_missing")
    if item.get("stage_authority_verified") is not True:
        errors.append("stage_authority_envelope_flag_missing")
    if not isinstance(data, dict):
        errors.append("canonical_data_missing")
        return sorted(set(errors))

    identity = str(data.get("gameIdentity") or data.get("gameId") or data.get("game_id") or data.get("id") or "")
    commence = str(data.get("commenceTime") or data.get("commence_time") or "")
    if not identity or not commence:
        errors.append("canonical_identity_or_commence_missing")
    elif item.get("SK") != f"LOCKED#GAME#{commence}#{identity}":
        errors.append("canonical_sk_payload_binding_mismatch")
    if str(item.get("slate_date") or slate_date) != str(slate_date):
        errors.append("canonical_slate_mismatch")
    if item.get("game_identity") not in (None, "") and str(item.get("game_identity")) != identity:
        errors.append("canonical_envelope_game_identity_mismatch")
    if item.get("game_id") not in (None, "") and str(item.get("game_id")) != _canonical_game_id(data):
        errors.append("canonical_envelope_provider_game_id_mismatch")
    if data.get("immutableLockedStorage") is not True:
        errors.append("immutable_locked_payload_flag_missing")
    if data.get("immutableLockedStorageKeyspace") != "LOCKED#GAME":
        errors.append("immutable_locked_payload_keyspace_mismatch")

    try:
        import mlb_immutable_locked_storage_patch as immutable_storage

        proof = data.get("canonicalPerGameStageAuthority") or {}
        if item.get("immutable_locked_storage_version") != immutable_storage.VERSION:
            errors.append("immutable_locked_envelope_version_mismatch")
        if data.get("immutableLockedStorageVersion") != immutable_storage.VERSION:
            errors.append("immutable_locked_payload_version_mismatch")
        if item.get("stage_authority_version") != immutable_storage.AUTHORITY_VERSION:
            errors.append("stage_authority_envelope_version_mismatch")
        if not item.get("stage_fingerprint"):
            errors.append("stage_fingerprint_envelope_missing")
        elif not isinstance(proof, dict) or item.get("stage_fingerprint") != proof.get(
            "stageFingerprint"
        ):
            errors.append("stage_fingerprint_envelope_payload_mismatch")
        errors.extend(immutable_storage.validate_canonical_stage_authority(history.PULLS, data))
    except Exception as exc:
        errors.append(f"canonical_stage_authority_validator_failed:{type(exc).__name__}")
    # Exact-vector and training-status metadata are deliberately not canonical
    # winner-lock authority.  Locks written before the selection/training
    # separation contract can lack that metadata while still carrying a valid,
    # exact immutable stage chain.  Classify those rows as training-ineligible
    # in ``_canonical_lock_authority`` instead of erasing their gradeable pick.
    # New writes remain fail-closed in
    # ``mlb_immutable_locked_storage_patch._require_vector_status``.
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract

        errors.extend(
            vector_contract.selection_lock_gradeability_integrity_errors(data)
        )
    except Exception as exc:
        errors.append(
            f"selection_vector_integrity_validator_failed:{type(exc).__name__}"
        )
    return sorted(set(errors))


def _canonical_lock_authority(item: Dict[str, Any], slate_date: str) -> Dict[str, Any]:
    data = item.get("data") or {}
    gate = next(
        (
            value
            for key in ("slatePredictionLock", "lastPossiblePredictionGate")
            for value in [data.get(key)]
            if isinstance(value, dict)
            and (
                value.get("lockAtUtc")
                or value.get("latestScoringPullAt")
                or value.get("locked") is not None
            )
        ),
        {},
    )
    canonical_lock_at = parse_dt(gate.get("lockAtUtc"))
    official_game_pk = _official_game_pk(data)
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract

        vector_errors = vector_contract.effective_selection_lock_vector_errors(data)
        vector_status_errors = vector_contract.validate_selection_lock_vector_status(
            data
        )
        training_exclusions = vector_contract.selection_lock_training_exclusions(
            data,
            vector_errors=vector_errors,
            vector_status_errors=vector_status_errors,
        )
    except Exception as exc:
        vector_errors = [f"exact_lock_vector_validator_failed:{type(exc).__name__}"]
        vector_status_errors = [
            f"selection_vector_status_validator_failed:{type(exc).__name__}"
        ]
        training_exclusions = sorted(
            {
                *(
                    str(reason)
                    for reason in (
                        data.get("trainingExclusionReasons")
                        or (data.get("mlFeatureFreeze") or {}).get(
                            "trainingExclusionReasons"
                        )
                        or []
                    )
                    if str(reason)
                ),
                *(f"exact_lock_vector_validation:{error}" for error in vector_errors),
                *(
                    f"selection_lock_vector_status_validation:{error}"
                    for error in vector_status_errors
                ),
            }
        )
    training = data.get("mlFeatureFreeze") or {}
    exact_vector_verified = not vector_errors
    learning_eligible = bool(
        exact_vector_verified
        and not vector_status_errors
        and not training_exclusions
        and data.get("trainingEligible", training.get("trainingEligible")) is True
    )
    return {
        "version": CANONICAL_LOCK_AUTHORITY_VERSION,
        "verified": True,
        "consistentRead": True,
        "sourcePk": item.get("PK"),
        "sourceSk": item.get("SK"),
        "recordType": item.get("record_type"),
        "immutableLocked": item.get("immutable_locked") is True,
        "stageAuthorityVerified": item.get("stage_authority_verified") is True,
        "stageAuthorityVersion": item.get("stage_authority_version"),
        "stageFingerprint": item.get("stage_fingerprint"),
        "persistedStageAuthorityValidated": True,
        # These values are re-derived from the consistently read immutable
        # payload.  Post-cutover acceptance binds the rendered audit timestamp
        # and official identity back to this authority instead of trusting a
        # mutable summary field on its own.
        "canonicalLockAtUtc": (
            canonical_lock_at.isoformat() if canonical_lock_at else None
        ),
        "officialGamePk": official_game_pk or None,
        "canonicalLockedGameId": _canonical_game_id(data) or None,
        "officialAuditEligible": True,
        "learningEligible": learning_eligible,
        "selectionLockIndependentOfTrainingVector": True,
        "exactLockVectorValidated": exact_vector_verified,
        "exactLockVectorValidationErrors": vector_errors,
        "selectionLockVectorStatusValidated": not vector_status_errors,
        "selectionLockVectorStatusValidationErrors": vector_status_errors,
        "trainingExclusionReasons": training_exclusions,
        "slateDateEt": slate_date,
        "providerGameId": _provider_game_id(data),
        "exactProviderIdentityMatched": False,
        "verifiedProviderAliasCrosswalkMatched": False,
        "providerIdentityMatchMethod": None,
        "matchMethod": None,
        "legacyOrDailyCardFallbackUsed": False,
    }


def _canonical_rejection_diagnostics(final: Dict[str, Any]) -> Dict[str, Any]:
    slate = str(final.get("slateDateEt") or "")
    provider_id = _provider_game_id(final)
    reasons = list((_CANONICAL_REJECTIONS_BY_SLATE.get(slate) or {}).get(provider_id) or [])
    invalid = bool(reasons)
    effective_reasons = reasons or ["canonical_lock_item_not_found"]
    return {
        "canonicalLockEvidenceStatus": "INVALID" if invalid else "MISSING",
        "canonicalLockValidationErrors": reasons,
        "canonicalLockAuthority": {
            "version": CANONICAL_LOCK_AUTHORITY_VERSION,
            "verified": False,
            "providerGameId": provider_id or None,
            "rejectionReasons": effective_reasons,
            "officialAuditEligible": False,
            "learningEligible": False,
        },
    }


def _is_canonical_graded_row(row: Dict[str, Any]) -> bool:
    authority = row.get("canonicalLockAuthority") or {}
    slate = str(row.get("slateDateEt") or "")
    method = authority.get("providerIdentityMatchMethod") or authority.get("matchMethod")
    exact_identity = bool(
        method == EXACT_PROVIDER_MATCH_METHOD
        and authority.get("exactProviderIdentityMatched") is True
    )
    crosswalk = authority.get("providerAliasCrosswalk") or {}
    crosswalk_identity = bool(
        method == VERIFIED_PROVIDER_ALIAS_MATCH_METHOD
        and authority.get("verifiedProviderAliasCrosswalkMatched") is True
        and isinstance(crosswalk, dict)
        and crosswalk.get("immutableManifestValidated") is True
        and crosswalk.get("uniqueBidirectionalCrosswalk") is True
        and str(crosswalk.get("providerEventId") or "") == _provider_game_id(row)
        and str(crosswalk.get("officialGamePk") or "")
        == str(authority.get("officialGamePk") or "")
        and (
            str(crosswalk.get("awayTeamNormalized") or ""),
            str(crosswalk.get("homeTeamNormalized") or ""),
        )
        == _ordered_teams(row)
    )
    return bool(
        row.get("status") == "GRADED"
        and isinstance(authority, dict)
        and authority.get("version") == CANONICAL_LOCK_AUTHORITY_VERSION
        and authority.get("verified") is True
        and authority.get("consistentRead") is True
        and authority.get("immutableLocked") is True
        and authority.get("stageAuthorityVerified") is True
        and authority.get("persistedStageAuthorityValidated") is True
        and authority.get(
            "officialAuditEligible",
            authority.get("exactLockVectorValidated"),
        ) is True
        and (exact_identity or crosswalk_identity)
        and authority.get("legacyOrDailyCardFallbackUsed") is False
        and authority.get("sourcePk") == f"GAME_WINNERS#mlb#{slate}"
        and str(authority.get("sourceSk") or "").startswith("LOCKED#GAME#")
        and authority.get("recordType") == CANONICAL_LOCK_RECORD_TYPE
    )


def _is_learning_eligible_graded_row(row: Dict[str, Any]) -> bool:
    authority = row.get("canonicalLockAuthority") or {}
    return bool(
        _is_canonical_graded_row(row)
        and authority.get(
            "learningEligible",
            authority.get("exactLockVectorValidated"),
        ) is True
        and authority.get("exactLockVectorValidated") is True
    )


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


def final_scores_last_24h(days_from: int = 3) -> List[Dict[str, Any]]:
    raw = http_get_json(scores_url(days_from=days_from))
    cutoff = now_utc() - timedelta(hours=WINDOW_HOURS)
    finals = []
    for game in raw or []:
        if not game.get("completed"):
            continue
        if not history.mlb_model_eligible_game(game):
            continue
        commence_dt = parse_dt(game.get("commence_time"))
        if not commence_dt or commence_dt < cutoff or commence_dt > now_utc() + timedelta(hours=1):
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
        finals.append({
            "id": game.get("id"),
            "gameKeyBase": f"{normalize_team(away)}|{normalize_team(home)}",
            "homeTeam": home,
            "awayTeam": away,
            "matchup": f"{away} at {home}",
            "commenceTime": game.get("commence_time"),
            "slateDateEt": slate_date_from_commence(game.get("commence_time")),
            "homeScore": home_score,
            "awayScore": away_score,
            "winner": winner,
            "margin": abs(home_score - away_score),
            "totalRuns": home_score + away_score,
            "completed": True,
        })
    return finals


def _query_predictions_for_slate(slate_date: str) -> List[Dict[str, Any]]:
    if history.PULLS is None:
        return []
    out: List[Dict[str, Any]] = []
    rejections: Dict[str, List[str]] = {}
    _CANONICAL_REJECTIONS_BY_SLATE[str(slate_date)] = rejections
    start_key = None
    while True:
        args = {
            "KeyConditionExpression": (
                history.Key("PK").eq(f"GAME_WINNERS#mlb#{slate_date}")
                & history.Key("SK").begins_with("LOCKED#GAME#")
            ),
            "ConsistentRead": True,
        }
        if start_key:
            args["ExclusiveStartKey"] = start_key
        resp = history.PULLS.query(**args)
        for item in resp.get("Items") or []:
            if not isinstance(item, dict):
                continue
            # Mutable GAME rows and immutable PRELOCK snapshots share this
            # partition with canonical locks.  They are neither official audit
            # candidates nor malformed lock evidence, so do not let their
            # expected schema differences turn a genuinely missing lock into an
            # INVALID_CANONICAL_LOCK result.
            if not str(item.get("SK") or "").startswith("LOCKED#GAME#"):
                continue
            errors = _canonical_lock_item_errors(item, slate_date)
            if errors:
                data = item.get("data") if isinstance(item.get("data"), dict) else {}
                provider_id = _provider_game_id(data) or str(item.get("game_id") or "")
                if provider_id:
                    rejections[provider_id] = sorted(set((rejections.get(provider_id) or []) + errors))
                continue
            data = dict(item.get("data") or {})
            data["canonicalLockAuthority"] = _canonical_lock_authority(item, slate_date)
            out.append(data)
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    return _apply_verified_provider_aliases(out, slate_date)


def predictions_index(finals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    dates = sorted(set([f.get("slateDateEt") for f in finals if f.get("slateDateEt")]))
    index: Dict[str, Dict[str, Any]] = {}
    for slate in dates:
        for pred in _query_predictions_for_slate(slate):
            key = _provider_game_id(pred)
            if not key:
                continue
            current = index.get(key)
            if current is None or str(pred.get("createdAt") or "") > str(current.get("createdAt") or ""):
                index[key] = pred
    return index


def audit_rows(finals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    index = predictions_index(finals)
    rows = []
    for final in finals:
        provider_id = _provider_game_id(final)
        pred = index.get(provider_id) or {}
        if not pred:
            diagnostics = _canonical_rejection_diagnostics(final)
            rows.append({
                **final,
                "status": (
                    "INVALID_CANONICAL_LOCK"
                    if diagnostics.get("canonicalLockEvidenceStatus") == "INVALID"
                    else "MISSING_CANONICAL_LOCK"
                ),
                **diagnostics,
            })
            continue
        teams_match = (
            normalize_team(pred.get("awayTeam")) == normalize_team(final.get("awayTeam"))
            and normalize_team(pred.get("homeTeam")) == normalize_team(final.get("homeTeam"))
        )
        if not provider_id or not teams_match:
            rows.append({
                **final,
                "status": "CANONICAL_LOCK_IDENTITY_MISMATCH",
                "canonicalLockAuthority": {
                    **dict(pred.get("canonicalLockAuthority") or {}),
                    "verified": False,
                    "rejectionReasons": ["exact_provider_id_matched_but_teams_mismatched"],
                    "officialAuditEligible": False,
                    "learningEligible": False,
                },
            })
            continue
        authority = dict(pred.get("canonicalLockAuthority") or {})
        method = authority.get("providerIdentityMatchMethod") or authority.get("matchMethod")
        if method == VERIFIED_PROVIDER_ALIAS_MATCH_METHOD:
            authority.update({
                "exactProviderIdentityMatched": False,
                "verifiedProviderAliasCrosswalkMatched": True,
                "providerIdentityMatchMethod": VERIFIED_PROVIDER_ALIAS_MATCH_METHOD,
                "matchMethod": VERIFIED_PROVIDER_ALIAS_MATCH_METHOD,
            })
        else:
            authority.update({
                "exactProviderIdentityMatched": True,
                "verifiedProviderAliasCrosswalkMatched": False,
                "providerIdentityMatchMethod": EXACT_PROVIDER_MATCH_METHOD,
                "matchMethod": EXACT_PROVIDER_MATCH_METHOD,
            })
        correct = normalize_team(pred.get("predictedWinner")) == normalize_team(final.get("winner"))
        rows.append({
            **final,
            "status": "GRADED",
            "predictedWinner": pred.get("predictedWinner"),
            "predictedSide": pred.get("predictedSide"),
            "score": pred.get("score"),
            "winProbabilityPct": pred.get("winProbabilityPct"),
            "confidenceTier": pred.get("confidenceTier"),
            "tags": pred.get("tags") or [],
            "selectionBeforeWinnerOptimizer": pred.get("selectionBeforeWinnerOptimizer"),
            "individualWinnerOptimized": pred.get("individualWinnerOptimized"),
            "optimizerFlippedPick": pred.get("optimizerFlippedPick"),
            "winnerOptimizer": pred.get("winnerOptimizer"),
            "homeSignal": pred.get("homeSignal"),
            "awaySignal": pred.get("awaySignal"),
            "exactVectorVerified": authority.get("exactLockVectorValidated"),
            "exactVectorValidationErrors": list(authority.get("exactLockVectorValidationErrors") or []),
            "trainingEligible": authority.get("learningEligible") is True,
            "trainingExclusionReasons": list(authority.get("trainingExclusionReasons") or []),
            "canonicalLockAuthority": authority,
            "correct": correct,
        })
    return rows


def _historical_key(row: Dict[str, Any]) -> str:
    return "|".join([
        str(row.get("id") or ""),
        str(row.get("gameKeyBase") or ""),
        str(row.get("commenceTime") or ""),
        str(row.get("predictedWinner") or ""),
    ])


def _dedupe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not _is_canonical_graded_row(row):
            continue
        key = _historical_key(row)
        if key not in seen:
            seen[key] = row
    return list(seen.values())


def _dedupe_learning_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        row for row in _dedupe_rows(rows)
        if _is_learning_eligible_graded_row(row)
    ]


def historical_audit_rows(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return deduped historical graded rows from prior stored audit runs.

    The workflow runs twice hourly, so the same completed game can appear in many
    rolling 24h reports. Dedupe prevents repeat runs from overweighting one game.
    """
    if history.PULLS is None:
        return []
    seen: Dict[str, Dict[str, Any]] = {}
    start_key = None
    configured_limit = HISTORICAL_AUDIT_RUN_LIMIT if limit is None else max(0, int(limit))
    queried_runs = 0
    cutoff = now_utc() - timedelta(days=HISTORICAL_AUDIT_WINDOW_DAYS)
    window_complete = False
    while not window_complete:
        try:
            args = {
                "KeyConditionExpression": history.Key("PK").eq("MLB_ROLLING_24H_AUDIT#RUNS"),
                "ScanIndexForward": False,
                "Limit": min(100, configured_limit - queried_runs) if configured_limit else 100,
            }
            if start_key:
                args["ExclusiveStartKey"] = start_key
            resp = history.PULLS.query(**args)
        except Exception:
            return list(seen.values())
        for item in resp.get("Items") or []:
            queried_runs += 1
            data = item.get("data") or {}
            audit_created = item.get("created_at") or data.get("createdAt")
            audit_created_dt = parse_dt(audit_created)
            if audit_created_dt and audit_created_dt < cutoff:
                # Runs are queried newest-first. Once a well-formed run is older
                # than the configured durable window, every later page is older.
                window_complete = True
                break
            for row in data.get("rows") or []:
                if not _is_canonical_graded_row(row):
                    continue
                key = _historical_key(row)
                if key not in seen:
                    copied = dict(row)
                    copied["sourceAuditCreatedAt"] = audit_created
                    seen[key] = copied
        start_key = resp.get("LastEvaluatedKey")
        if window_complete or not start_key:
            break
        if configured_limit and queried_runs >= configured_limit:
            break
    return list(seen.values())


def _rows_since(rows: List[Dict[str, Any]], days: Optional[int]) -> List[Dict[str, Any]]:
    if days is None:
        return _dedupe_rows(rows)
    cutoff = now_utc() - timedelta(days=days)
    out = []
    for row in _dedupe_rows(rows):
        dt = parse_dt(row.get("commenceTime"))
        if dt and dt >= cutoff:
            out.append(row)
    return out


def _accuracy(rows: List[Dict[str, Any]]) -> Optional[float]:
    graded = [r for r in rows if r.get("status") == "GRADED"]
    if not graded:
        return None
    return round(sum(1 for r in graded if r.get("correct")) / len(graded) * 100.0, 2)


def _tag_combo(tags: List[str]) -> str:
    return "+".join(sorted(set(tags or []))) or "NO_TAGS"


def _bucket(rows: List[Dict[str, Any]], key_fn) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.get("status") != "GRADED":
            continue
        keys = key_fn(row)
        if not isinstance(keys, list):
            keys = [keys]
        for key in keys:
            if key is None:
                continue
            data = out.setdefault(str(key), {"count": 0, "correct": 0, "accuracyPct": None})
            data["count"] += 1
            if row.get("correct"):
                data["correct"] += 1
    for data in out.values():
        data["accuracyPct"] = round(data["correct"] / data["count"] * 100.0, 2) if data["count"] else None
    return out


def _bounded_adjustment(acc: Optional[float], count: int, scale: float, cap: float) -> float:
    if acc is None:
        return 0.0
    if count <= 0:
        return 0.0
    if count == 1:
        return 0.75 if acc >= 100 else -0.75
    return round(max(-cap, min(cap, (float(acc) - 50.0) / scale)), 2)


def _adjustments_from_stats(stats: Dict[str, Dict[str, Any]], scale: float, cap: float) -> Dict[str, float]:
    return {
        key: _bounded_adjustment(stat.get("accuracyPct"), int(stat.get("count") or 0), scale=scale, cap=cap)
        for key, stat in stats.items()
    }


def _window_stat_package(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    graded = [r for r in rows if r.get("status") == "GRADED"]
    correct = [r for r in graded if r.get("correct")]
    tag_stats = _bucket(graded, lambda r: r.get("tags") or [])
    combo_stats = _bucket(graded, lambda r: _tag_combo(r.get("tags") or []))
    confidence_stats = _bucket(graded, lambda r: r.get("confidenceTier") or "UNKNOWN")
    flip_stats = _bucket(graded, lambda r: "FLIPPED" if r.get("optimizerFlippedPick") else "NOT_FLIPPED")
    return {
        "rowCount": len(graded),
        "correct": len(correct),
        "wrong": len(graded) - len(correct),
        "accuracyPct": round(len(correct) / len(graded) * 100.0, 2) if graded else None,
        "tagStats": tag_stats,
        "tagComboStats": combo_stats,
        "confidenceStats": confidence_stats,
        "optimizerFlipStats": flip_stats,
        "tagScoreAdjustments": _adjustments_from_stats(tag_stats, scale=18.0, cap=3.0),
        "tagComboScoreAdjustments": _adjustments_from_stats(combo_stats, scale=12.0, cap=5.0),
    }


def _build_learning_windows(current_rows: List[Dict[str, Any]], historical_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    current_learning_rows = _dedupe_learning_rows(current_rows or [])
    all_rows = _dedupe_learning_rows((current_rows or []) + (historical_rows or []))
    return {
        "current24h": _window_stat_package(_rows_since(current_learning_rows, 1)),
        "sevenDay": _window_stat_package(_rows_since(all_rows, 7)),
        "thirtyDay": _window_stat_package(_rows_since(all_rows, 30)),
        "season": _window_stat_package(_rows_since(all_rows, None)),
    }


def _blend_multi_window_adjustments(windows: Dict[str, Dict[str, Any]], adjustment_key: str, stats_key: str) -> Dict[str, float]:
    keys = sorted(set().union(*[(windows.get(w) or {}).get(adjustment_key, {}).keys() for w in MULTI_WINDOW_WEIGHTS]))
    out: Dict[str, float] = {}
    for key in keys:
        weighted_sum = 0.0
        weight_sum = 0.0
        for window_name, configured_weight in MULTI_WINDOW_WEIGHTS.items():
            package = windows.get(window_name) or {}
            adjustments = package.get(adjustment_key) or {}
            stats = (package.get(stats_key) or {}).get(key) or {}
            count = int(stats.get("count") or 0)
            if key not in adjustments:
                continue
            if window_name != "current24h" and count < MIN_MULTI_WINDOW_SAMPLE:
                continue
            weighted_sum += float(adjustments.get(key) or 0.0) * float(configured_weight)
            weight_sum += float(configured_weight)
        out[key] = round(max(-8.0, min(8.0, weighted_sum / weight_sum)), 2) if weight_sum else 0.0
    return out


def score_learning(rows: List[Dict[str, Any]], historical_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    historical_rows = historical_rows or []
    windows = _build_learning_windows(rows, historical_rows)
    tag_adjustments = _blend_multi_window_adjustments(windows, "tagScoreAdjustments", "tagStats")
    combo_adjustments = _blend_multi_window_adjustments(windows, "tagComboScoreAdjustments", "tagComboStats")

    return {
        "multiWindowStats": {
            name: {
                "rowCount": package.get("rowCount"),
                "correct": package.get("correct"),
                "wrong": package.get("wrong"),
                "accuracyPct": package.get("accuracyPct"),
                "tagStats": package.get("tagStats"),
                "tagComboStats": package.get("tagComboStats"),
                "confidenceStats": package.get("confidenceStats"),
                "optimizerFlipStats": package.get("optimizerFlipStats"),
            }
            for name, package in windows.items()
        },
        "windowAdjustments": {
            name: {
                "tagScoreAdjustments": package.get("tagScoreAdjustments"),
                "tagComboScoreAdjustments": package.get("tagComboScoreAdjustments"),
            }
            for name, package in windows.items()
        },
        # Backward-compatible current-window aliases for existing proof readers.
        "currentWindowStats": windows.get("current24h"),
        "historicalStats": {
            "historicalRowsUsed": len(_dedupe_learning_rows(historical_rows)),
            "historicalWindowDays": HISTORICAL_AUDIT_WINDOW_DAYS,
            "historicalRunLimit": HISTORICAL_AUDIT_RUN_LIMIT or None,
            "historicalRunLimitMeaning": "explicit_circuit_breaker_only" if HISTORICAL_AUDIT_RUN_LIMIT else "uncapped_within_window",
            "minHistoricalSampleForBlend": MIN_MULTI_WINDOW_SAMPLE,
        },
        "tagStats": (windows.get("current24h") or {}).get("tagStats") or {},
        "tagComboStats": (windows.get("current24h") or {}).get("tagComboStats") or {},
        "confidenceStats": (windows.get("current24h") or {}).get("confidenceStats") or {},
        "optimizerFlipStats": (windows.get("current24h") or {}).get("optimizerFlipStats") or {},
        "adjustments": {
            "tagScoreAdjustments": tag_adjustments,
            "tagComboScoreAdjustments": combo_adjustments,
            "multiWindowWeights": MULTI_WINDOW_WEIGHTS,
            "minMultiWindowSampleForNonCurrentWindows": MIN_MULTI_WINDOW_SAMPLE,
        },
        "policy": "Learning blends four windows: current 24h, 7-day, 30-day, and season. Current 24h keeps the model responsive; longer windows stabilize signal scoring when sample size is sufficient.",
    }


def summarize(rows: List[Dict[str, Any]], historical_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    historical_rows = historical_rows or []
    graded = [r for r in rows if r.get("status") == "GRADED"]
    correct = [r for r in graded if r.get("correct")]
    optimized = [r for r in graded if r.get("individualWinnerOptimized")]
    flipped = [r for r in graded if r.get("optimizerFlippedPick")]
    all_rows = _dedupe_rows((rows or []) + (historical_rows or []))
    seven_day_rows = _rows_since(all_rows, 7)
    thirty_day_rows = _rows_since(all_rows, 30)
    season_rows = _rows_since(all_rows, None)
    return {
        "windowHours": WINDOW_HOURS,
        "targetAccuracyPct": TARGET_ACCURACY_PCT,
        "completedFinalGames": len(rows),
        "gradedPredictionCount": len(graded),
        "missingPredictionCount": len(rows) - len(graded),
        "invalidCanonicalLockCount": sum(
            1 for row in rows if row.get("status") == "INVALID_CANONICAL_LOCK"
        ),
        "missingCanonicalLockCount": sum(
            1 for row in rows if row.get("status") == "MISSING_CANONICAL_LOCK"
        ),
        "optimizedPickCount": len(graded),
        "optimizedCorrect": len(correct),
        "optimizedWrong": len(graded) - len(correct),
        "rolling24hOptimizedAccuracyPct": round(len(correct) / len(graded) * 100.0, 2) if graded else None,
        "rolling24hTargetMet": (len(correct) / len(graded) * 100.0 >= TARGET_ACCURACY_PCT) if graded else None,
        "winnerOptimizerAppliedCount": len(optimized),
        "winnerOptimizerFlipCount": len(flipped),
        "allScoredPickAccuracyPct": _accuracy(graded),
        "historicalRowsUsedForLearning": len(_dedupe_rows(historical_rows)),
        "sevenDayRowsUsedForLearning": len(seven_day_rows),
        "sevenDayAccuracyPct": _accuracy(seven_day_rows),
        "thirtyDayRowsUsedForLearning": len(thirty_day_rows),
        "thirtyDayAccuracyPct": _accuracy(thirty_day_rows),
        "seasonRowsUsedForLearning": len(season_rows),
        "seasonAccuracyPct": _accuracy(season_rows),
        "multiWindowWeights": MULTI_WINDOW_WEIGHTS,
        "actionablePickCount": len(graded),
        "actionableCorrect": len(correct),
        "actionableWrong": len(graded) - len(correct),
        "rolling24hActionableAccuracyPct": round(len(correct) / len(graded) * 100.0, 2) if graded else None,
    }


def store_report(report: Dict[str, Any]) -> Dict[str, Any]:
    if history.PULLS is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    item = history.ddb_safe({
        "PK": "MLB_ROLLING_24H_AUDIT#LATEST",
        "SK": "LATEST",
        "record_type": "mlb_rolling_24h_audit_latest",
        "sport": "mlb",
        "created_at": report.get("createdAt"),
        "data": report,
    })
    dated = history.ddb_safe({
        "PK": "MLB_ROLLING_24H_AUDIT#RUNS",
        "SK": f"AUDIT#{report.get('createdAt')}",
        "record_type": "mlb_rolling_24h_audit_run",
        "sport": "mlb",
        "created_at": report.get("createdAt"),
        "data": report,
    })
    history.PULLS.put_item(Item=item)
    history.PULLS.put_item(Item=dated)
    return {"ok": True, "latestPk": item["PK"], "latestSk": item["SK"], "runPk": dated["PK"], "runSk": dated["SK"]}


def build(days_from: int = 3, store: bool = True, write_file: bool = True) -> Dict[str, Any]:
    finals = final_scores_last_24h(days_from=days_from)
    rows = audit_rows(finals)
    historical_rows = historical_audit_rows()
    learning = score_learning(rows, historical_rows=historical_rows)
    report = {
        "ok": True,
        "proofType": "MLB_ROLLING_24H_AUDIT",
        "createdAt": now_iso(),
        "sport": "mlb",
        "windowHours": WINDOW_HOURS,
        "summary": summarize(rows, historical_rows=historical_rows),
        "scoreLearning": learning,
        "rows": rows,
        "policy": "Audit every completed MLB game in the trailing 24 hours. The optimizer target is correct team-winner selection for every individual game; 90% is measured as rolling 24h accuracy across all optimized picks. Signal scoring blends current 24h, 7-day, 30-day, and season audit trends.",
    }
    if store:
        try:
            report["stored"] = store_report(report)
        except Exception as exc:
            report["storeError"] = str(exc)
    if write_file:
        os.makedirs("runtime_reports", exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
            f.write("\n")
    return report


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
