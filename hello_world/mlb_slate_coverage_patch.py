from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from boto3.dynamodb.conditions import Key

VERSION = "MLB-SLATE-COVERAGE-v4-immutable-provider-manifest-authority"
AUTHORITY_VERSION = "MLB-LAST-PRELOCK-PROMOTION-AUTHORITY-v1-canonical-read-overlay"
CANONICAL_RECORD_TYPE = "mlb_immutable_locked_single_game_prediction"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _norm_team(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _start_key(game: Dict[str, Any]) -> str:
    dt = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return dt.isoformat() if dt else str(game.get("commence_time") or game.get("commenceTime") or "unknown")


def game_identity(game: Dict[str, Any]) -> str:
    """Return a stable game identity that never collapses doubleheaders."""
    provider_id = game.get("game_id") or game.get("gameId") or game.get("id") or game.get("gameIdentity")
    if provider_id:
        value = str(provider_id)
        if value.startswith(("provider:", "key:", "teams:")):
            return value
        return f"provider:{value}"
    game_key = str(game.get("game_key") or game.get("gameKey") or "").strip()
    start = _start_key(game)
    if game_key:
        return f"key:{game_key}|start:{start}"
    away = _norm_team(game.get("away_team") or game.get("awayTeam"))
    home = _norm_team(game.get("home_team") or game.get("homeTeam"))
    return f"teams:{away}|{home}|start:{start}"


def _latest_games(lock_module: Any, pulls: List[Dict[str, Any]], slate: str) -> List[Dict[str, Any]]:
    by_identity: Dict[str, Tuple[datetime, Dict[str, Any]]] = {}
    for pull in pulls or []:
        pulled_at = lock_module._pull_dt(pull) or datetime.min.replace(tzinfo=timezone.utc)
        for game in pull.get("games") or []:
            if lock_module._game_day(game) != slate:
                continue
            identity = game_identity(game)
            current = by_identity.get(identity)
            if current is None or pulled_at >= current[0]:
                by_identity[identity] = (pulled_at, game)
    return sorted((item[1] for item in by_identity.values()), key=lock_module._game_sort)


def _provider_manifest_for_public(
    module: Any,
    lock_module: Any,
    pulls: List[Dict[str, Any]],
    slate: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Read the latest pull's independently stored full provider schedule.

    Public completeness must never be inferred from the odds-bearing ``games``
    array.  That array may legitimately omit a provider game with no supported
    book/market.  ``provider_manifest_games_for_lock`` verifies the manifest
    fingerprint, its exact pull membership, and its strongly consistent
    immutable DynamoDB readback before returning the schedule used here.
    """
    if not pulls:
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_MISSING:NO_PULL_HISTORY")
    latest_pull = pulls[-1]
    reader = getattr(getattr(module, "history", None), "provider_manifest_games_for_lock", None)
    if not callable(reader):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_VALIDATOR_UNAVAILABLE")
    games = reader(latest_pull, slate)
    if not isinstance(games, list):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:games_not_list")

    manifest = latest_pull.get("provider_schedule_manifest")
    binding = latest_pull.get("provider_manifest_binding")
    if not isinstance(manifest, dict) or not isinstance(binding, dict):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:manifest_or_binding_missing")
    declared_games = manifest.get("games")
    if not isinstance(declared_games, list):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:declared_games_not_list")
    try:
        declared_count = int(manifest.get("gameCount"))
    except Exception:
        declared_count = -1
    if declared_count != len(games) or len(declared_games) != len(games):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:game_count_mismatch")

    returned_ids = [game_identity(game) for game in games]
    declared_ids = [game_identity(game) for game in declared_games]
    if returned_ids != declared_ids:
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:returned_games_mismatch")
    if len(set(returned_ids)) != len(returned_ids):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:duplicate_game_identity")
    wrong_slate = [
        identity
        for identity, game in zip(returned_ids, games)
        if lock_module._game_day(game) != slate
    ]
    if wrong_slate:
        raise RuntimeError(
            "MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:game_slate_mismatch:"
            + ",".join(wrong_slate)
        )
    fingerprint = str(manifest.get("fingerprint") or "")
    if not fingerprint or str(binding.get("fingerprint") or "") != fingerprint:
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:fingerprint_binding_mismatch")

    return list(games), {
        "providerManifestValidated": True,
        "providerManifestVersion": manifest.get("version"),
        "providerManifestFingerprint": fingerprint,
        "providerManifestObservedAtUtc": manifest.get("observedAtUtc"),
        "providerManifestPullId": manifest.get("pullId"),
        "providerManifestImmutable": binding.get("immutable") is True,
        "providerManifestFullProviderSchedule": binding.get("fullProviderSchedule") is True,
    }


def _coverage(games: List[Dict[str, Any]], predictions: List[Dict[str, Any]], stored: List[Dict[str, Any]], store_requested: bool) -> Dict[str, Any]:
    expected = {game_identity(game): game for game in games}
    produced = {game_identity(row): row for row in predictions if row.get("predictedWinner")}
    missing = sorted(set(expected) - set(produced))
    extra = sorted(set(produced) - set(expected))
    stored_ok = len([row for row in stored if isinstance(row, dict) and row.get("ok")])
    matchup_counts: Dict[str, int] = {}
    for game in games:
        matchup = f"{_norm_team(game.get('away_team') or game.get('awayTeam'))}|{_norm_team(game.get('home_team') or game.get('homeTeam'))}"
        matchup_counts[matchup] = matchup_counts.get(matchup, 0) + 1
    doubleheaders = sorted(key for key, count in matchup_counts.items() if count > 1)
    complete = not missing and not extra and len(produced) == len(expected)
    if store_requested:
        complete = complete and stored_ok == len(produced)
    return {
        "applied": True,
        "version": VERSION,
        "strictCoverageRequired": True,
        "doubleheaderSafeIdentity": True,
        "manifestGameCount": len(expected),
        "predictionGameCount": len(produced),
        "storedPredictionCount": stored_ok,
        "storeRequested": bool(store_requested),
        "coverageRatio": round(len(produced) / len(expected), 4) if expected else None,
        "coverageComplete": complete,
        "operationalStatus": "COMPLETE" if complete else "INCOMPLETE_BLOCKED",
        "missingGameIdentities": missing,
        "extraGameIdentities": extra,
        "manifestGameIdentities": sorted(expected),
        "predictionGameIdentities": sorted(produced),
        "doubleheaderMatchups": doubleheaders,
        "publicAccuracyEligible": complete,
        "rules": [
            "Provider game id is the primary identity.",
            "When provider id is unavailable, game key plus commence time is required.",
            "Same-team doubleheaders must remain separate lock-manifest rows.",
            "Only validated immutable LOCKED#GAME rows count as official locks.",
        ],
    }


def _canonical_items(module: Any, slate: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    table = getattr(getattr(module, "history", None), "PULLS", None)
    if table is None:
        return [], "SNAPSHOTS_TABLE_not_configured"
    items: List[Dict[str, Any]] = []
    start_key = None
    try:
        while True:
            args: Dict[str, Any] = {
                "KeyConditionExpression": Key("PK").eq(f"GAME_WINNERS#mlb#{slate}"),
                "ConsistentRead": True,
            }
            if start_key:
                args["ExclusiveStartKey"] = start_key
            response = table.query(**args)
            items.extend(
                item
                for item in (response.get("Items") or [])
                if str(item.get("SK") or "").startswith("LOCKED#GAME#")
            )
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                break
    except Exception as exc:
        return [], f"canonical_query_failed:{exc}"
    return items, None


def _canonical_row(
    module: Any,
    item: Dict[str, Any],
    slate: str,
    manifest_game: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    if item.get("record_type") != CANONICAL_RECORD_TYPE:
        errors.append("wrong_record_type")
    if item.get("immutable_locked") is not True:
        errors.append("immutable_locked_item_flag_missing")
    if item.get("stage_authority_verified") is not True:
        errors.append("stage_authority_item_flag_missing")
    if not str(item.get("SK") or "").startswith("LOCKED#GAME#"):
        errors.append("wrong_keyspace")
    row = item.get("data")
    if not isinstance(row, dict):
        errors.append("canonical_data_missing")
        return None, errors
    row = copy.deepcopy(row)
    if str(row.get("slate_date") or row.get("slateDateEt") or "") != slate:
        errors.append("slate_mismatch")
    if row.get("immutableLockedStorage") is not True:
        errors.append("immutable_locked_row_flag_missing")
    if row.get("lockedPrediction") is not True:
        errors.append("locked_prediction_flag_missing")
    if not row.get("predictedWinner") or row.get("predictedSide") not in {"home", "away"}:
        errors.append("winner_or_side_missing")
    if game_identity(row) != game_identity(manifest_game):
        errors.append("manifest_game_identity_mismatch")
    if _norm_team(row.get("homeTeam") or row.get("home_team")) != _norm_team(
        manifest_game.get("home_team") or manifest_game.get("homeTeam")
    ):
        errors.append("manifest_home_team_mismatch")
    if _norm_team(row.get("awayTeam") or row.get("away_team")) != _norm_team(
        manifest_game.get("away_team") or manifest_game.get("awayTeam")
    ):
        errors.append("manifest_away_team_mismatch")
    manifest_start = _parse_dt(manifest_game.get("commence_time") or manifest_game.get("commenceTime"))
    row_start = _parse_dt(row.get("commenceTime") or row.get("commence_time"))
    if not manifest_start or row_start != manifest_start:
        errors.append("manifest_commence_time_mismatch")
    expected_cutoff = manifest_start - timedelta(minutes=45) if manifest_start else None
    row_cutoff = _parse_dt(
        row.get("lockedAtUtc")
        or (row.get("slatePredictionLock") or {}).get("lockAtUtc")
        or (row.get("frozenFeatureVector") or {}).get("lockAtUtc")
    )
    if not expected_cutoff or row_cutoff != expected_cutoff:
        errors.append("manifest_tminus45_cutoff_mismatch")
    try:
        import mlb_immutable_locked_storage_patch as immutable_storage

        errors.extend(
            immutable_storage.validate_canonical_stage_authority(
                module.history.PULLS,
                row,
            )
        )
    except Exception as exc:
        errors.append(f"stage_authority_validator_unavailable:{exc}")
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as exact_contract

        errors.extend(exact_contract.validate_exact_locked_row(row))
    except Exception as exc:
        errors.append(f"exact_vector_validator_unavailable:{exc}")
    return (row if not errors else None), sorted(set(errors))


def _canonical_by_identity(
    module: Any,
    slate: str,
    manifest: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[str]], Optional[str]]:
    manifest_by_id = {game_identity(game): game for game in manifest}
    manifest_ids = set(manifest_by_id)
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    invalid: Dict[str, List[str]] = {}
    items, query_error = _canonical_items(module, slate)
    for item in items:
        raw = item.get("data") if isinstance(item.get("data"), dict) else item
        identity = game_identity(raw)
        if identity not in manifest_ids:
            continue
        row, errors = _canonical_row(module, item, slate, manifest_by_id[identity])
        if errors:
            invalid.setdefault(identity, []).extend(errors)
            continue
        candidates.setdefault(identity, []).append(row or {})

    canonical: Dict[str, Dict[str, Any]] = {}
    for identity, rows in candidates.items():
        if identity in invalid:
            continue
        if len(rows) != 1:
            invalid.setdefault(identity, []).append("ambiguous_multiple_canonical_rows")
            continue
        canonical[identity] = rows[0]
    invalid = {identity: sorted(set(errors)) for identity, errors in invalid.items()}
    return canonical, invalid, query_error


def _per_game_cutoff(lock_module: Any, game: Dict[str, Any]) -> Optional[str]:
    start = lock_module._parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return (start - timedelta(minutes=lock_module.LOCK_MINUTES)).isoformat() if start else None


def _prelock_row(
    row: Dict[str, Any],
    public: Dict[str, Any],
    cutoff: Optional[str],
    pending_status: str = "OPEN_PRE_LOCK",
) -> Dict[str, Any]:
    out = copy.deepcopy(row or {})
    tags = {
        str(tag)
        for tag in (out.get("tags") or [])
        if str(tag)
        not in {
            "FINAL_LOCKED",
            "SLATE_LOCKED",
            "SLATE_WIDE_45_MIN_LOCK_POLICY",
            "OFFICIAL_PREDICTION",
            "OFFICIAL_LOCKED_PREDICTION",
            "OFFICIAL_PREDICTION_NOT_PLAYABLE",
            "CANONICAL_PER_GAME_LOCK",
        }
    }
    if pending_status == "OPEN_PRE_LOCK":
        tags.update({"PRE_LOCK_PREDICTION", "PER_GAME_CANONICAL_LOCK_PENDING"})
        official_status = "PRE_LOCK_PLATFORM_PREDICTION"
        reason = "canonical_per_game_lock_not_yet_available"
        recommendation_status = "PRE_LOCK_PREDICTION"
        display_group = "pre_lock_prediction"
    else:
        tags.update({pending_status, "PER_GAME_CANONICAL_LOCK_MISSING"})
        official_status = pending_status
        reason = "required_canonical_per_game_lock_missing"
        recommendation_status = pending_status
        display_group = "lock_failure"
    out.update({
        "lockedPrediction": False,
        "officialPrediction": False,
        "officialPick": False,
        "isOfficialDisplayPick": False,
        "officialPredictionStatus": official_status,
        "officialPredictionReason": reason,
        "recommendationStatus": recommendation_status,
        "displayGroup": display_group,
        "fullDataFinalPick": False,
        "accuracyTargetEligible": False,
        "slatePredictionLock": public,
        "perGameCanonicalLock": {
            "authorityVersion": AUTHORITY_VERSION,
            "status": pending_status,
            "lockAtUtc": cutoff,
            "canonical": False,
        },
        "tags": sorted(tags),
    })
    out.pop("lockedAtUtc", None)
    out.pop("finalGateStored", None)
    out.pop("frozenFeatureVector", None)
    out.pop("frozenFeatureVectorVersion", None)
    out.pop("frozenOutcomeFeatures", None)
    out.pop("frozenReliabilityFeatures", None)
    out.pop("featureVectorFrozenAtLock", None)
    out.pop("mlFeatureFreeze", None)
    out.pop("immutablePerGameStage", None)
    out.pop("immutableLockedStorage", None)
    out.pop("canonicalLockedStore", None)
    gate = dict(out.get("lastPossiblePredictionGate") or {})
    gate.update({
        "policyVersion": AUTHORITY_VERSION,
        "phase": "PRE_LOCK" if pending_status == "OPEN_PRE_LOCK" else pending_status,
        "finalWindowActive": False,
        "finalLocked": False,
        "slateWideLock": False,
        "perGameLock": True,
        "lockAtUtc": cutoff,
    })
    out["lastPossiblePredictionGate"] = gate
    return out


def _pending_status(game: Dict[str, Any], now: datetime, cutoff: Optional[str]) -> str:
    start = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    cutoff_at = _parse_dt(cutoff)
    if start and now >= start:
        return "MISSED_LOCK"
    if cutoff_at and now >= cutoff_at:
        return "LOCK_DUE_CANONICAL_MISSING"
    return "OPEN_PRE_LOCK"


def _display_card(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "gameId": row.get("gameId"),
        "gameIdentity": row.get("gameIdentity"),
        "gameKey": row.get("gameKey"),
        "homeTeam": row.get("homeTeam"),
        "awayTeam": row.get("awayTeam"),
        "commenceTime": row.get("commenceTime"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "confidenceTier": row.get("confidenceTier"),
        "teamWinProbabilityPct": row.get("teamWinProbabilityPct", row.get("winProbabilityPct")),
        "score": row.get("score"),
        "rank": row.get("rank"),
        "officialPrediction": bool(row.get("officialPrediction")),
        "officialPick": bool(row.get("officialPick")),
        "playable": bool(row.get("playable")),
        "playablePick": bool(row.get("playablePick")),
        "officialPredictionStatus": row.get("officialPredictionStatus"),
        "playabilityStatus": row.get("playabilityStatus"),
        "recommendationStatus": row.get("recommendationStatus"),
        "tags": row.get("tags") or [],
    }


def _official_row(row: Dict[str, Any], public: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(row or {})
    tags = {
        str(tag)
        for tag in (out.get("tags") or [])
        if str(tag) != "SLATE_WIDE_45_MIN_LOCK_POLICY"
    }
    tags.update({"FINAL_LOCKED", "OFFICIAL_PREDICTION", "OFFICIAL_LOCKED_PREDICTION", "CANONICAL_PER_GAME_LOCK"})
    lock_at = out.get("lockedAtUtc") or (out.get("frozenFeatureVector") or {}).get("lockAtUtc")
    row_lock = dict(public)
    row_lock.update(out.get("slatePredictionLock") or {})
    row_lock.update({
        "policyVersion": AUTHORITY_VERSION,
        "authorityVersion": AUTHORITY_VERSION,
        "slateWideLock": False,
        "perGameLock": True,
        "locked": True,
        "lockStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockAtUtc": lock_at,
    })
    out.update({
        "lockedPrediction": True,
        "officialPrediction": True,
        "officialPick": True,
        "isOfficialDisplayPick": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "officialPredictionReason": "validated_immutable_canonical_per_game_lock",
        "slatePredictionLock": row_lock,
        "perGameCanonicalLock": {
            "authorityVersion": AUTHORITY_VERSION,
            "status": "OFFICIAL_LOCKED_PREDICTION",
            "lockAtUtc": lock_at,
            "canonical": True,
        },
        "tags": sorted(tags),
    })
    gate = dict(out.get("lastPossiblePredictionGate") or {})
    gate.update({
        "policyVersion": AUTHORITY_VERSION,
        "phase": "FINAL_LOCKED",
        "finalWindowActive": False,
        "finalLocked": True,
        "slateWideLock": False,
        "perGameLock": True,
        "lockAtUtc": lock_at,
    })
    out["lastPossiblePredictionGate"] = gate
    return out


def _fail_closed(result: Dict[str, Any], error: str) -> Dict[str, Any]:
    out = copy.deepcopy(result or {})
    public = dict(out.get("slatePredictionLock") or {})
    public.update({
        "applied": False,
        "policyVersion": AUTHORITY_VERSION,
        "authorityVersion": AUTHORITY_VERSION,
        "slateWideLock": False,
        "perGameLock": True,
        "locked": False,
        "lockStatus": "CANONICAL_AUTHORITY_UNAVAILABLE_FAIL_CLOSED",
        "providerManifestValidated": False,
        "error": str(error),
    })
    out["slatePredictionLock"] = public
    out["locked"] = False
    out["operationalDefect"] = True
    out["allGamesPredicted"] = False
    out["allGamesHaveDisplayedWinnerPrediction"] = False
    out["predictions"] = [_prelock_row(row, public, None) for row in (out.get("predictions") or []) if isinstance(row, dict)]
    cards = [_display_card(row) for row in out["predictions"]]
    out["officialPredictionCount"] = 0
    out["officialPickCount"] = 0
    out["preLockPredictionCount"] = len(out["predictions"])
    out["requiredWinnerPredictionDisplay"] = cards
    out["officialPredictionDisplay"] = []
    out["nonOfficialPredictionDisplay"] = cards
    out["nonPlayableOfficialPredictionDisplay"] = []
    coverage = dict(out.get("slateCoverage") or {})
    coverage.update({
        "applied": False,
        "version": VERSION,
        "strictCoverageRequired": True,
        "coverageComplete": False,
        "canonicalCoverageComplete": False,
        "publicAccuracyEligible": False,
        "providerManifestValidated": False,
        "operationalStatus": "PROVIDER_MANIFEST_AUTHORITY_UNAVAILABLE_FAIL_CLOSED",
        "error": str(error),
        "canonicalReadAuthorityWriteCount": 0,
    })
    out["slateCoverage"] = coverage
    out["lastPossiblePredictionGate"] = {
        "applied": False,
        "policyVersion": AUTHORITY_VERSION,
        "slateWideLock": False,
        "perGameLock": True,
        "finalLockedCount": 0,
        "resultLocked": False,
        "error": str(error),
    }
    out["mlFeatureFreeze"] = {
        "applied": True,
        "canonicalPublicAuthorityVersion": AUTHORITY_VERSION,
        "frozenRowCount": 0,
        "trainingEligibleCount": 0,
        "coverageComplete": False,
        "pendingRowsAreNotFrozen": True,
    }
    return out


def apply(lock_module: Any):
    if getattr(lock_module, "_INQSI_MLB_SLATE_COVERAGE_PATCH_APPLIED", False):
        return lock_module

    lock_module._game_key = game_identity

    def latest_games(pulls: List[Dict[str, Any]], slate: str) -> List[Dict[str, Any]]:
        return _latest_games(lock_module, pulls, slate)

    lock_module._latest_games = latest_games
    original_lock_state = lock_module._lock_state

    def lock_state(pulls: List[Dict[str, Any]], slate: str) -> Dict[str, Any]:
        state = original_lock_state(pulls, slate)
        scoring = state.get("_scoring_pulls") or pulls
        manifest = latest_games(scoring, slate)
        public = {
            "manifestVersion": VERSION,
            "manifestGameCount": len(manifest),
            "manifestGameIdentities": [game_identity(game) for game in manifest],
            "doubleheaderSafeIdentity": True,
        }
        state.update(public)
        return state

    lock_module._lock_state = lock_state

    def locked_result(module: Any, result: Dict[str, Any], args: Tuple[Any, ...], kwargs: Dict[str, Any], store: bool) -> Dict[str, Any]:
        """Overlay canonical per-game locks without generating or storing a pick."""
        slate = str((result or {}).get("slate_date") or lock_module._slate_from_call(args, kwargs, module))
        pulls = module.history.query_pulls("mlb", slate, lock_module._limit(kwargs))
        pulls = sorted(
            pulls or [],
            key=lambda pull: lock_module._pull_dt(pull) or datetime.min.replace(tzinfo=timezone.utc),
        )
        manifest, manifest_authority = _provider_manifest_for_public(
            module,
            lock_module,
            pulls,
            slate,
        )
        # Derive every timing field from the verified full schedule rather than
        # the odds-bearing pull.  Keep aggregate pull metadata for observability.
        authority_pull = dict(pulls[-1])
        authority_pull["games"] = copy.deepcopy(manifest)
        state = original_lock_state([authority_pull], slate)
        state.update({
            "totalPullCountAvailable": len(pulls),
            "scoringPullCount": len(pulls),
            "latestAvailablePullAt": pulls[-1].get("pulled_at"),
            "latestScoringPullAt": pulls[-1].get("pulled_at"),
        })
        public = {key: value for key, value in state.items() if not key.startswith("_")}
        public.update(manifest_authority)
        canonical, invalid, query_error = _canonical_by_identity(module, slate, manifest)
        manifest_ids = [game_identity(game) for game in manifest]
        canonical_count = len(canonical)
        all_canonical = bool(manifest_ids) and canonical_count == len(manifest_ids)
        now = _now_utc()
        pending_states = {
            game_identity(game): _pending_status(
                game,
                now,
                _per_game_cutoff(lock_module, game),
            )
            for game in manifest
            if game_identity(game) not in canonical
        }
        lock_due_count = len([
            value for value in pending_states.values()
            if value == "LOCK_DUE_CANONICAL_MISSING"
        ])
        missed_lock_count = len([
            value for value in pending_states.values()
            if value == "MISSED_LOCK"
        ])
        lock_times = [
            _parse_dt(row.get("lockedAtUtc") or (row.get("frozenFeatureVector") or {}).get("lockAtUtc"))
            for row in canonical.values()
        ]
        lock_times = [value for value in lock_times if value]
        if all_canonical:
            lock_status = "COMPLETE_MANIFEST_ALL_CANONICAL"
        elif missed_lock_count:
            lock_status = "MISSED_LOCK"
        elif lock_due_count:
            lock_status = "LOCK_DUE_CANONICAL_MISSING"
        elif canonical_count:
            lock_status = "PARTIAL_PER_GAME_CANONICAL"
        elif not pulls:
            lock_status = "NO_PULL_HISTORY"
        else:
            lock_status = "OPEN_PRE_LOCK"
        public.update({
            "applied": query_error is None,
            "policyVersion": AUTHORITY_VERSION,
            "authorityVersion": AUTHORITY_VERSION,
            "slateWideLock": False,
            "perGameLock": True,
            "lockMinutesBeforeEachGame": lock_module.LOCK_MINUTES,
            "locked": all_canonical,
            "lockStatus": lock_status if query_error is None else "CANONICAL_READ_FAILED_FAIL_CLOSED",
            "lockAtUtc": max(lock_times).isoformat() if all_canonical and lock_times else None,
            "source": "immutable_provider_schedule_manifest_with_validated_locked_game_rows",
            "manifestVersion": VERSION,
            "manifestGameCount": len(manifest_ids),
            "manifestGameIdentities": manifest_ids,
            "canonicalLockedGameCount": canonical_count,
            "pendingCanonicalGameCount": max(len(manifest_ids) - canonical_count, 0),
            "lockDueCanonicalMissingCount": lock_due_count,
            "missedLockCount": missed_lock_count,
            "pendingCanonicalStatuses": pending_states,
            "canonicalCoverageComplete": all_canonical,
            "canonicalReadOperational": query_error is None,
            "canonicalReadError": query_error,
            "invalidCanonicalRows": invalid,
            "doubleheaderSafeIdentity": True,
            "rules": [
                "Each game locks independently 45 minutes before its own scheduled start.",
                "The last valid pre-lock prediction promoted into immutable LOCKED#GAME storage is final.",
                "Canonical rows are overlaid on public reads and are never recomputed.",
                "Games before their cutoff without a valid canonical row remain explicitly pre-lock.",
                "A missing canonical row at or after cutoff is an operational lock failure, never pre-lock or official.",
                "The result is locked only after every manifest game has a canonical row.",
                "The manifest is the independently stored full provider schedule, including games without supported odds.",
            ],
        })

        current: Dict[str, Dict[str, Any]] = {}
        extra_current: List[str] = []
        for row in (result or {}).get("predictions") or []:
            if not isinstance(row, dict):
                continue
            identity = game_identity(row)
            if identity in set(manifest_ids):
                current[identity] = row
            else:
                extra_current.append(identity)

        predictions: List[Dict[str, Any]] = []
        missing: List[str] = []
        for game in manifest:
            identity = game_identity(game)
            if identity in canonical:
                row = _official_row(canonical[identity], public)
            elif identity in current:
                cutoff = _per_game_cutoff(lock_module, game)
                row = _prelock_row(
                    current[identity],
                    public,
                    cutoff,
                    pending_states.get(identity, "OPEN_PRE_LOCK"),
                )
            else:
                missing.append(identity)
                continue
            row["slateCoverageVersion"] = VERSION
            predictions.append(row)

        displayed_complete = not missing and len(predictions) == len(manifest_ids)
        coverage = _coverage(manifest, predictions, [], False)
        coverage.update({
            "coverageComplete": displayed_complete,
            "operationalStatus": lock_status if query_error is None else "CANONICAL_READ_FAILED_FAIL_CLOSED",
            "publicAccuracyEligible": all_canonical,
            "canonicalAuthorityVersion": AUTHORITY_VERSION,
            "canonicalReadOperational": query_error is None,
            "canonicalReadError": query_error,
            "canonicalLockedGameCount": canonical_count,
            "pendingCanonicalGameCount": max(len(manifest_ids) - canonical_count, 0),
            "lockDueCanonicalMissingCount": lock_due_count,
            "missedLockCount": missed_lock_count,
            "pendingCanonicalStatuses": pending_states,
            "canonicalCoverageComplete": all_canonical,
            "invalidCanonicalRows": invalid,
            "missingGameIdentities": missing,
            "extraCurrentPredictionIdentities": sorted(set(extra_current)),
            "storeRequested": bool(store),
            "canonicalReadAuthorityWriteCount": 0,
            **manifest_authority,
        })
        public["coverageComplete"] = displayed_complete
        public["coverageStatus"] = coverage["operationalStatus"]

        official = [row for row in predictions if row.get("lockedPrediction") is True]
        prelock = [row for row in predictions if row.get("lockedPrediction") is not True]
        playable = [row for row in predictions if row.get("playable") is True or row.get("playablePick") is True]
        non_playable_official = [row for row in official if row not in playable]
        cards = [_display_card(row) for row in predictions]
        official_cards = [_display_card(row) for row in official]
        prelock_cards = [_display_card(row) for row in prelock]
        playable_cards = [_display_card(row) for row in playable]
        out = copy.deepcopy(result or {})
        out.update({
            "sport": "mlb",
            "slate_date": slate,
            "locked": all_canonical,
            "gameCount": len(manifest_ids),
            "count": len(predictions),
            "allGamesPredicted": displayed_complete,
            "totalPullCountAvailable": len(pulls),
            "scoringPullCount": len(pulls),
            "latestPullAt": (pulls[-1].get("pulled_at") if pulls else None),
            "latestScoringPullAt": (pulls[-1].get("pulled_at") if pulls else None),
            "officialPredictionCount": len(official),
            "officialPickCount": len(official),
            "preLockPredictionCount": len(prelock),
            "playablePredictionCount": len(playable),
            "actionablePickCount": len(playable),
            "nonPlayableOfficialPredictionCount": len(non_playable_official),
            "requiredGameWinnerPredictionCount": len(predictions),
            "displayPredictionCount": len(predictions),
            "allGamesHaveDisplayedWinnerPrediction": displayed_complete,
            "slatePredictionLock": public,
            "slateCoverage": coverage,
            "publicPerGameAuthority": {
                "applied": query_error is None,
                "version": AUTHORITY_VERSION,
                "canonicalLockedGameCount": canonical_count,
                "pendingCanonicalGameCount": max(len(manifest_ids) - canonical_count, 0),
                "lockDueCanonicalMissingCount": lock_due_count,
                "missedLockCount": missed_lock_count,
                "resultLocked": all_canonical,
                "recomputedLockedPredictions": False,
            },
            "operationalDefect": bool(
                query_error
                or invalid
                or not displayed_complete
                or lock_due_count
                or missed_lock_count
            ),
            "predictions": predictions,
            "requiredWinnerPredictionDisplay": cards,
            "officialPredictionDisplay": official_cards,
            "nonOfficialPredictionDisplay": prelock_cards,
            "playablePredictionDisplay": playable_cards,
            "nonPlayableOfficialPredictionDisplay": [
                _display_card(row) for row in non_playable_official
            ],
        })
        gate = dict(out.get("lastPossiblePredictionGate") or {})
        gate.update({
            "applied": True,
            "policyVersion": AUTHORITY_VERSION,
            "slateWideLock": False,
            "perGameLock": True,
            "finalLockedCount": canonical_count,
            "pendingCanonicalGameCount": max(len(manifest_ids) - canonical_count, 0),
            "lockDueCanonicalMissingCount": lock_due_count,
            "missedLockCount": missed_lock_count,
            "resultLocked": all_canonical,
        })
        out["lastPossiblePredictionGate"] = gate
        model_version = str(out.get("modelVersion") or "")
        for legacy_suffix in (
            "+slate-wide-45min-final-gate",
            "+last-possible-gate-v4-12h-individual-game-require-sportsdataio",
            "+last-possible-gate-v4-12h-individual-game-odds-api-only",
        ):
            model_version = model_version.replace(legacy_suffix, "")
        authority_suffix = "+last-prelock-promotion-authority-v1"
        if authority_suffix not in model_version:
            model_version += authority_suffix
        out["modelVersion"] = model_version
        for summary_key in (
            "winnerStackV2",
            "rolling24hAccuracyTarget",
            "accuracyTarget",
            "predictionSemantics",
            "mlOverlay",
        ):
            summary = out.get(summary_key)
            if isinstance(summary, dict):
                summary = dict(summary)
                summary.update({
                    "officialPredictionCount": len(official),
                    "officialPickCount": len(official),
                    "preLockPredictionCount": len(prelock),
                })
                if summary_key in {"rolling24hAccuracyTarget", "accuracyTarget"}:
                    summary["lastPossiblePredictionGate"] = copy.deepcopy(gate)
                out[summary_key] = summary
        freeze = dict(out.get("mlFeatureFreeze") or {})
        freeze.update({
            "applied": True,
            "canonicalPublicAuthorityVersion": AUTHORITY_VERSION,
            "frozenRowCount": canonical_count,
            "trainingEligibleCount": len([
                row
                for row in official
                if (row.get("mlFeatureFreeze") or {}).get("trainingEligible") is True
            ]),
            "coverageComplete": all_canonical,
            "pendingRowsAreNotFrozen": True,
        })
        out["mlFeatureFreeze"] = freeze
        return out

    lock_module._locked_result = locked_result
    lock_module._canonical_authority_result = locked_result
    lock_module.POLICY_VERSION = AUTHORITY_VERSION
    lock_module.PUBLIC_PER_GAME_AUTHORITY_VERSION = AUTHORITY_VERSION
    lock_module._INQSI_MLB_SLATE_COVERAGE_PATCH_APPLIED = True
    lock_module._INQSI_MLB_LAST_PRELOCK_PROMOTION_AUTHORITY_APPLIED = True
    return lock_module


def install_public_authority(module: Any, lock_module: Any) -> Any:
    """Make canonical overlay the final public-read authority wrapper."""
    if getattr(module, "_INQSI_MLB_PUBLIC_PER_GAME_AUTHORITY_APPLIED", False):
        return module
    apply(lock_module)
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            return lock_module._canonical_authority_result(
                module,
                result,
                args,
                kwargs,
                bool(kwargs.get("store")),
            )
        except Exception as exc:
            return _fail_closed(result, str(exc))

    module.predict_all = patched_predict_all
    module.MLB_PUBLIC_PER_GAME_AUTHORITY_VERSION = AUTHORITY_VERSION
    module._INQSI_MLB_PUBLIC_PER_GAME_AUTHORITY_APPLIED = True
    return module
