"""Autonomous historical backfill state machine for BBD stats and Odds API lines."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .canonical import digest, iso_utc, now_utc, strict_event_match
from .config import (
    BBD_ALLOWED_GAME_TYPES,
    HISTORICAL_SEASONS,
    HISTORICAL_SNAPSHOT_HORIZONS_MINUTES,
    Settings,
    parse_utc,
)
from .features import (
    Game,
    TeamGameStats,
    aggregate_game_plays,
    materialize_game_rows,
    parse_bbd_game,
    pregame_team_features,
)
from .markets import snapshot_consensus
from .providers import BBDClient, OddsApiClient, RateLimitError, TransientProviderError
from .storage import NflStore

STATE_PK = "NFL_AUTO_BACKFILL"
PHASES = ("BBD_GAMES", "BBD_PLAYS", "ODDS_SNAPSHOTS", "MATERIALIZE", "READY")


def _initial_state() -> dict[str, Any]:
    return {
        "phase": "BBD_GAMES",
        "season_index": 0,
        "game_type_index": 0,
        "offset": 0,
        "materialize_index": 0,
        "completed": False,
        "started_at": now_utc(),
    }


def backfill_state(store: NflStore) -> dict[str, Any]:
    return store.state_get(STATE_PK) or _initial_state()


def _advance_discovery_cursor(state: dict[str, Any]) -> dict[str, Any]:
    output = dict(state)
    output["offset"] = 0
    output["game_type_index"] = int(output.get("game_type_index") or 0) + 1
    if output["game_type_index"] >= len(BBD_ALLOWED_GAME_TYPES):
        output["game_type_index"] = 0
        output["season_index"] = int(output.get("season_index") or 0) + 1
    if output["season_index"] >= len(HISTORICAL_SEASONS):
        output.update({"phase": "BBD_PLAYS", "season_index": 0, "game_type_index": 0, "offset": 0})
    return output



def bbd_rate_admitted(state: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    if str(state.get("last_provider") or "") != "BBD":
        return True, {"known": False}
    transport = state.get("last_transport") or {}
    try:
        remaining = int(transport.get("rate_remaining"))
    except (TypeError, ValueError):
        return True, {"known": False}
    reset_raw = transport.get("rate_reset")
    retry_after: str | None = None
    reset_epoch: int | None = None
    try:
        reset_epoch = int(float(reset_raw))
        retry_after = datetime.fromtimestamp(reset_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        reset_epoch = None
    if remaining > 0:
        return True, {"known": True, "remaining": remaining, "retry_after": retry_after}
    if reset_epoch is not None and reset_epoch <= int(datetime.now(timezone.utc).timestamp()):
        return True, {"known": True, "remaining": remaining, "retry_after": retry_after}
    return False, {"known": True, "remaining": remaining, "retry_after": retry_after}


def discover_games_tick(store: NflStore, bbd: BBDClient, state: Mapping[str, Any]) -> dict[str, Any]:
    cursor = dict(state)
    season_index = int(cursor.get("season_index") or 0)
    game_type_index = int(cursor.get("game_type_index") or 0)
    if season_index >= len(HISTORICAL_SEASONS):
        cursor["phase"] = "BBD_PLAYS"
        store.state_put(STATE_PK, cursor)
        return {"ok": True, "phase": "BBD_GAMES", "status": "COMPLETE"}
    season = HISTORICAL_SEASONS[season_index]
    game_type = BBD_ALLOWED_GAME_TYPES[game_type_index]
    offset = int(cursor.get("offset") or 0)
    rows, pagination, transport = bbd.list_games_page(
        season=season,
        game_type=game_type,
        limit=200,
        offset=offset,
    )
    raw = {
        "season": season,
        "game_type": game_type,
        "offset": offset,
        "data": rows,
        "pagination": pagination,
        "transport": transport.to_dict(),
    }
    raw_provenance = store.put_raw(
        provider="bbd",
        logical_key=f"nfl/games/{season}/{game_type}/offset-{offset}",
        payload=raw,
    )
    accepted = 0
    rejected = 0
    for row in rows:
        try:
            game = parse_bbd_game(row)
        except (KeyError, TypeError, ValueError) as exc:
            rejected += 1
            store.put_op(
                "BACKFILL_EXCLUSION",
                f"BBD_GAME#{season}#{game_type}#{offset}#{rejected}",
                {"reason": str(exc), "row_digest": digest(row)},
            )
            continue
        store.put_game(game, raw_provenance={**raw_provenance, "row_digest": digest(row)})
        accepted += 1
    next_offset = offset + len(rows)
    total = int(pagination.get("total") or next_offset)
    if not rows or next_offset >= total:
        cursor = _advance_discovery_cursor(cursor)
    else:
        cursor["offset"] = next_offset
    cursor.update(
        {
            "last_provider": "BBD",
            "last_transport": transport.to_dict(),
            "last_batch_accepted": accepted,
            "last_batch_rejected": rejected,
        }
    )
    store.state_put(STATE_PK, cursor)
    return {
        "ok": True,
        "phase": "BBD_GAMES",
        "season": season,
        "game_type": game_type,
        "offset": offset,
        "accepted": accepted,
        "rejected": rejected,
        "next_phase": cursor["phase"],
    }


def _game_from_item(row: Mapping[str, Any]) -> Game:
    return Game(
        game_id=str(row["game_id"]),
        season=int(row["season"]),
        week=int(row["week"]),
        game_type=str(row["game_type"]),
        kickoff_utc=str(row["kickoff_utc"]),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
        home_score=int(row["home_score"]),
        away_score=int(row["away_score"]),
        home_rest=float(row.get("home_rest") or 7.0),
        away_rest=float(row.get("away_rest") or 7.0),
        stadium=str(row.get("stadium") or "") or None,
        roof=str(row.get("roof") or "") or None,
        surface=str(row.get("surface") or "") or None,
    )


def plays_tick(store: NflStore, bbd: BBDClient, state: Mapping[str, Any]) -> dict[str, Any]:
    game_item = store.next_game_missing_stats()
    if game_item is None:
        cursor = {**dict(state), "phase": "ODDS_SNAPSHOTS"}
        store.state_put(STATE_PK, cursor)
        return {"ok": True, "phase": "BBD_PLAYS", "status": "COMPLETE"}
    game = _game_from_item(game_item)
    plays, transport = bbd.all_plays(game_id=game.game_id)
    raw_provenance = store.put_raw(
        provider="bbd",
        logical_key=f"nfl/plays/{game.season}/{game.game_id}",
        payload={"game_id": game.game_id, "data": plays, "transport": transport},
    )
    stats = aggregate_game_plays(game, plays)
    if any(row.plays < 20 for row in stats.values()):
        store.put_op(
            "BACKFILL_WARNING",
            f"LOW_PLAY_COUNT#{game.game_id}",
            {"game_id": game.game_id, "play_counts": {team: row.plays for team, row in stats.items()}},
        )
    store.put_game_stats(
        game.game_id,
        stats,
        raw_provenance=raw_provenance,
        transport=transport,
    )
    cursor = {
        **dict(state),
        "last_game_id": game.game_id,
        "last_provider": "BBD",
        "last_play_count": len(plays),
        "last_transport": transport[-1] if transport else {},
    }
    store.state_put(STATE_PK, cursor)
    return {
        "ok": True,
        "phase": "BBD_PLAYS",
        "game_id": game.game_id,
        "plays": len(plays),
        "team_stats": sorted(stats),
    }


def _find_odds_event(game: Game, events: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    matches = [
        event
        for event in events
        if strict_event_match(
            home_team=game.home_team,
            away_team=game.away_team,
            commence_time=game.kickoff_utc,
            odds_event=event,
        )
    ]
    if len(matches) > 1:
        raise ValueError("ODDS_API_AMBIGUOUS_EVENT_MATCH")
    return matches[0] if matches else None




def historical_quota_admitted(store: NflStore, settings: Settings) -> tuple[bool, dict[str, Any]]:
    state = backfill_state(store)
    transport = state.get("last_transport") or {}
    try:
        remaining = int(transport.get("requests_remaining"))
        used = int(transport.get("requests_used"))
    except (TypeError, ValueError):
        # One discovery call is needed to learn the account counters.
        return True, {"known": False}
    total = max(0, remaining + used)
    reserve = int(total * max(0.0, min(settings.shared_quota_reserve_percent, 100.0)) / 100.0)
    reserve += max(0, settings.quota_race_buffer_credits)
    return remaining > reserve, {
        "known": True,
        "remaining": remaining,
        "used": used,
        "total": total,
        "reserve": reserve,
    }

def odds_snapshot_tick(store: NflStore, odds: OddsApiClient, state: Mapping[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    if settings is not None:
        admitted, quota = historical_quota_admitted(store, settings)
        if not admitted:
            return {"ok": True, "phase": "ODDS_SNAPSHOTS", "status": "DEFERRED_SHARED_QUOTA_RESERVE", "quota": quota}
    game_item = store.next_game_missing_odds(HISTORICAL_SNAPSHOT_HORIZONS_MINUTES)
    if game_item is None:
        cursor = {**dict(state), "phase": "MATERIALIZE", "materialize_index": 0}
        store.state_put(STATE_PK, cursor)
        return {"ok": True, "phase": "ODDS_SNAPSHOTS", "status": "COMPLETE"}
    game = _game_from_item(game_item)
    existing = store.odds_for_game(game.game_id)
    horizon = next(
        value for value in HISTORICAL_SNAPSHOT_HORIZONS_MINUTES if value not in existing
    )
    requested_at = iso_utc(game.kickoff - timedelta(minutes=horizon))
    payload, transport = odds.historical_odds(snapshot_at=requested_at)
    actual_at = str(payload.get("timestamp") or requested_at)
    events = [row for row in payload.get("data") or [] if isinstance(row, Mapping)]
    event = _find_odds_event(game, events)
    if event is None:
        store.put_op(
            "BACKFILL_EXCLUSION",
            f"ODDS_EVENT_NOT_FOUND#{game.game_id}#{horizon}",
            {
                "game_id": game.game_id,
                "horizon_minutes": horizon,
                "requested_at": requested_at,
                "response_timestamp": actual_at,
                "event_count": len(events),
            },
        )
        # Store a terminal marker so one absent historical snapshot cannot stall
        # the whole corpus forever. Materialization will exclude the target row.
        consensus: dict[str, Any] = {
            "snapshot_at": actual_at,
            "missing": True,
            "moneyline": {},
            "spread": {},
            "total": {},
        }
        raw_provenance = store.put_raw(
            provider="odds-api",
            logical_key=f"nfl/historical/{game.game_id}/horizon-{horizon}",
            payload={"requested_at": requested_at, "response": payload},
        )
    else:
        consensus = snapshot_consensus(event)
        consensus["snapshot_at"] = actual_at
        consensus["requested_at"] = requested_at
        consensus["event_id"] = event.get("id")
        raw_provenance = store.put_raw(
            provider="odds-api",
            logical_key=f"nfl/historical/{game.game_id}/horizon-{horizon}",
            payload={"requested_at": requested_at, "response_timestamp": actual_at, "event": event},
        )
    store.put_odds_snapshot(
        game_id=game.game_id,
        horizon_minutes=horizon,
        snapshot_at=actual_at,
        consensus=consensus,
        raw_provenance=raw_provenance,
        transport=transport.to_dict(),
    )
    cursor = {
        **dict(state),
        "last_game_id": game.game_id,
        "last_horizon_minutes": horizon,
        "last_provider": "ODDS_API",
        "last_transport": transport.to_dict(),
    }
    store.state_put(STATE_PK, cursor)
    return {
        "ok": True,
        "phase": "ODDS_SNAPSHOTS",
        "game_id": game.game_id,
        "horizon_minutes": horizon,
        "matched": event is not None,
        "requests_remaining": transport.requests_remaining,
        "requests_used": transport.requests_used,
        "requests_last": transport.requests_last,
    }


def _stats_from_item(item: Mapping[str, Any]) -> dict[str, TeamGameStats]:
    output: dict[str, TeamGameStats] = {}
    for team, raw in (item.get("team_stats") or {}).items():
        output[str(team)] = TeamGameStats(
            team=str(raw["team"]),
            game_id=str(raw["game_id"]),
            offensive_epa=float(raw["offensive_epa"]),
            pass_epa=float(raw["pass_epa"]),
            rush_epa=float(raw["rush_epa"]),
            success_rate=float(raw["success_rate"]),
            explosive_rate=float(raw["explosive_rate"]),
            turnover_rate=float(raw["turnover_rate"]),
            early_down_pass_rate=float(raw["early_down_pass_rate"]),
            third_down_success=float(raw["third_down_success"]),
            defensive_epa_allowed=float(raw["defensive_epa_allowed"]),
            defensive_success_allowed=float(raw["defensive_success_allowed"]),
            plays=int(raw["plays"]),
        )
    return output


def materialize_tick(
    store: NflStore,
    settings: Settings,
    state: Mapping[str, Any],
    *,
    batch_size: int = 40,
) -> dict[str, Any]:
    game_items = store.list_games()
    games = [_game_from_item(row) for row in game_items]
    stats_by_game: dict[str, dict[str, TeamGameStats]] = {}
    for game in games:
        stats_item = store.get_game_item(game.game_id, "BBD_STATS")
        if stats_item:
            stats_by_game[game.game_id] = _stats_from_item(stats_item)
    rolling = pregame_team_features(games, stats_by_game)
    index = int(state.get("materialize_index") or 0)
    end = min(len(games), index + max(1, int(batch_size)))
    written = 0
    excluded = 0
    for game, game_item in zip(games[index:end], game_items[index:end]):
        stats_item = store.get_game_item(game.game_id, "BBD_STATS")
        odds_items = store.odds_for_game(game.game_id)
        snapshots = {
            horizon: {
                **dict(item.get("consensus") or {}),
                "snapshot_at": item.get("snapshot_at"),
            }
            for horizon, item in odds_items.items()
        }
        bbd_provenance = {
            "game": game_item.get("bbd_game_provenance"),
            "plays": (stats_item or {}).get("bbd_plays_provenance"),
        }
        odds_provenance = {
            str(horizon): item.get("odds_provenance")
            for horizon, item in odds_items.items()
        }
        rows, reasons = materialize_game_rows(
            game=game,
            rolling=rolling.get(game.game_id) or {},
            snapshots=snapshots,
            bbd_provenance=bbd_provenance,
            odds_provenance=odds_provenance,
            min_bookmakers=settings.min_bookmakers,
        )
        for row in rows:
            store.put_feature(row)
            written += 1
        for target, reason in reasons.items():
            excluded += 1
            store.put_op(
                "TRAINING_EXCLUSION",
                f"{game.game_id}#{target}",
                {"game_id": game.game_id, "target": target, "reason": reason},
            )
    cursor = {**dict(state), "materialize_index": end}
    if end >= len(games):
        cursor.update({"phase": "READY", "completed": True, "completed_at": now_utc()})
    store.state_put(STATE_PK, cursor)
    return {
        "ok": True,
        "phase": "MATERIALIZE",
        "start_index": index,
        "end_index": end,
        "game_count": len(games),
        "feature_rows_written": written,
        "target_rows_excluded": excluded,
        "next_phase": cursor["phase"],
    }


def historical_tick(
    *,
    store: NflStore,
    settings: Settings,
    bbd: BBDClient,
    odds: OddsApiClient,
) -> dict[str, Any]:
    if not settings.historical_backfill_enabled:
        return {"ok": False, "status": "HISTORICAL_BACKFILL_DISABLED"}
    if not store.acquire_lease("HISTORICAL_TICK"):
        return {"ok": True, "status": "LEASE_HELD", "phase": backfill_state(store).get("phase")}
    try:
        state = backfill_state(store)
        phase = str(state.get("phase") or "BBD_GAMES")
        try:
            if phase in {"BBD_GAMES", "BBD_PLAYS"}:
                admitted, quota = bbd_rate_admitted(state)
                if not admitted:
                    return {
                        "ok": True,
                        "status": "DEFERRED_BBD_RATE_LIMIT",
                        "phase": phase,
                        "quota": quota,
                    }
            if phase == "BBD_GAMES":
                return discover_games_tick(store, bbd, state)
            if phase == "BBD_PLAYS":
                return plays_tick(store, bbd, state)
            if phase == "ODDS_SNAPSHOTS":
                return odds_snapshot_tick(store, odds, state, settings)
            if phase == "MATERIALIZE":
                return materialize_tick(store, settings, state)
            if phase == "READY":
                return {"ok": True, "status": "READY", "phase": "READY", "completed": True}
            raise RuntimeError(f"NFL_BACKFILL_PHASE_INVALID:{phase}")
        except RateLimitError as exc:
            deferred = {
                **dict(state),
                "last_deferred_at": now_utc(),
                "last_deferred_reason": str(exc),
            }
            store.state_put(STATE_PK, deferred)
            return {"ok": True, "status": "DEFERRED_PROVIDER_RATE_LIMIT", "phase": phase}
        except TransientProviderError as exc:
            deferred = {
                **dict(state),
                "last_deferred_at": now_utc(),
                "last_deferred_reason": str(exc),
            }
            store.state_put(STATE_PK, deferred)
            return {"ok": False, "status": "DEFERRED_TRANSIENT_PROVIDER", "phase": phase}
    finally:
        store.release_lease("HISTORICAL_TICK")
