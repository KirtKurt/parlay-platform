from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Dict, List, Tuple, Any


def american_to_prob_raw(american: int) -> float:
    if american == 0:
        raise ValueError("American odds cannot be 0")
    if american < 0:
        a = abs(american)
        return a / (a + 100.0)
    return 100.0 / (american + 100.0)


def vig_adjust_two_way(p1_raw: float, p2_raw: float) -> Tuple[float, float]:
    s = p1_raw + p2_raw
    if s <= 0:
        return 0.5, 0.5
    return p1_raw / s, p2_raw / s


@dataclass
class GameML:
    game_id: str
    home: str
    away: str
    home_american: int
    away_american: int
    home_p_raw: float
    away_p_raw: float
    home_p: float
    away_p: float
    favorite: str
    dog: str
    fav_p: float
    dog_p: float
    leader_gap: float


def normalize_game(game: Dict[str, Any]) -> GameML:
    game_id = str(game.get("game_id", "G?"))
    home = str(game["home"])
    away = str(game["away"])
    ml = game.get("ml") or {}
    home_odds = int(ml["home"])
    away_odds = int(ml["away"])

    home_p_raw = american_to_prob_raw(home_odds)
    away_p_raw = american_to_prob_raw(away_odds)
    home_p, away_p = vig_adjust_two_way(home_p_raw, away_p_raw)

    if home_p >= away_p:
        favorite, dog = home, away
        fav_p, dog_p = home_p, away_p
    else:
        favorite, dog = away, home
        fav_p, dog_p = away_p, home_p

    return GameML(
        game_id=game_id,
        home=home,
        away=away,
        home_american=home_odds,
        away_american=away_odds,
        home_p_raw=home_p_raw,
        away_p_raw=away_p_raw,
        home_p=home_p,
        away_p=away_p,
        favorite=favorite,
        dog=dog,
        fav_p=fav_p,
        dog_p=dog_p,
        leader_gap=(fav_p - dog_p),
    )


def detect_regime(games: List[GameML]) -> str:
    avg_gap = sum(g.leader_gap for g in games) / max(1, len(games))
    if avg_gap >= 0.08:
        return "CLEAR"
    if avg_gap >= 0.05:
        return "MODERATE"
    return "COMPRESSED"


def combo_prob(games: List[GameML], picks: List[str]) -> float:
    p = 1.0
    for g, pick in zip(games, picks):
        if pick == g.home:
            p *= g.home_p
        elif pick == g.away:
            p *= g.away_p
        else:
            raise ValueError(f"Pick {pick} not in game {g.game_id}")
    return p


def underdog_count(games: List[GameML], picks: List[str]) -> int:
    return sum(1 for g, pick in zip(games, picks) if pick == g.dog)


def nba_b11c1_score(games: List[GameML], picks: List[str], regime: str) -> float:
    base = combo_prob(games, picks) * 100.0
    dogs = underdog_count(games, picks)

    score = base
    if dogs == 1:
        score += 1.25
    elif dogs == 2:
        score += 0.35
    elif dogs == 0:
        score += 0.60
    else:  # 3 dogs
        score -= 5.0

    if regime == "COMPRESSED" and dogs == 3:
        score -= 3.0

    return score


def generate_all_combos(games: List[GameML]) -> List[List[str]]:
    return [list(c) for c in product(*[[g.home, g.away] for g in games])]


def enforce_structural_ranking(games: List[GameML], ranked: List[Dict[str, Any]], regime: str) -> List[Dict[str, Any]]:
    by_dogs: Dict[int, List[Dict[str, Any]]] = {0: [], 1: [], 2: [], 3: []}
    for row in ranked:
        by_dogs[int(row["underdogs"])].append(row)

    for k in by_dogs:
        by_dogs[k].sort(key=lambda r: (r["score"], r["combo_prob"]), reverse=True)

    top: List[Dict[str, Any]] = []
    top.extend(by_dogs[1][:2])
    by_dogs[1] = by_dogs[1][2:]

    if regime in ("CLEAR", "MODERATE"):
        if by_dogs[0]:
            top.append(by_dogs[0].pop(0))
        elif by_dogs[2]:
            top.append(by_dogs[2].pop(0))
    else:
        if by_dogs[2]:
            top.append(by_dogs[2].pop(0))
        elif by_dogs[0]:
            top.append(by_dogs[0].pop(0))

    rest = by_dogs[0] + by_dogs[1] + by_dogs[2] + by_dogs[3]
    rest.sort(key=lambda r: (r["score"], r["combo_prob"]), reverse=True)

    non_all_dog = [r for r in rest if int(r["underdogs"]) != 3]
    all_dog = [r for r in rest if int(r["underdogs"]) == 3]

    final = top + non_all_dog + all_dog
    for i, r in enumerate(final, start=1):
        r["rank"] = i
    return final[:8]


def rank_nba_b11c1(games_input: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(games_input) != 3:
        raise ValueError("NBA-B1.1C.1 expects exactly 3 games for 3-leg slate")

    games = [normalize_game(g) for g in games_input]
    regime = detect_regime(games)

    combos = generate_all_combos(games)

    rows: List[Dict[str, Any]] = []
    for picks in combos:
        p = combo_prob(games, picks)
        dogs = underdog_count(games, picks)
        score = nba_b11c1_score(games, picks, regime)

        rows.append(
            {
                "picks": picks,
                "combo_prob": round(p, 6),
                "implied_win_pct": round(p * 100.0, 2),
                "underdogs": dogs,
                "score": round(score, 4),
            }
        )

    rows.sort(key=lambda r: (r["score"], r["combo_prob"]), reverse=True)
    rows = enforce_structural_ranking(games, rows, regime)

    legs = []
    for g in games:
        legs.append(
            {
                "game_id": g.game_id,
                "home": g.home,
                "away": g.away,
                "ml": {"home": g.home_american, "away": g.away_american},
                "p_norm": {"home": round(g.home_p, 4), "away": round(g.away_p, 4)},
                "favorite": g.favorite,
                "leader_gap": round(g.leader_gap, 4),
            }
        )

    return {"ok": True, "sport": "nba", "model": "NBA-B1.1C.1", "regime": regime, "legs": legs, "ranked": rows}
