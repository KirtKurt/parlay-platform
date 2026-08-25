#!/usr/bin/env python3
"""Recover NFL historical game metadata without inventing kickoff timestamps.

BBD supplies historical NFL results/statistics but its current game payload omits an
exact kickoff timestamp and sometimes abbreviates Los Angeles as ``LA``. Historical
model features require a point-in-time kickoff anchor for T-1440/T-60/T-10 odds
snapshots, so guessing a time or team would create leakage and corrupt chronology.

This repair joins each BBD REG/POST result to The Odds API *historical events*
endpoint at pregame-safe schedule snapshots. The Odds API event supplies the exact
commence_time plus unambiguous home/away team names. Only a unique, role-correct
match is admitted. The script prepares the entire corpus before changing the
backfill cursor, writes only the dedicated NFL Auto historical tables/bucket, and
never touches predictions, champions, other sports, or production authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

import boto3

from nfl_auto.canonical import digest, normalize_team, now_utc
from nfl_auto.config import BBD_ALLOWED_GAME_TYPES, HISTORICAL_SEASONS, Settings, parse_utc
from nfl_auto.features import Game, parse_bbd_game
from nfl_auto.historical import STATE_PK, backfill_state
from nfl_auto.providers import BBDClient, OddsApiClient
from nfl_auto.storage import NflStore

VERSION = "NFL-HISTORICAL-GAME-METADATA-RECOVERY-v1"
SAFE_ANCHOR_HOURS_UTC = (23, 11)
MIN_ACCEPTED_GAMES = 1000
MAX_REJECTION_FRACTION = 0.20


def _game_date(row: Mapping[str, Any]) -> date:
    raw = row.get("game_date") or row.get("gameday") or row.get("date")
    if raw in (None, ""):
        raise ValueError("BBD_GAME_DATE_MISSING")
    text = str(raw).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return parse_utc(text).date()
        except (TypeError, ValueError):
            raise ValueError("BBD_GAME_DATE_INVALID") from None


def safe_schedule_anchors(game_date: date) -> tuple[str, str]:
    """Return two pregame-safe discovery snapshots for an NFL local game date.

    The prior-day 23:55Z snapshot is safely before every NFL game on the following
    local calendar date. The same-day 11:55Z snapshot remains before even the
    earliest modern London/Germany kickoff and improves recall when an event was
    not yet listed the prior evening.
    """

    previous = datetime.combine(
        game_date - timedelta(days=1), time(23, 55), tzinfo=timezone.utc
    )
    same_day = datetime.combine(game_date, time(11, 55), tzinfo=timezone.utc)
    return (
        previous.isoformat(timespec="seconds").replace("+00:00", "Z"),
        same_day.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _optional_team(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return normalize_team(value)
    except (TypeError, ValueError):
        return None


def _unknown_role_allowed(raw: Any, canonical: str) -> bool:
    text = str(raw or "").strip().upper().replace(".", "")
    if text == "LA":
        return canonical in {"LAR", "LAC"}
    return False


def match_schedule_event(
    row: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Return one unique Odds API schedule event for a BBD game row.

    Known BBD team identities must match exactly by home/away role. The only
    intentionally tolerated unresolved alias is ``LA`` and it is resolved solely
    when the known opponent plus home/away role yields one unique LAR/LAC event.
    """

    raw_home = row.get("home_team") or row.get("home")
    raw_away = row.get("away_team") or row.get("away")
    known_home = _optional_team(raw_home)
    known_away = _optional_team(raw_away)
    if known_home is None and not _unknown_role_allowed(raw_home, "LAR"):
        raise ValueError(f"NFL_TEAM_UNRECOGNIZED:{str(raw_home)[:80]}")
    if known_away is None and not _unknown_role_allowed(raw_away, "LAR"):
        raise ValueError(f"NFL_TEAM_UNRECOGNIZED:{str(raw_away)[:80]}")
    if known_home is None and known_away is None:
        raise ValueError("ODDS_API_SCHEDULE_MATCH_INSUFFICIENT_TEAM_IDENTITY")

    bbd_date = _game_date(row)
    candidates: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    for event in events:
        try:
            event_home = normalize_team(event.get("home_team"))
            event_away = normalize_team(event.get("away_team"))
            commence = parse_utc(str(event.get("commence_time") or ""))
        except (TypeError, ValueError):
            continue
        if known_home is not None and event_home != known_home:
            continue
        if known_away is not None and event_away != known_away:
            continue
        if known_home is None and not _unknown_role_allowed(raw_home, event_home):
            continue
        if known_away is None and not _unknown_role_allowed(raw_away, event_away):
            continue
        # NFL games on a US/local BBD date can commence on that UTC date or the
        # following UTC date (prime-time games). Anything else is not this row.
        if commence.date() not in {bbd_date, bbd_date + timedelta(days=1)}:
            continue
        key = (
            str(event.get("id") or ""),
            event_home,
            event_away,
            commence.isoformat(),
        )
        candidates[key] = event

    if not candidates:
        raise ValueError("ODDS_API_HISTORICAL_SCHEDULE_EVENT_NOT_FOUND")
    if len(candidates) != 1:
        raise ValueError("ODDS_API_HISTORICAL_SCHEDULE_EVENT_AMBIGUOUS")
    return next(iter(candidates.values()))


def augment_bbd_row(row: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(row)
    output["kickoff_utc"] = str(event.get("commence_time") or "")
    output["home_team"] = str(event.get("home_team") or "")
    output["away_team"] = str(event.get("away_team") or "")
    # parse_bbd_game remains the canonical admission authority and validates
    # season/week/type/teams/scores after this schedule-only augmentation.
    return output


def _stack_function_name(cf: Any, stack_name: str) -> str:
    response = cf.describe_stack_resource(
        StackName=stack_name, LogicalResourceId="NflAutonomousFunction"
    )
    value = ((response.get("StackResourceDetail") or {}).get("PhysicalResourceId"))
    if not value:
        raise RuntimeError("NFL_AUTONOMOUS_FUNCTION_NOT_FOUND")
    return str(value)


def load_runtime_environment(*, region: str, stack_name: str) -> tuple[Settings, NflStore]:
    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    function_name = _stack_function_name(cf, stack_name)
    config = lam.get_function_configuration(FunctionName=function_name)
    env = ((config.get("Environment") or {}).get("Variables") or {})
    required_prefix = "NFL_AUTO_"
    for key, value in env.items():
        if key.startswith(required_prefix):
            os.environ[key] = str(value)
    os.environ["AWS_REGION"] = region
    settings = Settings.from_env()
    return settings, NflStore(settings)


def _season_rows(bbd: BBDClient, season: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the season surface without BBD's currently inconsistent POST filter."""

    offset = 0
    rows: list[dict[str, Any]] = []
    transports: list[dict[str, Any]] = []
    while True:
        payload, meta = bbd._get(  # noqa: SLF001 - isolated contract repair.
            "/v1/nfl/games",
            {"season": int(season), "limit": 200, "offset": offset},
        )
        page = payload.get("data") or []
        if not isinstance(page, list):
            raise RuntimeError("BBD_NFL_GAMES_DATA_INVALID")
        transports.append(meta.to_dict())
        accepted_page = [
            dict(row)
            for row in page
            if isinstance(row, Mapping)
            and str(row.get("game_type") or "").upper() in BBD_ALLOWED_GAME_TYPES
        ]
        rows.extend(accepted_page)
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), Mapping) else {}
        offset += len(page)
        total = int(pagination.get("total") or offset)
        if not page or offset >= total:
            break
    return rows, transports


def _schedule_cache_entry(
    *,
    store: NflStore,
    odds: OddsApiClient,
    game_date: date,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    events_by_key: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    for anchor in safe_schedule_anchors(game_date):
        payload, transport = odds.historical_events(snapshot_at=anchor)
        payload_events = [
            dict(row) for row in (payload.get("data") or []) if isinstance(row, Mapping)
        ]
        observations.append(
            {
                "requested_at": anchor,
                "response_timestamp": payload.get("timestamp"),
                "transport": transport.to_dict(),
                "events": payload_events,
            }
        )
        for event in payload_events:
            key = (
                str(event.get("id") or ""),
                str(event.get("home_team") or ""),
                str(event.get("away_team") or ""),
                str(event.get("commence_time") or ""),
            )
            events_by_key[key] = event
    raw_ref = store.put_raw(
        provider="odds-api",
        logical_key=f"nfl/historical-schedule/{game_date.isoformat()}",
        payload={
            "authority": "THE_ODDS_API_HISTORICAL_EVENTS",
            "pregame_safe_anchors": list(safe_schedule_anchors(game_date)),
            "observations": observations,
        },
    )
    return {"events": list(events_by_key.values()), "raw_ref": raw_ref}


def recover(*, region: str, stack_name: str) -> dict[str, Any]:
    settings, store = load_runtime_environment(region=region, stack_name=stack_name)
    before = backfill_state(store)
    existing_games = store.list_games()
    champions = {target: store.champion(target) for target in ("moneyline_home_win", "spread_home_cover", "total_over")}
    prediction_count = sum(1 for _ in store.scan_all(store.predictions))
    feature_count = sum(len(store.feature_rows(target)) for target in champions)

    if any(champions.values()):
        raise RuntimeError("NFL_METADATA_RECOVERY_REFUSES_EXISTING_CHAMPION")
    if prediction_count:
        raise RuntimeError("NFL_METADATA_RECOVERY_REFUSES_EXISTING_PREDICTIONS")
    if feature_count:
        raise RuntimeError("NFL_METADATA_RECOVERY_REFUSES_EXISTING_FEATURE_ROWS")
    if existing_games:
        # A rerun is safe only when previously written game metadata is internally
        # consistent. The repair will compare each duplicate before skipping it.
        allowed_existing = {str(row.get("game_id")) for row in existing_games}
    else:
        allowed_existing = set()

    bbd = BBDClient(secret_arn=settings.bbd_secret_arn)
    odds = OddsApiClient(secret_arn=settings.odds_secret_arn)
    prepared: list[tuple[Game, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
    rejected = Counter()
    season_counts: dict[str, int] = {}
    schedule_cache: dict[date, dict[str, Any]] = {}

    for season in HISTORICAL_SEASONS:
        rows, transports = _season_rows(bbd, season)
        season_counts[str(season)] = len(rows)
        bbd_ref = store.put_raw(
            provider="bbd",
            logical_key=f"nfl/recovery/games/{season}",
            payload={
                "season": season,
                "eligible_game_types": list(BBD_ALLOWED_GAME_TYPES),
                "data": rows,
                "transports": transports,
            },
        )
        for row in rows:
            try:
                row_date = _game_date(row)
                if row_date not in schedule_cache:
                    schedule_cache[row_date] = _schedule_cache_entry(
                        store=store, odds=odds, game_date=row_date
                    )
                schedule = schedule_cache[row_date]
                event = match_schedule_event(row, schedule["events"])
                game = parse_bbd_game(augment_bbd_row(row, event))
                prepared.append((game, row, bbd_ref, {"event": event, **schedule}))
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                rejected[str(exc)] += 1

    total_considered = len(prepared) + sum(rejected.values())
    rejection_fraction = (sum(rejected.values()) / total_considered) if total_considered else 1.0
    if len(prepared) < MIN_ACCEPTED_GAMES:
        raise RuntimeError(
            f"NFL_METADATA_RECOVERY_ACCEPTED_BELOW_FLOOR:{len(prepared)}<{MIN_ACCEPTED_GAMES}"
        )
    if rejection_fraction > MAX_REJECTION_FRACTION:
        raise RuntimeError(
            f"NFL_METADATA_RECOVERY_REJECTION_FRACTION_TOO_HIGH:{rejection_fraction:.4f}"
        )

    written = 0
    idempotent = 0
    for game, row, bbd_ref, schedule in prepared:
        existing = store.get_game_item(game.game_id, "META")
        if existing:
            expected = (game.kickoff_utc, game.home_team, game.away_team, game.season, game.week)
            observed = (
                str(existing.get("kickoff_utc")),
                str(existing.get("home_team")),
                str(existing.get("away_team")),
                int(existing.get("season") or 0),
                int(existing.get("week") or 0),
            )
            if observed != expected:
                raise RuntimeError(f"NFL_METADATA_RECOVERY_EXISTING_GAME_CONFLICT:{game.game_id}")
            idempotent += 1
            continue
        event = schedule["event"]
        schedule_ref = schedule["raw_ref"]
        store.put_game(
            game,
            raw_provenance={
                "bbd": dict(bbd_ref),
                "bbd_row_sha256": digest(row),
                "kickoff_authority": {
                    "provider": "THE_ODDS_API_HISTORICAL_EVENTS",
                    "raw": dict(schedule_ref),
                    "event_id": event.get("id"),
                    "event_sha256": digest(event),
                    "commence_time": event.get("commence_time"),
                    "home_team": event.get("home_team"),
                    "away_team": event.get("away_team"),
                    "pregame_safe": True,
                },
            },
        )
        written += 1

    after_count = len(store.list_games())
    if after_count < MIN_ACCEPTED_GAMES:
        raise RuntimeError("NFL_METADATA_RECOVERY_PERSISTED_COUNT_BELOW_FLOOR")

    repaired_state = {
        "phase": "BBD_PLAYS",
        "season_index": 0,
        "game_type_index": 0,
        "offset": 0,
        "materialize_index": 0,
        "completed": False,
        "started_at": str(before.get("started_at") or now_utc()),
        "metadata_recovery_version": VERSION,
        "metadata_recovery_completed_at": now_utc(),
        "metadata_recovery_game_count": after_count,
        "metadata_recovery_rejected": int(sum(rejected.values())),
    }
    store.state_put(STATE_PK, repaired_state)
    final_state = backfill_state(store)
    return {
        "ok": True,
        "proofType": "NFL_HISTORICAL_GAME_METADATA_RECOVERY",
        "version": VERSION,
        "at": now_utc(),
        "stack": stack_name,
        "beforeState": before,
        "afterState": final_state,
        "seasonEligibleCounts": season_counts,
        "scheduleDateCount": len(schedule_cache),
        "preparedGameCount": len(prepared),
        "persistedGameCount": after_count,
        "writtenGameCount": written,
        "idempotentGameCount": idempotent,
        "rejectedCount": int(sum(rejected.values())),
        "rejectionFraction": rejection_fraction,
        "rejectionReasons": dict(rejected.most_common(20)),
        "productionAuthorityChanged": False,
        "immutablePredictionHistoryRewritten": False,
        "postStartPredictionCreated": False,
        "humanWinnerSelection": False,
        "directOtherSportWrite": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack-name", default="parlay-platform-nfl-auto")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = recover(region=args.region, stack_name=args.stack_name)
    except Exception as exc:
        result = {
            "ok": False,
            "proofType": "NFL_HISTORICAL_GAME_METADATA_RECOVERY",
            "version": VERSION,
            "at": now_utc(),
            "error": f"{type(exc).__name__}:{exc}",
            "productionAuthorityChanged": False,
            "immutablePredictionHistoryRewritten": False,
            "postStartPredictionCreated": False,
            "humanWinnerSelection": False,
            "directOtherSportWrite": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
