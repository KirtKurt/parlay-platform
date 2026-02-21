import json
import os
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from typing import Any, Dict, Optional, List, Tuple
import urllib.request
import urllib.parse

from boto3.dynamodb.conditions import Key
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")




from nba_algorithm import rank_nba_b11c1

SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None

SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE", "")
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
outcomes_tbl = dynamodb.Table(OUTCOMES_TABLE) if OUTCOMES_TABLE else None

def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

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

def _build_oddsapi_url_ncaam_h2h() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    return "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds/?" + urllib.parse.urlencode(params)

def _pull_ncaam_snapshot(run_type: str, t: Optional[str] = None) -> Dict[str, Any]:
    raw = _http_get_json(_build_oddsapi_url_ncaam_h2h())
    slate_date_et = _get_slate_date_et()
    filtered_games = _filter_games_by_slate_date(raw, slate_date_et)
    compact = _compact_nba_h2h(filtered_games)
    slate_date_et = _get_slate_date_et()
    stored = _store_snapshot(run_type, compact, slate_date_et, t, sport="ncaam")
    return {"ok": True, "count": compact["count"], "stored": {"pk": stored["PK"], "sk": stored["SK"]}}

def compute_game_signals(sport: str, t: str, slate_date_et: str, snapshots_by_t: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Implement the logic to compute signals for each game
    # This is a placeholder implementation
    signals = []
    t4_snapshot = snapshots_by_t.get("T4") or snapshots_by_t.get("T1")
    if not t4_snapshot:
        return signals

    for game in t4_snapshot["data"]["games"]:
        game_id = game.get("id")
        commence_time = game.get("commence_time")
        home_team = game.get("home_team")
        away_team = game.get("away_team")
        # Compute signals and other required fields
        signal = {
            "game_id": game_id,
            "slate_date_et": slate_date_et,
            "t": t,
            "commence_time": commence_time,
            "home_team": home_team,
            "away_team": away_team,
            # Add more fields as needed
        }
        signals.append(signal)
    return signals

def _calculate_signals_and_classify(games: List[Dict[str, Any]], snapshots: List[Dict[str, Any]], coinflip_lite: bool) -> List[Dict[str, Any]]:
    # Implement signal calculation and classification logic
    # Placeholder implementation
    classified_games = []
    for game in games:
        # Calculate signals and classify each game
        classified_game = {
            "game_id": game.get("id"),
            "signals": {},  # Add signal calculations here
            "class": "INELIGIBLE",  # Determine class based on signals
            "disallowed": False,  # Determine if disallowed
        }
        classified_games.append(classified_game)
    return classified_games

def _generate_diagnostics(games_t4: List[Dict[str, Any]], classified: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_games_t4 = len(games_t4)
    disallowed_both_negative_t1_t3 = sum(1 for game in classified if game["disallowed"])
    strong_solid_count = sum(1 for game in classified if game["class"] == "STRONG_SOLID")
    coinflip_count = sum(1 for game in classified if game["class"] == "COIN_FLIP")
    solid_count = sum(1 for game in classified if game["class"] == "SOLID")
    ineligible_count = sum(1 for game in classified if game["class"] == "INELIGIBLE")
    missing_odds_count = sum(1 for game in games_t4 if _best_ml_for_engine(game) is None)

    sample_disallowed = [
        {
            "game_id": game.get("id"),
            "matchup": f"{game.get('home_team')} vs {game.get('away_team')}",
            "reason": "Disallowed"
        }
        for game in classified if game["disallowed"]
    ][:10]

    return {
        "total_games_t4": total_games_t4,
        "disallowed_both_negative_t1_t3": disallowed_both_negative_t1_t3,
        "strong_solid_count": strong_solid_count,
        "coinflip_count": coinflip_count,
        "solid_count": solid_count,
        "ineligible_count": ineligible_count,
        "missing_odds_count": missing_odds_count,
        "sample_disallowed": sample_disallowed
    }
def _build_ncaam_b1c23(max_parlays: int, coinflip_lite: bool) -> Dict[str, Any]:
    built: List[Dict[str, Any]] = []

    # Ensure all required snapshots are available
    snapshots = [_latest_snapshot(f"T{i}", "ncaam") for i in range(1, 5)]
    missing_snapshots = [f"T{i}" for i, s in enumerate(snapshots, 1) if s is None]
    if missing_snapshots:
        return {
            "ok": True,
            "model": "NCAAM-B1.1C.2.3",
            "slate_date_et": _get_slate_date_et(),
            "parlays_requested": max_parlays,
            "parlays_built": 0,
            "refusal": {
                "code": "MISSING_REQUIRED_T_SNAPSHOTS",
                "reason": "Missing required snapshots",
                "missing": missing_snapshots
            }
        }

    built: List[Dict[str, Any]] = []

    # Implement exclusion rule and signal model
    games = snapshots[3]["data"]["games"]  # Use T4 for game list
    classified_games = _calculate_signals_and_classify(games, snapshots, coinflip_lite)

    # Implement parlay construction rules
    used_game_ids = set()

    for parlay_index in range(max_parlays):
        # Build each parlay
        parlay = []  # Placeholder for parlay construction logic
        if not parlay:
            if parlay_index == 0:
                refusal = {
                    "code": "FIRST_SLATE_INELIGIBLE",
                    "reason": "First slate ineligible",
                    "diagnostics": _generate_diagnostics(games, classified_games)
                }
                return {
                    "ok": True,
                    "parlays_requested": max_parlays,
                    "parlays_built": 0,
                    "refusal": refusal
                }
            break
        built.append(parlay)

    refusal = None
    if len(built) < max_parlays:
        refusal = {
            "code": "INSUFFICIENT_PARLAYS",
            "reason": "Not enough eligible games to build requested parlays",
            "diagnostics": _generate_diagnostics(games, classified_games)
        }

    # Implement combo ranking
    # ...

    # Implement audit logging
    # ...

    return {
        "ok": True,
        "model": "NCAAM-B1.1C.2.3",
        "slate_date_et": _get_slate_date_et(),
        "parlays_requested": max_parlays,
        "parlays_built": len(built),
        "refusal": refusal,
        "parlays": built
    }

def lambda_handler(event, context):
    if event.get("httpMethod") == "GET" and event.get("path") == "/v1/health":
        return _resp(200, {"status": "healthy"})

    if event.get("httpMethod") == "POST" and event.get("path") == "/v1/pull/nba":
        body = _parse_json(event.get("body"))
        t = body.get("t")
        run_type = body.get("run", "manual")
        result = _pull_nba_snapshot(run_type, t)
        result = _resp(200, result)
        # Compute and store signals
        snapshots_by_t = {t: _latest_snapshot(t, "ncaam") for t in ["T1", "T2", "T3", "T4"]}
        signals = compute_game_signals("ncaam", t, _get_slate_date_et(), snapshots_by_t)
        for signal in signals:
            signal_ledger_tbl.put_item(Item={
                "PK": f"LEDGER#ncaam#{_get_slate_date_et()}#{t}",
                "SK": f"GAME#{signal['game_id']}",
                **signal
            })
        return result

    if event.get("httpMethod") == "GET" and event.get("path") == "/v1/snapshots":
        query_params = event.get("queryStringParameters", {})
        sport = query_params.get("sport", "nba")
        limit = int(query_params.get("limit", 10))

        key_expr = Key("PK").eq(f"SPORT#{sport}")
        resp = snapshots_tbl.query(
            KeyConditionExpression=key_expr,
            ScanIndexForward=False,
            Limit=limit,
        )
        items = resp.get("Items", [])
        return _resp(200, {"ok": True, "items": items})

    if event.get("httpMethod") == "POST" and event.get("path") == "/v1/pull/ncaam":
        body = _parse_json(event.get("body"))
        t = body.get("t")
        run_type = body.get("run", "manual")
        result = _pull_ncaam_snapshot(run_type, t)
        result = _resp(200, result)
        # Compute and store signals
        snapshots_by_t = {t: _latest_snapshot(t, "nba") for t in ["T1", "T2", "T3", "T4"]}
        signals = compute_game_signals("nba", t, _get_slate_date_et(), snapshots_by_t)
        for signal in signals:
            signal_ledger_tbl.put_item(Item={
                "PK": f"LEDGER#nba#{_get_slate_date_et()}#{t}",
                "SK": f"GAME#{signal['game_id']}",
                **signal
            })
        return result

    if event.get("httpMethod") == "POST" and event.get("path") == "/v1/build/ncaam/b1c23":
        body = _parse_json(event.get("body"))
        max_parlays = min(int(body.get("max_parlays", 7)), 7)
        coinflip_lite = bool(body.get("coinflip_lite", False))
        result = _build_ncaam_b1c23(max_parlays, coinflip_lite)
        return _resp(200, result)

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
            chosen_games = []
            games_for_engine = []
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
            if len(games_for_engine) == 3:
                ranked = rank_nba_b11c1(games_for_engine)
            else:
                if parlay_index == 1:
                    return _resp(200, {
                        "ok": True,
                        "parlays_requested": 4,
                        "parlays_built": 0,
                        "refusal": {"code": "FIRST_SLATE_INELIGIBLE", "reason": "First slate ineligible"}
                    })
                break

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

    if event.get("httpMethod") == "GET" and event.get("path") == "/v1/ledger":
        query_params = event.get("queryStringParameters", {})
        sport = query_params.get("sport")
        t = query_params.get("t")
        date = query_params.get("date")
        if not sport or not t or not date:
            return _resp(400, {"error": "Missing required query parameters"})

        key_expr = Key("PK").eq(f"LEDGER#{sport}#{date}#{t}")
        resp = signal_ledger_tbl.query(
            KeyConditionExpression=key_expr,
            ScanIndexForward=False
        )
        items = resp.get("Items", [])
        return _resp(200, {"ok": True, "items": items})

    if event.get("httpMethod") == "POST" and event.get("path") == "/v1/outcomes":
        body = _parse_json(event.get("body"))
        sport = body.get("sport")
        slate_date_et = body.get("slate_date_et")
        game_id = body.get("game_id")
        winner_team = body.get("winner_team")
        home_score = body.get("home_score")
        away_score = body.get("away_score")

        if not all([sport, slate_date_et, game_id, winner_team, home_score, away_score]):
            return _resp(400, {"error": "Missing required fields in request body"})

        # Determine if the underdog won
        ledger_key = f"LEDGER#{sport}#{slate_date_et}#T4"
        ledger_resp = signal_ledger_tbl.get_item(Key={"PK": ledger_key, "SK": f"GAME#{game_id}"})
        ledger_item = ledger_resp.get("Item")
        underdog_won = False
        if ledger_item:
            # Logic to determine underdog based on T4 odds
            underdog_won = True  # Placeholder logic

        outcomes_tbl.put_item(Item={
            "PK": f"OUTCOME#{sport}#{slate_date_et}",
            "SK": f"GAME#{game_id}",
            "winner_team": winner_team,
            "home_score": home_score,
            "away_score": away_score,
            "underdog_won": underdog_won
        })
        return _resp(200, {"ok": True})

    return _resp(404, {"error": "Not Found"})

def scheduler_handler(event, context):
    """
    EventBridge scheduler entrypoint.
    Expected event payload:
      {"sport":"ncaam","t":"T2","run":"mid_pull"}
      {"sport":"nba","t":"T1","run":"base_pull"}
    """
    event = event or {}
    sport = event.get("sport")
    t = event.get("t")
    run = event.get("run")

    # Scheduler does NOT build parlays. It only pulls snapshots.
    try:
        if sport == "nba":
            result = _pull_nba_snapshot(run_type=run, t=t)
        elif sport == "ncaam":
            result = _pull_ncaam_snapshot(run_type=run, t=t)
        else:
            return _resp(400, {"ok": False, "error": "Unsupported sport", "sport": sport})

        return _resp(200, {"ok": True, "sport": sport, "t": t, "run": run, "result": result})
    except Exception as e:
        return _resp(500, {"ok": False, "sport": sport, "t": t, "run": run, "error": str(e)})

    try:
        if sport == "nba":
            result = _pull_nba_snapshot(run_type=run, t=t)
        elif sport == "ncaam":
            result = _pull_ncaam_snapshot(run_type=run, t=t)
        else:
            return _resp(400, {"ok": False, "error": "Unsupported sport"})

        return _resp(200, {"ok": True, "sport": sport, "t": t, "run": run, "result": result})
    except Exception as e:
        return _resp(500, {"ok": False, "error": str(e)})
    built: List[Dict[str, Any]] = []
    # Ensure all required snapshots are available
    snapshots = [_latest_snapshot(f"T{i}", "ncaam") for i in range(1, 5)]
    missing_snapshots = [f"T{i}" for i, s in enumerate(snapshots, 1) if s is None]
    if missing_snapshots:
        return {
            "ok": True,
            "model": "NCAAM-B1.1C.2.3",
            "slate_date_et": _get_slate_date_et(),
            "parlays_requested": max_parlays,
            "parlays_built": 0,
            "refusal": {
                "code": "MISSING_REQUIRED_T_SNAPSHOTS",
                "reason": "Missing required snapshots",
                "missing": missing_snapshots
            }
        }

    # Implement exclusion rule and signal model
    # ...

    # Implement parlay construction rules
    # ...

    # Implement combo ranking
    # ...

    # Implement audit logging
    # ...

    # Diagnostics and refusal logic
    total_games = len(snapshots[3]["data"]["games"])
    disallowed_both_negative = sum(1 for game in snapshots[3]["data"]["games"] if _calculate_net_delta(game, snapshots) is None)
    strong_solid = sum(1 for game in snapshots[3]["data"]["games"] if _classify_game(game)["class"] == "STRONG_SOLID")
    coinflip = sum(1 for game in snapshots[3]["data"]["games"] if _classify_game(game)["class"] == "COIN_FLIP")
    solid = sum(1 for game in snapshots[3]["data"]["games"] if _classify_game(game)["class"] == "SOLID")
    ineligible = sum(1 for game in snapshots[3]["data"]["games"] if _classify_game(game)["class"] == "INELIGIBLE")
    missing_odds_games = sum(1 for game in snapshots[3]["data"]["games"] if _best_ml_for_engine(game) is None)

    top_disallowed_samples = [
        {"game_id": game.get("id"), "reason": "Both negative" if _calculate_net_delta(game, snapshots) is None else "Other"}
        for game in snapshots[3]["data"]["games"][:5]
    ]

    refusal = None
    if len(built) == 0:
        if missing_snapshots:
            refusal = {
                "code": "MISSING_REQUIRED_T_SNAPSHOTS",
                "reason": "Missing required snapshots",
                "missing": missing_snapshots
            }
        elif total_games == 0 or strong_solid < 2:
            refusal = {
                "code": "FIRST_SLATE_INELIGIBLE",
                "reason": "First slate ineligible"
            }
        else:
            refusal = {
                "code": "INSUFFICIENT_ELIGIBLE_GAMES",
                "reason": "Not enough eligible games to build requested parlays",
                "diagnostics": {
                    "counts": {
                        "total_games": total_games,
                        "disallowed_both_negative": disallowed_both_negative,
                        "strong_solid": strong_solid,
                        "coinflip": coinflip,
                        "solid": solid,
                        "ineligible": ineligible,
                        "missing_odds_games": missing_odds_games
                    },
                    "top_disallowed_samples": top_disallowed_samples
                }
            }

    return {
        "ok": True,
        "model": "NCAAM-B1.1C.2.3",
        "slate_date_et": _get_slate_date_et(),
        "parlays_requested": max_parlays,
        "parlays_built": len(built),
        "refusal": refusal,
        "parlays": built
    }
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
def _get_slate_date_et() -> str:
    eastern = ZoneInfo("America/New_York")
    return datetime.now(eastern).strftime('%Y-%m-%d')

def _filter_games_by_slate_date(games: list, slate_date_et: str) -> list:
    eastern = ZoneInfo("America/New_York")
    filtered_games = []
    for game in games:
        commence_time = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
        commence_time_et = commence_time.astimezone(eastern).strftime('%Y-%m-%d')
        if commence_time_et == slate_date_et:
            filtered_games.append(game)
    return filtered_games

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
    # Store all bookmakers returned by OddsAPI
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

def _store_snapshot(run_type: str, data: Dict[str, Any], slate_date_et: str, t: Optional[str] = None, sport: str = "nba") -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")

    asof = _now_iso()
    slate_id = f"{sport.upper()}_{slate_date_et}_{run_type}"
    sk_prefix = f"{t}#DATE#{slate_date_et}#ASOF#{asof}#SLATE#{slate_id}" if t else f"ASOF#{asof}#SLATE#{slate_id}"
    item = {
        "t": t if t else None,
        "PK": f"SPORT#{sport}",
        "SK": sk_prefix,
        "sport": sport,
        "slate_id": slate_id,
        "asof": asof,
        "created_at": asof,
        "data": data,
        "slate_date_et": slate_date_et,
        "meta": {"source": "theOddsAPI", "run_type": run_type, "pulled_at": asof},
    }
    snapshots_tbl.put_item(Item=item)
    return item

def _pull_nba_snapshot(run_type: str, t: Optional[str] = None) -> Dict[str, Any]:
    raw = _http_get_json(_build_oddsapi_url_nba_h2h())
    slate_date_et = _get_slate_date_et()
    filtered_games = _filter_games_by_slate_date(raw, slate_date_et)
    compact = _compact_nba_h2h(filtered_games)
    stored = _store_snapshot(run_type, compact, slate_date_et, t)
    return {"ok": True, "count": compact["count"], "stored": {"pk": stored["PK"], "sk": stored["SK"]}}

def _latest_snapshot(t: Optional[str] = None, sport: str = "nba") -> Optional[Dict[str, Any]]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    key_expr = Key("PK").eq(f"SPORT#{sport}")
    resp = snapshots_tbl.query(
        KeyConditionExpression=key_expr,
        ScanIndexForward=False,
        Limit=50,
    )
    items = resp.get("Items", [])
    today_et = _get_slate_date_et()
    for item in items:
        if item.get("t") == t and item.get("slate_date_et") == today_et:
            return item
    return None

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
