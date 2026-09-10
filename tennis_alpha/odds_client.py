from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Mapping

BASE_URL = os.getenv("TA_ODDS_BASE_URL", "https://api.the-odds-api.com/v4")


def get(path: str, params: Mapping[str, Any], api_key: str) -> Any:
    query = dict(params)
    query["apiKey"] = api_key
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": "tennis-alpha/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def active_tennis_keys(api_key: str) -> list[str]:
    sports = get("/sports/", {"all": "false"}, api_key)
    keys = []
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


def best_h2h(event: Mapping[str, Any]) -> tuple[str, str, float, float] | None:
    home = str(event.get("home_team") or "")
    away = str(event.get("away_team") or "")
    if not home or not away or home == away:
        return None
    prices: dict[str, list[float]] = {home: [], away: []}
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes") or []:
                name = str(outcome.get("name") or "")
                try:
                    price = float(outcome.get("price"))
                except (TypeError, ValueError):
                    continue
                if name in prices and price != 0:
                    prices[name].append(price)
    if not prices[home] or not prices[away]:
        return None
    return home, away, max(prices[home]), max(prices[away])
