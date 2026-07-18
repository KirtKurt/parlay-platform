from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

VERSION = "MLB-DAILY-LOCK-DIAGNOSTIC-v2-non-official"


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _authoritative(item: Dict[str, Any]) -> bool:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    picks = data.get("picks") if isinstance(data.get("picks"), list) else []
    locked_at = _parse_dt(item.get("locked_at") or item.get("created_at"))
    latest_pull = _parse_dt(item.get("latest_pull_at"))
    first_start = _parse_dt(item.get("first_game_start_utc"))
    game_count = _as_int(item.get("game_count"))
    prediction_count = _as_int(item.get("prediction_count"))
    if item.get("per_game_lock") is True or item.get("lock_policy") == "each_mlb_game_minus_45_minutes":
        try:
            import mlb_daily_lock_ml_vector_preservation_patch as exact_contract
            import mlb_ml_clean_cohort_v1 as cohort
        except Exception:
            return False
        proof = data.get("perGameLockProof") if isinstance(data.get("perGameLockProof"), list) else []
        if not (
            item.get("locked") is True
            and item.get("all_games_predicted") is True
            and item.get("coverage_complete") is True
            and game_count > 0
            and game_count == prediction_count == len(picks) == len(proof)
            and _as_int(item.get("canonical_immutable_game_row_count")) == game_count
        ):
            return False
        proof_by_game = {str(row.get("gameId") or row.get("gameIdentity") or ""): row for row in proof if isinstance(row, dict)}
        for row in picks:
            if not isinstance(row, dict):
                return False
            vector = row.get("frozenFeatureVector") if isinstance(row.get("frozenFeatureVector"), dict) else {}
            lock_at = _parse_dt(vector.get("lockAtUtc") or row.get("lockedAtUtc"))
            source_at = _parse_dt(vector.get("sourcePullAtUtc") or row.get("predictionSourcePullAt"))
            start_at = _parse_dt(row.get("commenceTime") or row.get("commence_time"))
            game_key = str(row.get("gameId") or row.get("gameIdentity") or "")
            game_proof = proof_by_game.get(game_key)
            staged_at = _parse_dt((game_proof or {}).get("actualStagedAtUtc"))
            proof_lock_at = _parse_dt((game_proof or {}).get("scheduledLockAtUtc"))
            proof_source_at = _parse_dt((game_proof or {}).get("sourcePullAtUtc"))
            labels = vector.get("labels") if isinstance(vector.get("labels"), dict) else {}
            if not (
                game_proof
                and game_proof.get("writeOnce") is True
                and game_proof.get("canonicalImmutableGameRow") is True
                and lock_at
                and source_at
                and start_at
                and staged_at
                and proof_lock_at == lock_at
                and proof_source_at == source_at
                and source_at <= lock_at < start_at
                and lock_at == start_at - timedelta(minutes=_as_int(item.get("lock_minutes_before_each_game")) or 45)
                and staged_at < start_at
                and labels.get("homeWon") is None
                and labels.get("pickCorrect") is None
                and vector.get("fingerprint") == cohort.fingerprint_for_vector(vector)
                and not exact_contract.validate_exact_locked_row(row)
            ):
                return False
        return True
    return bool(
        item.get("locked") is True
        and item.get("all_games_predicted") is True
        and locked_at
        and latest_pull
        and latest_pull <= locked_at
        and (first_start is None or locked_at < first_start)
        and game_count > 0
        and game_count == prediction_count == len(picks)
    )


def _daily_lock_rows(module: Any, slate_date: str) -> List[Dict[str, Any]]:
    history = getattr(module, "history", None)
    table = getattr(history, "PULLS", None)
    if table is None:
        return []
    try:
        item = table.get_item(
            Key={
                "PK": f"LOCKED_PICKS#mlb#{slate_date}",
                "SK": "DAILY_LOCK#TMINUS45",
            },
            ConsistentRead=True,
        ).get("Item")
    except Exception:
        return []
    if not isinstance(item, dict) or not _authoritative(item):
        return []

    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    picks = data.get("picks") if isinstance(data.get("picks"), list) else []
    locked_at = str(item.get("locked_at") or item.get("created_at") or "")
    latest_pull = str(item.get("latest_pull_at") or "")
    out: List[Dict[str, Any]] = []
    for raw in picks:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        vector = row.get("frozenFeatureVector") if isinstance(row.get("frozenFeatureVector"), dict) else {}
        vector_lock_at = str(
            vector.get("lockAtUtc")
            or (row.get("slatePredictionLock") or {}).get("lockAtUtc")
            or row.get("lockedAtUtc")
            or locked_at
        )
        vector_source_at = str(
            vector.get("sourcePullAtUtc")
            or row.get("predictionSourcePullAt")
            or (row.get("slatePredictionLock") or {}).get("latestScoringPullAt")
            or latest_pull
        )
        per_game = bool(item.get("per_game_lock") is True or item.get("lock_policy") == "each_mlb_game_minus_45_minutes")
        row_lock = row.get("slatePredictionLock") if isinstance(row.get("slatePredictionLock"), dict) else {}
        row_audit = row.get("lockedCardAudit") if isinstance(row.get("lockedCardAudit"), dict) else {}
        actual_staged_at = row_lock.get("actualStagedAtUtc") or row_audit.get("actualStagedAtUtc")
        tags = {str(value) for value in (row.get("tags") or [])}
        tags.update({
            "FINAL_LOCKED",
            "SLATE_LOCKED",
            "NOT_PLAYABLE",
            "LEGACY_DAILY_CARD_DIAGNOSTIC",
        })
        row.update({
            "sport": "mlb",
            "slateDateEt": slate_date,
            "slate_date": slate_date,
            "lockedPrediction": True,
            "officialPrediction": False,
            "officialPredictionStatus": "DIAGNOSTIC_DAILY_CARD_NOT_OFFICIAL",
            "actionablePick": False,
            "accuracyTargetEligible": False,
            "playable": False,
            "playablePick": False,
            "recommendationStatus": "OFFICIAL_PREDICTION_NOT_PLAYABLE",
            "predictionSourcePullAt": vector_source_at,
            "lockedAtUtc": vector_lock_at,
            "createdAt": locked_at,
            "lockedAmericanOdds": row.get("lockedAmericanOdds") if row.get("lockedAmericanOdds") is not None else row.get("americanOdds"),
            "tags": sorted(tags),
            "slatePredictionLock": {
                "locked": True,
                "finalLocked": True,
                "phase": "GAME_LOCKED" if per_game else "SLATE_LOCKED",
                "lockAtUtc": vector_lock_at,
                "scheduledLockAtUtc": vector_lock_at,
                "actualStagedAtUtc": actual_staged_at,
                "latestScoringPullAt": vector_source_at,
                "source": "immutable_daily_locked_card",
                "perGameLock": per_game,
                "slateWideLock": not per_game,
            },
            "legacyDailyCardDiagnostic": {
                "applied": True,
                "version": VERSION,
                "authoritySource": "LOCKED_PICKS_DAILY_LOCK_TMINUS45",
                "officialAuditEligible": False,
                "learningEligible": False,
                "pk": item.get("PK"),
                "sk": item.get("SK"),
                "writeOnceCard": True,
                "perGameLock": per_game,
                "lockedAtUtc": vector_lock_at,
                "actualStagedAtUtc": actual_staged_at,
                "sourcePullAtUtc": vector_source_at,
                "cardStoredAtUtc": locked_at,
                "cardLatestPullAtUtc": latest_pull,
                "gameCount": _as_int(item.get("game_count")),
                "predictionCount": _as_int(item.get("prediction_count")),
            },
        })
        out.append(row)
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_DAILY_LOCK_AUDIT_FALLBACK_APPLIED", False):
        return module

    def legacy_daily_card_diagnostic_rows(slate_date: str):
        return _daily_lock_rows(module, str(slate_date))

    # Kept as an explicit diagnostic reader only. It must never be appended to
    # _query_predictions_for_slate, because that function feeds official grading,
    # accuracy ledgers, score learning, and ML promotion gates.
    module.legacy_daily_card_diagnostic_rows = legacy_daily_card_diagnostic_rows
    module.MLB_DAILY_LOCK_AUDIT_FALLBACK_VERSION = VERSION
    module.MLB_DAILY_LOCK_AUDIT_FALLBACK_OFFICIAL_ELIGIBLE = False
    module._INQSI_MLB_DAILY_LOCK_AUDIT_FALLBACK_APPLIED = True
    return module
