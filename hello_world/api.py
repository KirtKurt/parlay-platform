def _choose_best_3(snapshot):
    # Pick 3 UNIQUE games. Keep best candidate per game_id, then select top 3 by gap.
    best_by_game = {}

    for g in snapshot["data"]["games"]:
        game_id = g.get("id")
        if not game_id:
            continue

        books = g.get("books", {}) or {}

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

            if abs(ho_i) > 9000 or abs(ao_i) > 9000:
                continue

            p1 = _american_to_prob(ho_i)
            p2 = _american_to_prob(ao_i)
            gap = abs(_vig_norm(p1, p2)[0] - _vig_norm(p1, p2)[1])

            cand = (gap, g, book, ho_i, ao_i)
            prev = best_by_game.get(game_id)
            if prev is None or cand[0] > prev[0]:
                best_by_game[game_id] = cand

    unique_candidates = sorted(best_by_game.values(), key=lambda x: x[0], reverse=True)
    top3 = unique_candidates[:3]

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

def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or "/"

    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    if path in ("/health", "/v1/health") and method == "GET":
        return _resp(200, {"ok": True, "ts": _now_iso()})

    return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})


def scheduler_handler(event, context):
    return _pull_nba_snapshot((event or {}).get("run", "scheduled"))
        })

    return chosen
