"""Top-level autonomous orchestration and status reporting."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .canonical import iso_utc, now_utc
from .config import Settings, TARGETS, parse_utc
from .historical import backfill_state, historical_tick
from .live import live_tick
from .providers import BBDClient, OddsApiClient
from .storage import NflStore
from .trainer import train_all_targets


def build_runtime() -> tuple[Settings, NflStore]:
    settings = Settings.from_env()
    return settings, NflStore(settings)


def build_providers(settings: Settings) -> tuple[BBDClient, OddsApiClient]:
    return (
        BBDClient(secret_arn=settings.bbd_secret_arn),
        OddsApiClient(secret_arn=settings.odds_secret_arn),
    )


def status_payload(store: NflStore, settings: Settings) -> dict[str, Any]:
    state = backfill_state(store)
    games = store.list_games()
    champions = {
        target: (
            {
                "model_digest": row.get("model_digest"),
                "authority_state": row.get("authority_state"),
                "promoted_at": row.get("promoted_at"),
                "audit": ((row.get("report") or {}).get("audit") or {}),
            }
            if (row := store.champion(target))
            else None
        )
        for target in TARGETS
    }
    feature_counts = {target: len(store.feature_rows(target)) for target in TARGETS}
    historical_count = sum(int(row.get("season") or 0) <= 2025 for row in games)
    live_schedule_count = sum(int(row.get("season") or 0) == 2026 for row in games)
    return {
        "ok": True,
        "sport": "NFL",
        "mode": "REGULAR_SEASON_LIVE" if settings.live_collection_allowed() else "HISTORICAL_ONLY",
        "live_collection_start_utc": settings.live_collection_start_utc,
        "preseason_collection_enabled": False,
        "preseason_predictions_enabled": False,
        "backfill": state,
        "historical_game_count": historical_count,
        "live_schedule_game_count": live_schedule_count,
        "feature_counts": feature_counts,
        "champions": champions,
        "providers": {
            "statistics": "BBD",
            "odds": "THE_ODDS_API",
            "dual_provenance_required": True,
        },
        "targets": list(TARGETS),
        "decision_horizon_minutes": 10,
        "at": now_utc(),
    }



def _training_due(store: NflStore, *, now: datetime | None = None, minimum_hours: int = 20) -> tuple[bool, dict[str, Any] | None]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = store.state_get("NFL_AUTO_TRAINING_SCHEDULE")
    if not state or not state.get("last_attempt_at"):
        return True, state
    try:
        last = parse_utc(str(state["last_attempt_at"]))
    except (TypeError, ValueError):
        return True, state
    return current - last >= timedelta(hours=minimum_hours), state


def _record_training_attempt(store: NflStore, result: Mapping[str, Any]) -> None:
    store.state_put(
        "NFL_AUTO_TRAINING_SCHEDULE",
        {
            "last_attempt_at": now_utc(),
            "last_status": result.get("status"),
            "last_ok": result.get("ok"),
        },
    )

def autonomous_tick(
    *,
    settings: Settings,
    store: NflStore,
    bbd: BBDClient,
    odds: OddsApiClient,
    bedrock_client: Any = None,
) -> dict[str, Any]:
    state = backfill_state(store)
    if str(state.get("phase")) != "READY":
        return historical_tick(store=store, settings=settings, bbd=bbd, odds=odds)
    missing_champions = [target for target in TARGETS if store.champion(target) is None]
    if missing_champions:
        due, schedule_state = _training_due(store)
        if not due:
            return {
                "ok": True,
                "status": "TRAINING_RETRY_NOT_DUE",
                "missing_champions": missing_champions,
                "last_attempt_at": (schedule_state or {}).get("last_attempt_at"),
                "at": now_utc(),
            }
        result = train_all_targets(
            store=store,
            settings=settings,
            bedrock_client=bedrock_client,
        )
        _record_training_attempt(store, result)
        return {"ok": bool(result.get("ok")), "status": "TRAINING", "training": result}
    return {
        "ok": True,
        "status": "HISTORICAL_READY",
        "champions": {target: store.champion(target).get("model_digest") for target in TARGETS},
        "live_collection_allowed": settings.live_collection_allowed(),
        "at": now_utc(),
    }


def run_action(action: str, *, now: datetime | None = None) -> dict[str, Any]:
    settings, store = build_runtime()
    normalized = action.strip().lower()
    if normalized in {"status", "health"}:
        return status_payload(store, settings)
    if normalized == "train":
        if str(backfill_state(store).get("phase")) != "READY":
            return {"ok": True, "status": "TRAINING_DEFERRED_BACKFILL_NOT_READY"}
        result = train_all_targets(store=store, settings=settings)
        _record_training_attempt(store, result)
        return result
    if normalized in {"tick", "autonomous_tick", "historical_tick"}:
        bbd, odds = build_providers(settings)
        return autonomous_tick(settings=settings, store=store, bbd=bbd, odds=odds)
    if normalized == "live_tick":
        # The date gate is checked before clients are constructed, so scheduled
        # preseason invocations do not read provider secrets or call live APIs.
        if not settings.live_collection_allowed(now or datetime.now(timezone.utc)):
            return {
                "ok": True,
                "status": "HISTORICAL_ONLY",
                "live_collection_start_utc": settings.live_collection_start_utc,
                "now": iso_utc(now or datetime.now(timezone.utc)),
                "preseason_predictions": 0,
            }
        bbd, odds = build_providers(settings)
        return live_tick(
            store=store,
            settings=settings,
            bbd=bbd,
            odds=odds,
            now=now or datetime.now(timezone.utc),
        )
    if normalized == "credential_smoke":
        bbd, odds = build_providers(settings)
        account, bbd_transport = bbd.account()
        # The Odds API historical events call is low-cost and verifies the paid
        # historical entitlement without spending multi-market snapshot credits.
        check_at = "2025-09-05T00:00:00Z"
        events, odds_transport = odds.historical_events(snapshot_at=check_at)
        return {
            "ok": True,
            "status": "PROVIDERS_AUTHENTICATED",
            "bbd_account_shape": sorted((account.get("data") or {}).keys()),
            "bbd_transport": bbd_transport.to_dict(),
            "odds_event_count": len(events.get("data") or []),
            "odds_transport": odds_transport.to_dict(),
            "at": iso_utc(now or datetime.now(timezone.utc)),
        }
    raise ValueError(f"NFL_AUTO_ACTION_UNSUPPORTED:{normalized}")
