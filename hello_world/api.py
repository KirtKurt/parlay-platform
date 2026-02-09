import json
import os
import math
import urllib.request
import logging
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from hello_world.nba_algorithm import rank_nba_b11c1

def _choose_best_3(snapshot: dict) -> List[dict]:
    # Placeholder implementation
    # This function should return a list of 3 chosen games based on the snapshot
    return snapshot.get("data", {}).get("games", [])[:3]

# ======================================================
# ENV / AWS
# ======================================================
dynamodb = boto3.resource("dynamodb")

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None

# ======================================================
# CONSTANTS
# ======================================================
PANEL_BOOKS = ("fanduel", "draftkings", "betmgm", "caesars")

# ======================================================
# BASIC HELPERS
# ======================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resp(status: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _json_default(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError


def _american_to_prob(a: int) -> float:
    if a < 0:
        return abs(a) / (abs(a) + 100)
    return 100 / (a + 100)


def _vig_norm(p1: float, p2: float):
    s = p1 + p2
    return (p1 / s, p2 / s)


# ======================================================
# PANEL CONSENSUS
# ======================================================
def _fav_side_and_prob(ml: dict):
    ho = ml.get("home")
    ao = ml.get("away")
    if ho is None or ao is None:
        return None, 0.0

    p1 = _american_to_prob(int(ho))
    p2 = _american_to_prob(int(ao))
    p1, p2 = _vig_norm(p1, p2)

    return ("home", p1) if p1 >= p2 else ("away", p2)


def _mean_std(vals: List[float]):
    if not vals:
        return 0.0, 0.0
    mean = sum(vals) / len(vals)
    if len(vals) == 1:
        return mean, 0.0
    var = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
    return mean, math.sqrt(var)


def _panel_consensus(game: dict) -> dict:
    books = game.get("books", {})
    fav_probs = []
    fav_sides = []
    present = []

    for book in PANEL_BOOKS:
        if book not in books:
            continue
        side, p = _fav_side_and_prob(books[book].get("ml", {}))
        if side:
            present.append(book)
            fav_sides.append(side)
            fav_probs.append(p)

    total = len(present)
    if total == 0:
        return {
            "books_present": [],
            "panel_total": 0,
            "panel_confirm_ratio": 0.0,
            "panel_avg_fav_p": None,
            "panel_std_fav_p": None,
            "panel_disagree": True,
        }

    confirm = max(
        sum(1 for s in fav_sides if s == "home"),
        sum(1 for s in fav_sides if s == "away"),
    )

    avg_p, std_p = _mean_std(fav_probs)

    return {
        "books_present": present,
        "panel_total": total,
        "panel_confirm_ratio": round(confirm / total, 3),
        "panel_avg_fav_p": round(avg_p, 4),
        "panel_std_fav_p": round(std_p, 4),
        "panel_disagree": std_p > 0.03,
    }


# ======================================================
# STEAM / RESISTANCE (DK vs FD)
# ======================================================
def _steam_resistance_signals(books: dict) -> dict:
    fd = books.get("fanduel", {}).get("ml")
    dk = books.get("draftkings", {}).get("ml")

    if not fd or not dk:
        return {"steam": False, "resistance": False, "coinflip": False}

    fd_p = _vig_norm(
        _american_to_prob(fd["home"]),
        _american_to_prob(fd["away"]),
    )[0]
    dk_p = _vig_norm(
        _american_to_prob(dk["home"]),
        _american_to_prob(dk["away"]),
    )[0]

    gap = abs(fd_p - dk_p)

    return {
        "steam": gap >= 0.05,
        "resistance": gap <= 0.01,
        "coinflip": gap <= 0.02,
        "gap": round(gap, 4),
        "fd_fav_p": round(fd_p, 4),
        "dk_fav_p": round(dk_p, 4),
    }


# ======================================================
# SNAPSHOT PULL
# ======================================================
def _pull_nba_snapshot(run_type: str):
    url = (
        "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
        f"?markets=h2h&oddsFormat=american&apiKey={ODDS_API_KEY}"
    )

    with urllib.request.urlopen(url) as r:
        raw = json.loads(r.read().decode())

    games = []
    for g in raw:
        books = {}
        for b in g.get("bookmakers", []):
            key = b.get("key")
            if key not in PANEL_BOOKS:
                continue
            outs = b.get("markets", [{}])[0].get("outcomes", [])
            if len(outs) != 2:
                continue
            books[key] = {
                "ml": {
                    "home": outs[0]["price"],
                    "away": outs[1]["price"],
                }
            }

        games.append({
            "id": g["id"],
            "home_team": g["home_team"],
            "away_team": g["away_team"],
            "commence_time": g["commence_time"],
            "books": books,
        })

    item = {
        "PK": "SPORT#nba",
        "SK": f"ASOF#{_now_iso()}#SLATE#NBA_{run_type}",
        "sport": "nba",
        "created_at": _now_iso(),
        "data": {"games": games, "count": len(games)},
        "meta": {"run_type": run_type, "source": "theOddsAPI"},
    }

    snapshots_tbl.put_item(Item=item)
    return {"ok": True, "count": len(games), "stored": {"pk": item["PK"], "sk": item["SK"]}}


def _latest_snapshot():
    resp = snapshots_tbl.query(
        KeyConditionExpression=Key("PK").eq("SPORT#nba"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


# ======================================================
# STEP 3 SCORING
# ======================================================
def apply_signal_adjustments(payload: dict) -> dict:
    rows = payload.get("ranked", [])
    chosen = payload.get("chosen_games", [])

    any_steam_confirmed = False
    panel_disagree = False
    any_coinflip = False

    for g in chosen:
        sig = g.get("signals", {})
        pan = g.get("panel", {})
        if sig.get("steam") and pan.get("panel_confirm_ratio", 0) >= 0.75:
            any_steam_confirmed = True
        if pan.get("panel_disagree"):
            panel_disagree = True
        if sig.get("coinflip"):
            any_coinflip = True

    for r in rows:
        base = r["score"]
        adj = 0.0
        if any_steam_confirmed:
            adj += 0.75
        if panel_disagree:
            adj -= 0.50
        if any_coinflip:
            adj -= 0.25

        r["base_score"] = round(base, 4)
        r["signal_adj"] = round(adj, 4)
        r["score_final"] = round(base + adj, 4)

    rows.sort(key=lambda x: (x["score_final"], x["combo_prob"]), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    payload["ranked"] = rows
    payload.setdefault("signals_summary", {})
    payload["signals_summary"]["scoring_mode"] = "STEP3_PANEL_WEIGHTED"
    return payload


# ======================================================
# LAMBDA HANDLERS (ALWAYS LAST)
# ======================================================
logging.basicConfig(level=logging.INFO)

def lambda_handler(event, context):
    try:
        path = event.get("path", "")
        method = event.get("httpMethod", "")

        if path == "/v1/health":
            return _resp(200, {"ok": True, "ts": _now_iso()})

        if path == "/v1/pull/nba" and method == "POST":
            return _resp(200, _pull_nba_snapshot("manual"))

        if path == "/v1/rank/nba" and method == "POST":
            snap = _latest_snapshot()
            if not snap:
                return _resp(400, {"ok": False, "error": "No snapshot found"})

            chosen = _choose_best_3(snap)
            if len(chosen) != 3:
                return _resp(500, {"ok": False, "error": "Unable to choose 3 games", "chosen": chosen})

            games_for_engine = [
                {"game_id": g["game_id"], "home": g["home"], "away": g["away"], "ml": g["ml"]}
                for g in chosen
            ]

            ranked = rank_nba_b11c1(games_for_engine)

            ranked["chosen_games"] = chosen
            ranked["source_snapshot"] = {"pk": snap.get("PK"), "sk": snap.get("SK")}

            ranked = apply_signal_adjustments(ranked)
            return _resp(200, ranked)

        return _resp(404, {"ok": False, "error": "Not Found"})

    except Exception as e:
        logging.exception("Exception in lambda_handler")
        return _resp(500, {"ok": False, "error": "Internal server error"})


def scheduler_handler(event, context):
    # Implement the scheduler handler logic here
    run_type = (event or {}).get("run", "scheduled")
    return _pull_nba_snapshot(run_type)
