from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Mapping

import boto3

from bootstrap import bootstrap_tour
from clv import from_snaps
from handler import predict, settle, status
from live_signals import live_signals
from odds_client import active_tennis_keys, best_h2h, get
from persist import load_ratings, save_ratings
from seed_model import seed_tour
from surface import surface_from_sport_key

TABLE_NAME = os.environ["TA_TABLE"]
ODDS_API_KEY = os.environ.get("TA_ODDS_API_KEY") or os.environ.get("ODDS_API_KEY", "")
REGIONS = tuple(x.strip() for x in os.getenv("TA_ODDS_REGIONS", "us,us2,uk,eu,au").split(",") if x.strip())
CUTOFF_MINUTES = int(os.getenv("TA_PREDICTION_CUTOFF_MINUTES", "10"))
table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime:
    raw = str(value or "").replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tour_from_key(sport_key: str) -> str:
    return "wta" if "wta" in sport_key else "atp"


def _date_int(commence: str) -> int:
    try:
        return int(_parse(commence).strftime("%Y%m%d"))
    except ValueError:
        return 0


def collect_live() -> Dict[str, Any]:
    keys = active_tennis_keys(ODDS_API_KEY)
    stored = predicted = skipped = 0
    errors: Dict[str, str] = {}
    now = _now()
    books = {"atp": load_ratings("atp"), "wta": load_ratings("wta")}
    for sport_key in keys:
        try:
            events = get(
                f"/sports/{sport_key}/odds",
                {"regions": ",".join(REGIONS), "markets": "h2h", "oddsFormat": "american", "dateFormat": "iso"},
                ODDS_API_KEY,
            )
        except Exception as exc:
            errors[sport_key] = str(exc)
            continue
        if not isinstance(events, list):
            continue
        tour = _tour_from_key(sport_key)
        surface = surface_from_sport_key(sport_key)
        slams = any(x in sport_key for x in ("aus_open", "french_open", "wimbledon", "us_open"))
        for event in events:
            event_id = str(event.get("id") or "")
            pair = best_h2h(event)
            commence = str(event.get("commence_time") or "")
            if not event_id or pair is None or not commence:
                skipped += 1
                continue
            try:
                if now > _parse(commence) - timedelta(minutes=CUTOFF_MINUTES):
                    skipped += 1
                    continue
            except ValueError:
                skipped += 1
                continue
            player, opponent, po, oo = pair
            signals = live_signals(books[tour], player, opponent, po, oo, surface, slams, _date_int(commence))
            clean = {k: v for k, v in signals.items() if k not in {"matched_player", "matched_opponent", "surface"}}
            table.put_item(Item={
                "PK": f"LIVE#{event_id}", "SK": "LATEST", "event_id": event_id, "sport_key": sport_key,
                "tour": tour, "player": player, "opponent": opponent,
                "matched_player": signals["matched_player"], "matched_opponent": signals["matched_opponent"],
                "surface": surface, "commence_time": commence,
                "player_odds": Decimal(str(po)), "opponent_odds": Decimal(str(oo)),
                "signals": {k: (Decimal(str(v)) if isinstance(v, float) else v) for k, v in clean.items()},
                "stack": "tennis-alpha", "updated_at": now.isoformat(),
            })
            table.put_item(Item={
                "PK": f"LIVE#{event_id}", "SK": f"SNAP#{now.isoformat()}",
                "player_odds": Decimal(str(po)), "opponent_odds": Decimal(str(oo)), "stack": "tennis-alpha",
            })
            if not table.get_item(Key={"PK": f"LIVE#{event_id}", "SK": "OPEN"}).get("Item"):
                table.put_item(Item={
                    "PK": f"LIVE#{event_id}", "SK": "OPEN",
                    "player_odds": Decimal(str(po)), "opponent_odds": Decimal(str(oo)),
                    "captured_at": now.isoformat(), "stack": "tennis-alpha",
                })
            predict({"match_id": event_id, "tour": tour, "player": player, "opponent": opponent, "signals": clean})
            stored += 1
            predicted += 1
    return {"stack": "tennis-alpha", "sport_keys": keys, "stored": stored, "predicted": predicted, "skipped": skipped, "errors": errors, "ratings": {t: books[t].matches for t in books}, "model": status()}


def settle_recent() -> Dict[str, Any]:
    trained = missing = clv_rows = 0
    errors: Dict[str, str] = {}
    keys = active_tennis_keys(ODDS_API_KEY)
    books = {"atp": load_ratings("atp"), "wta": load_ratings("wta")}
    dirty = {"atp": False, "wta": False}
    for sport_key in keys:
        try:
            events = get(f"/sports/{sport_key}/scores/", {"daysFrom": 3, "dateFormat": "iso"}, ODDS_API_KEY)
        except Exception as exc:
            errors[sport_key] = str(exc)
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            if not event.get("completed"):
                continue
            scores = event.get("scores") or []
            if len(scores) != 2:
                continue
            try:
                s0, s1 = int(scores[0]["score"]), int(scores[1]["score"])
            except (KeyError, TypeError, ValueError):
                continue
            if s0 == s1:
                continue
            winner = str(scores[0]["name"] if s0 > s1 else scores[1]["name"])
            event_id = str(event.get("id") or "")
            item = table.get_item(Key={"PK": f"LIVE#{event_id}", "SK": "LATEST"}, ConsistentRead=True).get("Item")
            if not item:
                missing += 1
                continue
            signals = item.get("signals") or {}
            clean = {k: (float(v) if k != "best_of_five" else bool(v)) for k, v in signals.items()}
            player_won = str(item["player"]) == winner
            result = settle({
                "match_id": event_id, "tour": item.get("tour") or _tour_from_key(sport_key),
                "player": item["player"], "opponent": item["opponent"], "player_won": player_won,
                "event_time": item.get("commence_time"), "signals": clean,
            })
            trained += int(result.get("trained") or 0)
            tour = str(item.get("tour") or _tour_from_key(sport_key))
            p_name = str(item.get("matched_player") or item["player"])
            o_name = str(item.get("matched_opponent") or item["opponent"])
            surface = str(item.get("surface") or surface_from_sport_key(sport_key))
            if player_won:
                books[tour].observe(p_name, o_name, surface, _date_int(str(item.get("commence_time") or "")))
            else:
                books[tour].observe(o_name, p_name, surface, _date_int(str(item.get("commence_time") or "")))
            dirty[tour] = True
            open_row = table.get_item(Key={"PK": f"LIVE#{event_id}", "SK": "OPEN"}).get("Item")
            if open_row and result.get("pre_update_probability") is not None:
                report = from_snaps(float(result["pre_update_probability"]), open_row, {"player_odds": item.get("player_odds"), "opponent_odds": item.get("opponent_odds")}, player_won)
                table.put_item(Item={
                    "PK": f"CLV#{event_id}", "SK": "RECORD", "tour": tour,
                    "model_p": Decimal(str(report["model_p"])), "open_fair_p": Decimal(str(report["open_fair_p"])),
                    "close_fair_p": Decimal(str(report["close_fair_p"])), "clv": Decimal(str(report["clv"])),
                    "line_move": Decimal(str(report["line_move"])), "beat_close": report["beat_close"],
                    "player_won": player_won, "stack": "tennis-alpha",
                })
                clv_rows += 1
    for tour, changed in dirty.items():
        if changed:
            save_ratings(tour, books[tour])
    return {"stack": "tennis-alpha", "trained": trained, "missing_live_rows": missing, "clv_rows": clv_rows, "errors": errors}


def lambda_handler(event: Mapping, context: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = dict(event or {})
    if isinstance(payload.get("body"), str) and payload["body"]:
        try:
            payload.update(json.loads(payload["body"]))
        except json.JSONDecodeError:
            pass
    path = str(payload.get("path") or "")
    action = str(payload.get("action") or "collect")
    if path.endswith("/bootstrap"):
        action = "bootstrap"
    elif path.endswith("/pipeline/settle"):
        action = "settle"
    elif path.endswith("/seed"):
        action = "seed_model"
    if action == "settle":
        body = settle_recent()
    elif action == "bootstrap":
        body = {"atp": bootstrap_tour("atp"), "wta": bootstrap_tour("wta")}
    elif action == "seed_model":
        body = {"atp": seed_tour("atp"), "wta": seed_tour("wta"), "model": status()}
    else:
        body = collect_live()
    return {"statusCode": 200, "body": json.dumps(body, default=str)}
