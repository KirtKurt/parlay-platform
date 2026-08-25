from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Mapping

import boto3
from boto3.dynamodb.conditions import Attr

from handler import predict, settle, status

TABLE_NAME = os.environ["TENNIS_LEARNING_TABLE"]
ODDS_API_KEY = os.environ["ODDS_API_KEY"]
BASE_URL = os.getenv("TENNIS_ODDS_BASE_URL", "https://api.the-odds-api.com/v4")
# Evaluate every current bookmaker region that can carry match-winner H2H odds.
# us_dfs is intentionally reported separately because it is a DFS/player-prop
# region rather than a two-sided match-winner H2H region.
DEFAULT_H2H_REGIONS = "us,us2,uk,eu,au,fr,se,us_ex"
REGIONS = tuple(
    dict.fromkeys(
        x.strip()
        for x in os.getenv("TENNIS_ODDS_REGIONS", DEFAULT_H2H_REGIONS).split(",")
        if x.strip()
    )
)
NON_H2H_PROVIDER_REGIONS = ("us_dfs",)
PREDICTION_CUTOFF_MINUTES = max(
    0, int(os.getenv("TENNIS_PREDICTION_CUTOFF_MINUTES", "10"))
)
table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _get(path: str, params: Mapping[str, Any]) -> Any:
    query = dict(params)
    query["apiKey"] = ODDS_API_KEY
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "inqis-tennis-learning/1.5"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _decimal(value: Any) -> Decimal:
    number = _finite(value)
    if number is None:
        raise ValueError(f"non-finite numeric value: {value!r}")
    return Decimal(str(number))


def _parse_utc(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _deadline(commence_time: Any) -> datetime:
    return _parse_utc(commence_time) - timedelta(minutes=PREDICTION_CUTOFF_MINUTES)


def _discover_tennis_keys() -> list[str]:
    configured = [
        x.strip()
        for x in os.getenv("TENNIS_ODDS_SPORT_KEYS", "").split(",")
        if x.strip() and x.strip() != "tennis"
    ]
    if configured:
        return sorted(set(configured))

    sports = _get("/sports/", {"all": "false"})
    keys: list[str] = []
    for sport in sports if isinstance(sports, list) else []:
        key = str(sport.get("key") or "")
        if (
            key.startswith("tennis_")
            and str(sport.get("group") or "").lower() == "tennis"
            and bool(sport.get("active", False))
            and not bool(sport.get("has_outrights", False))
        ):
            keys.append(key)
    return sorted(set(keys))


def _best_h2h(event: Mapping[str, Any]) -> tuple[float, float] | None:
    names = [
        str(event.get("home_team") or ""),
        str(event.get("away_team") or ""),
    ]
    if not all(names) or names[0] == names[1]:
        return None
    prices: Dict[str, list[float]] = {names[0]: [], names[1]: []}
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes") or []:
                name = str(outcome.get("name") or "")
                price = _finite(outcome.get("price"))
                if name in prices and price is not None and price != 0:
                    prices[name].append(price)
    if not prices[names[0]] or not prices[names[1]]:
        return None
    pair = max(prices[names[0]]), max(prices[names[1]])
    return pair if all(math.isfinite(x) and x != 0 for x in pair) else None


def _coverage_write(
    *,
    event_id: str,
    sport_key: str,
    player: str,
    opponent: str,
    commence_time: str,
    evaluation_status: str,
    now: str,
    reason: str | None = None,
    bookmaker_count: int = 0,
) -> None:
    item: Dict[str, Any] = {
        "PK": f"COVERAGE#{event_id}",
        "SK": "LATEST",
        "event_id": event_id,
        "sport_key": sport_key,
        "player": player,
        "opponent": opponent,
        "commence_time": commence_time,
        "evaluation_status": evaluation_status,
        "evaluated_regions": list(REGIONS),
        "non_h2h_provider_regions": list(NON_H2H_PROVIDER_REGIONS),
        "bookmaker_count": bookmaker_count,
        "prediction_cutoff_minutes": PREDICTION_CUTOFF_MINUTES,
        "updated_at": now,
        "source": "the-odds-api-v4",
    }
    if reason:
        item["reason"] = reason
    table.put_item(Item=item)


def _merge_odds_event(target: Dict[str, Any], source: Mapping[str, Any]) -> None:
    existing = target.setdefault("bookmakers", [])
    seen = {
        (str(book.get("key") or ""), str(book.get("title") or ""))
        for book in existing
    }
    for book in source.get("bookmakers") or []:
        identity = (str(book.get("key") or ""), str(book.get("title") or ""))
        if identity in seen:
            # The same bookmaker can appear through more than one region alias.
            # Preserve the first full payload rather than double-counting it.
            continue
        existing.append(book)
        seen.add(identity)


def collect_live() -> Dict[str, Any]:
    discovered = _discover_tennis_keys()
    if not discovered:
        raise RuntimeError("no active non-outright tennis sport keys discovered")

    request_errors: Dict[str, str] = {}
    inventory: Dict[str, Dict[str, Any]] = {}
    key_event_counts: Dict[str, int] = {}

    # /events is the authoritative inventory. It prevents the odds response from
    # silently defining the slate and lets us account for every provider-listed match.
    for sport_key in discovered:
        try:
            events = _get(f"/sports/{sport_key}/events", {"dateFormat": "iso"})
        except Exception as exc:
            request_errors[f"events:{sport_key}"] = str(exc)
            continue
        if not isinstance(events, list):
            request_errors[f"events:{sport_key}"] = "unexpected events response"
            continue
        key_event_counts[sport_key] = len(events)
        for event in events:
            event_id = str(event.get("id") or "")
            if not event_id:
                request_errors[f"inventory:{sport_key}:{len(inventory)}"] = (
                    "provider event missing event id"
                )
                continue
            candidate = dict(event)
            candidate["sport_key"] = sport_key
            candidate["bookmakers"] = []
            inventory[event_id] = candidate

    eventful_keys = sorted(
        sport_key for sport_key, count in key_event_counts.items() if count > 0
    )

    # Query every H2H bookmaker region independently. Any regional request error
    # fails the cycle closed so partial regional coverage can never be reported green.
    regional_successes = 0
    regional_requests = 0
    for sport_key in eventful_keys:
        for region in REGIONS:
            regional_requests += 1
            try:
                events = _get(
                    f"/sports/{sport_key}/odds",
                    {
                        "regions": region,
                        "markets": "h2h",
                        "oddsFormat": "american",
                        "dateFormat": "iso",
                    },
                )
                regional_successes += 1
            except Exception as exc:
                request_errors[f"odds:{sport_key}:{region}"] = str(exc)
                continue
            if not isinstance(events, list):
                request_errors[f"odds:{sport_key}:{region}"] = (
                    "unexpected odds response"
                )
                continue
            for event in events:
                event_id = str(event.get("id") or "")
                if event_id not in inventory:
                    request_errors[f"odds_inventory_mismatch:{sport_key}:{region}:{event_id}"] = (
                        "odds event absent from provider event inventory"
                    )
                    continue
                _merge_odds_event(inventory[event_id], event)

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    stored = predicted = no_h2h = malformed = deadline_skipped = 0
    prediction_errors = 0
    resolved = 0
    bookmaker_total = 0

    for event_id, event in inventory.items():
        sport_key = str(event.get("sport_key") or "")
        player = str(event.get("home_team") or "")
        opponent = str(event.get("away_team") or "")
        commence_time = str(event.get("commence_time") or "")
        bookmakers = event.get("bookmakers") or []
        bookmaker_count = len(bookmakers)
        bookmaker_total += bookmaker_count

        if not player or not opponent or player == opponent or not commence_time:
            malformed += 1
            resolved += 1
            try:
                _coverage_write(
                    event_id=event_id,
                    sport_key=sport_key,
                    player=player,
                    opponent=opponent,
                    commence_time=commence_time,
                    evaluation_status="MALFORMED_PROVIDER_EVENT",
                    reason="missing/invalid participants or commence_time",
                    bookmaker_count=bookmaker_count,
                    now=now,
                )
            except Exception as exc:
                request_errors[f"coverage_write:{event_id}"] = str(exc)
            continue

        try:
            deadline = _deadline(commence_time)
        except ValueError as exc:
            malformed += 1
            resolved += 1
            try:
                _coverage_write(
                    event_id=event_id,
                    sport_key=sport_key,
                    player=player,
                    opponent=opponent,
                    commence_time=commence_time,
                    evaluation_status="MALFORMED_PROVIDER_EVENT",
                    reason=f"invalid commence_time: {exc}",
                    bookmaker_count=bookmaker_count,
                    now=now,
                )
            except Exception as write_exc:
                request_errors[f"coverage_write:{event_id}"] = str(write_exc)
            continue

        if now_dt > deadline:
            deadline_skipped += 1
            resolved += 1
            _coverage_write(
                event_id=event_id,
                sport_key=sport_key,
                player=player,
                opponent=opponent,
                commence_time=commence_time,
                evaluation_status="T10_CLOSED",
                reason="prediction window closed; immutable T-10 rule preserved",
                bookmaker_count=bookmaker_count,
                now=now,
            )
            continue

        pair = _best_h2h(event)
        if pair is None:
            no_h2h += 1
            resolved += 1
            _coverage_write(
                event_id=event_id,
                sport_key=sport_key,
                player=player,
                opponent=opponent,
                commence_time=commence_time,
                evaluation_status="NO_TWO_SIDED_H2H",
                reason="all H2H bookmaker regions evaluated; no two-sided price available",
                bookmaker_count=bookmaker_count,
                now=now,
            )
            continue

        signals = {
            "player_odds": pair[0],
            "opponent_odds": pair[1],
            "elo_diff": 0.0,
            "surface_elo_diff": 0.0,
            "recent_win_rate_diff": 0.0,
            "serve_points_won_diff": 0.0,
            "return_points_won_diff": 0.0,
            "break_points_saved_diff": 0.0,
            "rest_days_diff": 0.0,
            "best_of_five": False,
        }
        try:
            safe_signals = {
                k: (
                    _decimal(v)
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                    else v
                )
                for k, v in signals.items()
            }
            table.put_item(
                Item={
                    "PK": f"LIVE#{event_id}",
                    "SK": "LATEST",
                    "event_id": event_id,
                    "sport_key": sport_key,
                    "player": player,
                    "opponent": opponent,
                    "commence_time": commence_time,
                    "signals": safe_signals,
                    "source": "the-odds-api-v4-all-h2h-regions",
                    "updated_at": now,
                    "prediction_cutoff_minutes": PREDICTION_CUTOFF_MINUTES,
                    "evaluated_regions": list(REGIONS),
                    "bookmaker_count": bookmaker_count,
                }
            )
            predict(
                {
                    "match_id": event_id,
                    "player": player,
                    "opponent": opponent,
                    "signals": signals,
                }
            )
            _coverage_write(
                event_id=event_id,
                sport_key=sport_key,
                player=player,
                opponent=opponent,
                commence_time=commence_time,
                evaluation_status="PREDICTED_T10_COMPLIANT",
                bookmaker_count=bookmaker_count,
                now=now,
            )
        except Exception as exc:
            prediction_errors += 1
            request_errors[f"prediction:{event_id}"] = str(exc)
            try:
                _coverage_write(
                    event_id=event_id,
                    sport_key=sport_key,
                    player=player,
                    opponent=opponent,
                    commence_time=commence_time,
                    evaluation_status="PREDICTION_ERROR",
                    reason=str(exc),
                    bookmaker_count=bookmaker_count,
                    now=now,
                )
            except Exception as write_exc:
                request_errors[f"coverage_write:{event_id}"] = str(write_exc)
            resolved += 1
            continue
        stored += 1
        predicted += 1
        resolved += 1

    total_events = len(inventory)
    coverage_complete = (
        total_events > 0
        and resolved == total_events
        and malformed == 0
        and prediction_errors == 0
        and not request_errors
        and regional_successes == regional_requests
    )

    summary = {
        "coverage_contract": "ALL_ACTIVE_NON_OUTRIGHT_MATCHES_ALL_H2H_BOOKMAKER_REGIONS",
        "h2h_regions": list(REGIONS),
        "non_h2h_provider_regions": list(NON_H2H_PROVIDER_REGIONS),
        "sport_keys_truncated": 0,
        "discovered_sport_keys": len(discovered),
        "eventful_sport_keys": len(eventful_keys),
        "regional_requests": regional_requests,
        "regional_successes": regional_successes,
        "inventory_events": total_events,
        "resolved_events": resolved,
        "coverage_complete": coverage_complete,
        "stored": stored,
        "predicted": predicted,
        "no_two_sided_h2h": no_h2h,
        "deadline_skipped": deadline_skipped,
        "prediction_cutoff_minutes": PREDICTION_CUTOFF_MINUTES,
        "malformed": malformed,
        "prediction_errors": prediction_errors,
        "bookmaker_records": bookmaker_total,
        "errors": request_errors,
        "model": status(),
    }

    if not coverage_complete:
        raise RuntimeError(
            "Tennis all-match/all-region coverage incomplete: "
            + json.dumps(summary, default=str, sort_keys=True)
        )
    return summary


def _winner(score_event: Mapping[str, Any]) -> str | None:
    if not score_event.get("completed"):
        return None
    scores = score_event.get("scores") or []
    if len(scores) != 2:
        return None
    try:
        first, second = int(scores[0]["score"]), int(scores[1]["score"])
    except (KeyError, TypeError, ValueError):
        return None
    if first == second:
        return None
    return str(scores[0]["name"] if first > second else scores[1]["name"])


def _stored_sport_keys() -> list[str]:
    keys: set[str] = set()
    kwargs: Dict[str, Any] = {
        "FilterExpression": Attr("PK").begins_with("LIVE#"),
        "ProjectionExpression": "sport_key",
    }
    while True:
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            key = str(item.get("sport_key") or "")
            if key.startswith("tennis_"):
                keys.add(key)
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            break
        kwargs["ExclusiveStartKey"] = cursor
    return sorted(keys)


def settle_recent() -> Dict[str, Any]:
    sport_keys = _stored_sport_keys()
    trained = duplicates = missing = score_events = successful_keys = malformed = 0
    late_snapshots = 0
    errors: Dict[str, str] = {}
    for sport_key in sport_keys:
        try:
            events = _get(
                f"/sports/{sport_key}/scores/",
                {"daysFrom": 3, "dateFormat": "iso"},
            )
            successful_keys += 1
        except Exception as exc:
            errors[sport_key] = str(exc)
            continue
        if not isinstance(events, list):
            errors[sport_key] = "unexpected scores response"
            continue
        score_events += len(events)
        for event in events:
            winner, event_id = _winner(event), str(event.get("id") or "")
            if not winner or not event_id:
                continue
            item = table.get_item(
                Key={"PK": f"LIVE#{event_id}", "SK": "LATEST"},
                ConsistentRead=True,
            ).get("Item")
            if not item:
                missing += 1
                continue
            try:
                snapshot_at = _parse_utc(item.get("updated_at"))
                commence_time = item.get("commence_time") or event.get("commence_time")
                if snapshot_at > _deadline(commence_time):
                    late_snapshots += 1
                    continue
                signals = {
                    k: (float(v) if isinstance(v, Decimal) else v)
                    for k, v in dict(item["signals"]).items()
                }
                if any(
                    _finite(v) is None
                    for k, v in signals.items()
                    if k != "best_of_five"
                ):
                    raise ValueError("stored signals contain non-finite values")
                result = settle(
                    {
                        "match_id": event_id,
                        "player": str(item["player"]),
                        "opponent": str(item["opponent"]),
                        "event_time": str(
                            event.get("commence_time")
                            or item.get("commence_time")
                            or datetime.now(timezone.utc).isoformat()
                        ),
                        "player_won": winner == str(item["player"]),
                        "signals": signals,
                        "source": "the-odds-api-v4-scores",
                        "source_mode": "live_settlement_t10",
                    }
                )
            except (ValueError, ArithmeticError) as exc:
                malformed += 1
                errors[f"event:{event_id}"] = str(exc)
                continue
            trained += int(result.get("trained", False))
            duplicates += int(result.get("duplicate", False))
    if sport_keys and successful_keys == 0:
        raise RuntimeError(f"all tracked tennis score requests failed: {errors}")
    return {
        "sport_keys": len(sport_keys),
        "successful_sport_keys": successful_keys,
        "score_events": score_events,
        "trained": trained,
        "duplicates": duplicates,
        "missing_snapshots": missing,
        "late_snapshots_skipped": late_snapshots,
        "prediction_cutoff_minutes": PREDICTION_CUTOFF_MINUTES,
        "malformed": malformed,
        "errors": errors,
        "model": status(),
    }


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    action = str(event.get("action") or "collect")
    result = settle_recent() if action == "settle" else collect_live()
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(result, default=str),
    }
