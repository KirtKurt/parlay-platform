import json
import os
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, List, Tuple
import urllib.request
import urllib.parse

import boto3
from boto3.dynamodb.conditions import Key

from nba_algorithm import rank_nba_b11c1

def _choose_best_3(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    games = snapshot.get("data", {}).get("games", [])
    # Assuming the games are sorted by some criteria, we take the first 3 unique games
    return games[:3]
# HANDLERS
def _calculate_net_delta(game: dict, snapshots: List[Dict[str, Any]]) -> Optional[float]:
    gid = game.get("game_id") or game.get("id")
    t4_game = next((g for g in snapshots[3]["data"]["games"] if g.get("id") == gid), None)
    t1_game = next((g for g in snapshots[0]["data"]["games"] if g.get("id") == gid), None)

    if not t4_game or not t1_game:
        return None

    def get_fav_p(game: dict) -> Optional[float]:
        dk = (game.get("books", {}).get("draftkings") or {}).get("ml")
        fd = (game.get("books", {}).get("fanduel") or {}).get("ml")
        panel = _panel_metrics(game)
        if dk:
            _, fav_p = _fav_side_and_prob(dk)
            return fav_p
        elif fd:
            _, fav_p = _fav_side_and_prob(fd)
            return fav_p
        elif panel.get("panel_avg_fav_p") is not None:
            return panel["panel_avg_fav_p"]
        return None

    fav_p_t4 = get_fav_p(t4_game)
    fav_p_t1 = get_fav_p(t1_game)

    if fav_p_t4 is None or fav_p_t1 is None:
        return None

    return round(fav_p_t4 - fav_p_t1, 4)

def lambda_handler(event, context):
    if event.get("httpMethod") == "GET" and event.get("path") == "/v1/health":
        return _resp(200, {"status": "healthy"})

    if event.get("httpMethod") == "POST" and event.get("path") == "/v1/pull/nba":
        body = _parse_json(event.get("body"))
        t = body.get("t")
        run_type = body.get("run", "manual")
        result = _pull_nba_snapshot(run_type, t)
        return _resp(200, result)

    if event.get("httpMethod") == "GET" and event.get("path") == "/v1/snapshots":
        snapshot = _latest_snapshot()
        return _resp(200, snapshot)

    if event.get("httpMethod") == "GET" and event.get("path") == "/v1/snapshots/latest":
        t = event.get("queryStringParameters", {}).get("t")
        snapshot = _latest_snapshot(t)
        return _resp(200, snapshot)
        body = _parse_json(event.get("body"))
        games_input = body.get("games", [])
        
        if isinstance(games_input, list) and len(games_input) == 3:
            # Manual mode
            return _resp(200, rank_nba_b11c1(games_input))

        # Auto mode
        snapshot = _latest_snapshot()
        games = snapshot["data"]["games"]
        chosen_games = []
        games_for_engine = []

        for game in games:
            books = game.get("books", {})
            for book in BOOK_PRIORITY:
                if book in books:
                    ml = books[book]["ml"]
                    if "home" in ml and "away" in ml:
                        chosen_games.append({
                            "game_id": game["id"],
                            "home": game["home_team"],
                            "away": game["away_team"],
                            "ml": {"home": int(ml["home"]), "away": int(ml["away"])},
                            "book_used": book,
                            "books_src": game["books"]
                        })
                        games_for_engine.append({
                            "game_id": game["id"],
                            "home": game["home_team"],
                            "away": game["away_team"],
                            "ml": {"home": int(ml["home"]), "away": int(ml["away"])}
                        })
                        break
            if len(chosen_games) == 3:
                break

        # Compute signals and panel for each chosen game
        for game in chosen_games:
            game["signals"] = _steam_resistance_signals(game["books_src"])
            game["panel"] = _panel_metrics({"books": game["books_src"]})

        ranked = rank_nba_b11c1(games_for_engine)

        # Step3 scoring adjustments
        for combo in ranked["ranked"]:
            steam_align = sum(
                1 for i in range(3)
                if chosen_games[i]["signals"]["steam"] and ranked["legs"][i]["favorite"] == combo["picks"][i]
            )

            strongest_steam_idx = max(
                (i for i, game in enumerate(chosen_games) if game["signals"]["gap"] is not None),
                key=lambda i: chosen_games[i]["signals"]["gap"],
                default=None
            )
            signal_adj = 0.0

            if steam_align == 1:
                signal_adj += 0.75
            elif steam_align >= 2:
                signal_adj += 1.25

            if strongest_steam_idx is not None and ranked["legs"][strongest_steam_idx]["favorite"] == combo["picks"][strongest_steam_idx]:
                signal_adj += 0.50

            if any(game["signals"]["resistance"] for game in chosen_games):
                signal_adj -= 0.75

            if any(game["signals"]["coinflip"] for game in chosen_games):
                signal_adj -= 0.50

            if any(game["panel"]["panel_disagree"] for game in chosen_games):
                signal_adj -= 0.50

            combo["base_score"] = combo["score"]
            combo["signal_adj"] = signal_adj
            combo["score_final"] = combo["base_score"] + signal_adj
            combo["steam_align"] = steam_align

        # Re-rank by score_final then combo_prob
        ranked["ranked"].sort(key=lambda x: (-x["score_final"], -x["combo_prob"]))
        for i, combo in enumerate(ranked["ranked"], start=1):
            combo["rank"] = i

        # Signals summary
        signals_summary = {
            "steam_games": [game["game_id"] for game in chosen_games if game["signals"]["steam"]],
            "resistance_games": [game["game_id"] for game in chosen_games if game["signals"]["resistance"]],
            "coinflip_games": [game["game_id"] for game in chosen_games if game["signals"]["coinflip"]],
            "scoring_mode": "STEP3_PANEL_WEIGHTED"
        }
        return _resp(200, {
            "ok": True,
            "model": ranked.get("model"),
            "regime": ranked.get("regime"),
            "legs": ranked.get("legs"),
            "ranked": ranked.get("ranked"),
            "chosen_games": chosen_games,
            "source_snapshot": {"pk": snapshot["PK"], "sk": snapshot["SK"]},
            "signals_summary": signals_summary
        })

    if event.get("httpMethod") == "POST" and event.get("path") == "/v1/build/nba/4":
        # Retrieve snapshots T1, T2, T3, T4
        snapshots = [_latest_snapshot(f"T{i}") for i in range(1, 5)]
        if any(s is None for s in snapshots):
            return _resp(200, {
                "ok": False,
                "refusal": {"code": "MISSING_SNAPSHOT", "reason": "One or more required snapshots (T1-T4) are missing"}
            })

        # Use T4 for building, T1-T3 for validation
        games = snapshots[3]["data"]["games"]
        eligible_games = [_classify_game(game) for game in games]

        built = []
        used_game_ids = set()

        for parlay_index in range(1, 5):
            strong = [g for g in eligible_games if g["class"] == "STRONG_SOLID" and g.get("game_id") not in used_game_ids]
            coin = [g for g in eligible_games if g["class"] == "COIN_FLIP" and g.get("game_id") not in used_game_ids]

            if len(strong) >= 2:
                strong.sort(key=lambda x: -x["gap"])
                s1, s2 = strong[:2]
                third = coin[0] if coin else strong[2] if len(strong) > 2 else None
                if third:
                    slate = [s1, s2, third]
                else:
                    slate = []
            else:
                slate = []

            if not slate:
                if parlay_index == 1:
                    # Calculate pool counts
                    pool_counts = {
                        "STRONG_SOLID": sum(1 for game in eligible_games if game["class"] == "STRONG_SOLID"),
                        "SOLID": sum(1 for game in eligible_games if game["class"] == "SOLID"),
                        "COIN_FLIP": sum(1 for game in eligible_games if game["class"] == "COIN_FLIP"),
                        "MARGINAL": sum(1 for game in eligible_games if game["class"] == "MARGINAL"),
                        "INELIGIBLE": sum(1 for game in eligible_games if game["class"] == "INELIGIBLE"),
                    }

                    # Determine top candidates
                    top_candidates = sorted(
                        eligible_games,
                        key=lambda x: x.get("gap", 0),
                        reverse=True
                    )[:10]
                    top_candidates_info = [
                        {
                            "game_id": game.get("game_id") or game.get("id"),
                            "home_team": game.get("home_team") or game.get("home"),
                            "away_team": game.get("away_team") or game.get("away"),
                            "class": game.get("class"),
                            "factors": game.get("factors"),
                            "net_delta": _calculate_net_delta(game, snapshots)
                        }
                        for game in top_candidates
                    ]

                    # Include SKs used
                    sks_used = [snapshot["SK"] for snapshot in snapshots]

                    return _resp(200, {
                        "ok": True,
                        "parlays_requested": 4,
                        "parlays_built": 0,
                        "refusal": {"code": "FIRST_SLATE_INELIGIBLE", "reason": "First slate ineligible"},
                        "debug": {
                            "pool_counts": pool_counts,
                            "top_candidates": top_candidates_info,
                            "sks_used": sks_used
                        }
                    })
                break

            solid_count = sum(1 for game in slate if game["class"] == "STRONG_SOLID")
            coin_flip_count = sum(1 for game in slate if game["class"] == "COIN_FLIP")

            if solid_count < 2 or coin_flip_count > 1:
                if parlay_index == 1:
                    return _resp(200, {
                        "ok": True,
                        "parlays_requested": 4,
                        "parlays_built": 0,
                        "refusal": {"code": "FIRST_SLATE_INELIGIBLE", "reason": "First slate ineligible"}
                    })
                break

            slate.sort(key=lambda x: -x["gap"])
            chosen_games = slate[:3]
            for game in chosen_games:
                gid = game.get("game_id") or game.get("id")
                if gid:
                    used_game_ids.add(gid)

            structure_tag = "CLEAN_3_SOLID" if coin_flip_count == 0 else "MIXED_2_SOLID_1_CF"
            if coin_flip_count == 1 and any(len(game["factors"]) >= 3 for game in chosen_games if game["class"] == "COIN_FLIP"):
                structure_tag = "MARGINAL_MIXED"

            games_for_engine = []
            for game in chosen_games:
                gid = game.get("game_id") or game.get("id")
                ht = game.get("home_team") or game.get("home")
                at = game.get("away_team") or game.get("away")
                if gid and ht and at:
                    games_for_engine.append({
                        "game_id": gid,
                        "home": ht,
                        "away": at,
                        "ml": game["ml"]
                    })
            ranked = rank_nba_b11c1(games_for_engine)

            built.append({
                "parlay_index": parlay_index,
                "structure_tag": structure_tag,
                "legs": chosen_games,
                "ranked": ranked["ranked"][:8],
                "source_snapshot": {"pk": snapshots[3]["PK"], "sk": snapshots[3]["SK"]}
            })

        refusal = None
        if len(built) < 4:
            refusal = {"code": "INSUFFICIENT_PARLAYS", "reason": "Not enough eligible games to build 4 parlays"}

        return _resp(200, {
            "ok": True,
            "parlays_requested": 4,
            "parlays_built": len(built),
            "refusal": refusal,
            "parlays": built
        })

    return _resp(404, {"error": "Not Found"})

def scheduler_handler(event, context):
    result = _pull_nba_snapshot("scheduled")
    return _resp(200, result)
# AWS / ENV
# =========================
dynamodb = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None

# =========================
# CONFIG
# =========================
PANEL_BOOKS = ("fanduel", "draftkings", "betmgm", "caesars")
BOOK_PRIORITY = ("draftkings", "fanduel", "betmgm", "caesars")

# Classification thresholds (based on leader gap)
SOLID_GAP = 0.08
MODERATE_GAP = 0.05

# Coin-flip “uncertainty factor” thresholds
DISAGREE_STD = 0.03
COINFLIP_FACTORS_MIN = 2

# =========================
# BASIC HELPERS
# =========================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _json_default(o):
    if isinstance(o, Decimal):
        return float(o)
    return str(o)

def _resp(status: int, body: Any) -> Dict[str, Any]:
    result = {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default)
    }
    return result

def _parse_json(body: Optional[str]) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        return json.loads(body)
    except Exception:
        return {}

def _american_to_prob(a: int) -> float:
    return abs(a) / (abs(a) + 100) if a < 0 else 100 / (a + 100)

def _vig_norm(p1: float, p2: float) -> Tuple[float, float]:
    s = p1 + p2
    return (p1 / s, p2 / s) if s > 0 else (0.5, 0.5)

def _mean_std(vals: List[float]) -> Tuple[float, float]:
    if not vals:
        return 0.0, 0.0
    m = sum(vals) / len(vals)
    if len(vals) == 1:
        return m, 0.0
    var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
    return m, math.sqrt(var)

# =========================
# ODDS API + SNAPSHOT
# =========================
def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _build_oddsapi_url_nba_h2h() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    return "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?" + urllib.parse.urlencode(params)

def _compact_nba_h2h(raw_games: list) -> Dict[str, Any]:
    allowed = set(PANEL_BOOKS)
    all_keys_seen = set()
    games_out = []

    for g in raw_games:
        home = g.get("home_team")
        away = g.get("away_team")
        gid = g.get("id")
        ct = g.get("commence_time")

        books_out: Dict[str, Any] = {}

        for b in g.get("bookmakers", []) or []:
            key = (b.get("key") or "").lower().strip()
            if not key:
                continue
            all_keys_seen.add(key)
            if key not in allowed:
                continue

            h2h = next((m for m in (b.get("markets") or []) if m.get("key") == "h2h"), None)
            if not h2h:
                continue

            ho = ao = None
            for o in (h2h.get("outcomes") or []):
                if o.get("name") == home:
                    ho = o.get("price")
                elif o.get("name") == away:
                    ao = o.get("price")

            if ho is None or ao is None:
                continue

            books_out[key] = {"ml": {"home": int(ho), "away": int(ao)}}

        games_out.append({
            "id": gid,
            "commence_time": ct,
            "home_team": home,
            "away_team": away,
            "books": books_out,
        })

    return {
        "games": games_out,
        "count": len(games_out),
        "available_book_keys": sorted(all_keys_seen),
        "panel_books": list(PANEL_BOOKS),
    }

def _store_snapshot(run_type: str, data: Dict[str, Any], t: Optional[str] = None) -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")

    asof = _now_iso()
    slate_id = f"NBA_{asof[:10]}_{run_type}"
    sk_prefix = f"{t}#" if t else ""
    item = {
        "PK": "SPORT#nba",
        "SK": f"{sk_prefix}ASOF#{asof}#SLATE#{slate_id}",
        "sport": "nba",
        "slate_id": slate_id,
        "asof": asof,
        "created_at": asof,
        "data": data,
        "meta": {"source": "theOddsAPI", "run_type": run_type, "pulled_at": asof},
    }
    snapshots_tbl.put_item(Item=item)
    return item

def _pull_nba_snapshot(run_type: str, t: Optional[str] = None) -> Dict[str, Any]:
    raw = _http_get_json(_build_oddsapi_url_nba_h2h())
    compact = _compact_nba_h2h(raw)
    stored = _store_snapshot(run_type, compact, t)
    return {"ok": True, "count": compact["count"], "stored": {"pk": stored["PK"], "sk": stored["SK"]}}

def _latest_snapshot(t: Optional[str] = None) -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    key_expr = Key("PK").eq("SPORT#nba")
    if t:
        key_expr = key_expr & Key("SK").begins_with(f"{t}#")
    resp = snapshots_tbl.query(
        KeyConditionExpression=key_expr,
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        raise RuntimeError("No NBA snapshots found")
    return items[0]

# =========================
# PANEL CONSENSUS + SIGNALS
# =========================
def _fav_side_and_prob(ml: dict) -> Tuple[Optional[str], float]:
    ho = ml.get("home")
    ao = ml.get("away")
    if ho is None or ao is None:
        return None, 0.0
    ho = int(ho); ao = int(ao)
    p_h_raw = _american_to_prob(ho)
    p_a_raw = _american_to_prob(ao)
    p_h, p_a = _vig_norm(p_h_raw, p_a_raw)
    return ("home", float(p_h)) if p_h >= p_a else ("away", float(p_a))

def _panel_metrics(game: dict) -> dict:
    books = game.get("books", {}) or {}
    present = []
    fav_probs = []
    fav_sides = []

    for book in PANEL_BOOKS:
        if book not in books:
            continue
        ml = (books[book] or {}).get("ml", {})
        side, fav_p = _fav_side_and_prob(ml)
        if side is None:
            continue
        present.append(book)
        fav_sides.append(side)
        fav_probs.append(fav_p)

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

    home_votes = sum(1 for s in fav_sides if s == "home")
    away_votes = total - home_votes
    confirm = max(home_votes, away_votes)

    avg_p, std_p = _mean_std(fav_probs)
    return {
        "books_present": present,
        "panel_total": total,
        "panel_confirm_ratio": round(confirm / total, 3),
        "panel_avg_fav_p": round(avg_p, 4),
        "panel_std_fav_p": round(std_p, 4),
        "panel_disagree": std_p > DISAGREE_STD,
    }

def _steam_resistance_signals(books: dict) -> dict:
    fd = (books.get("fanduel") or {}).get("ml")
    dk = (books.get("draftkings") or {}).get("ml")
    if not fd or not dk:
        return {"steam": False, "resistance": False, "coinflip": False, "gap": None}

    fd_side, fd_fav_p = _fav_side_and_prob(fd)
    dk_side, dk_fav_p = _fav_side_and_prob(dk)
    if not fd_side or not dk_side:
        return {"steam": False, "resistance": False, "coinflip": False, "gap": None}

    gap = abs(fd_fav_p - dk_fav_p)
    steam = gap >= 0.03
    coinflip = max(fd_fav_p, dk_fav_p) < 0.525
    resistance = steam and ((fd_fav_p > dk_fav_p and dk_fav_p > 0.5) or (dk_fav_p > fd_fav_p and fd_fav_p > 0.5))

    return {
        "steam": steam,
        "resistance": resistance,
        "coinflip": coinflip,
        "gap": round(gap, 4),
        "fd_fav_p": round(fd_fav_p, 4),
        "dk_fav_p": round(dk_fav_p, 4),
    }

# =========================
# CLASSIFICATION: SOLID / COIN FLIP
# =========================
def _best_ml_for_engine(game: dict) -> Optional[dict]:
    books = game.get("books", {}) or {}
    for b in BOOK_PRIORITY:
        if b in books and "ml" in books[b]:
            ml = books[b]["ml"]
            if ml.get("home") is not None and ml.get("away") is not None:
                return {"home": int(ml["home"]), "away": int(ml["away"]), "book": b}
    return None

def _leader_gap_from_ml(ml: dict) -> float:
    side, fav_p = _fav_side_and_prob(ml)
    if side is None:
        return 0.0
    # compute normalized gap as fav - dog
    ho = int(ml["home"]); ao = int(ml["away"])
    p_h, p_a = _vig_norm(_american_to_prob(ho), _american_to_prob(ao))
    return abs(p_h - p_a)

def _classify_game(game: dict) -> dict:
    gid = game.get("id") or game.get("game_id")
    home_team = game.get("home_team") or game.get("home")
    away_team = game.get("away_team") or game.get("away")
    ml_pack = _best_ml_for_engine(game)
    if not ml_pack:
        return {
            "class": "INELIGIBLE",
            "gap": 0.0,
            "factors": ["NO_ODDS"],
            "book_used": None,
            "game_id": gid,
            "home_team": home_team,
            "away_team": away_team,
        }

    ml = {"home": ml_pack["home"], "away": ml_pack["away"]}
    gap = _leader_gap_from_ml(ml)
    panel = _panel_metrics(game)
    sig = _steam_resistance_signals(game.get("books", {}))

    factors = []
    # factor 1: compressed gap
    if gap < MODERATE_GAP:
        factors.append("LOW_GAP")
    # factor 2: panel disagreement
    if panel.get("panel_disagree"):
        factors.append("PANEL_DISAGREE")
    # factor 3: missing panel coverage
    if (panel.get("panel_total") or 0) < 2:
        factors.append("LOW_PANEL_COVERAGE")
    # factor 4: coinflip signal
    if sig.get("coinflip"):
        factors.append("COINFLIP_SIGNAL")

    # classification
    if gap >= SOLID_GAP and not panel.get("panel_disagree"):
        cls = "STRONG_SOLID"
    elif gap >= SOLID_GAP:
        cls = "SOLID"
    else:
        # coin flip requires multiple uncertainty factors (your rule)
        cls = "COIN_FLIP" if len(factors) >= COINFLIP_FACTORS_MIN else "MARGINAL"

    return {
        "class": cls,
        "gap": round(gap, 4),
        "factors": factors,
        "book_used": ml_pack["book"],
        "panel": panel,
        "signals": sig,
        "ml": {"home": ml_pack["home"], "away": ml_pack["away"]},
    }

# =========================
# =========================
