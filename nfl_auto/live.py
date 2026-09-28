"""Date-gated 2026 regular-season collection and immutable T-10 prediction."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .canonical import digest, iso_utc, normalize_team, now_utc, strict_event_match
from .config import (
    HISTORICAL_SNAPSHOT_HORIZONS_MINUTES,
    LIVE_SEASON,
    PUBLIC_DECISION_HORIZON_MINUTES,
    Settings,
    TARGETS,
    parse_utc,
)
from .features import (
    Game,
    TeamGameStats,
    aggregate_game_plays,
    build_inference_features,
    materialize_game_rows,
    parse_bbd_game,
    pregame_team_features,
)
from .markets import snapshot_consensus
from .model import ResidualLogisticModel
from .providers import BBDClient, OddsApiClient
from .storage import NflStore


def _game_from_item(row: Mapping[str, Any]) -> Game:
    return Game(
        game_id=str(row["game_id"]),
        season=int(row["season"]),
        week=int(row["week"]),
        game_type=str(row["game_type"]),
        kickoff_utc=str(row["kickoff_utc"]),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
        home_score=int(row.get("home_score") or 0),
        away_score=int(row.get("away_score") or 0),
        home_rest=float(row.get("home_rest") or 7.0),
        away_rest=float(row.get("away_rest") or 7.0),
        stadium=str(row.get("stadium") or "") or None,
        roof=str(row.get("roof") or "") or None,
        surface=str(row.get("surface") or "") or None,
    )


def _stats_from_item(item: Mapping[str, Any]) -> dict[str, TeamGameStats]:
    result: dict[str, TeamGameStats] = {}
    for team, raw in (item.get("team_stats") or {}).items():
        result[str(team)] = TeamGameStats(
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
    return result



def _reason_key(reason: str) -> str:
    return str(reason).split(":", 1)[0][:80]


def _count_reason(counts: dict[str, int], reason: str) -> None:
    key = _reason_key(reason)
    counts[key] = counts.get(key, 0) + 1


def _stored_kickoffs(store: NflStore) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in store.list_games():
        if int(item.get("season") or 0) != LIVE_SEASON:
            continue
        game_id = str(item.get("game_id") or "")
        kickoff = str(item.get("kickoff_utc") or "")
        if game_id and kickoff:
            found[game_id] = kickoff
    return found


def _kickoff_state(store: NflStore) -> dict[str, Any]:
    state = store.state_get("NFL_AUTO_KICKOFF_RESOLUTION") or {}
    return state if isinstance(state, Mapping) else {}


def _remembered_kickoffs(store: NflStore) -> dict[str, str]:
    games = _kickoff_state(store).get("games")
    if not isinstance(games, Mapping):
        return {}
    return {str(game_id): str(kickoff) for game_id, kickoff in games.items() if kickoff and kickoff != "UNRESOLVED"}


def _schedule_game(row: Mapping[str, Any], kickoff_utc: str | None = None) -> tuple[Game | None, str | None]:
    payload = dict(row)
    if kickoff_utc and not payload.get("kickoff_utc"):
        payload["kickoff_utc"] = kickoff_utc
    try:
        return parse_bbd_game(payload), None
    except (KeyError, TypeError, ValueError) as exc:
        return None, str(exc)


def _matching_commence(home: Any, away: Any, events: list[Mapping[str, Any]]) -> str | None:
    found: set[str] = set()
    for event in events:
        try:
            if normalize_team(event.get("home_team")) != normalize_team(home):
                continue
            if normalize_team(event.get("away_team")) != normalize_team(away):
                continue
        except (TypeError, ValueError):
            continue
        commence = str(event.get("commence_time") or "")
        if commence:
            found.add(commence)
    if len(found) == 1:
        return next(iter(found))
    return None


def _events_for_weeks(odds: OddsApiClient, pending: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    events: list[Mapping[str, Any]] = []
    by_week: dict[int, list[Mapping[str, Any]]] = {}
    for row in pending:
        try:
            week = int(row.get("week") or 0)
        except (TypeError, ValueError):
            week = 0
        by_week.setdefault(week, []).append(row)
    for group in by_week.values():
        dates = sorted(str(row.get("game_date") or "")[:10] for row in group if row.get("game_date"))
        if not dates:
            continue
        try:
            payload, _transport = odds.historical_events(snapshot_at=f"{dates[0]}T12:00:00Z")
        except Exception:
            continue
        for event in payload.get("data") or []:
            if isinstance(event, Mapping):
                events.append(event)
    try:
        live_events, _transport = odds.live_odds()
    except Exception:
        live_events = []
    events.extend(event for event in live_events if isinstance(event, Mapping))
    return events


def sync_regular_season_schedule(store: NflStore, bbd: BBDClient, odds: OddsApiClient | None = None) -> dict[str, Any]:
    accepted = 0
    rejected = 0
    pages = 0
    offset = 0
    reasons: dict[str, int] = {}
    stored = _stored_kickoffs(store)
    remembered = _remembered_kickoffs(store)
    pending: list[tuple[dict[str, Any], dict[str, str]]] = []
    while pages < 3:
        rows, pagination, transport = bbd.list_games_page(
            season=LIVE_SEASON,
            game_type="REG",
            limit=200,
            offset=offset,
        )
        raw_provenance = store.put_raw(
            provider="bbd",
            logical_key=f"nfl/live-schedule/{LIVE_SEASON}/offset-{offset}",
            payload={
                "season": LIVE_SEASON,
                "game_type": "REG",
                "data": rows,
                "pagination": pagination,
                "transport": transport.to_dict(),
            },
        )
        for row in rows:
            game_id = str(row.get("game_id") or "")
            known_kickoff = stored.get(game_id) or remembered.get(game_id)
            game, reason = _schedule_game(row, known_kickoff)
            if game is None and reason == "BBD_KICKOFF_MISSING":
                pending.append((dict(row), raw_provenance))
                continue
            if game is None or game.season != LIVE_SEASON or game.game_type != "REG":
                rejected += 1
                exclusion = reason or "NOT_LIVE_REGULAR_SEASON"
                _count_reason(reasons, exclusion)
                store.put_op(
                    "LIVE_SCHEDULE_EXCLUSION",
                    f"{offset}#{rejected}",
                    {"reason": exclusion, "row_digest": digest(row)},
                )
                continue
            store.put_game(game, raw_provenance={**raw_provenance, "row_digest": digest(row)})
            accepted += 1
        offset += len(rows)
        pages += 1
        total = int(pagination.get("total") or offset)
        if not rows or offset >= total:
            break
    events: list[Mapping[str, Any]] = []
    unmatched_sample: list[dict[str, Any]] = []
    if pending and odds is not None:
        now = datetime.now(timezone.utc)
        attempted = _kickoff_state(store).get("attempted")
        attempted = dict(attempted) if isinstance(attempted, Mapping) else {}
        due: list[tuple[dict[str, Any], dict[str, str]]] = []
        for row, raw_provenance in pending:
            game_id = str(row.get("game_id") or "")
            last = str(attempted.get(game_id) or "")
            retry = True
            if last:
                try:
                    retry = now - parse_utc(last) >= timedelta(hours=12)
                except (TypeError, ValueError):
                    retry = True
            if not retry:
                rejected += 1
                _count_reason(reasons, "BBD_KICKOFF_RETRY_WAITING")
                continue
            due.append((row, raw_provenance))
            if game_id:
                attempted[game_id] = iso_utc(now)
        events = _events_for_weeks(odds, [row for row, _provenance in due]) if due else []
        pending = due
        resolved: dict[str, str] = dict(remembered)
        for row, raw_provenance in pending:
            game_id = str(row.get("game_id") or "")
            commence = _matching_commence(row.get("home_team"), row.get("away_team"), events)
            game, reason = _schedule_game(row, commence)
            if game is None or game.season != LIVE_SEASON or game.game_type != "REG":
                rejected += 1
                exclusion = reason or "NOT_LIVE_REGULAR_SEASON"
                _count_reason(reasons, exclusion)
                if game_id:
                    resolved.pop(game_id, None)
                if len(unmatched_sample) < 5:
                    unmatched_sample.append(
                        {
                            "game_id": game_id,
                            "home": row.get("home_team"),
                            "away": row.get("away_team"),
                            "game_date": row.get("game_date"),
                            "week": row.get("week"),
                            "reason": exclusion,
                        }
                    )
                store.put_op(
                    "LIVE_SCHEDULE_EXCLUSION",
                    f"kickoff#{game_id or rejected}",
                    {"reason": exclusion, "row_digest": digest(row)},
                )
                continue
            store.put_game(
                game,
                raw_provenance={**raw_provenance, "row_digest": digest(row), "kickoff_source": "odds_api_commence_time"},
            )
            if game_id:
                resolved[game_id] = game.kickoff_utc
            accepted += 1
        store.state_put("NFL_AUTO_KICKOFF_RESOLUTION", {"games": resolved, "attempted": attempted})
    elif pending:
        for row, _raw_provenance in pending:
            rejected += 1
            _count_reason(reasons, "BBD_KICKOFF_MISSING")
    return {
        "accepted": accepted,
        "rejected": rejected,
        "pages": pages,
        "rejection_reasons": reasons,
        "odds_event_count": len(events),
        "unmatched_sample": unmatched_sample,
    }


def refresh_one_completed_game(store: NflStore, bbd: BBDClient, now: datetime) -> dict[str, Any] | None:
    for item in store.list_games():
        if int(item.get("season") or 0) != LIVE_SEASON:
            continue
        game = _game_from_item(item)
        if game.kickoff + timedelta(hours=5) > now:
            continue
        if game.home_score == 0 and game.away_score == 0:
            continue
        if store.get_game_item(game.game_id, "BBD_STATS") is not None:
            continue
        plays, transport = bbd.all_plays(game_id=game.game_id)
        if len(plays) < 40:
            store.put_op(
                "LIVE_SETTLEMENT_DEFERRED",
                f"BBD_PLAYS_NOT_READY#{game.game_id}",
                {"game_id": game.game_id, "play_count": len(plays)},
            )
            return {"game_id": game.game_id, "status": "BBD_PLAYS_NOT_READY", "plays": len(plays)}
        raw_provenance = store.put_raw(
            provider="bbd",
            logical_key=f"nfl/live-plays/{game.game_id}",
            payload={"game_id": game.game_id, "data": plays, "transport": transport},
        )
        stats = aggregate_game_plays(game, plays)
        store.put_game_stats(
            game.game_id,
            stats,
            raw_provenance=raw_provenance,
            transport=transport,
        )
        return {"game_id": game.game_id, "plays": len(plays)}
    return None


def _find_event(game: Game, events: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    matches = [
        row
        for row in events
        if strict_event_match(
            home_team=game.home_team,
            away_team=game.away_team,
            commence_time=game.kickoff_utc,
            odds_event=row,
        )
    ]
    if len(matches) > 1:
        raise ValueError("NFL_LIVE_ODDS_MATCH_AMBIGUOUS")
    return matches[0] if matches else None


def _store_historical_horizon(
    store: NflStore,
    odds: OddsApiClient,
    game: Game,
    horizon: int,
) -> dict[str, Any]:
    requested_at = iso_utc(game.kickoff - timedelta(minutes=horizon))
    payload, transport = odds.historical_odds(snapshot_at=requested_at)
    event = _find_event(
        game,
        [row for row in payload.get("data") or [] if isinstance(row, Mapping)],
    )
    if event is None:
        return {"matched": False, "game_id": game.game_id, "horizon": horizon}
    actual_at = str(payload.get("timestamp") or requested_at)
    consensus = snapshot_consensus(event)
    consensus.update(
        {"snapshot_at": actual_at, "requested_at": requested_at, "event_id": event.get("id")}
    )
    provenance = store.put_raw(
        provider="odds-api",
        logical_key=f"nfl/live-history/{game.game_id}/horizon-{horizon}",
        payload={"requested_at": requested_at, "response_timestamp": actual_at, "event": event},
    )
    store.put_odds_snapshot(
        game_id=game.game_id,
        horizon_minutes=horizon,
        snapshot_at=actual_at,
        consensus=consensus,
        raw_provenance=provenance,
        transport=transport.to_dict(),
    )
    return {
        "matched": True,
        "game_id": game.game_id,
        "horizon": horizon,
        "requests_remaining": transport.requests_remaining,
    }


def capture_market_snapshots(
    store: NflStore,
    odds: OddsApiClient,
    games: list[Game],
    now: datetime,
) -> dict[str, Any]:
    events, transport = odds.live_odds()
    board_provenance = store.put_raw(
        provider="odds-api",
        logical_key=f"nfl/live-board/{iso_utc(now).replace(':', '-')}",
        payload={"captured_at": iso_utc(now), "data": events, "transport": transport.to_dict()},
    )
    captured_t10: list[str] = []
    for game in games:
        minutes_before = (game.kickoff - now).total_seconds() / 60.0
        if not 10.0 <= minutes_before <= 16.0:
            continue
        event = _find_event(game, events)
        if event is None:
            continue
        consensus = snapshot_consensus(event)
        consensus.update(
            {"snapshot_at": iso_utc(now), "requested_at": iso_utc(now), "event_id": event.get("id")}
        )
        store.put_odds_snapshot(
            game_id=game.game_id,
            horizon_minutes=PUBLIC_DECISION_HORIZON_MINUTES,
            snapshot_at=iso_utc(now),
            consensus=consensus,
            raw_provenance={**board_provenance, "event_digest": digest(event)},
            transport=transport.to_dict(),
        )
        captured_t10.append(game.game_id)

    # Backfill at most one already-elapsed T-24h/T-60 horizon per invocation.
    historical_backfill: dict[str, Any] | None = None
    for game in sorted(games, key=lambda row: row.kickoff):
        if game.kickoff <= now:
            continue
        existing = store.odds_for_game(game.game_id)
        for horizon in (1440, 60):
            if horizon in existing:
                continue
            desired = game.kickoff - timedelta(minutes=horizon)
            if now >= desired:
                historical_backfill = _store_historical_horizon(store, odds, game, horizon)
                break
        if historical_backfill is not None:
            break
    return {
        "live_event_count": len(events),
        "captured_t10": captured_t10,
        "historical_horizon": historical_backfill,
        "transport": transport.to_dict(),
    }



def materialize_one_settled_game(
    store: NflStore,
    settings: Settings,
    now: datetime,
) -> dict[str, Any] | None:
    game_items = store.list_games()
    games = [_game_from_item(row) for row in game_items]
    stats_by_game: dict[str, dict[str, TeamGameStats]] = {}
    for game in games:
        item = store.get_game_item(game.game_id, "BBD_STATS")
        if item:
            stats_by_game[game.game_id] = _stats_from_item(item)
    rolling = pregame_team_features(games, stats_by_game)
    for game, game_item in zip(games, game_items):
        if game.season != LIVE_SEASON or game.game_type != "REG":
            continue
        if game.kickoff + timedelta(hours=5) > now:
            continue
        stats_item = store.get_game_item(game.game_id, "BBD_STATS")
        if not stats_item:
            continue
        if not store.odds_for_game(game.game_id).get(10):
            continue
        if all(
            store.feature_for_game(target, game.kickoff_utc, game.game_id) is not None
            for target in TARGETS
        ):
            continue
        odds_items = store.odds_for_game(game.game_id)
        snapshots = {
            horizon: {**dict(item.get("consensus") or {}), "snapshot_at": item.get("snapshot_at")}
            for horizon, item in odds_items.items()
        }
        rows, reasons = materialize_game_rows(
            game=game,
            rolling=rolling.get(game.game_id) or {},
            snapshots=snapshots,
            bbd_provenance={
                "game": game_item.get("bbd_game_provenance"),
                "plays": stats_item.get("bbd_plays_provenance"),
            },
            odds_provenance={
                str(horizon): item.get("odds_provenance")
                for horizon, item in odds_items.items()
            },
            min_bookmakers=settings.min_bookmakers,
        )
        for row in rows:
            if store.feature_for_game(row.target, row.kickoff_utc, row.event_key) is None:
                store.put_feature(row)
        for target, reason in reasons.items():
            store.put_op(
                "LIVE_TRAINING_EXCLUSION",
                f"{game.game_id}#{target}",
                {"game_id": game.game_id, "target": target, "reason": reason},
            )
        return {
            "game_id": game.game_id,
            "feature_rows_written": len(rows),
            "excluded": reasons,
        }
    return None

def _selection(target: str, probability: float, line: float, game: Game) -> str:
    positive = probability >= 0.5
    if target == "moneyline_home_win":
        return game.home_team if positive else game.away_team
    if target == "spread_home_cover":
        team = game.home_team if positive else game.away_team
        selected_line = line if positive else -line
        return f"{team} {selected_line:+g}"
    if target == "total_over":
        return f"{'OVER' if positive else 'UNDER'} {line:g}"
    raise ValueError("NFL_TARGET_UNSUPPORTED")


def create_t10_predictions(store: NflStore, settings: Settings, games: list[Game]) -> dict[str, Any]:
    all_games = [_game_from_item(row) for row in store.list_games()]
    stats_by_game: dict[str, dict[str, TeamGameStats]] = {}
    for game in all_games:
        item = store.get_game_item(game.game_id, "BBD_STATS")
        if item:
            stats_by_game[game.game_id] = _stats_from_item(item)
    rolling = pregame_team_features(all_games, stats_by_game)
    created: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for game in games:
        odds_items = store.odds_for_game(game.game_id)
        if 10 not in odds_items:
            continue
        snapshots = {
            horizon: {**dict(item.get("consensus") or {}), "snapshot_at": item.get("snapshot_at")}
            for horizon, item in odds_items.items()
        }
        for target in TARGETS:
            if store.prediction(game.game_id, target) is not None:
                continue
            champion = store.champion(target)
            if not champion:
                blocked.append({"game_id": game.game_id, "target": target, "reason": "NO_HISTORICAL_CHAMPION"})
                continue
            try:
                features, prior, line = build_inference_features(
                    game=game,
                    rolling=rolling.get(game.game_id) or {},
                    snapshots=snapshots,
                    target=target,
                    min_bookmakers=settings.min_bookmakers,
                )
                model = ResidualLogisticModel.from_dict(champion["model"])
                probability = model.predict_probability(features, prior)
                snapshot_at = str(snapshots[10]["snapshot_at"])
                payload = {
                    "event_key": game.game_id,
                    "season": game.season,
                    "week": game.week,
                    "kickoff_utc": game.kickoff_utc,
                    "target": target,
                    "decision_horizon_minutes": PUBLIC_DECISION_HORIZON_MINUTES,
                    "decision_time": snapshot_at,
                    "model_digest": champion["model_digest"],
                    "market_prior": prior,
                    "probability": probability,
                    "line": line,
                    "selection": _selection(target, probability, line, game),
                    "feature_hash": digest(
                        {
                            "target": target,
                            "event_key": game.game_id,
                            "features": features,
                            "prior": prior,
                            "model_digest": champion["model_digest"],
                        }
                    ),
                    "frozen_features": list(features),
                    "authority_state": "IMMUTABLE_T10_PUBLICATION",
                    "regular_season_only": True,
                }
                store.put_prediction(payload)
                created.append(
                    {
                        "game_id": game.game_id,
                        "target": target,
                        "selection": payload["selection"],
                        "probability": probability,
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                blocked.append({"game_id": game.game_id, "target": target, "reason": str(exc)})
    return {"created": created, "blocked": blocked}


def live_tick(
    *,
    store: NflStore,
    settings: Settings,
    bbd: BBDClient,
    odds: OddsApiClient,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not settings.live_collection_allowed(current):
        return {
            "ok": True,
            "status": "HISTORICAL_ONLY",
            "live_collection_start_utc": settings.live_collection_start_utc,
            "now": iso_utc(current),
            "preseason_predictions": 0,
        }
    if not store.acquire_lease("LIVE_TICK", ttl_seconds=240):
        return {"ok": True, "status": "LEASE_HELD"}
    try:
        schedule = sync_regular_season_schedule(store, bbd, odds)
        games = [
            _game_from_item(row)
            for row in store.list_games()
            if int(row.get("season") or 0) == LIVE_SEASON and str(row.get("game_type")) == "REG"
        ]
        completed_refresh = refresh_one_completed_game(store, bbd, current)
        settled_materialization = materialize_one_settled_game(store, settings, current)
        market = capture_market_snapshots(store, odds, games, current)
        predictions = create_t10_predictions(store, settings, games)
        result = {
            "ok": True,
            "status": "REGULAR_SEASON_LIVE",
            "now": iso_utc(current),
            "schedule": schedule,
            "completed_refresh": completed_refresh,
            "settled_materialization": settled_materialization,
            "market": market,
            "predictions": predictions,
            "preseason_predictions": 0,
        }
        store.put_op("LIVE_TICK", iso_utc(current), result)
        return result
    finally:
        store.release_lease("LIVE_TICK")
