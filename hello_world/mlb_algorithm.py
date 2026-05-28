from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Dict, List, Tuple


@dataclass
class MLBGame:
    game_id: str
    home: str
    away: str
    home_american: int
    away_american: int
    home_p: float
    away_p: float
    favorite: str
    dog: str
    favorite_odds: int
    dog_odds: int
    leader_gap: float
    tags: List[str]


def american_to_prob_raw(american: int) -> float:
    if american == 0:
        raise ValueError("American odds cannot be 0")
    if american < 0:
        a = abs(american)
        return a / (a + 100.0)
    return 100.0 / (american + 100.0)


def vig_adjust_two_way(p1_raw: float, p2_raw: float) -> Tuple[float, float]:
    total = p1_raw + p2_raw
    if total <= 0:
        return 0.5, 0.5
    return p1_raw / total, p2_raw / total


def american_to_decimal(american: int) -> float:
    if american < 0:
        return 1.0 + (100.0 / abs(american))
    return 1.0 + (american / 100.0)


def decimal_to_american(decimal_odds: float) -> int:
    if decimal_odds >= 2.0:
        return int(round((decimal_odds - 1.0) * 100))
    return int(round(-100.0 / (decimal_odds - 1.0)))


def normalize_mlb_game(game: Dict[str, Any]) -> MLBGame:
    game_id = str(game.get("game_id") or game.get("id") or "G?")
    home = str(game["home"])
    away = str(game["away"])
    ml = game.get("ml") or {}
    home_odds = int(ml["home"])
    away_odds = int(ml["away"])

    home_raw = american_to_prob_raw(home_odds)
    away_raw = american_to_prob_raw(away_odds)
    home_p, away_p = vig_adjust_two_way(home_raw, away_raw)

    tags: List[str] = []

    if home_p >= away_p:
        favorite = home
        dog = away
        favorite_odds = home_odds
        dog_odds = away_odds
        leader_gap = home_p - away_p
    else:
        favorite = away
        dog = home
        favorite_odds = away_odds
        dog_odds = home_odds
        leader_gap = away_p - home_p

    if -180 <= favorite_odds <= -130 and leader_gap < 0.14:
        tags.append("VULNERABLE_HOLD")
        tags.append("FLAT_FAVORITE_DOWNGRADE")

    if 100 <= dog_odds <= 145:
        tags.append("MLB_LIVE_DOG")

    if leader_gap < 0.05:
        tags.append("COMPRESSED_MARKET")

    return MLBGame(
        game_id=game_id,
        home=home,
        away=away,
        home_american=home_odds,
        away_american=away_odds,
        home_p=home_p,
        away_p=away_p,
        favorite=favorite,
        dog=dog,
        favorite_odds=favorite_odds,
        dog_odds=dog_odds,
        leader_gap=leader_gap,
        tags=tags,
    )


def combo_prob(games: List[MLBGame], picks: List[str]) -> float:
    p = 1.0
    for game, pick in zip(games, picks):
        if pick == game.home:
            p *= game.home_p
        elif pick == game.away:
            p *= game.away_p
        else:
            raise ValueError(f"Pick {pick} not in game {game.game_id}")
    return p


def combo_decimal_odds(games: List[MLBGame], picks: List[str]) -> float:
    dec = 1.0
    for game, pick in zip(games, picks):
        if pick == game.home:
            dec *= american_to_decimal(game.home_american)
        elif pick == game.away:
            dec *= american_to_decimal(game.away_american)
        else:
            raise ValueError(f"Pick {pick} not in game {game.game_id}")
    return dec


def underdog_count(games: List[MLBGame], picks: List[str]) -> int:
    return sum(1 for game, pick in zip(games, picks) if pick == game.dog)


def combo_tags(games: List[MLBGame], picks: List[str]) -> List[str]:
    tags: List[str] = []
    for game, pick in zip(games, picks):
        if pick == game.dog:
            if "MLB_LIVE_DOG" in game.tags:
                tags.append(f"{game.game_id}:DOG:MLB_LIVE_DOG")
            if "COMPRESSED_MARKET" in game.tags:
                tags.append(f"{game.game_id}:DOG:COMPRESSED_MARKET")
        else:
            if "VULNERABLE_HOLD" in game.tags:
                tags.append(f"{game.game_id}:FAV:VULNERABLE_HOLD")
            if "FLAT_FAVORITE_DOWNGRADE" in game.tags:
                tags.append(f"{game.game_id}:FAV:FLAT_FAVORITE_DOWNGRADE")
            if "COMPRESSED_MARKET" in game.tags:
                tags.append(f"{game.game_id}:FAV:COMPRESSED_MARKET")
    return tags


def mlb_b10a3_score(games: List[MLBGame], picks: List[str]) -> float:
    probability_score = combo_prob(games, picks) * 100.0
    dogs = underdog_count(games, picks)
    tags = combo_tags(games, picks)

    score = probability_score

    if dogs == 1:
        score += 1.10
    elif dogs == 2:
        live_dog_count = sum(1 for tag in tags if ":DOG:MLB_LIVE_DOG" in tag)
        vulnerable_hold_count = sum(1 for tag in tags if ":FAV:VULNERABLE_HOLD" in tag)
        if live_dog_count >= 1 or vulnerable_hold_count >= 2:
            score += 0.75
        else:
            score -= 0.50
    elif dogs == 0:
        vulnerable_hold_count = sum(1 for tag in tags if ":FAV:VULNERABLE_HOLD" in tag)
        score += 0.40
        score -= 0.45 * vulnerable_hold_count
    else:
        score -= 6.00

    score += 0.55 * sum(1 for tag in tags if ":DOG:MLB_LIVE_DOG" in tag)
    score -= 0.60 * sum(1 for tag in tags if ":FAV:FLAT_FAVORITE_DOWNGRADE" in tag)
    score -= 0.35 * sum(1 for tag in tags if ":FAV:COMPRESSED_MARKET" in tag)

    return score


def enforce_mlb_structure(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_dogs = {0: [], 1: [], 2: [], 3: []}
    for row in rows:
        by_dogs[int(row["underdogs"])].append(row)

    for dog_count in by_dogs:
        by_dogs[dog_count].sort(key=lambda r: (r["score"], r["combo_prob"]), reverse=True)

    top: List[Dict[str, Any]] = []

    if by_dogs[1]:
        top.append(by_dogs[1].pop(0))
    if by_dogs[0]:
        top.append(by_dogs[0].pop(0))
    if by_dogs[2]:
        top.append(by_dogs[2].pop(0))
    elif by_dogs[1]:
        top.append(by_dogs[1].pop(0))

    rest = by_dogs[0] + by_dogs[1] + by_dogs[2]
    rest.sort(key=lambda r: (r["score"], r["combo_prob"]), reverse=True)
    all_dog = sorted(by_dogs[3], key=lambda r: (r["score"], r["combo_prob"]), reverse=True)

    final = top + rest + all_dog
    for index, row in enumerate(final, start=1):
        row["rank"] = index
    return final[:8]


def rank_mlb_b10a3(games_input: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(games_input) != 3:
        raise ValueError("MLB-B1.0A.3 expects exactly 3 games")

    games = [normalize_mlb_game(game) for game in games_input]
    combos = [list(combo) for combo in product(*[[game.home, game.away] for game in games])]

    rows: List[Dict[str, Any]] = []
    for picks in combos:
        p = combo_prob(games, picks)
        dec = combo_decimal_odds(games, picks)
        dogs = underdog_count(games, picks)
        score = mlb_b10a3_score(games, picks)
        rows.append(
            {
                "picks": picks,
                "combo_prob": round(p, 6),
                "implied_win_pct": round(p * 100.0, 2),
                "parlay_decimal": round(dec, 4),
                "parlay_american": decimal_to_american(dec),
                "underdogs": dogs,
                "tags": combo_tags(games, picks),
                "score": round(score, 4),
            }
        )

    rows.sort(key=lambda r: (r["score"], r["combo_prob"]), reverse=True)
    ranked = enforce_mlb_structure(rows)

    legs = []
    for game in games:
        legs.append(
            {
                "game_id": game.game_id,
                "home": game.home,
                "away": game.away,
                "ml": {"home": game.home_american, "away": game.away_american},
                "p_norm": {"home": round(game.home_p, 4), "away": round(game.away_p, 4)},
                "favorite": game.favorite,
                "dog": game.dog,
                "favorite_odds": game.favorite_odds,
                "dog_odds": game.dog_odds,
                "leader_gap": round(game.leader_gap, 4),
                "tags": game.tags,
            }
        )

    return {
        "ok": True,
        "sport": "mlb",
        "model": "MLB-B1.0A.3",
        "legs": legs,
        "ranked": ranked,
    }
