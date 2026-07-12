from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VERSION = "MLB-DAILY-LOCK-AUDIT-FALLBACK-v1.1-authoritative-write-once-card"


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
        tags = {str(value) for value in (row.get("tags") or [])}
        tags.update({
            "FINAL_LOCKED",
            "SLATE_LOCKED",
            "OFFICIAL_LOCKED_PREDICTION",
            "OFFICIAL_PREDICTION",
            "NOT_PLAYABLE",
            "IMMUTABLE_DAILY_LOCK_FALLBACK",
        })
        row.update({
            "sport": "mlb",
            "slateDateEt": slate_date,
            "slate_date": slate_date,
            "lockedPrediction": True,
            "officialPrediction": True,
            "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
            "actionablePick": False,
            "accuracyTargetEligible": False,
            "playable": False,
            "playablePick": False,
            "recommendationStatus": "OFFICIAL_PREDICTION_NOT_PLAYABLE",
            "predictionSourcePullAt": latest_pull,
            "createdAt": locked_at,
            "lockedAmericanOdds": row.get("lockedAmericanOdds") if row.get("lockedAmericanOdds") is not None else row.get("americanOdds"),
            "tags": sorted(tags),
            "slatePredictionLock": {
                "locked": True,
                "finalLocked": True,
                "phase": "SLATE_LOCKED",
                "lockAtUtc": locked_at,
                "latestScoringPullAt": latest_pull,
                "source": "immutable_daily_locked_card",
            },
            "immutableDailyLockFallback": {
                "applied": True,
                "version": VERSION,
                "authoritySource": "LOCKED_PICKS_DAILY_LOCK_TMINUS45",
                "pk": item.get("PK"),
                "sk": item.get("SK"),
                "writeOnceCard": True,
                "lockedAtUtc": locked_at,
                "sourcePullAtUtc": latest_pull,
                "gameCount": _as_int(item.get("game_count")),
                "predictionCount": _as_int(item.get("prediction_count")),
            },
        })
        out.append(row)
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_DAILY_LOCK_AUDIT_FALLBACK_APPLIED", False):
        return module

    import mlb_locked_card_audit_v1 as base

    original_query = module._query_predictions_for_slate
    original_copy = base._copy_audit_fields

    def query_predictions_for_slate(slate_date: str):
        rows = list(original_query(slate_date) or [])
        rows.extend(_daily_lock_rows(module, str(slate_date)))
        return rows

    def copy_audit_fields(pred: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(original_copy(pred))
        fallback = pred.get("immutableDailyLockFallback") or {}
        if fallback.get("applied") is True:
            out.update({
                "americanOdds": pred.get("americanOdds"),
                "lockedAmericanOdds": pred.get("lockedAmericanOdds"),
                "priceBook": pred.get("priceBook"),
                "priceSource": pred.get("priceSource"),
                "fairProbabilityPct": pred.get("fairProbabilityPct"),
                "teamWinProbabilityPct": pred.get("teamWinProbabilityPct") or pred.get("winProbabilityPct"),
                "immutableDailyLockFallback": fallback,
            })
            audit = dict(out.get("lockedCardAudit") or {})
            audit.update({
                "authoritySource": "immutable_daily_locked_card",
                "authorityVersion": VERSION,
                "writeOnceCard": True,
                "dailyLockPk": fallback.get("pk"),
                "dailyLockSk": fallback.get("sk"),
            })
            out["lockedCardAudit"] = audit
        return out

    module._query_predictions_for_slate = query_predictions_for_slate
    base._copy_audit_fields = copy_audit_fields
    module.MLB_DAILY_LOCK_AUDIT_FALLBACK_VERSION = VERSION
    module._INQSI_MLB_DAILY_LOCK_AUDIT_FALLBACK_APPLIED = True
    return module
