from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

import inqsi_pull_history as history_contract
from mlb_slate_coverage_patch import game_identity

PAYLOAD_FINGERPRINT_VERSION = history_contract.CANONICAL_PAYLOAD_FINGERPRINT_VERSION


VERSION = "INQSI-MLB-DAILY-LOCK-v4-per-game-tminus45-staged-write-once"
LOCK_POLICY = "each_mlb_game_minus_45_minutes"
REQUIRED_LOCK_MINUTES = 45
STAGE_RECORD_TYPE = "mlb_staged_per_game_tminus45_lock"
CUTOFF_STABILIZATION_SECONDS = 120
SOURCE_WINDOW_VERSION = "mlb_per_game_cutoff_source_window_v1"
ATTEMPT_DIAGNOSTICS_VERSION = "MLB-PER-GAME-LOCK-DIAGNOSTICS-v1-append-only"
ATTEMPT_RECORD_TYPE = "mlb_per_game_lock_attempt_diagnostic"
ATTEMPT_OUTCOME_RECORD_TYPE = "mlb_per_game_lock_attempt_outcome_diagnostic"
PROMOTION_POLICY_VERSION = "MLB-LAST-PRELOCK-PROMOTION-v1-at-cutoff-no-rescore"
PREGAME_SNAPSHOT_RECORD_TYPE = "mlb_immutable_prelock_prediction_snapshot"
LIVE_PREDICTION_RECORD_TYPE = "mlb_single_game_moneyline_prediction"
PREGAME_SNAPSHOT_VERSION = "MLB-PREGAME-PREDICTION-SNAPSHOT-v2-post-write-ack"
PREGAME_PERSISTENCE_PROOF_TYPE = "DDB_LIVE_PREDICTION_PUT_SUCCESS_ACK-v1"

_DIAGNOSTIC_STATES = {
    "WAITING_FOR_CUTOFF_STABILIZATION",
    "DUE_NOT_STAGED",
    "INVALID_STAGE_BLOCKED",
    "STAGED_CANONICAL_WRITE_BLOCKED",
    "MISSED_NOT_BACKFILLED",
}

_SCOPED_PULLS: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "inqsi_mlb_scoped_per_game_lock_pulls",
    default=None,
)


def _supported_payload_fingerprint_version(value: Any) -> bool:
    # Missing means a legacy snapshot written before the algorithm identifier
    # was added.  It is still required to match the canonical persisted-row
    # hash; unknown named algorithms always fail closed.
    return value in (None, "", PAYLOAD_FINGERPRINT_VERSION)


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
        "recordType": item.get("record_type"),
        "modelVersion": item.get("model_version"),
        "lockPolicy": item.get("lock_policy"),
        "immutableStaged": item.get("immutable_staged"),
        "writeOnce": item.get("write_once"),
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
        "promotionPolicyVersion": item.get("promotion_policy_version"),
        "candidateProof": item.get("candidate_proof") or {},
        "providerManifestAuthority": item.get("provider_manifest_authority") or {},
        "manifestGameCount": item.get("manifest_game_count"),
        "manifestGameIdentities": (item.get("data") or {}).get("manifestGameIdentities") or [],
    }


def _stage_fingerprint(item: Dict[str, Any]) -> str:
    payload = json.dumps(_plain(_fingerprint_material(item)), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _consistent_item(table: Any, key: Dict[str, str]) -> Optional[Dict[str, Any]]:
    try:
        item = table.get_item(Key=key, ConsistentRead=True).get("Item")
    except Exception:
        return None
    return item if isinstance(item, dict) else None


def _provider_manifest_authority_errors(table: Any, item: Dict[str, Any]) -> List[str]:
    """Verify the stage's full-slate pointer against its write-once record."""
    import inqsi_pull_history as history_contract

    errors: List[str] = []
    authority = item.get("provider_manifest_authority") or {}
    if not isinstance(authority, dict) or not authority:
        return ["provider_manifest_authority_missing"]
    required_true = ("immutable", "writeOnce", "fullProviderSchedule", "consistentReadVerified")
    for key in required_true:
        if authority.get(key) is not True:
            errors.append(f"provider_manifest_authority_{key}_missing")
    if authority.get("version") != history_contract.PROVIDER_MANIFEST_VERSION:
        errors.append("provider_manifest_authority_version_mismatch")
    if authority.get("recordType") != history_contract.PROVIDER_MANIFEST_RECORD_TYPE:
        errors.append("provider_manifest_authority_record_type_mismatch")
    pk = str(authority.get("pk") or "")
    sk = str(authority.get("sk") or "")
    if not pk or not sk:
        return sorted(set([*errors, "provider_manifest_authority_key_missing"]))
    stored = _consistent_item(table, {"PK": pk, "SK": sk})
    if not stored:
        return sorted(set([*errors, "immutable_provider_manifest_readback_missing"]))
    manifest = stored.get("data") or {}
    if not isinstance(manifest, dict) or not manifest:
        return sorted(set([*errors, "immutable_provider_manifest_payload_missing"]))
    fingerprint = history_contract.provider_manifest_fingerprint(manifest)
    if (
        stored.get("record_type") != history_contract.PROVIDER_MANIFEST_RECORD_TYPE
        or stored.get("write_once") is not True
        or stored.get("PK") != pk
        or stored.get("SK") != sk
        or str(stored.get("manifest_fingerprint") or "") != fingerprint
        or str(manifest.get("fingerprint") or "") != fingerprint
        or str(authority.get("fingerprint") or "") != fingerprint
    ):
        errors.append("immutable_provider_manifest_readback_mismatch")
    if (
        str(manifest.get("slateDate") or "") != str(item.get("slate_date") or "")
        or str(authority.get("slateDate") or "") != str(item.get("slate_date") or "")
    ):
        errors.append("provider_manifest_stage_slate_mismatch")
    games = list(manifest.get("games") or [])
    raw_identities = [history_contract.provider_game_identity("mlb", game) for game in games]
    canonical_identities = [game_identity(game) for game in games]
    declared_canonical = list((item.get("data") or {}).get("manifestGameIdentities") or [])
    try:
        authority_count = int(authority.get("gameCount"))
        stage_count = int(item.get("manifest_game_count"))
    except Exception:
        authority_count = stage_count = -1
    if authority_count != len(games) or stage_count != len(games):
        errors.append("provider_manifest_stage_game_count_mismatch")
    if list(authority.get("gameIdentities") or []) != raw_identities:
        errors.append("provider_manifest_authority_identity_mismatch")
    if list(authority.get("canonicalGameIdentities") or []) != canonical_identities:
        errors.append("provider_manifest_authority_canonical_identity_mismatch")
    if declared_canonical != canonical_identities:
        errors.append("provider_manifest_stage_membership_mismatch")
    if len(set(canonical_identities)) != len(canonical_identities):
        errors.append("provider_manifest_stage_duplicate_identity")

    stage_identity = str(item.get("game_identity") or "")
    try:
        index = canonical_identities.index(stage_identity)
        manifest_game = games[index]
    except ValueError:
        manifest_game = None
        errors.append("stage_game_missing_from_provider_manifest")
    row = ((item.get("data") or {}).get("row") or {})
    if manifest_game:
        if _parse_iso(manifest_game.get("commence_time")) != _parse_iso(item.get("commence_time")):
            errors.append("provider_manifest_stage_commence_mismatch")
        if _norm(manifest_game.get("home_team")) != _norm(row.get("homeTeam") or row.get("home_team")):
            errors.append("provider_manifest_stage_home_team_mismatch")
        if _norm(manifest_game.get("away_team")) != _norm(row.get("awayTeam") or row.get("away_team")):
            errors.append("provider_manifest_stage_away_team_mismatch")
    return sorted(set(errors))


def _candidate_snapshot_authority_errors(table: Any, item: Dict[str, Any]) -> List[str]:
    """Read back the exact pre-lock prediction selected by the stage."""
    errors: List[str] = []
    proof = item.get("candidate_proof") or {}
    pk = str(proof.get("pk") or "")
    sk = str(proof.get("sk") or "")
    if not pk or not sk:
        return ["candidate_snapshot_key_missing"]
    candidate = _consistent_item(table, {"PK": pk, "SK": sk})
    if not candidate:
        return ["candidate_snapshot_consistent_readback_missing"]
    candidate_row = candidate.get("data") or {}
    if not isinstance(candidate_row, dict) or not candidate_row:
        return ["candidate_snapshot_row_missing"]
    stage_row = ((item.get("data") or {}).get("row") or {})
    expected_pk = f"GAME_WINNERS#mlb#{item.get('slate_date')}"
    raw_identity = _raw_game_identity(stage_row)
    if candidate.get("PK") != expected_pk or candidate.get("SK") != sk:
        errors.append("candidate_snapshot_key_mismatch")
    if not sk.startswith(f"PREGAME#GAME#{raw_identity}#"):
        errors.append("candidate_snapshot_sk_identity_mismatch")
    if (
        candidate.get("record_type") != PREGAME_SNAPSHOT_RECORD_TYPE
        or proof.get("recordType") != PREGAME_SNAPSHOT_RECORD_TYPE
        or candidate.get("immutable_pregame") is not True
        or candidate.get("write_once") is not True
    ):
        errors.append("candidate_snapshot_not_immutable_write_once")
    if (
        str(candidate.get("slate_date") or "") != str(item.get("slate_date") or "")
        or str(candidate.get("game_identity") or "") != raw_identity
        or game_identity(candidate_row) != str(item.get("game_identity") or "")
    ):
        errors.append("candidate_snapshot_game_binding_mismatch")
    timestamp_fields = (
        ("prediction_created_at_utc", "predictionCreatedAtUtc"),
        ("prediction_persisted_at_utc", "predictionPersistedAtUtc"),
        ("prediction_source_pull_at_utc", "predictionSourcePullAtUtc"),
    )
    for stored_key, proof_key in timestamp_fields:
        if _parse_iso(candidate.get(stored_key)) != _parse_iso(proof.get(proof_key)):
            errors.append(f"candidate_snapshot_{stored_key}_mismatch")
    if str(candidate.get("prediction_source_pull_id") or "") != str(proof.get("predictionSourcePullId") or ""):
        errors.append("candidate_snapshot_source_pull_id_mismatch")
    expected_live_pk = f"GAME_WINNERS#mlb#{item.get('slate_date')}"
    expected_live_sk = (
        f"GAME#{candidate_row.get('commenceTime') or 'unknown'}#"
        f"{candidate.get('game_identity') or candidate_row.get('gameIdentity') or candidate_row.get('gameId')}"
    )
    if (
        candidate.get("snapshot_version") != PREGAME_SNAPSHOT_VERSION
        or proof.get("snapshotVersion") != PREGAME_SNAPSHOT_VERSION
    ):
        errors.append("candidate_snapshot_version_mismatch")
    candidate_fingerprint_version = candidate.get("prediction_payload_fingerprint_version")
    proof_fingerprint_version = proof.get("predictionPayloadFingerprintVersion")
    fingerprint_contract_ready = bool(
        _supported_payload_fingerprint_version(candidate_fingerprint_version)
        and _supported_payload_fingerprint_version(proof_fingerprint_version)
        and (candidate_fingerprint_version or None)
        == (proof_fingerprint_version or None)
    )
    if not fingerprint_contract_ready:
        errors.append("candidate_snapshot_payload_fingerprint_version_mismatch")
    if (
        candidate.get("prediction_persistence_proof_type") != PREGAME_PERSISTENCE_PROOF_TYPE
        or proof.get("persistenceProofType") != PREGAME_PERSISTENCE_PROOF_TYPE
    ):
        errors.append("candidate_snapshot_post_write_ack_missing")
    if (
        candidate.get("prediction_persistence_write_pk") != expected_live_pk
        or candidate.get("prediction_persistence_write_sk") != expected_live_sk
        or proof.get("persistenceWritePk") != expected_live_pk
        or proof.get("persistenceWriteSk") != expected_live_sk
    ):
        errors.append("candidate_snapshot_live_write_binding_mismatch")
    fingerprint_version = candidate_fingerprint_version or None
    if fingerprint_contract_ready:
        row_fingerprint = _payload_fingerprint(candidate_row, fingerprint_version)
        if row_fingerprint != str(proof.get("candidateRowFingerprint") or ""):
            errors.append("candidate_snapshot_row_fingerprint_mismatch")
        if (
            str(candidate.get("prediction_payload_fingerprint") or "")
            != row_fingerprint
            or str(proof.get("predictionPayloadFingerprint") or "")
            != row_fingerprint
        ):
            errors.append("candidate_snapshot_persisted_payload_fingerprint_mismatch")
        if _payload_fingerprint(candidate, fingerprint_version) != str(
            proof.get("candidateSnapshotFingerprint") or ""
        ):
            errors.append("candidate_snapshot_item_fingerprint_mismatch")
        if _payload_fingerprint(
            _selection_material(candidate_row), fingerprint_version
        ) != str(proof.get("candidateSelectionFingerprint") or ""):
            errors.append("candidate_snapshot_selection_fingerprint_mismatch")
    if (candidate_row.get("frozenFeatureVector") or {}).get("fingerprint") != proof.get("candidateVectorFingerprint"):
        errors.append("candidate_snapshot_vector_fingerprint_mismatch")
    if stage_row.get("lastPrelockSelectionFingerprint") != proof.get("candidateSelectionFingerprint"):
        errors.append("stage_selection_not_candidate_snapshot_selection")
    for key in ("winner", "correct", "success", "homeWon", "pickCorrect", "outcome", "finalScore"):
        if key in candidate_row:
            errors.append(f"candidate_snapshot_contains_{key}")
    labels = (candidate_row.get("frozenFeatureVector") or {}).get("labels") or {}
    if labels.get("homeWon") is not None or labels.get("pickCorrect") is not None:
        errors.append("candidate_snapshot_vector_contains_outcome")
    return sorted(set(errors))


def _source_window_authority_errors(table: Any, item: Dict[str, Any]) -> List[str]:
    """Verify every bound pull entry against the persisted pull history."""
    errors: List[str] = []
    source_window = item.get("source_window") or {}
    entries = source_window.get("pulls") or []
    cutoff = _parse_iso(item.get("scheduled_lock_at_utc"))
    if not isinstance(entries, list) or not entries:
        return ["bound_source_window_missing"]
    timestamps: List[datetime] = []
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append("bound_source_window_entry_invalid")
            continue
        pull_id = str(entry.get("pullId") or "")
        pulled_at = _parse_iso(entry.get("pulledAtUtc"))
        fingerprint = str(entry.get("gameSnapshotFingerprint") or "")
        key_tuple = (pull_id, str(entry.get("pulledAtUtc") or ""), fingerprint)
        if not pull_id or not pulled_at or not fingerprint or key_tuple in seen:
            errors.append("bound_source_window_entry_incomplete_or_duplicate")
            continue
        seen.add(key_tuple)
        timestamps.append(pulled_at)
        if not cutoff or pulled_at > cutoff:
            errors.append("bound_source_window_pull_after_cutoff")
        pull_item = _consistent_item(table, {
            "PK": f"PULLS#mlb#{item.get('slate_date')}",
            "SK": f"PULL#{entry.get('pulledAtUtc')}#{pull_id}",
        })
        if not pull_item:
            errors.append(f"bound_source_window_pull_readback_missing:{index}")
            continue
        pull = pull_item.get("data") or {}
        if (
            pull_item.get("record_type") != "pull_run"
            or str(pull.get("pull_id") or "") != pull_id
            or _parse_iso(pull.get("pulled_at")) != pulled_at
            or str(pull.get("slate_date") or "") != str(item.get("slate_date") or "")
        ):
            errors.append(f"bound_source_window_pull_readback_mismatch:{index}")
            continue
        matching = _matching_game(pull, str(item.get("game_identity") or ""))
        if not matching or _game_snapshot_fingerprint(matching) != fingerprint:
            errors.append(f"bound_source_window_game_fingerprint_mismatch:{index}")
    if timestamps != sorted(timestamps):
        errors.append("bound_source_window_not_chronological")
    latest = entries[-1] if entries and isinstance(entries[-1], dict) else {}
    if (
        _parse_iso(latest.get("pulledAtUtc")) != _parse_iso(item.get("source_pull_at_utc"))
        or str(latest.get("pullId") or "") != str(item.get("source_pull_id") or "")
        or int(item.get("pull_depth") or 0) != len(entries)
    ):
        errors.append("bound_source_window_terminal_pull_mismatch")
    return sorted(set(errors))


def persisted_stage_authority_errors(table: Any, item: Dict[str, Any]) -> List[str]:
    """Validate the persisted authority chain required for canonical promotion."""
    errors: List[str] = []
    if item.get("model_version") != VERSION:
        errors.append("stage_model_version_mismatch")
    if item.get("lock_policy") != LOCK_POLICY:
        errors.append("stage_lock_policy_mismatch")
    if item.get("record_type") != STAGE_RECORD_TYPE:
        errors.append("stage_record_type_mismatch")
    if item.get("immutable_staged") is not True or item.get("write_once") is not True:
        errors.append("stage_not_immutable_write_once")
    commence = _parse_iso(item.get("commence_time"))
    cutoff = _parse_iso(item.get("scheduled_lock_at_utc"))
    staged_at = _parse_iso(item.get("staged_at_utc"))
    created_at = _parse_iso(item.get("created_at"))
    stable_at = cutoff + timedelta(seconds=CUTOFF_STABILIZATION_SECONDS) if cutoff else None
    if not commence or not cutoff or cutoff != commence - timedelta(minutes=REQUIRED_LOCK_MINUTES):
        errors.append("stage_not_exact_commence_minus_45_cutoff")
    if not staged_at or not stable_at or staged_at < stable_at or not commence or staged_at >= commence:
        errors.append("stage_not_after_stabilization_and_before_start")
    if created_at != staged_at:
        errors.append("stage_created_at_not_staged_at")
    source_window = item.get("source_window") or {}
    if (
        source_window.get("version") != SOURCE_WINDOW_VERSION
        or _parse_iso(source_window.get("scheduledCutoffAtUtc")) != cutoff
        or _parse_iso(source_window.get("closedAtUtc")) != staged_at
        or int(source_window.get("stabilizationSeconds") or -1) != CUTOFF_STABILIZATION_SECONDS
    ):
        errors.append("stage_bound_source_window_metadata_mismatch")
    errors.extend(_provider_manifest_authority_errors(table, item))
    errors.extend(_candidate_snapshot_authority_errors(table, item))
    errors.extend(_source_window_authority_errors(table, item))
    return sorted(set(errors))


def _get_stage(module: Any, slate: str, game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    response = module.TABLE.get_item(Key=_stage_key(module, slate, game), ConsistentRead=True)
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def _conditional_collision(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    return str((response.get("Error") or {}).get("Code") or "") == "ConditionalCheckFailedException"


def _diagnostic_prefix(module: Any, game: Dict[str, Any]) -> str:
    digest = hashlib.sha256(game_identity(game).encode("utf-8")).hexdigest()
    return f"PER_GAME_LOCK_DIAGNOSTIC#TMINUS{module.LOCK_MINUTES}#{digest}"


def _diagnostic_sk(
    module: Any,
    game: Dict[str, Any],
    attempted_at: datetime,
    attempt_id: str,
    event: str,
) -> str:
    timestamp = attempted_at.astimezone(timezone.utc).isoformat()
    return f"{_diagnostic_prefix(module, game)}#ATTEMPT#{timestamp}#{attempt_id}#{event}"


def _diagnostic_fingerprint(item: Dict[str, Any]) -> str:
    material = {
        str(key): value
        for key, value in _plain(item).items()
        if key != "diagnostic_fingerprint"
    }
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _put_diagnostic(module: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    """Append one immutable diagnostic event without affecting lock authority."""
    prepared = module.history.ddb_safe(copy.deepcopy(item))
    prepared["diagnostic_fingerprint"] = _diagnostic_fingerprint(prepared)
    key = {"PK": prepared["PK"], "SK": prepared["SK"]}
    try:
        module.TABLE.put_item(
            Item=prepared,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return {
            "ok": True,
            "created": True,
            "pk": prepared["PK"],
            "sk": prepared["SK"],
            "fingerprint": prepared["diagnostic_fingerprint"],
        }
    except Exception as exc:
        if not _conditional_collision(exc):
            return {
                "ok": False,
                "created": False,
                "pk": prepared["PK"],
                "sk": prepared["SK"],
                "error": f"diagnostic_write_failed:{type(exc).__name__}:{exc}",
            }
        try:
            existing = module.TABLE.get_item(Key=key, ConsistentRead=True).get("Item")
        except Exception as read_exc:
            return {
                "ok": False,
                "created": False,
                "pk": prepared["PK"],
                "sk": prepared["SK"],
                "error": f"diagnostic_collision_readback_failed:{type(read_exc).__name__}:{read_exc}",
            }
        if (
            isinstance(existing, dict)
            and existing.get("diagnostic_fingerprint") == prepared["diagnostic_fingerprint"]
        ):
            return {
                "ok": True,
                "created": False,
                "immutableExisting": True,
                "pk": prepared["PK"],
                "sk": prepared["SK"],
                "fingerprint": prepared["diagnostic_fingerprint"],
            }
        return {
            "ok": False,
            "created": False,
            "pk": prepared["PK"],
            "sk": prepared["SK"],
            "error": "diagnostic_write_once_collision_mismatch",
        }


def _diagnostic_base(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    game: Dict[str, Any],
    status: Dict[str, Any],
    attempted_at: datetime,
    attempt_id: str,
    force: bool,
) -> Dict[str, Any]:
    scoring = _scoring_pulls(module, pulls, game)
    source = scoring[-1] if scoring else {}
    source_at = _pull_at(module, source)
    latest_at = _pull_at(module, pulls[-1]) if pulls else None
    start = _start(module, game)
    lock_at = _lock_at(module, game)
    stable_at = _cutoff_stable_at(module, game)
    return {
        "PK": module._lock_pk(slate),
        "diagnostics_version": ATTEMPT_DIAGNOSTICS_VERSION,
        "sport": "mlb",
        "slate_date": slate,
        "model_version": VERSION,
        "lock_policy": LOCK_POLICY,
        "write_once": True,
        "attempt_id": attempt_id,
        "attempted_at_utc": attempted_at.astimezone(timezone.utc).isoformat(),
        "game_identity": game_identity(game),
        "game_id": game.get("game_id") or game.get("gameId") or game.get("id"),
        "commence_time": start.isoformat() if start else None,
        "scheduled_lock_at_utc": lock_at.isoformat() if lock_at else None,
        "source_window_stable_at_utc": stable_at.isoformat() if stable_at else None,
        "state_at_attempt": status.get("state"),
        "state_errors_at_attempt": list(status.get("errors") or []),
        "force_requested": bool(force),
        "pull_count_available": len(pulls),
        "pull_depth_at_cutoff": len(scoring),
        "latest_available_pull_at_utc": latest_at.isoformat() if latest_at else None,
        "latest_cutoff_pull_at_utc": source_at.isoformat() if source_at else None,
        "latest_cutoff_pull_id": source.get("pull_id"),
        "manifest_game_count": len(manifest),
        "manifest_game_identities": [game_identity(entry) for entry in manifest],
    }


def _begin_attempt_diagnostics(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    statuses: Iterable[Dict[str, Any]],
    attempted_at: datetime,
    force: bool,
) -> List[Dict[str, Any]]:
    by_identity = {str(status.get("gameIdentity") or ""): status for status in statuses or []}
    attempts: List[Dict[str, Any]] = []
    for game in manifest:
        identity = game_identity(game)
        status = by_identity.get(identity) or {}
        if status.get("state") not in _DIAGNOSTIC_STATES:
            continue
        if status.get("state") == "MISSED_NOT_BACKFILLED":
            history = _diagnostic_history(module, slate, game, limit=20)
            latest = history.get("latestAttempt") or {}
            if latest.get("outcome") == "MISSED_NOT_BACKFILLED":
                # A missed lock is terminal. Preserve the first immutable proof,
                # but do not append two more records every minute until midnight.
                continue
        attempt_id = uuid4().hex
        try:
            base = _diagnostic_base(
                module,
                slate,
                pulls,
                manifest,
                game,
                status,
                attempted_at,
                attempt_id,
                force,
            )
        except Exception as exc:
            base = {
                "PK": module._lock_pk(slate),
                "diagnostics_version": ATTEMPT_DIAGNOSTICS_VERSION,
                "sport": "mlb",
                "slate_date": slate,
                "model_version": VERSION,
                "lock_policy": LOCK_POLICY,
                "write_once": True,
                "attempt_id": attempt_id,
                "attempted_at_utc": attempted_at.astimezone(timezone.utc).isoformat(),
                "game_identity": identity,
                "game_id": game.get("game_id") or game.get("gameId") or game.get("id"),
                "commence_time": (_start(module, game) or attempted_at).isoformat(),
                "scheduled_lock_at_utc": (_lock_at(module, game) or attempted_at).isoformat(),
                "state_at_attempt": status.get("state"),
                "state_errors_at_attempt": list(status.get("errors") or []),
                "force_requested": bool(force),
                "diagnostic_context_error": f"{type(exc).__name__}:{exc}",
            }
        start_item = {
            **base,
            "SK": _diagnostic_sk(module, game, attempted_at, attempt_id, "START"),
            "record_type": ATTEMPT_RECORD_TYPE,
            "diagnostic_event": "ATTEMPT_STARTED",
            "created_at": attempted_at.astimezone(timezone.utc).isoformat(),
        }
        attempts.append({
            "attemptId": attempt_id,
            "game": game,
            "base": base,
            "startWrite": _put_diagnostic(module, start_item),
        })
    return attempts


def _attempt_outcome(
    status: Dict[str, Any],
    failures: List[Dict[str, Any]],
    exception: Optional[Exception],
) -> Tuple[str, str]:
    if exception is not None:
        return "INVOCATION_EXCEPTION", f"{type(exception).__name__}:{exception}"
    state = str(status.get("state") or "UNKNOWN")
    failure_reasons = [str(item.get("reason") or "") for item in failures if item.get("reason")]
    if state == "LOCKED_CANONICAL":
        return (
            "LOCKED_CANONICAL_WITH_ERRORS" if failure_reasons else "LOCKED_CANONICAL",
            ",".join(sorted(set(failure_reasons))) if failure_reasons else state,
        )
    if state == "WAITING_FOR_CUTOFF_STABILIZATION":
        return "WAITING_FOR_CUTOFF_STABILIZATION", state
    if state == "MISSED_NOT_BACKFILLED":
        return "MISSED_NOT_BACKFILLED", state
    if failure_reasons:
        return "FAILED", ",".join(sorted(set(failure_reasons)))
    if state in {"INVALID_STAGE_BLOCKED", "STAGED_CANONICAL_WRITE_BLOCKED", "DUE_NOT_STAGED"}:
        return "FAILED", state
    return "NO_ACTION", state


def _finish_attempt_diagnostics(
    module: Any,
    attempts: List[Dict[str, Any]],
    progress: Optional[Dict[str, Any]],
    failures: Optional[List[Dict[str, Any]]] = None,
    exception: Optional[Exception] = None,
) -> Dict[str, Any]:
    statuses = {
        str(status.get("gameIdentity") or ""): status
        for status in ((progress or {}).get("games") or [])
    }
    all_failures = list(failures or [])
    finished_at = module._now_utc().astimezone(timezone.utc)
    summaries: List[Dict[str, Any]] = []
    for attempt in attempts:
        base = attempt["base"]
        identity = str(base.get("game_identity") or "")
        status = statuses.get(identity) or {
            "gameIdentity": identity,
            "state": base.get("state_at_attempt"),
            "errors": base.get("state_errors_at_attempt") or [],
        }
        related_failures = [
            copy.deepcopy(item)
            for item in all_failures
            if str(item.get("gameIdentity") or "") == identity
        ]
        outcome, reason = _attempt_outcome(status, related_failures, exception)
        attempted_at = _parse_iso(base.get("attempted_at_utc")) or finished_at
        outcome_item = {
            **base,
            "SK": _diagnostic_sk(
                module,
                attempt["game"],
                attempted_at,
                attempt["attemptId"],
                "OUTCOME",
            ),
            "record_type": ATTEMPT_OUTCOME_RECORD_TYPE,
            "diagnostic_event": "ATTEMPT_OUTCOME",
            "outcome": outcome,
            "reason": reason,
            "state_after_attempt": status.get("state"),
            "state_errors_after_attempt": list(status.get("errors") or []),
            "failure_details": related_failures,
            "exception_type": type(exception).__name__ if exception is not None else None,
            "exception_message": str(exception) if exception is not None else None,
            "stage_present_after_attempt": status.get("sourcePullAtUtc") not in (None, ""),
            "canonical_proven_after_attempt": status.get("state") == "LOCKED_CANONICAL",
            "finished_at_utc": finished_at.isoformat(),
            "elapsed_milliseconds": max(
                int((finished_at - attempted_at).total_seconds() * 1000),
                0,
            ),
            "created_at": finished_at.isoformat(),
        }
        outcome_write = _put_diagnostic(module, outcome_item)
        summaries.append({
            "attemptId": attempt["attemptId"],
            "gameIdentity": identity,
            "stateAtAttempt": base.get("state_at_attempt"),
            "stateAfterAttempt": status.get("state"),
            "outcome": outcome,
            "reason": reason,
            "startWrite": attempt.get("startWrite"),
            "outcomeWrite": outcome_write,
        })
    return {
        "version": ATTEMPT_DIAGNOSTICS_VERSION,
        "appendOnly": True,
        "writeOnce": True,
        "attemptedGameCount": len(summaries),
        "attempts": summaries,
    }


def _diagnostic_history(module: Any, slate: str, game: Dict[str, Any], limit: int = 20) -> Dict[str, Any]:
    prefix = _diagnostic_prefix(module, game)
    try:
        response = module.TABLE.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": module._lock_pk(slate),
                ":prefix": prefix,
            },
            ConsistentRead=True,
            ScanIndexForward=False,
            Limit=limit,
        )
    except Exception as exc:
        return {
            "ok": False,
            "version": ATTEMPT_DIAGNOSTICS_VERSION,
            "error": f"diagnostic_query_failed:{type(exc).__name__}:{exc}",
        }
    items = [item for item in (response.get("Items") or []) if isinstance(item, dict)]
    by_attempt: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        attempt_id = str(item.get("attempt_id") or item.get("SK") or "")
        by_attempt.setdefault(attempt_id, []).append(item)
    latest_attempt_records = max(
        by_attempt.values(),
        key=lambda records: max(
            (
                str(item.get("attempted_at_utc") or ""),
                str(item.get("finished_at_utc") or ""),
                str(item.get("SK") or ""),
            )
            for item in records
        ),
        default=[],
    )
    latest_outcomes = [
        item
        for item in latest_attempt_records
        if item.get("diagnostic_event") == "ATTEMPT_OUTCOME"
    ]
    latest = max(
        latest_outcomes or latest_attempt_records,
        key=lambda item: (
            str(item.get("finished_at_utc") or ""),
            str(item.get("SK") or ""),
        ),
        default=None,
    )
    return {
        "ok": True,
        "version": ATTEMPT_DIAGNOSTICS_VERSION,
        "appendOnly": True,
        "writeOnce": True,
        "queriedRecordCount": len(items),
        "historyTruncated": bool(response.get("LastEvaluatedKey")),
        "latestAttempt": ({
            "attemptId": latest.get("attempt_id"),
            "attemptedAtUtc": latest.get("attempted_at_utc"),
            "finishedAtUtc": latest.get("finished_at_utc"),
            "stateAtAttempt": latest.get("state_at_attempt"),
            "stateAfterAttempt": latest.get("state_after_attempt"),
            "outcome": latest.get("outcome"),
            "reason": latest.get("reason"),
            "failureDetails": latest.get("failure_details") or [],
            "startObserved": any(
                item.get("attempt_id") == latest.get("attempt_id")
                and item.get("diagnostic_event") == "ATTEMPT_STARTED"
                for item in latest_attempt_records
            ),
            "outcomeObserved": latest.get("diagnostic_event") == "ATTEMPT_OUTCOME",
        } if latest else None),
    }


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
        "source": "persisted_last_prelock_prediction_at_own_tminus45",
        "sourceAtOrBeforeLock": bool(source_at and lock_at and source_at <= lock_at),
        "manifestVersion": VERSION,
        "manifestGameCount": len(manifest),
        "manifestGameIdentities": [game_identity(item) for item in manifest],
        "lockedGameCountAtWrite": locked_count,
        "rules": [
            "Each game locks independently 45 minutes before its own scheduled start.",
            "The last persisted pre-lock prediction at or before that game's cutoff becomes the final lock.",
            "No model, signal, optimizer, or direction scoring is rerun at lock.",
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
    fingerprint_version: Optional[str] = PAYLOAD_FINGERPRINT_VERSION,
) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    selection_fingerprint = _payload_fingerprint(
        _selection_material(out), fingerprint_version
    )
    lock = _per_game_lock(module, slate, game, source_pull, staged_at, manifest, locked_count)
    coverage = _manifest_coverage(manifest, locked_count)
    out.update({
        "slate_date": slate,
        "slateDateEt": slate,
        "gameIdentity": out.get("gameIdentity") or game_identity(game).replace("provider:", "", 1),
        "gameId": out.get("gameId") or game.get("game_id") or game.get("id"),
        "commenceTime": game.get("commence_time") or game.get("commenceTime") or out.get("commenceTime"),
        "homeTeam": game.get("home_team") or game.get("homeTeam") or out.get("homeTeam"),
        "awayTeam": game.get("away_team") or game.get("awayTeam") or out.get("awayTeam"),
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
            "selectionPolicy": "last_persisted_prelock_prediction_at_individual_game_tminus45",
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
        "lastPrelockPromotionVersion": PROMOTION_POLICY_VERSION,
        "modelOrSignalRecomputedAtLock": False,
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
    try:
        import mlb_ml_exact_lock_vector_patch
        import mlb_ml_frozen_features
        import mlb_official_prediction_semantics

        mlb_ml_exact_lock_vector_patch.apply(mlb_ml_frozen_features)
        normalized = mlb_official_prediction_semantics.enhance_result({
            "predictions": [out],
            "slateCoverage": coverage,
            "slatePredictionLock": lock,
        })
        normalized_rows = normalized.get("predictions") or []
        if len(normalized_rows) != 1:
            raise RuntimeError("official_semantics_did_not_return_one_row")
        out = mlb_ml_frozen_features.freeze_row(
            normalized_rows[0],
            coverage_complete=True,
        )
    except Exception as exc:
        raise RuntimeError(f"LAST_PRELOCK_FINALIZATION_FAILED:{exc}") from exc
    if _payload_fingerprint(_selection_material(out), fingerprint_version) != selection_fingerprint:
        raise RuntimeError("LAST_PRELOCK_SELECTION_CHANGED_DURING_FINALIZATION")
    out["lastPrelockSelectionFingerprint"] = selection_fingerprint
    out["lastPrelockPromotionVersion"] = PROMOTION_POLICY_VERSION
    out["modelOrSignalRecomputedAtLock"] = False
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


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _raw_game_identity(game: Dict[str, Any]) -> str:
    identity = game_identity(game)
    return identity.replace("provider:", "", 1) if identity.startswith("provider:") else identity


def _query_prediction_items(module: Any, slate: str, prefix: str, limit: int = 500) -> List[Dict[str, Any]]:
    table = module.history.PULLS
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={
            ":pk": f"GAME_WINNERS#mlb#{slate}",
            ":prefix": prefix,
        },
        ConsistentRead=True,
        ScanIndexForward=False,
        Limit=limit,
    )
    return [copy.deepcopy(item) for item in (response.get("Items") or []) if isinstance(item, dict)]


def _candidate_created_at(item: Dict[str, Any], row: Dict[str, Any]) -> Optional[datetime]:
    return _parse_iso(
        item.get("prediction_created_at_utc")
        or item.get("created_at")
        or row.get("createdAt")
        or row.get("created_at")
    )


def _candidate_persisted_at(item: Dict[str, Any]) -> Optional[datetime]:
    # This must be sampled after a successful DynamoDB live-row put.  A client
    # timestamp sampled before the write is not persistence authority.
    return _parse_iso(item.get("prediction_persisted_at_utc"))


def _candidate_source_at(item: Dict[str, Any], row: Dict[str, Any]) -> Optional[datetime]:
    lock = row.get("slatePredictionLock") or row.get("lastPossiblePredictionGate") or {}
    return _parse_iso(
        item.get("prediction_source_pull_at_utc")
        or row.get("predictionSourcePullAt")
        or lock.get("latestScoringPullAt")
        or (row.get("frozenFeatureVector") or {}).get("sourcePullAtUtc")
    )


def _candidate_source_id(item: Dict[str, Any], row: Dict[str, Any]) -> str:
    lock = row.get("slatePredictionLock") or row.get("lastPossiblePredictionGate") or {}
    return str(
        item.get("prediction_source_pull_id")
        or row.get("predictionSourcePullId")
        or lock.get("latestScoringPullId")
        or ""
    )


def _selection_material(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: copy.deepcopy(row.get(key))
        for key in (
            "predictedWinner",
            "predictedSide",
            "opponent",
            "americanOdds",
            "priceBook",
            "priceSource",
            "marketSide",
            "fairProbabilityPct",
            "winProbability",
            "winProbabilityPct",
            "teamWinProbabilityPct",
            "edgeVsBook",
            "edgeVsBookPct",
            "expectedValue",
            "expectedValuePct",
            "promoted",
            "promotionStatus",
            "blockedReasons",
            "score",
            "confidenceTier",
            "pickQuality",
            "homeSignal",
            "awaySignal",
            "fundamentalsSnapshot",
        )
    }


def _payload_fingerprint(
    payload: Any,
    version: Optional[str] = PAYLOAD_FINGERPRINT_VERSION,
) -> str:
    if version in (None, ""):
        return history_contract.legacy_payload_fingerprint(payload)
    if version == PAYLOAD_FINGERPRINT_VERSION:
        return history_contract.canonical_payload_fingerprint(payload)
    raise ValueError(f"unsupported payload fingerprint version: {version}")


def _candidate_items(module: Any, slate: str, game: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_identity = _raw_game_identity(game)
    return _query_prediction_items(
        module,
        slate,
        f"PREGAME#GAME#{raw_identity}#",
    )


def _source_pull_for_candidate(
    module: Any,
    scoring: List[Dict[str, Any]],
    source_at: datetime,
    source_id: str,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    earlier: List[Dict[str, Any]] = []
    same_timestamp: List[Dict[str, Any]] = []
    for pull in scoring:
        pulled_at = _pull_at(module, pull)
        if not pulled_at:
            continue
        if pulled_at < source_at:
            earlier.append(pull)
        elif pulled_at == source_at:
            same_timestamp.append(pull)

    if source_id:
        exact = [
            pull
            for pull in same_timestamp
            if str(pull.get("pull_id") or "") == source_id
        ]
    else:
        # A timestamp alone is authoritative only when it identifies one pull.
        # Otherwise two provider responses could share the same completion time
        # while carrying different prices.
        exact = same_timestamp if len(same_timestamp) == 1 else []
    if len(exact) != 1:
        return None, earlier
    matching = exact[0]
    return matching, earlier + [matching]


def _candidate_price_matches_source(
    row: Dict[str, Any],
    source_pull: Dict[str, Any],
    game: Dict[str, Any],
) -> bool:
    side = row.get("predictedSide")
    selected_signal = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    selected_signal = selected_signal if isinstance(selected_signal, dict) else {}
    book = str(row.get("priceBook") or selected_signal.get("priceBook") or "").lower().strip()
    price = row.get("lockedAmericanOdds")
    if price in (None, "", 0, 0.0):
        price = row.get("americanOdds") or selected_signal.get("americanOdds")
    source_game = _matching_game(source_pull, game_identity(game))
    book_payload = ((source_game or {}).get("books") or {}).get(book) or {}
    market = book_payload.get("ml") or book_payload.get("moneyline") or {}
    source_price = market.get(side) if side in {"home", "away"} else None
    try:
        return bool(book and price not in (None, "", 0, 0.0) and float(price) == float(source_price))
    except Exception:
        return False


def _last_prelock_candidate(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    scoring: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    lock_at = _lock_at(module, game)
    if not lock_at:
        return None, None, [], ["scheduled_lock_missing"]
    expected_identity = game_identity(game)
    expected_home = _norm(game.get("home_team") or game.get("homeTeam"))
    expected_away = _norm(game.get("away_team") or game.get("awayTeam"))
    eligible: List[Tuple[datetime, datetime, datetime, Dict[str, Any], Dict[str, Any]]] = []
    observed_prelock = 0
    for item in _candidate_items(module, slate, game):
        if item.get("record_type") != PREGAME_SNAPSHOT_RECORD_TYPE:
            continue
        row = item.get("data") or {}
        if not isinstance(row, dict) or game_identity(row) != expected_identity:
            continue
        if (
            _norm(row.get("homeTeam") or row.get("home_team")) != expected_home
            or _norm(row.get("awayTeam") or row.get("away_team")) != expected_away
        ):
            continue
        created_at = _candidate_created_at(item, row)
        persisted_at = _candidate_persisted_at(item)
        source_at = _candidate_source_at(item, row)
        if (
            not created_at
            or not persisted_at
            or not source_at
            or created_at > lock_at
            or persisted_at > lock_at
            or source_at > lock_at
        ):
            continue
        observed_prelock += 1
        eligible.append((persisted_at, created_at, source_at, item, row))
    if not eligible:
        return None, None, [], [
            "no_persisted_prelock_prediction_at_or_before_cutoff"
            if observed_prelock == 0
            else "no_valid_persisted_prelock_prediction"
        ]

    rejected: List[Dict[str, Any]] = []
    for persisted_at, created_at, source_at, item, row in sorted(
        eligible,
        key=lambda entry: (entry[0], entry[1], entry[2]),
        reverse=True,
    ):
        errors: List[str] = []
        if item.get("snapshot_version") != PREGAME_SNAPSHOT_VERSION:
            errors.append("persisted_prelock_snapshot_version_mismatch")
        raw_fingerprint_version = item.get("prediction_payload_fingerprint_version")
        fingerprint_version = (
            None
            if raw_fingerprint_version in (None, "")
            else raw_fingerprint_version
        )
        fingerprint_version_supported = _supported_payload_fingerprint_version(
            raw_fingerprint_version
        )
        if not fingerprint_version_supported:
            errors.append("persisted_prelock_payload_fingerprint_version_unsupported")
        if item.get("prediction_persistence_proof_type") != PREGAME_PERSISTENCE_PROOF_TYPE:
            errors.append("persisted_prelock_post_write_ack_missing")
        expected_live_pk = f"GAME_WINNERS#mlb#{slate}"
        persisted_identity = str(
            item.get("game_identity")
            or row.get("gameIdentity")
            or row.get("gameId")
            or "unknown"
        )
        expected_live_sk = f"GAME#{row.get('commenceTime') or 'unknown'}#{persisted_identity}"
        if item.get("prediction_persistence_write_pk") != expected_live_pk:
            errors.append("persisted_prelock_write_pk_mismatch")
        if item.get("prediction_persistence_write_sk") != expected_live_sk:
            errors.append("persisted_prelock_write_sk_mismatch")
        if fingerprint_version_supported and item.get(
            "prediction_payload_fingerprint"
        ) != _payload_fingerprint(row, fingerprint_version):
            errors.append("persisted_prelock_payload_fingerprint_mismatch")
        if not row.get("predictedWinner") or row.get("predictedSide") not in {"home", "away"}:
            errors.append("persisted_prelock_selection_missing")
        if row.get("predictedSide") == "home":
            expected_winner = game.get("home_team") or game.get("homeTeam")
        else:
            expected_winner = game.get("away_team") or game.get("awayTeam")
        if _norm(row.get("predictedWinner")) != _norm(expected_winner):
            errors.append("persisted_prelock_winner_side_mismatch")
        if not _selected_price_proven(row):
            errors.append("persisted_prelock_selected_side_real_book_price_missing")
        source_id = _candidate_source_id(item, row)
        source_pull, bound_scoring = _source_pull_for_candidate(module, scoring, source_at, source_id)
        if source_pull is None:
            errors.append("persisted_prelock_source_pull_not_found_in_cutoff_history")
        elif not _candidate_price_matches_source(row, source_pull, game):
            errors.append("persisted_prelock_selected_price_not_in_source_pull")
        if created_at < source_at:
            errors.append("persisted_prelock_created_before_source_pull")
        if persisted_at < created_at:
            errors.append("persisted_prelock_written_before_prediction_creation")
        source_age = (lock_at - source_at).total_seconds() / 60.0
        if source_age > module.MAX_LATEST_PULL_AGE_MINUTES:
            errors.append("persisted_prelock_prediction_source_stale_at_cutoff")
        for key in ("winner", "correct", "success", "homeWon", "pickCorrect", "outcome", "finalScore"):
            if key in row:
                errors.append(f"persisted_prelock_contains_{key}")
        labels = (row.get("frozenFeatureVector") or {}).get("labels") or {}
        if labels.get("homeWon") is not None or labels.get("pickCorrect") is not None:
            errors.append("persisted_prelock_vector_contains_outcome")
        try:
            import mlb_ml_feature_missingness_v1 as missingness
            import mlb_temporal_features_v1 as temporal

            if not temporal.provenance_is_lock_safe(
                (row.get("homeSignal") or {}).get("temporalFeatures"), source_at, lock_at
            ):
                errors.append("persisted_prelock_home_temporal_provenance_not_lock_safe")
            if not temporal.provenance_is_lock_safe(
                (row.get("awaySignal") or {}).get("temporalFeatures"), source_at, lock_at
            ):
                errors.append("persisted_prelock_away_temporal_provenance_not_lock_safe")
            if not missingness.provenance_is_lock_safe(
                row.get("fundamentalsSnapshot"), source_at, lock_at, _parse_iso
            ):
                errors.append("persisted_prelock_fundamentals_provenance_not_lock_safe")
        except Exception as exc:
            errors.append(f"persisted_prelock_provenance_verifier_unavailable:{exc}")

        if errors:
            rejected.append({
                "sk": item.get("SK"),
                "predictionPersistedAtUtc": persisted_at.isoformat(),
                "errors": sorted(set(errors)),
            })
            continue

        proof = {
            "version": PROMOTION_POLICY_VERSION,
            "pk": item.get("PK"),
            "sk": item.get("SK"),
            "recordType": item.get("record_type"),
            "snapshotVersion": item.get("snapshot_version"),
            "predictionCreatedAtUtc": created_at.isoformat(),
            "predictionPersistedAtUtc": persisted_at.isoformat(),
            "persistenceProofType": item.get("prediction_persistence_proof_type"),
            "persistenceWritePk": item.get("prediction_persistence_write_pk"),
            "persistenceWriteSk": item.get("prediction_persistence_write_sk"),
            "predictionPayloadFingerprint": item.get("prediction_payload_fingerprint"),
            "predictionPayloadFingerprintVersion": item.get("prediction_payload_fingerprint_version"),
            "candidateSnapshotFingerprint": _payload_fingerprint(item, fingerprint_version),
            "predictionSourcePullAtUtc": source_at.isoformat(),
            "predictionSourcePullId": source_pull.get("pull_id") if source_pull else source_id or None,
            "sourceAtOrBeforeCutoff": source_at <= lock_at,
            "createdAtOrBeforeCutoff": created_at <= lock_at,
            "persistedAtOrBeforeCutoff": persisted_at <= lock_at,
            "candidateRowFingerprint": _payload_fingerprint(row, fingerprint_version),
            "candidateSelectionFingerprint": _payload_fingerprint(
                _selection_material(row), fingerprint_version
            ),
            "candidateVectorFingerprint": (row.get("frozenFeatureVector") or {}).get("fingerprint"),
            "rejectedNewerCandidateCount": len(rejected),
            "rejectedNewerCandidates": rejected,
            "promotionRule": "last_valid_persisted_prediction_at_or_before_own_tminus45_becomes_final_lock",
            "modelOrSignalRecomputedAtLock": False,
        }
        return copy.deepcopy(row), proof, bound_scoring, []

    rejection_errors = sorted({
        error
        for rejection in rejected
        for error in (rejection.get("errors") or [])
    })
    return None, None, [], ["no_valid_persisted_prelock_prediction", *rejection_errors]


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
    candidate_proof = item.get("candidate_proof") or {}
    raw_bound_entries = source_window.get("pulls") or []
    bound_entries = raw_bound_entries if isinstance(raw_bound_entries, list) else []
    current_entries = _source_window_entries(module, scoring, game)
    bound_keys = {_source_window_key(entry) for entry in bound_entries if isinstance(entry, dict)}
    current_keys = {_source_window_key(entry) for entry in current_entries}
    source_window_closed_at = _parse_iso(source_window.get("closedAtUtc"))
    if item.get("model_version") != VERSION:
        errors.append("stage_model_version_mismatch")
    if item.get("lock_policy") != LOCK_POLICY:
        errors.append("stage_lock_policy_mismatch")
    if item.get("record_type") != STAGE_RECORD_TYPE or item.get("immutable_staged") is not True:
        errors.append("not_immutable_per_game_stage")
    if item.get("write_once") is not True:
        errors.append("stage_not_write_once")
    if item.get("promotion_policy_version") != PROMOTION_POLICY_VERSION:
        errors.append("wrong_last_prelock_promotion_policy")
    if candidate_proof.get("version") != PROMOTION_POLICY_VERSION:
        errors.append("missing_last_prelock_candidate_proof")
    if candidate_proof.get("modelOrSignalRecomputedAtLock") is not False:
        errors.append("lock_rescore_not_explicitly_disabled")
    if candidate_proof.get("sourceAtOrBeforeCutoff") is not True:
        errors.append("candidate_source_not_proven_prelock")
    if candidate_proof.get("createdAtOrBeforeCutoff") is not True:
        errors.append("candidate_creation_not_proven_prelock")
    if candidate_proof.get("persistedAtOrBeforeCutoff") is not True:
        errors.append("candidate_persistence_not_proven_prelock")
    if candidate_proof.get("persistenceProofType") != PREGAME_PERSISTENCE_PROOF_TYPE:
        errors.append("candidate_post_write_ack_proof_missing")
    if candidate_proof.get("snapshotVersion") != PREGAME_SNAPSHOT_VERSION:
        errors.append("candidate_snapshot_version_mismatch")
    if not _supported_payload_fingerprint_version(
        candidate_proof.get("predictionPayloadFingerprintVersion")
    ):
        errors.append("candidate_payload_fingerprint_version_unsupported")
    if row.get("modelOrSignalRecomputedAtLock") is not False:
        errors.append("row_lock_rescore_not_explicitly_disabled")
    if row.get("lastPrelockPromotionVersion") != PROMOTION_POLICY_VERSION:
        errors.append("row_last_prelock_promotion_version_mismatch")
    if row.get("lastPrelockSelectionFingerprint") != candidate_proof.get("candidateSelectionFingerprint"):
        errors.append("promoted_selection_differs_from_last_prelock_candidate")
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
    candidate_created_at = _parse_iso(candidate_proof.get("predictionCreatedAtUtc"))
    candidate_persisted_at = _parse_iso(candidate_proof.get("predictionPersistedAtUtc"))
    if (
        not candidate_created_at
        or not candidate_persisted_at
        or not lock_at
        or candidate_created_at > candidate_persisted_at
        or candidate_persisted_at > lock_at
    ):
        errors.append("candidate_post_write_ack_timestamp_invalid")
    latest_bound = bound_entries[-1] if bound_entries and isinstance(bound_entries[-1], dict) else {}
    if source_at != _parse_iso(latest_bound.get("pulledAtUtc")):
        errors.append("source_pull_timestamp_mismatch_bound_window")
    if str(item.get("source_pull_id") or "") != str(latest_bound.get("pullId") or ""):
        errors.append("source_pull_id_mismatch_bound_window")
    if int(item.get("pull_depth") or 0) != len(bound_entries):
        errors.append("pull_depth_mismatch")
    if item.get("stage_fingerprint") != _stage_fingerprint(item):
        errors.append("stage_fingerprint_mismatch")
    errors.extend(persisted_stage_authority_errors(module.TABLE, item))
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


def _provider_manifest_authority(
    module: Any,
    pull: Dict[str, Any],
    slate: str,
    manifest: List[Dict[str, Any]],
) -> Dict[str, Any]:
    reader = getattr(module.history, "provider_manifest_authority_for_lock", None)
    if not callable(reader):
        raise RuntimeError("provider_manifest_authority_reader_unavailable")
    authority = reader(pull, slate, manifest)
    if not isinstance(authority, dict) or not authority:
        raise RuntimeError("provider_manifest_authority_missing")
    out = copy.deepcopy(authority)
    out["canonicalGameIdentities"] = [game_identity(entry) for entry in manifest]
    return out


def _provider_schedule_material(games: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize schedule-only fields exactly as the immutable provider manifest does."""
    return sorted(
        [history_contract._provider_manifest_game("mlb", game) for game in games or []],
        key=history_contract._manifest_sort_key,
    )


def _manifest_authority_identity(authority: Dict[str, Any]) -> Tuple[str, ...]:
    return tuple(
        str(authority.get(key) or "")
        for key in ("version", "pk", "sk", "fingerprint", "pullId", "observedAtUtc")
    )


def _pull_history_identity(module: Any, pulls: Iterable[Dict[str, Any]]) -> Tuple[Tuple[str, ...], ...]:
    """Cheap immutable-history identity used to avoid redundant full-table reads."""
    return tuple(
        (
            str(pull.get("pull_id") or ""),
            (_pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc)).isoformat(),
            str((pull.get("provider_schedule_manifest") or {}).get("fingerprint") or ""),
            str((pull.get("provider_manifest_binding") or {}).get("fingerprint") or ""),
        )
        for pull in pulls or []
    )


def _select_provider_manifest_authority(
    module: Any,
    pulls: Iterable[Dict[str, Any]],
    slate: str,
    manifest: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Select the newest immutable pull that proves the complete daily slate.

    The provider legitimately stops returning games after they start.  That
    makes the newest response a valid *contracted* response, but not the
    full-slate authority for later locks.  The selected authority may therefore
    be older than the scoring pull.  It is schedule proof only; candidate and
    price selection remain bound to the last persisted pre-cutoff prediction.
    """
    ordered_pulls = sorted(
        list(pulls or []),
        key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc),
    )
    if not ordered_pulls:
        raise RuntimeError("provider_manifest_pull_history_missing")
    if not manifest:
        raise RuntimeError("provider_manifest_complete_slate_missing")

    manifest_reader = getattr(module.history, "provider_manifest_games_for_lock", None)
    if not callable(manifest_reader):
        raise RuntimeError("provider_manifest_games_reader_unavailable")

    expected_material = _provider_schedule_material(manifest)
    expected_by_identity = {game_identity(game): game for game in expected_material}
    if len(expected_by_identity) != len(expected_material):
        raise RuntimeError("provider_manifest_complete_slate_duplicate_game_identity")

    # The latest response must itself be authentic.  A contraction is accepted
    # only when it omits games that had already begun when that response was
    # observed; unknown, mutated, or prematurely omitted games fail closed.
    latest_pull = ordered_pulls[-1]
    latest_games = manifest_reader(latest_pull, slate)
    latest_material = _provider_schedule_material(latest_games)
    latest_identities = [game_identity(game) for game in latest_material]
    if len(set(latest_identities)) != len(latest_identities):
        raise RuntimeError("provider_manifest_latest_feed_duplicate_game_identity")
    unknown = sorted(set(latest_identities) - set(expected_by_identity))
    if unknown:
        raise RuntimeError(
            "provider_manifest_latest_feed_unknown_game_identity:" + ",".join(unknown)
        )
    latest_by_identity = {game_identity(game): game for game in latest_material}
    changed = sorted(
        identity
        for identity, game in latest_by_identity.items()
        if game != expected_by_identity.get(identity)
    )
    if changed:
        raise RuntimeError(
            "provider_manifest_latest_feed_schedule_changed:" + ",".join(changed)
        )
    latest_manifest = latest_pull.get("provider_schedule_manifest") or {}
    observed_at = _parse_iso(latest_manifest.get("observedAtUtc"))
    if not observed_at:
        raise RuntimeError("provider_manifest_latest_feed_observed_at_invalid")
    prematurely_omitted = sorted(
        identity
        for identity, game in expected_by_identity.items()
        if identity not in latest_by_identity
        and (not _parse_iso(game.get("commence_time")) or _parse_iso(game.get("commence_time")) > observed_at)
    )
    if prematurely_omitted:
        raise RuntimeError(
            "provider_manifest_latest_feed_future_game_omitted:"
            + ",".join(prematurely_omitted)
        )

    authority_failures: List[str] = []
    for candidate_pull in reversed(ordered_pulls):
        candidate_manifest = candidate_pull.get("provider_schedule_manifest") or {}
        candidate_games = list(candidate_manifest.get("games") or [])
        candidate_material = _provider_schedule_material(candidate_games)
        candidate_identities = [game_identity(game) for game in candidate_material]
        if candidate_identities != [game_identity(game) for game in expected_material]:
            continue
        if candidate_material != expected_material:
            authority_failures.append(
                f"{candidate_pull.get('pull_id') or 'unknown'}:schedule_material_mismatch"
            )
            continue
        try:
            return _provider_manifest_authority(
                module,
                candidate_pull,
                slate,
                manifest,
            )
        except Exception as exc:
            authority_failures.append(
                f"{candidate_pull.get('pull_id') or 'unknown'}:{exc}"
            )

    suffix = ":" + "|".join(authority_failures) if authority_failures else ""
    raise RuntimeError("provider_manifest_full_slate_authority_missing" + suffix)


def _generate_stage(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    manifest: List[Dict[str, Any]],
    scoring: List[Dict[str, Any]],
    locked_count: int,
    provider_manifest_authority: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    start = _start(module, game)
    now = module._now_utc().astimezone(timezone.utc)
    if not start or now >= start:
        return None, ["late_stage_blocked_game_already_started"]
    stable_at = _cutoff_stable_at(module, game)
    if not stable_at or now < stable_at:
        return None, ["cutoff_source_window_still_stabilizing"]
    candidate, candidate_proof, bound_scoring, candidate_errors = _last_prelock_candidate(
        module,
        slate,
        game,
        scoring,
    )
    if candidate_errors or not candidate or not candidate_proof or not bound_scoring:
        return None, candidate_errors or ["persisted_prelock_prediction_missing"]
    source = bound_scoring[-1]
    try:
        row = _prepare_row(
            module,
            candidate,
            slate,
            game,
            source,
            now,
            manifest,
            locked_count,
            candidate_proof.get("predictionPayloadFingerprintVersion") or None,
        )
    except Exception as exc:
        return None, [str(exc)]
    if row.get("lastPrelockSelectionFingerprint") != candidate_proof.get("candidateSelectionFingerprint"):
        return None, ["last_prelock_selection_fingerprint_changed"]
    source_window = {
        "version": SOURCE_WINDOW_VERSION,
        "closedAtUtc": now.isoformat(),
        "scheduledCutoffAtUtc": (_lock_at(module, game) or now).isoformat(),
        "stabilizationSeconds": CUTOFF_STABILIZATION_SECONDS,
        "pulls": _source_window_entries(module, bound_scoring, game),
    }
    if not isinstance(provider_manifest_authority, dict) or not provider_manifest_authority:
        return None, ["provider_manifest_authority_missing"]
    item = module.history.ddb_safe({
        **_stage_key(module, slate, game),
        "record_type": STAGE_RECORD_TYPE,
        "sport": "mlb",
        "slate_date": slate,
        "model_version": VERSION,
        "lock_policy": LOCK_POLICY,
        "promotion_policy_version": PROMOTION_POLICY_VERSION,
        "immutable_staged": True,
        "write_once": True,
        "game_identity": game_identity(game),
        "game_id": row.get("gameId"),
        "commence_time": row.get("commenceTime"),
        "scheduled_lock_at_utc": (_lock_at(module, game) or now).isoformat(),
        "staged_at_utc": now.isoformat(),
        "source_pull_at_utc": (_pull_at(module, source) or now).isoformat(),
        "source_pull_id": source.get("pull_id"),
        "pull_depth": len(bound_scoring),
        "source_window": source_window,
        "candidate_proof": candidate_proof,
        "provider_manifest_authority": provider_manifest_authority,
        "manifest_game_count": len(manifest),
        "data": {
            "row": row,
            "manifestGameIdentities": [game_identity(entry) for entry in manifest],
        },
        "created_at": now.isoformat(),
    })
    item["stage_fingerprint"] = _stage_fingerprint(item)
    errors = _validate_stage(module, item, slate, game, manifest, bound_scoring)
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


def _daily_authority_errors(
    module: Any,
    slate: str,
    item: Any,
    manifest: List[Dict[str, Any]],
    progress: Dict[str, Any],
) -> List[str]:
    """Prove that a DAILY_LOCK row is only an index over valid per-game locks.

    The daily key predates the per-game authority model, so key presence alone is
    not authority.  A current row is accepted only when its version/policy fields
    are exact and every aggregate proof resolves to a currently valid immutable
    stage plus canonical game row.
    """
    if not isinstance(item, dict):
        return ["daily_lock_item_missing_or_not_object"]

    errors: List[str] = []
    expected_identities = [game_identity(game) for game in manifest]
    expected_identity_set = set(expected_identities)
    expected_count = len(expected_identities)
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    picks = data.get("picks") if isinstance(data.get("picks"), list) else []
    proofs = data.get("perGameLockProof") if isinstance(data.get("perGameLockProof"), list) else []
    coverage = data.get("slateCoverage") if isinstance(data.get("slateCoverage"), dict) else {}
    summary = data.get("predictionSummary") if isinstance(data.get("predictionSummary"), dict) else {}
    stages = progress.get("stages") if isinstance(progress.get("stages"), dict) else {}
    canonical = progress.get("canonical") if isinstance(progress.get("canonical"), dict) else {}

    def exact_count(value: Any, expected: int) -> bool:
        try:
            return int(value) == expected
        except (TypeError, ValueError):
            return False

    if item.get("PK") != module._lock_pk(slate) or item.get("SK") != module._lock_sk():
        errors.append("daily_lock_key_mismatch")
    if item.get("record_type") != "mlb_daily_locked_individual_game_moneyline_picks":
        errors.append("daily_lock_record_type_not_per_game_authority")
    if str(item.get("sport") or "").lower() != "mlb" or str(item.get("slate_date") or "") != slate:
        errors.append("daily_lock_sport_or_slate_mismatch")
    if item.get("model_version") != VERSION or item.get("manifest_version") != VERSION:
        errors.append("daily_lock_model_or_manifest_version_mismatch")
    if item.get("single_game_model") != module.mlb_game_winner_engine.MODEL_VERSION:
        errors.append("daily_lock_single_game_model_version_mismatch")
    if (
        item.get("locked") is not True
        or item.get("per_game_lock") is not True
        or item.get("slate_wide_lock") is not False
    ):
        errors.append("daily_lock_not_explicit_per_game_authority")
    if (
        item.get("lock_policy") != LOCK_POLICY
        or not exact_count(item.get("lock_minutes_before_each_game"), module.LOCK_MINUTES)
    ):
        errors.append("daily_lock_policy_mismatch")
    if item.get("source") != "canonical_write_once_game_rows_from_each_game_own_tminus45":
        errors.append("daily_lock_source_authority_mismatch")
    if (
        item.get("all_games_predicted") is not True
        or item.get("coverage_complete") is not True
        or item.get("coverage_status") != "COMPLETE"
        or item.get("doubleheader_safe_identity") is not True
    ):
        errors.append("daily_lock_coverage_claim_incomplete")

    for field in (
        "game_count",
        "manifest_game_count",
        "prediction_count",
        "canonical_immutable_game_row_count",
    ):
        if not exact_count(item.get(field), expected_count):
            errors.append(f"daily_lock_{field}_mismatch")
    if expected_count == 0:
        errors.append("daily_lock_manifest_empty")
    if not exact_count(progress.get("stagedCount"), expected_count):
        errors.append("daily_lock_valid_stage_count_mismatch")
    if not exact_count(progress.get("canonicalCount"), expected_count):
        errors.append("daily_lock_canonical_readback_count_mismatch")
    if set(stages) != expected_identity_set:
        errors.append("daily_lock_stage_identity_coverage_mismatch")
    if set(canonical) != expected_identity_set:
        errors.append("daily_lock_canonical_identity_coverage_mismatch")

    manifest_proof = data.get("manifestGameIdentities")
    if not isinstance(manifest_proof, list) or manifest_proof != expected_identities:
        errors.append("daily_lock_manifest_identity_proof_mismatch")
    pick_identities = [game_identity(pick) for pick in picks if isinstance(pick, dict)]
    if len(picks) != expected_count or len(set(pick_identities)) != expected_count or set(pick_identities) != expected_identity_set:
        errors.append("daily_lock_pick_identity_coverage_mismatch")

    if len(proofs) != expected_count:
        errors.append("daily_lock_per_game_proof_count_mismatch")
    proof_identities = [
        str(proof.get("gameIdentity") or "")
        for proof in proofs
        if isinstance(proof, dict)
    ]
    if len(set(proof_identities)) != expected_count or set(proof_identities) != expected_identity_set:
        errors.append("daily_lock_per_game_proof_identity_mismatch")
    for proof in proofs:
        if not isinstance(proof, dict):
            errors.append("daily_lock_per_game_proof_not_object")
            continue
        identity = str(proof.get("gameIdentity") or "")
        stage = stages.get(identity)
        if not isinstance(stage, dict):
            errors.append(f"daily_lock_stage_missing_for_proof:{identity}")
            continue
        if (
            stage.get("model_version") != VERSION
            or stage.get("lock_policy") != LOCK_POLICY
            or stage.get("promotion_policy_version") != PROMOTION_POLICY_VERSION
        ):
            errors.append(f"daily_lock_stage_authority_version_mismatch:{identity}")
        if stage.get("stage_fingerprint") != _stage_fingerprint(stage):
            errors.append(f"daily_lock_stage_fingerprint_invalid:{identity}")
        expected_proof = {
            "gameIdentity": stage.get("game_identity"),
            "gameId": stage.get("game_id"),
            "commenceTime": stage.get("commence_time"),
            "scheduledLockAtUtc": stage.get("scheduled_lock_at_utc"),
            "actualStagedAtUtc": stage.get("staged_at_utc"),
            "sourcePullAtUtc": stage.get("source_pull_at_utc"),
            "sourcePullId": stage.get("source_pull_id"),
            "sourceWindow": stage.get("source_window") or {},
            "stageFingerprint": stage.get("stage_fingerprint"),
            "writeOnce": True,
            "canonicalImmutableGameRow": True,
        }
        if _plain(proof) != _plain(expected_proof):
            errors.append(f"daily_lock_per_game_proof_payload_mismatch:{identity}")

    if (
        coverage.get("version") != VERSION
        or coverage.get("lockCoverageComplete") is not True
        or coverage.get("manifestGameIdentities") != expected_identities
        or not exact_count(coverage.get("manifestGameCount"), expected_count)
        or not exact_count(coverage.get("lockedGameCountAtWrite"), expected_count)
        or not exact_count(coverage.get("pendingGameCountAtWrite"), 0)
        or coverage.get("operationalStatus") != "COMPLETE_MANIFEST_ALL_LOCKED"
    ):
        errors.append("daily_lock_slate_coverage_proof_mismatch")
    if (
        summary.get("allGamesPredicted") is not True
        or summary.get("perGameLock") is not True
        or not exact_count(summary.get("canonicalImmutableGameRowCount"), expected_count)
    ):
        errors.append("daily_lock_prediction_summary_proof_mismatch")
    return sorted(set(errors))


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
            "lockAttemptDiagnosticsVersion": ATTEMPT_DIAGNOSTICS_VERSION,
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
        raw_existing = module._get_lock_item(slate)
        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        manifest = module._latest_games_for_date(slate, pulls)
        now = module._now_utc().astimezone(timezone.utc)
        progress = _progress(module, slate, pulls, manifest, now, ensure_canonical=False) if manifest else {"games": [], "stagedCount": 0, "canonicalCount": 0, "pendingCount": 0, "stabilizingCount": 0, "dueMissingCount": 0, "missedCount": 0}
        daily_authority_errors = (
            _daily_authority_errors(module, slate, raw_existing, manifest, progress)
            if raw_existing
            else []
        )
        existing = module._lock_response(raw_existing) if raw_existing and not daily_authority_errors else None
        manifest_by_identity = {game_identity(game): game for game in manifest}
        per_game_status: List[Dict[str, Any]] = []
        for raw_status in progress.get("games") or []:
            status = copy.deepcopy(raw_status)
            game = manifest_by_identity.get(str(status.get("gameIdentity") or ""))
            status["attemptDiagnostics"] = (
                _diagnostic_history(module, slate, game)
                if game is not None
                else {
                    "ok": False,
                    "version": ATTEMPT_DIAGNOSTICS_VERSION,
                    "error": "diagnostic_game_identity_not_in_manifest",
                }
            )
            per_game_status.append(status)
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
            "perGameStatus": per_game_status,
            "firstGameStartEt": min([value for value in (_start(module, game) for game in manifest) if value]).astimezone(module.EASTERN).isoformat() if any(_start(module, game) for game in manifest) else None,
            "lockTimeEt": min(cutoffs).astimezone(module.EASTERN).isoformat() if cutoffs else None,
            "lastGameLockTimeEt": max(cutoffs).astimezone(module.EASTERN).isoformat() if cutoffs else None,
            "nextGameLockAtUtc": future[0].isoformat() if future else None,
            "nowEt": now.astimezone(module.EASTERN).isoformat(),
            "lockDue": bool(not existing and (progress.get("dueMissingCount") or progress.get("missedCount"))),
            "minutesUntilLock": round((future[0] - now).total_seconds() / 60.0, 2) if future else 0,
            "invalidExistingDailyLock": bool(raw_existing and daily_authority_errors),
            "dailyLockAuthorityErrors": daily_authority_errors,
        }

    def run_lock(slate_date: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        slate = slate_date or module._today_et()
        if module.TABLE is None:
            return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "error": "SNAPSHOTS_TABLE not configured"}
        raw_existing = module._get_lock_item(slate)
        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        if not pulls:
            if raw_existing:
                authority_errors = _daily_authority_errors(module, slate, raw_existing, [], {})
                return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY", "failClosed": True, "dailyLockAuthorityErrors": authority_errors}
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "NO_STORED_ODDS_API_PULL_HISTORY"}
        manifest = module._latest_games_for_date(slate, pulls)
        if not manifest:
            if raw_existing:
                authority_errors = _daily_authority_errors(module, slate, raw_existing, [], {})
                return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY", "failClosed": True, "dailyLockAuthorityErrors": authority_errors, "pullCount": len(pulls)}
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "NO_MLB_GAMES_FOR_SLATE_DATE", "pullCount": len(pulls)}

        now = module._now_utc().astimezone(timezone.utc)
        # Observe actionability without repairing anything, then durably record the
        # invocation before any stage/canonical write is attempted.
        observed = _progress(module, slate, pulls, manifest, now, ensure_canonical=False)
        if raw_existing:
            authority_errors = _daily_authority_errors(
                module,
                slate,
                raw_existing,
                manifest,
                observed,
            )
            if authority_errors:
                return {
                    "ok": False,
                    "sport": "mlb",
                    "modelVersion": VERSION,
                    "slateDateEt": slate,
                    "locked": False,
                    "reason": "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY",
                    "failClosed": True,
                    "dailyLockAuthorityErrors": authority_errors,
                    "perGameLockProgress": observed,
                }
            existing = module._lock_response(raw_existing)
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": True, "lock": existing, "perGameLockProgress": observed}
        attempts = _begin_attempt_diagnostics(
            module,
            slate,
            pulls,
            manifest,
            observed.get("games") or [],
            now,
            force,
        )
        failures: List[Dict[str, Any]] = []
        latest_progress = observed
        manifest_authority_cache: Dict[Tuple[Any, ...], Dict[str, Any]] = {}

        def manifest_authority_for(
            current_pulls: List[Dict[str, Any]],
            current_manifest: List[Dict[str, Any]],
        ) -> Dict[str, Any]:
            cache_key: Tuple[Any, ...] = (
                _pull_history_identity(module, current_pulls),
                tuple(game_identity(entry) for entry in current_manifest),
            )
            if cache_key not in manifest_authority_cache:
                manifest_authority_cache[cache_key] = _select_provider_manifest_authority(
                    module,
                    current_pulls,
                    slate,
                    current_manifest,
                )
            return copy.deepcopy(manifest_authority_cache[cache_key])

        def respond(payload: Dict[str, Any], progress: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            out = dict(payload)
            diagnostics = _finish_attempt_diagnostics(
                module,
                attempts,
                progress or latest_progress,
                failures,
            )
            out["perGameLockAttemptDiagnostics"] = diagnostics
            if out.get("ok") is False:
                failed_outcomes = [
                    attempt
                    for attempt in diagnostics.get("attempts") or []
                    if (attempt.get("outcomeWrite") or {}).get("ok") is not True
                ]
                if failed_outcomes:
                    raise RuntimeError(
                        "LOCK_ATTEMPT_DIAGNOSTIC_PERSIST_FAILED:"
                        + json.dumps(failed_outcomes, sort_keys=True, default=str)
                    )
            return out

        try:
            pre = _progress(module, slate, pulls, manifest, now, ensure_canonical=True)
            latest_progress = pre
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

                    try:
                        manifest_authority = manifest_authority_for(
                            pulls,
                            manifest,
                        )
                    except Exception as exc:
                        failures.append({
                            "gameIdentity": identity,
                            "reason": "PROVIDER_MANIFEST_AUTHORITY_NOT_STAGED",
                            "errors": [str(exc)],
                        })
                        failed = True
                        break

                    item, errors = _generate_stage(
                        module,
                        slate,
                        game,
                        manifest,
                        scoring,
                        pre.get("stagedCount", 0) + 1,
                        manifest_authority,
                    )
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
                    pull_history_unchanged = (
                        _pull_history_identity(module, refreshed_pulls)
                        == _pull_history_identity(module, pulls)
                    )
                    refreshed_manifest = (
                        manifest
                        if pull_history_unchanged
                        else module._latest_games_for_date(slate, refreshed_pulls)
                    )
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
                    try:
                        refreshed_manifest_authority = manifest_authority_for(
                            refreshed_pulls,
                            refreshed_manifest,
                        )
                    except Exception as exc:
                        failures.append({
                            "gameIdentity": identity,
                            "reason": "PROVIDER_MANIFEST_AUTHORITY_CHANGED_OR_INVALID_DURING_SOURCE_WINDOW_CLOSE_NOT_STAGED",
                            "errors": [str(exc)],
                        })
                        failed = True
                        break
                    pulls = refreshed_pulls
                    manifest = refreshed_manifest
                    game = refreshed_game
                    source_window_unchanged = (
                        _source_window_entries(module, scoring, game)
                        == _source_window_entries(module, refreshed_scoring, game)
                    )
                    authority_unchanged = (
                        _manifest_authority_identity(item.get("provider_manifest_authority") or {})
                        == _manifest_authority_identity(refreshed_manifest_authority)
                    )
                    if source_window_unchanged and authority_unchanged:
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

            final_pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
            final_history_unchanged = (
                _pull_history_identity(module, final_pulls)
                == _pull_history_identity(module, pulls)
            )
            pulls = final_pulls
            if not final_history_unchanged:
                manifest = module._latest_games_for_date(slate, pulls)
            progress = _progress(module, slate, pulls, manifest, module._now_utc().astimezone(timezone.utc), ensure_canonical=True)
            latest_progress = progress
            if progress.get("missedCount"):
                return respond({"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "MISSED_PER_GAME_LOCK_NOT_BACKFILLED", "failClosed": True, "forceIgnoredForSafety": bool(force), "failures": failures, "perGameLockProgress": progress}, progress)
            if failures or progress.get("dueMissingCount"):
                return respond({"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL", "failClosed": True, "forceIgnoredForSafety": bool(force), "failures": failures, "perGameLockProgress": progress}, progress)
            if progress.get("stagedCount") < len(manifest) or progress.get("canonicalCount") < len(manifest):
                return respond({"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER", "forceIgnoredForSafety": bool(force), "perGameLockProgress": progress}, progress)

            item = _daily_item(module, slate, pulls, manifest, progress)
            try:
                module.TABLE.put_item(Item=item, ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
                locked = module._lock_response(item)
                return respond({"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": False, "lock": locked, "perGameLockProgress": progress}, progress)
            except Exception as exc:
                if _conditional_collision(exc):
                    collision_item = module._get_lock_item(slate)
                    authority_errors = _daily_authority_errors(
                        module,
                        slate,
                        collision_item,
                        manifest,
                        progress,
                    )
                    existing = module._lock_response(collision_item) if collision_item and not authority_errors else None
                    if existing:
                        return respond({"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": True, "lock": existing, "perGameLockProgress": progress}, progress)
                    failures.append({
                        "reason": "DAILY_LOCK_CONDITIONAL_COLLISION_NOT_PER_GAME_AUTHORITY",
                        "errors": authority_errors,
                    })
                    return respond({"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY", "failClosed": True, "dailyLockAuthorityErrors": authority_errors, "perGameLockProgress": progress}, progress)
                raise
        except Exception as exc:
            _finish_attempt_diagnostics(
                module,
                attempts,
                latest_progress,
                failures,
                exception=exc,
            )
            raise

    module.MODEL_VERSION = VERSION
    module.LOCK_POLICY = LOCK_POLICY
    module._status_payload = status_payload
    module.run_lock = run_lock
    module.MLB_DAILY_PER_GAME_LOCK_VERSION = VERSION
    module.MLB_LAST_PRELOCK_PROMOTION_VERSION = PROMOTION_POLICY_VERSION
    module.MLB_PER_GAME_LOCK_ATTEMPT_DIAGNOSTICS_VERSION = ATTEMPT_DIAGNOSTICS_VERSION
    module._INQSI_MLB_DAILY_PER_GAME_LOCK_V1 = True
    return module
