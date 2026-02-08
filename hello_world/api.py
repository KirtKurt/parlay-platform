def _choose_best_3(snapshot):
    """
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
