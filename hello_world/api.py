def _choose_best_3(snapshot):
    """
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o % 1 == 0 else float(o)
    return str(o)


def _resp(status: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type",
        },
        "body": json.dumps(body, default=_json_default),
    }
    Pick 3 UNIQUE games.
    We score candidates per (game,book), keep the best candidate per game_id,
    then take the top 3 games by gap.
    """
    best_by_game = {}  # game_id -> (gap, g, book, ho, ao)

    for g in snapshot["data"]["games"]:
        game_id = g.get("id")
        if not game_id:
            continue

        books = g.get("books", {}) or {}

        # evaluate candidate rows for both books (if present)
        for book in ("fanduel", "draftkings"):
            if book not in books:
                continue

            ml = books[book].get("ml", {})
            ho, ao = ml.get("home"), ml.get("away")
            if ho is None or ao is None:
                continue

            try:
                ho_i = int(ho)
                ao_i = int(ao)
            except Exception:
                continue

            # optional: ignore absurd outliers
            if abs(ho_i) > 9000 or abs(ao_i) > 9000:
                continue

            p1 = _american_to_prob(ho_i)
            p2 = _american_to_prob(ao_i)
            gap = abs(_vig_norm(p1, p2)[0] - _vig_norm(p1, p2)[1])

            cand = (gap, g, book, ho_i, ao_i)

            # keep only the best-scoring candidate per game
            prev = best_by_game.get(game_id)
            if prev is None or cand[0] > prev[0]:
                best_by_game[game_id] = cand

    # sort unique games by gap descending and take top 3
    unique_candidates = sorted(best_by_game.values(), key=lambda x: x[0], reverse=True)
    top3 = unique_candidates[:3]
def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or "/"

    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    if path in ("/health", "/v1/health") and method == "GET":
        return _resp(200, {"ok": True, "ts": _now_iso()})

    if path == "/v1/pull/nba" and method == "POST":
        return _resp(200, _pull_nba_snapshot("manual"))

    if path == "/v1/snapshots" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        sport = (qs.get("sport") or "nba").lower()
        limit = int(qs.get("limit", 5))
        resp = snapshots_tbl.query(
            KeyConditionExpression=Key("PK").eq(f"SPORT#{sport}"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return _resp(200, {"ok": True, "items": resp.get("Items", [])})

    if path == "/v1/rank/nba" and method == "POST":
        payload = _parse_json(event.get("body"))

        if isinstance(payload.get("games"), list) and len(payload["games"]) == 3:
            return _resp(200, rank_nba_b11c1(payload["games"]))

        snap = _latest_snapshot()
        chosen = _choose_best_3(snap)
        if len(chosen) != 3:
            return _resp(500, {"ok": False, "error": "Unable to choose 3 games", "chosen": chosen})

        engine_games = [{"game_id": g["game_id"], "home": g["home"], "away": g["away"], "ml": g["ml"]} for g in chosen]
        ranked = rank_nba_b11c1(engine_games)

        ranked["chosen_games"] = chosen
        ranked["signals_summary"] = {
            "steam_games": [g["game_id"] for g in chosen if g["signals"]["steam"]],
            "resistance_games": [g["game_id"] for g in chosen if g["signals"]["resistance"]],
            "coinflip_games": [g["game_id"] for g in chosen if g["signals"]["coinflip"]],
        }
        ranked["source_snapshot"] = {"pk": snap["PK"], "sk": snap["SK"]}

        return _resp(200, ranked)

    return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})


def scheduler_handler(event, context):
    return _pull_nba_snapshot((event or {}).get("run", "scheduled"))
    chosen = []
    for gap, g, book, ho, ao in top3:
        chosen.append({
            "game_id": g["id"],
            "home": g["home_team"],
            "away": g["away_team"],
            "ml": {"home": ho, "away": ao},
            "book_used": book,
            "gap": round(gap, 4),
            "signals": _steam_resistance_signals(g.get("books", {})),
        })

    return chosen
