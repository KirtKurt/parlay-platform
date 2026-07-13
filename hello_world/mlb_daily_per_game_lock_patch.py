from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mlb_slate_coverage_patch import game_identity


VERSION = "INQSI-MLB-DAILY-LOCK-v4-per-game-tminus45-staged-write-once"
LOCK_POLICY = "each_mlb_game_minus_45_minutes"
STAGE_RECORD_TYPE = "mlb_staged_per_game_tminus45_lock"
CUTOFF_STABILIZATION_SECONDS = 120
SOURCE_WINDOW_VERSION = "mlb_per_game_cutoff_source_window_v1"

_SCOPED_PULLS: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "inqsi_mlb_scoped_per_game_lock_pulls",
    default=None,
)


def _parse_dt(module: Any, value: Any) -> Optional[datetime]:
    parsed = module._parse_dt(value)
    return parsed.astimezone(timezone.utc) if parsed else None


def _start(module: Any, game: Dict[str, Any]) -> Optional[datetime]:
    return _parse_dt(module, game.get("commence_time") or game.get("commenceTime"))


def _lock_at(module: Any, game: Dict[str, Any]) -> Optional[datetime]:
    start = _start(module, game)
    return start - timedelta(minutes=module.LOCK_MINUTES) if start else None


def _pull_at(module: Any, pull: Dict[str, Any]) -> Optional[datetime]:
    return _parse_dt(module, pull.get("pulled_at") or pull.get("asof") or pull.get("created_at"))


def _has_moneyline(game: Dict[str, Any]) -> bool:
    for payload in (game.get("books") or {}).values():
        market = (payload or {}).get("ml") or (payload or {}).get("moneyline") or {}
        if market.get("home") not in (None, "") and market.get("away") not in (None, ""):
            return True
    return False


def _matching_game(pull: Dict[str, Any], identity: str) -> Optional[Dict[str, Any]]:
    for game in pull.get("games") or []:
        if game_identity(game) == identity and _has_moneyline(game):
            return game
    return None


def _scoring_pulls(
    module: Any,
    pulls: Iterable[Dict[str, Any]],
    game: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return only valid snapshots for one game at/before its scheduled cutoff."""
    identity = game_identity(game)
    cutoff = _lock_at(module, game)
    if not cutoff:
        return []
    selected: List[Dict[str, Any]] = []
    for pull in sorted(pulls or [], key=lambda item: _pull_at(module, item) or datetime.min.replace(tzinfo=timezone.utc)):
        pulled_at = _pull_at(module, pull)
        if not pulled_at or pulled_at > cutoff:
            continue
        matching = _matching_game(pull, identity)
        if not matching:
            continue
        scoped = copy.deepcopy(pull)
        scoped["games"] = [copy.deepcopy(matching)]
        selected.append(scoped)
    return selected


def _game_snapshot_fingerprint(game: Dict[str, Any]) -> str:
    payload = json.dumps(_plain(game), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_window_entry(module: Any, pull: Dict[str, Any], game: Dict[str, Any]) -> Dict[str, Any]:
    matching = _matching_game(pull, game_identity(game))
    pulled_at = _pull_at(module, pull)
    return {
        "pullId": str(pull.get("pull_id") or ""),
        "pulledAtUtc": pulled_at.isoformat() if pulled_at else None,
        "gameSnapshotFingerprint": _game_snapshot_fingerprint(matching or {}),
    }


def _source_window_entries(module: Any, scoring: Iterable[Dict[str, Any]], game: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [_source_window_entry(module, pull, game) for pull in scoring]


def _source_window_key(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(entry.get("pullId") or ""),
        str(entry.get("pulledAtUtc") or ""),
        str(entry.get("gameSnapshotFingerprint") or ""),
    )


def _cutoff_stable_at(module: Any, game: Dict[str, Any]) -> Optional[datetime]:
    lock_at = _lock_at(module, game)
    return lock_at + timedelta(seconds=CUTOFF_STABILIZATION_SECONDS) if lock_at else None


def _install_scoped_query(history: Any) -> None:
    """Install a ContextVar-based read scope without changing the normal query path."""
    if getattr(history, "_INQSI_MLB_PER_GAME_QUERY_SCOPE_V1", False):
        return
    original = history.query_pulls

    def query_pulls(sport: str, date: Optional[str] = None, limit: int = 500):
        scoped = _SCOPED_PULLS.get()
        requested_sport = str(sport or "").strip().lower()
        if scoped and requested_sport == "mlb" and str(date or "") == str(scoped.get("slate")):
            size = min(max(int(limit), 1), 500)
            return copy.deepcopy(list(scoped.get("pulls") or [])[:size])
        return original(sport, date, limit)

    history.query_pulls = query_pulls
    history._INQSI_MLB_PER_GAME_QUERY_SCOPE_V1 = True


@contextmanager
def _pull_scope(history: Any, slate: str, pulls: List[Dict[str, Any]]):
    _install_scoped_query(history)
    token = _SCOPED_PULLS.set({"slate": slate, "pulls": copy.deepcopy(pulls)})
    try:
        yield
    finally:
        _SCOPED_PULLS.reset(token)


def _stage_sk(module: Any, game: Dict[str, Any]) -> str:
    digest = hashlib.sha256(game_identity(game).encode("utf-8")).hexdigest()
    return f"PER_GAME_LOCK#TMINUS{module.LOCK_MINUTES}#{digest}"


def _stage_key(module: Any, slate: str, game: Dict[str, Any]) -> Dict[str, str]:
    return {"PK": module._lock_pk(slate), "SK": _stage_sk(module, game)}


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _fingerprint_material(item: Dict[str, Any]) -> Dict[str, Any]:
    row = (item.get("data") or {}).get("row") or {}
    vector = row.get("frozenFeatureVector") or {}
    selected_signal = row.get("homeSignal") if row.get("predictedSide") == "home" else row.get("awaySignal")
    selected_signal = selected_signal if isinstance(selected_signal, dict) else {}
    return {
        "version": VERSION,
        "slateDateEt": item.get("slate_date"),
        "gameIdentity": item.get("game_identity"),
        "gameId": row.get("gameId"),
        "commenceTime": row.get("commenceTime"),
        "homeTeam": row.get("homeTeam"),
        "awayTeam": row.get("awayTeam"),
        "scheduledLockAtUtc": item.get("scheduled_lock_at_utc"),
        "sourcePullAtUtc": item.get("source_pull_at_utc"),
        "sourcePullId": item.get("source_pull_id"),
        "sourceWindow": item.get("source_window") or {},
        "actualStagedAtUtc": item.get("staged_at_utc"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "americanOdds": row.get("lockedAmericanOdds", row.get("americanOdds")),
        "priceBook": row.get("priceBook") or selected_signal.get("priceBook"),
        "priceSource": row.get("priceSource") or selected_signal.get("priceSource"),
        "vectorVersion": vector.get("version"),
        "vectorFingerprint": vector.get("fingerprint"),
        "manifestGameIdentities": (item.get("data") or {}).get("manifestGameIdentities") or [],
    }


def _stage_fingerprint(item: Dict[str, Any]) -> str:
    payload = json.dumps(_plain(_fingerprint_material(item)), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_stage(module: Any, slate: str, game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    response = module.TABLE.get_item(Key=_stage_key(module, slate, game), ConsistentRead=True)
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def _conditional_collision(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    return str((response.get("Error") or {}).get("Code") or "") == "ConditionalCheckFailedException"


def _manifest_coverage(manifest: List[Dict[str, Any]], locked_count: int) -> Dict[str, Any]:
    identities = [game_identity(game) for game in manifest]
    return {
        "applied": True,
        "version": VERSION,
        "strictCoverageRequired": True,
        "doubleheaderSafeIdentity": True,
        "coverageComplete": bool(identities),
        "coverageMeaning": "complete_slate_manifest_membership_at_each_game_lock",
        "manifestCoverageComplete": bool(identities),
        "lockCoverageComplete": locked_count == len(identities) and bool(identities),
        "manifestGameCount": len(identities),
        "manifestGameIdentities": identities,
        "lockedGameCountAtWrite": locked_count,
        "pendingGameCountAtWrite": max(len(identities) - locked_count, 0),
        "publicAccuracyEligible": bool(identities),
        "operationalStatus": "COMPLETE_MANIFEST_ALL_LOCKED" if locked_count == len(identities) else "COMPLETE_MANIFEST_STAGED_LOCKING",
    }


def _per_game_lock(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    source_pull: Dict[str, Any],
    staged_at: datetime,
    manifest: List[Dict[str, Any]],
    locked_count: int,
) -> Dict[str, Any]:
    start = _start(module, game)
    lock_at = _lock_at(module, game)
    source_at = _pull_at(module, source_pull)
    return {
        "applied": True,
        "policyVersion": VERSION,
        "lockPolicy": LOCK_POLICY,
        "slateWideLock": False,
        "perGameLock": True,
        "locked": True,
        "finalLocked": True,
        "phase": "GAME_LOCKED",
        "lockStatus": "LOCKED_AT_OWN_TMINUS45",
        "lockMinutesBeforeGame": module.LOCK_MINUTES,
        "gameIdentity": game_identity(game),
        "gameStartUtc": start.isoformat() if start else None,
        "lockAtUtc": lock_at.isoformat() if lock_at else None,
        "scheduledLockAtUtc": lock_at.isoformat() if lock_at else None,
        "actualStagedAtUtc": staged_at.isoformat(),
        "latestScoringPullAt": source_at.isoformat() if source_at else None,
        "latestScoringPullId": source_pull.get("pull_id"),
        "source": "latest_valid_game_pull_at_or_before_own_tminus45",
        "sourceAtOrBeforeLock": bool(source_at and lock_at and source_at <= lock_at),
        "manifestVersion": VERSION,
        "manifestGameCount": len(manifest),
        "manifestGameIdentities": [game_identity(item) for item in manifest],
        "lockedGameCountAtWrite": locked_count,
        "rules": [
            "Each game locks independently 45 minutes before its own scheduled start.",
            "Only the latest valid pull at or before that game's scheduled lock is used.",
            "A missing stage is never created at or after the game start.",
            "The immutable full-slate manifest is bound to every game-level lock.",
        ],
    }


def _remove_outcomes(row: Dict[str, Any]) -> None:
    for key in ("winner", "correct", "success", "homeWon", "pickCorrect", "outcome", "finalScore"):
        row.pop(key, None)


def _prepare_row(
    module: Any,
    row: Dict[str, Any],
    slate: str,
    game: Dict[str, Any],
    source_pull: Dict[str, Any],
    staged_at: datetime,
    manifest: List[Dict[str, Any]],
    locked_count: int,
) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    lock = _per_game_lock(module, slate, game, source_pull, staged_at, manifest, locked_count)
    coverage = _manifest_coverage(manifest, locked_count)
    out.update({
        "slate_date": slate,
        "slateDateEt": slate,
        "gameIdentity": out.get("gameIdentity") or game_identity(game).replace("provider:", "", 1),
        "lockedPrediction": True,
        "officialPrediction": True,
        "officialPick": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockedAtUtc": lock.get("lockAtUtc"),
        "scheduledLockAtUtc": lock.get("scheduledLockAtUtc"),
        "actualStagedAtUtc": lock.get("actualStagedAtUtc"),
        "predictionSourcePullAt": lock.get("latestScoringPullAt"),
        "predictionSourcePullId": lock.get("latestScoringPullId"),
        "lockedAmericanOdds": out.get("lockedAmericanOdds") if out.get("lockedAmericanOdds") not in (None, "") else out.get("americanOdds"),
        "slatePredictionLock": lock,
        "lastPossiblePredictionGate": {
            **lock,
            "gateWindowMinutesBeforeStart": {
                "opensAt": module.LOCK_MINUTES,
                "closesAt": module.LOCK_MINUTES,
                "meaning": "individual_game_tminus_cutoff",
            },
            "finalWindowActive": True,
        },
        "slateCoverage": coverage,
        "lockedCardAudit": {
            "applied": True,
            "version": VERSION,
            "selectionPolicy": "latest_valid_pull_at_or_before_individual_game_tminus45",
            "lockedFlag": True,
            "lockAtUtc": lock.get("lockAtUtc"),
            "explicitSourceAtUtc": lock.get("latestScoringPullAt"),
            "actualStagedAtUtc": lock.get("actualStagedAtUtc"),
            "createdAtNotUsedAsScoringSource": True,
            "preventsLateRows": True,
            "perGameLock": True,
            "manifestGameCount": len(manifest),
            "manifestGameIdentities": [game_identity(item) for item in manifest],
        },
        "perGameLockVersion": VERSION,
        "immutablePerGameStage": True,
    })
    tags = {
        str(tag) for tag in (out.get("tags") or [])
        if str(tag) not in {"SLATE_WIDE_45_MIN_LOCK_POLICY", "SLATE_LOCKED"}
    }
    tags.update({"FINAL_LOCKED", "PER_GAME_TMINUS45_LOCKED", "COMPLETE_SLATE_MANIFEST_BOUND"})
    out["tags"] = sorted(tags)
    freeze = dict(out.get("mlFeatureFreeze") or {})
    freeze.update({
        "completeSlateCoverage": True,
        "completeSlateCoverageMeaning": "complete_slate_manifest_membership_at_game_lock",
        "perGameLockVersion": VERSION,
        "scheduledLockAtUtc": lock.get("lockAtUtc"),
        "actualStagedAtUtc": lock.get("actualStagedAtUtc"),
    })
    exclusions = [str(reason) for reason in (freeze.get("trainingExclusionReasons") or []) if str(reason) != "incomplete_slate_coverage"]
    freeze["trainingExclusionReasons"] = exclusions
    if not exclusions and freeze.get("exactVectorCreated") is not False:
        freeze["trainingEligible"] = True
    out["mlFeatureFreeze"] = freeze
    _remove_outcomes(out)
    return out


def _vector_errors(row: Dict[str, Any], game: Dict[str, Any], lock_at: datetime, source_at: datetime) -> List[str]:
    errors: List[str] = []
    vector = row.get("frozenFeatureVector") or {}
    if not isinstance(vector, dict) or not vector:
        return ["missing_exact_frozen_feature_vector"]
    try:
        import mlb_ml_clean_cohort_v1 as cohort
        import mlb_ml_feature_missingness_v1 as missingness
        import mlb_temporal_features_v1 as temporal

        if vector.get("version") != cohort.FEATURE_SNAPSHOT_VERSION:
            errors.append("wrong_frozen_feature_vector_version")
        if vector.get("fingerprintVersion") != cohort.FINGERPRINT_VERSION:
            errors.append("wrong_frozen_feature_fingerprint_version")
        if vector.get("fingerprint") != cohort.fingerprint_for_vector(vector):
            errors.append("frozen_feature_fingerprint_mismatch")
        if vector.get("temporalFeatureVersion") != temporal.VERSION:
            errors.append("wrong_temporal_feature_version")
        if vector.get("temporalFeaturesAtOrBeforeLock") is not True:
            errors.append("temporal_features_not_lock_safe")
        temporal_source = _parse_iso(vector.get("temporalSourcePullAtUtc"))
        if not temporal_source or temporal_source > source_at or temporal_source > lock_at:
            errors.append("temporal_source_after_or_missing_lock_source")
        if not temporal.provenance_is_lock_safe(
            (row.get("homeSignal") or {}).get("temporalFeatures"), source_at, lock_at
        ):
            errors.append("home_temporal_provenance_not_lock_safe")
        if not temporal.provenance_is_lock_safe(
            (row.get("awaySignal") or {}).get("temporalFeatures"), source_at, lock_at
        ):
            errors.append("away_temporal_provenance_not_lock_safe")
        if vector.get("missingnessFeatureVersion") != missingness.VERSION:
            errors.append("wrong_fundamental_missingness_version")
        if vector.get("fundamentalMasksAtOrBeforeLock") is not True:
            errors.append("fundamental_masks_not_lock_safe")
        if not missingness.provenance_is_lock_safe(
            row.get("fundamentalsSnapshot"), source_at, lock_at, _parse_iso
        ):
            errors.append("fundamentals_snapshot_provenance_not_lock_safe")
        snapshot = row.get("fundamentalsSnapshot") or {}
        if vector.get("fundamentalsSnapshotVersion") != snapshot.get("version"):
            errors.append("fundamentals_snapshot_version_mismatch")
        if _parse_iso(vector.get("fundamentalsSnapshotAsOfUtc")) != _parse_iso(snapshot.get("asOfUtc")):
            errors.append("fundamentals_snapshot_timestamp_mismatch")
    except Exception as exc:
        errors.append(f"frozen_vector_verifier_unavailable:{exc}")
    if str(vector.get("gameId") or "") != str(row.get("gameId") or ""):
        errors.append("vector_game_identity_mismatch")
    if _parse_iso(vector.get("lockAtUtc")) != lock_at:
        errors.append("vector_scheduled_lock_mismatch")
    if _parse_iso(vector.get("sourcePullAtUtc")) != source_at:
        errors.append("vector_source_pull_mismatch")
    labels = vector.get("labels") or {}
    if labels.get("homeWon") is not None or labels.get("pickCorrect") is not None:
        errors.append("pregame_vector_contains_outcome")
    if str(vector.get("predictedWinner") or "").strip().lower() != str(row.get("predictedWinner") or "").strip().lower():
        errors.append("vector_predicted_winner_mismatch")
    if vector.get("predictedSide") != row.get("predictedSide"):
        errors.append("vector_predicted_side_mismatch")
    if game_identity(row) != game_identity(game):
        errors.append("row_manifest_game_identity_mismatch")
    return errors


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _selected_price_proven(row: Dict[str, Any]) -> bool:
    side = row.get("predictedSide")
    signal = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    signal = signal if isinstance(signal, dict) else {}
    price = row.get("lockedAmericanOdds")
    if price in (None, "", 0, 0.0):
        price = row.get("americanOdds") or signal.get("americanOdds")
    book = row.get("priceBook") or signal.get("priceBook")
    source = str(row.get("priceSource") or signal.get("priceSource") or "").lower()
    return bool(price not in (None, "", 0, 0.0) and book and source in {"real_book", "locked_real_book"})


def _validate_stage(
    module: Any,
    item: Dict[str, Any],
    slate: str,
    game: Dict[str, Any],
    manifest: List[Dict[str, Any]],
    scoring: List[Dict[str, Any]],
) -> List[str]:
    errors: List[str] = []
    row = (item.get("data") or {}).get("row") or {}
    expected_manifest = [game_identity(entry) for entry in manifest]
    actual_manifest = list((item.get("data") or {}).get("manifestGameIdentities") or [])
    lock_at = _lock_at(module, game)
    start = _start(module, game)
    staged_at = _parse_iso(item.get("staged_at_utc"))
    source_at = _parse_iso(item.get("source_pull_at_utc"))
    stable_at = _cutoff_stable_at(module, game)
    source_window = item.get("source_window") or {}
    raw_bound_entries = source_window.get("pulls") or []
    bound_entries = raw_bound_entries if isinstance(raw_bound_entries, list) else []
    current_entries = _source_window_entries(module, scoring, game)
    bound_keys = {_source_window_key(entry) for entry in bound_entries if isinstance(entry, dict)}
    current_keys = {_source_window_key(entry) for entry in current_entries}
    source_window_closed_at = _parse_iso(source_window.get("closedAtUtc"))
    if item.get("record_type") != STAGE_RECORD_TYPE or item.get("immutable_staged") is not True:
        errors.append("not_immutable_per_game_stage")
    if str(item.get("slate_date") or "") != slate:
        errors.append("stage_slate_mismatch")
    if str(item.get("game_identity") or "") != game_identity(game):
        errors.append("stage_game_identity_mismatch")
    if actual_manifest != expected_manifest:
        errors.append("manifest_changed_after_game_lock")
    if not lock_at or _parse_iso(item.get("scheduled_lock_at_utc")) != lock_at:
        errors.append("scheduled_lock_mismatch")
    if not start or not staged_at or staged_at >= start:
        errors.append("late_stage_at_or_after_game_start")
    if source_window.get("version") != SOURCE_WINDOW_VERSION:
        errors.append("missing_or_wrong_bound_source_window_version")
    if not source_window_closed_at or source_window_closed_at != staged_at:
        errors.append("source_window_close_timestamp_mismatch")
    if stable_at and (not staged_at or staged_at < stable_at):
        errors.append("cutoff_source_window_not_stabilized")
    if not isinstance(raw_bound_entries, list) or not bound_entries:
        errors.append("missing_bound_source_window_pulls")
    elif len(bound_keys) != len(bound_entries):
        errors.append("duplicate_bound_source_window_pull")
    if bound_keys - current_keys:
        errors.append("bound_source_window_pull_missing_or_changed")
    if not source_at or not lock_at or source_at > lock_at:
        errors.append("source_after_or_missing_scheduled_lock")
    latest_bound = bound_entries[-1] if bound_entries and isinstance(bound_entries[-1], dict) else {}
    if source_at != _parse_iso(latest_bound.get("pulledAtUtc")):
        errors.append("source_pull_timestamp_mismatch_bound_window")
    if str(item.get("source_pull_id") or "") != str(latest_bound.get("pullId") or ""):
        errors.append("source_pull_id_mismatch_bound_window")
    if int(item.get("pull_depth") or 0) != len(bound_entries):
        errors.append("pull_depth_mismatch")
    if item.get("stage_fingerprint") != _stage_fingerprint(item):
        errors.append("stage_fingerprint_mismatch")
    if lock_at and source_at:
        errors.extend(_vector_errors(row, game, lock_at, source_at))
    if not _selected_price_proven(row):
        errors.append("selected_side_real_book_price_missing")
    for key in ("winner", "correct", "success", "homeWon", "pickCorrect", "outcome", "finalScore"):
        if key in row:
            errors.append(f"pregame_stage_contains_{key}")
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as exact_contract

        errors.extend(exact_contract.validate_exact_locked_row(row))
    except Exception as exc:
        errors.append(f"exact_locked_row_validator_unavailable:{exc}")
    return sorted(set(errors))


def _generate_stage(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    manifest: List[Dict[str, Any]],
    scoring: List[Dict[str, Any]],
    locked_count: int,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    start = _start(module, game)
    now = module._now_utc().astimezone(timezone.utc)
    if not start or now >= start:
        return None, ["late_stage_blocked_game_already_started"]
    stable_at = _cutoff_stable_at(module, game)
    if not stable_at or now < stable_at:
        return None, ["cutoff_source_window_still_stabilizing"]
    with _pull_scope(module.history, slate, scoring):
        result = module.mlb_game_winner_engine.predict_all(slate, store=False, limit=500)
    candidates = [row for row in (result.get("predictions") or []) if game_identity(row) == game_identity(game)]
    if len(candidates) != 1:
        return None, [f"scoped_prediction_count_{len(candidates)}"]
    source = scoring[-1]
    row = _prepare_row(module, candidates[0], slate, game, source, now, manifest, locked_count)
    source_window = {
        "version": SOURCE_WINDOW_VERSION,
        "closedAtUtc": now.isoformat(),
        "scheduledCutoffAtUtc": (_lock_at(module, game) or now).isoformat(),
        "stabilizationSeconds": CUTOFF_STABILIZATION_SECONDS,
        "pulls": _source_window_entries(module, scoring, game),
    }
    item = module.history.ddb_safe({
        **_stage_key(module, slate, game),
        "record_type": STAGE_RECORD_TYPE,
        "sport": "mlb",
        "slate_date": slate,
        "model_version": VERSION,
        "lock_policy": LOCK_POLICY,
        "immutable_staged": True,
        "write_once": True,
        "game_identity": game_identity(game),
        "game_id": row.get("gameId"),
        "commence_time": row.get("commenceTime"),
        "scheduled_lock_at_utc": (_lock_at(module, game) or now).isoformat(),
        "staged_at_utc": now.isoformat(),
        "source_pull_at_utc": (_pull_at(module, source) or now).isoformat(),
        "source_pull_id": source.get("pull_id"),
        "pull_depth": len(scoring),
        "source_window": source_window,
        "manifest_game_count": len(manifest),
        "data": {
            "row": row,
            "manifestGameIdentities": [game_identity(entry) for entry in manifest],
        },
        "created_at": now.isoformat(),
    })
    item["stage_fingerprint"] = _stage_fingerprint(item)
    errors = _validate_stage(module, item, slate, game, manifest, scoring)
    return (item if not errors else None), errors


def _put_stage(module: Any, item: Dict[str, Any], slate: str, game: Dict[str, Any]) -> Dict[str, Any]:
    try:
        module.TABLE.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return item
    except Exception as exc:
        if not _conditional_collision(exc):
            raise
        existing = _get_stage(module, slate, game)
        if not existing:
            raise RuntimeError("PER_GAME_STAGE_CONDITIONAL_COLLISION_WITHOUT_READBACK") from exc
        return existing


def _canonical_store(module: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    response = module.mlb_game_winner_engine._store_prediction(copy.deepcopy(row))
    required = bool(
        isinstance(response, dict)
        and response.get("ok") is True
        and response.get("storageClass") == "LOCKED_IMMUTABLE"
        and response.get("writeOnce") is True
        and response.get("exactVectorVerified") is True
    )
    if not required:
        raise RuntimeError(f"CANONICAL_PER_GAME_WRITE_NOT_PROVEN:{response}")
    return response


def _canonical_readback(module: Any, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    identity = str(row.get("gameIdentity") or row.get("gameId") or row.get("game_id") or "unknown")
    commence = str(row.get("commenceTime") or row.get("commence_time") or "unknown")
    slate = str(row.get("slate_date") or row.get("slateDateEt") or "unknown")
    response = module.history.PULLS.get_item(
        Key={
            "PK": f"GAME_WINNERS#mlb#{slate}",
            "SK": f"LOCKED#GAME#{commence}#{identity}",
        },
        ConsistentRead=True,
    )
    item = response.get("Item")
    stored = (item or {}).get("data") if isinstance((item or {}).get("data"), dict) else None
    if not stored:
        return None
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as exact_contract

        if exact_contract.validate_exact_locked_row(stored):
            return None
    except Exception:
        return None
    incoming_vector = row.get("frozenFeatureVector") or {}
    stored_vector = stored.get("frozenFeatureVector") or {}
    if (
        incoming_vector.get("fingerprint") != stored_vector.get("fingerprint")
        or row.get("predictedWinner") != stored.get("predictedWinner")
        or row.get("predictedSide") != stored.get("predictedSide")
    ):
        return None
    return {
        "ok": True,
        "pk": (item or {}).get("PK"),
        "sk": (item or {}).get("SK"),
        "storageClass": "LOCKED_IMMUTABLE",
        "writeOnce": True,
        "exactVectorVerified": True,
        "immutableExisting": True,
    }


def _late_backfill_count(module: Any, stage: Optional[Dict[str, Any]], scoring: List[Dict[str, Any]], game: Dict[str, Any]) -> int:
    if not stage:
        return 0
    source_window = stage.get("source_window") or {}
    bound_entries = source_window.get("pulls") or []
    if not isinstance(bound_entries, list):
        return 0
    bound_keys = {
        _source_window_key(entry)
        for entry in bound_entries
        if isinstance(entry, dict)
    }
    current_keys = {
        _source_window_key(entry)
        for entry in _source_window_entries(module, scoring, game)
    }
    return len(current_keys - bound_keys)


def _progress(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    now: datetime,
    *,
    ensure_canonical: bool,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    valid_stages: Dict[str, Dict[str, Any]] = {}
    canonical: Dict[str, Dict[str, Any]] = {}
    for game in manifest:
        identity = game_identity(game)
        start = _start(module, game)
        lock_at = _lock_at(module, game)
        scoring = _scoring_pulls(module, pulls, game)
        stage = _get_stage(module, slate, game)
        errors = _validate_stage(module, stage, slate, game, manifest, scoring) if stage else []
        late_backfill_count = _late_backfill_count(module, stage, scoring, game)
        state = "LOCKED_STAGED" if stage and not errors else "PENDING"
        if errors:
            state = "INVALID_STAGE_BLOCKED"
        elif not stage and start and now >= start:
            state = "MISSED_NOT_BACKFILLED"
        elif not stage and lock_at and now >= lock_at and now < (_cutoff_stable_at(module, game) or lock_at):
            state = "WAITING_FOR_CUTOFF_STABILIZATION"
        elif not stage and lock_at and now >= lock_at:
            state = "DUE_NOT_STAGED"
        if stage and not errors:
            valid_stages[identity] = stage
            try:
                stage_row = (stage.get("data") or {}).get("row") or {}
                stored = _canonical_store(module, stage_row) if ensure_canonical else _canonical_readback(module, stage_row)
                if not stored:
                    raise RuntimeError("canonical_immutable_game_row_missing_or_invalid")
                canonical[identity] = stored
                state = "LOCKED_CANONICAL"
            except Exception as exc:
                errors = [f"canonical_write_failed:{exc}"]
                state = "STAGED_CANONICAL_WRITE_BLOCKED"
        rows.append({
            "gameIdentity": identity,
            "gameId": game.get("game_id") or game.get("gameId") or game.get("id"),
            "commenceTime": (start.isoformat() if start else None),
            "scheduledLockAtUtc": (lock_at.isoformat() if lock_at else None),
            "state": state,
            "sourcePullAtUtc": stage.get("source_pull_at_utc") if stage else None,
            "actualStagedAtUtc": stage.get("staged_at_utc") if stage else None,
            "pullDepthAtCutoff": len(scoring),
            "sourceWindowStabilizationSeconds": CUTOFF_STABILIZATION_SECONDS,
            "sourceWindowStableAtUtc": (_cutoff_stable_at(module, game) or lock_at).isoformat() if lock_at else None,
            "lateBackfillDetected": late_backfill_count > 0,
            "lateBackfillPullCount": late_backfill_count,
            "errors": errors,
        })
    return {
        "games": rows,
        "stages": valid_stages,
        "canonical": canonical,
        "stagedCount": len(valid_stages),
        "canonicalCount": len(canonical),
        "pendingCount": len([row for row in rows if row["state"] in {"PENDING", "WAITING_FOR_CUTOFF_STABILIZATION"}]),
        "stabilizingCount": len([row for row in rows if row["state"] == "WAITING_FOR_CUTOFF_STABILIZATION"]),
        "dueMissingCount": len([row for row in rows if row["state"] in {"DUE_NOT_STAGED", "STAGED_CANONICAL_WRITE_BLOCKED", "INVALID_STAGE_BLOCKED"}]),
        "missedCount": len([row for row in rows if row["state"] == "MISSED_NOT_BACKFILLED"]),
    }


def _daily_item(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    progress: Dict[str, Any],
) -> Dict[str, Any]:
    rows = [copy.deepcopy(((progress["stages"][game_identity(game)].get("data") or {}).get("row") or {})) for game in manifest]
    ranked = sorted(rows, key=lambda row: (float(row.get("score") or 0), float(row.get("winProbability") or 0)), reverse=True)
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
        coverage = dict(row.get("slateCoverage") or {})
        coverage.update({"lockCoverageComplete": True, "lockedGameCountAtWrite": len(manifest), "pendingGameCountAtWrite": 0, "operationalStatus": "COMPLETE_MANIFEST_ALL_LOCKED"})
        row["slateCoverage"] = coverage
    picks = module._sort_picks([module._compact_pick(row) for row in ranked])
    starts = [_start(module, game) for game in manifest]
    starts = [value for value in starts if value]
    cutoffs = [_lock_at(module, game) for game in manifest]
    cutoffs = [value for value in cutoffs if value]
    stages = list(progress["stages"].values())
    sources = [_parse_iso(item.get("source_pull_at_utc")) for item in stages]
    sources = [value for value in sources if value]
    now = module._now_utc().astimezone(timezone.utc)
    coverage = _manifest_coverage(manifest, len(manifest))
    return module.history.ddb_safe({
        "PK": module._lock_pk(slate),
        "SK": module._lock_sk(),
        "record_type": "mlb_daily_locked_individual_game_moneyline_picks",
        "sport": "mlb",
        "slate_date": slate,
        "model_version": VERSION,
        "single_game_model": module.mlb_game_winner_engine.MODEL_VERSION,
        "locked": True,
        "locked_at": now.isoformat(),
        "locked_at_et": now.astimezone(module.EASTERN).isoformat(),
        "first_game_start_et": min(starts).astimezone(module.EASTERN).isoformat(),
        "first_game_start_utc": min(starts).isoformat(),
        "lock_time_et": min(cutoffs).astimezone(module.EASTERN).isoformat(),
        "last_game_lock_time_et": max(cutoffs).astimezone(module.EASTERN).isoformat(),
        "lock_minutes_before_first_game": module.LOCK_MINUTES,
        "lock_minutes_before_each_game": module.LOCK_MINUTES,
        "lock_policy": LOCK_POLICY,
        "per_game_lock": True,
        "slate_wide_lock": False,
        "source": "canonical_write_once_game_rows_from_each_game_own_tminus45",
        "latest_pull_at": max(sources).isoformat(),
        "latest_pull_id": max(stages, key=lambda item: str(item.get("source_pull_at_utc") or "")).get("source_pull_id"),
        "pull_count": len(pulls),
        "game_count": len(manifest),
        "manifest_version": VERSION,
        "manifest_game_count": len(manifest),
        "prediction_count": len(picks),
        "promoted_count": len([pick for pick in picks if pick.get("promoted")]),
        "all_games_predicted": True,
        "coverage_complete": True,
        "coverage_status": "COMPLETE",
        "doubleheader_safe_identity": True,
        "canonical_immutable_game_row_count": progress.get("canonicalCount"),
        "data": {
            "picks": picks,
            "manifestGameIdentities": [game_identity(game) for game in manifest],
            "slateCoverage": coverage,
            "perGameLockProof": [
                {
                    "gameIdentity": item.get("game_identity"),
                    "gameId": item.get("game_id"),
                    "commenceTime": item.get("commence_time"),
                    "scheduledLockAtUtc": item.get("scheduled_lock_at_utc"),
                    "actualStagedAtUtc": item.get("staged_at_utc"),
                    "sourcePullAtUtc": item.get("source_pull_at_utc"),
                    "sourcePullId": item.get("source_pull_id"),
                    "sourceWindow": item.get("source_window") or {},
                    "stageFingerprint": item.get("stage_fingerprint"),
                    "writeOnce": True,
                    "canonicalImmutableGameRow": True,
                }
                for item in stages
            ],
            "predictionSummary": {
                "allGamesPredicted": True,
                "perGameLock": True,
                "canonicalImmutableGameRowCount": progress.get("canonicalCount"),
            },
        },
        "created_at": now.isoformat(),
    })


def apply(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_DAILY_PER_GAME_LOCK_V1", False):
        return module

    original_lock_response = module._lock_response

    def lock_response(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        response = original_lock_response(item)
        if not response or not item:
            return response
        data = item.get("data") or {}
        response.update({
            "lockPolicy": item.get("lock_policy") or LOCK_POLICY,
            "perGameLock": item.get("per_game_lock") is True,
            "slateWideLock": item.get("slate_wide_lock") is True,
            "lockMinutesBeforeEachGame": item.get("lock_minutes_before_each_game"),
            "lastGameLockTimeEt": item.get("last_game_lock_time_et"),
            "canonicalImmutableGameRowCount": item.get("canonical_immutable_game_row_count"),
            "perGameLockProof": data.get("perGameLockProof") or [],
            "writeOnce": True,
        })
        return response

    module._lock_response = lock_response

    def status_payload(slate_date: Optional[str] = None) -> Dict[str, Any]:
        slate = slate_date or module._today_et()
        base = {
            "ok": True,
            "sport": "mlb",
            "modelVersion": VERSION,
            "singleGameModelVersion": module.mlb_game_winner_engine.MODEL_VERSION,
            "slateDateEt": slate,
            "lockPolicy": LOCK_POLICY,
            "perGameLock": True,
            "slateWideLock": False,
            "lockMinutesBeforeEachGame": module.LOCK_MINUTES,
            "minPullsPerGameForLock": module.MIN_PULLS_PER_GAME_FOR_LOCK,
            "maxSourceAgeAtGameLockMinutes": module.MAX_LATEST_PULL_AGE_MINUTES,
        }
        if module.TABLE is None:
            return {**base, "ok": False, "error": "SNAPSHOTS_TABLE not configured", "locked": False, "lock": None}
        existing = module._lock_response(module._get_lock_item(slate))
        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        manifest = module._latest_games_for_date(slate, pulls)
        now = module._now_utc().astimezone(timezone.utc)
        progress = _progress(module, slate, pulls, manifest, now, ensure_canonical=False) if manifest else {"games": [], "stagedCount": 0, "canonicalCount": 0, "pendingCount": 0, "stabilizingCount": 0, "dueMissingCount": 0, "missedCount": 0}
        cutoffs = sorted([value for value in (_lock_at(module, game) for game in manifest) if value])
        future = [value for value in cutoffs if value > now]
        return {
            **base,
            "locked": bool(existing),
            "lock": existing,
            "pullCount": len(pulls),
            "gameCount": len(manifest),
            "latestPullAt": pulls[-1].get("pulled_at") if pulls else None,
            "stagedGameCount": progress.get("stagedCount"),
            "canonicalImmutableGameRowCount": progress.get("canonicalCount"),
            "pendingGameCount": progress.get("pendingCount"),
            "stabilizingGameCount": progress.get("stabilizingCount"),
            "dueMissingGameCount": progress.get("dueMissingCount"),
            "missedGameCount": progress.get("missedCount"),
            "perGameStatus": progress.get("games") or [],
            "firstGameStartEt": min([value for value in (_start(module, game) for game in manifest) if value]).astimezone(module.EASTERN).isoformat() if any(_start(module, game) for game in manifest) else None,
            "lockTimeEt": min(cutoffs).astimezone(module.EASTERN).isoformat() if cutoffs else None,
            "lastGameLockTimeEt": max(cutoffs).astimezone(module.EASTERN).isoformat() if cutoffs else None,
            "nextGameLockAtUtc": future[0].isoformat() if future else None,
            "nowEt": now.astimezone(module.EASTERN).isoformat(),
            "lockDue": bool(not existing and (progress.get("dueMissingCount") or progress.get("missedCount"))),
            "minutesUntilLock": round((future[0] - now).total_seconds() / 60.0, 2) if future else 0,
        }

    def run_lock(slate_date: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        slate = slate_date or module._today_et()
        if module.TABLE is None:
            return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "error": "SNAPSHOTS_TABLE not configured"}
        existing = module._lock_response(module._get_lock_item(slate))
        if existing:
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": True, "lock": existing}
        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        if not pulls:
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "NO_STORED_ODDS_API_PULL_HISTORY"}
        manifest = module._latest_games_for_date(slate, pulls)
        if not manifest:
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "NO_MLB_GAMES_FOR_SLATE_DATE", "pullCount": len(pulls)}

        now = module._now_utc().astimezone(timezone.utc)
        pre = _progress(module, slate, pulls, manifest, now, ensure_canonical=True)
        failures: List[Dict[str, Any]] = []
        for game_status in pre.get("games") or []:
            if game_status.get("state") != "DUE_NOT_STAGED":
                continue
            identity = game_status["gameIdentity"]
            game = next(entry for entry in manifest if game_identity(entry) == identity)
            scoring = _scoring_pulls(module, pulls, game)
            stable_item: Optional[Dict[str, Any]] = None
            failed = False
            for _attempt in range(3):
                lock_at = _lock_at(module, game)
                source_at = _pull_at(module, scoring[-1]) if scoring else None
                source_age = ((lock_at - source_at).total_seconds() / 60.0) if lock_at and source_at else None
                if len(scoring) < module.MIN_PULLS_PER_GAME_FOR_LOCK:
                    failures.append({"gameIdentity": identity, "reason": "INSUFFICIENT_PULL_DEPTH_NOT_STAGED", "pullDepth": len(scoring)})
                    failed = True
                    break
                if source_age is None or source_age > module.MAX_LATEST_PULL_AGE_MINUTES:
                    failures.append({"gameIdentity": identity, "reason": "STALE_OR_MISSING_CUTOFF_PULL_NOT_STAGED", "sourceAgeAtLockMinutes": source_age})
                    failed = True
                    break

                item, errors = _generate_stage(module, slate, game, manifest, scoring, pre.get("stagedCount", 0) + 1)
                if errors or not item:
                    failures.append({"gameIdentity": identity, "reason": "PER_GAME_STAGE_VALIDATION_FAILED", "errors": errors})
                    failed = True
                    break

                # Close the source window against a consistent re-read immediately
                # before the immutable stage write. If an in-flight pull appeared,
                # regenerate from that newer at-or-before-cutoff window.
                refreshed_pulls = sorted(
                    module._pulls_for_date(slate),
                    key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc),
                )
                refreshed_manifest = module._latest_games_for_date(slate, refreshed_pulls)
                if [game_identity(entry) for entry in refreshed_manifest] != [game_identity(entry) for entry in manifest]:
                    failures.append({"gameIdentity": identity, "reason": "MANIFEST_CHANGED_DURING_SOURCE_WINDOW_CLOSE_NOT_STAGED"})
                    failed = True
                    break
                refreshed_game = next(
                    (entry for entry in refreshed_manifest if game_identity(entry) == identity),
                    None,
                )
                if refreshed_game is None:
                    failures.append({"gameIdentity": identity, "reason": "GAME_MISSING_DURING_SOURCE_WINDOW_CLOSE_NOT_STAGED"})
                    failed = True
                    break
                refreshed_scoring = _scoring_pulls(module, refreshed_pulls, refreshed_game)
                pulls = refreshed_pulls
                manifest = refreshed_manifest
                game = refreshed_game
                if _source_window_entries(module, scoring, game) == _source_window_entries(module, refreshed_scoring, game):
                    scoring = refreshed_scoring
                    stable_item = item
                    break
                scoring = refreshed_scoring

            if failed:
                continue
            if stable_item is None:
                failures.append({"gameIdentity": identity, "reason": "SOURCE_WINDOW_CHANGED_REPEATEDLY_NOT_STAGED"})
                continue

            stored = _put_stage(module, stable_item, slate, game)
            stored_errors = _validate_stage(module, stored, slate, game, manifest, scoring)
            if stored_errors:
                failures.append({"gameIdentity": identity, "reason": "PER_GAME_STAGE_READBACK_INVALID", "errors": stored_errors})
                continue
            try:
                _canonical_store(module, (stored.get("data") or {}).get("row") or {})
            except Exception as exc:
                failures.append({"gameIdentity": identity, "reason": "CANONICAL_IMMUTABLE_GAME_WRITE_FAILED", "errors": [str(exc)]})

        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        manifest = module._latest_games_for_date(slate, pulls)
        progress = _progress(module, slate, pulls, manifest, module._now_utc().astimezone(timezone.utc), ensure_canonical=True)
        if progress.get("missedCount"):
            return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "MISSED_PER_GAME_LOCK_NOT_BACKFILLED", "failClosed": True, "forceIgnoredForSafety": bool(force), "failures": failures, "perGameLockProgress": progress}
        if failures or progress.get("dueMissingCount"):
            return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL", "failClosed": True, "forceIgnoredForSafety": bool(force), "failures": failures, "perGameLockProgress": progress}
        if progress.get("stagedCount") < len(manifest) or progress.get("canonicalCount") < len(manifest):
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER", "forceIgnoredForSafety": bool(force), "perGameLockProgress": progress}

        item = _daily_item(module, slate, pulls, manifest, progress)
        try:
            module.TABLE.put_item(Item=item, ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
            locked = module._lock_response(item)
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": False, "lock": locked, "perGameLockProgress": progress}
        except Exception as exc:
            if _conditional_collision(exc):
                existing = module._lock_response(module._get_lock_item(slate))
                if existing:
                    return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": True, "lock": existing}
            raise

    module.MODEL_VERSION = VERSION
    module.LOCK_POLICY = LOCK_POLICY
    module._status_payload = status_payload
    module.run_lock = run_lock
    module.MLB_DAILY_PER_GAME_LOCK_VERSION = VERSION
    module._INQSI_MLB_DAILY_PER_GAME_LOCK_V1 = True
    return module
