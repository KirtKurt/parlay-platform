ALLOWED_BOOKS = {"fanduel", "draftkings", "caesars"}

for b in g.get("bookmakers", []):
    key = b.get("key")
    if key not in ALLOWED_BOOKS:
        continue

    h2h = b.get("markets", [{}])[0].get("outcomes", [])
    if len(h2h) != 2:
        continue

    books[key] = {
        "ml": {
            "home": int(h2h[0]["price"]),
            "away": int(h2h[1]["price"]),
        }
    }
