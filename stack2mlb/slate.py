"""Apply 2stackMLB to a slate. Tonight's NYY/NYM and CWS/STL are fixtures."""
from __future__ import annotations

from stack2mlb.bayes_market import update as bayes_update
from stack2mlb.elo import EloBook
from stack2mlb.market import fair_from_american
from stack2mlb.markov import p_home as markov_p_home
from stack2mlb.poisson_bridge import home_probability, residual_features
from stack2mlb.stack import decide


TONIGHT = [
    {
        "game_id": "2026-09-11-NYM@NYY",
        "date": "2026-09-11",
        "away_team": "NYM",
        "home_team": "NYY",
        "away_id": "121",
        "home_id": "147",
        "home_starter": "rodon",
        "away_starter": "mclean",
        "home_odds": -142,
        "away_odds": 122,
        "p_lgb": 0.77,
        "p_poisson": 0.54,
        "lambda_home": 4.55,
        "lambda_away": 3.95,
        "elo_home": 1564.0,
        "elo_away": 1488.0,
        "elo_home_starter": 1548.0,
        "elo_away_starter": 1510.0,
    },
    {
        "game_id": "2026-09-11-CWS@STL",
        "date": "2026-09-11",
        "away_team": "CWS",
        "home_team": "STL",
        "away_id": "145",
        "home_id": "138",
        "home_starter": "liberatore",
        "away_starter": "kay",
        "home_odds": -108,
        "away_odds": -112,
        "p_lgb_away_team": 0.64,
        "p_poisson_away_team": 0.44,
        "lambda_home": 4.20,
        "lambda_away": 4.35,
        "elo_home": 1504.0,
        "elo_away": 1518.0,
        "elo_home_starter": 1492.0,
        "elo_away_starter": 1506.0,
    },
]


def _book_for(row: dict) -> EloBook:
    book = EloBook()
    book.teams[row["home_id"]] = float(row["elo_home"])
    book.teams[row["away_id"]] = float(row["elo_away"])
    book.pitchers[row["home_starter"]] = float(row["elo_home_starter"])
    book.pitchers[row["away_starter"]] = float(row["elo_away_starter"])
    return book


def score_row(row: dict, markov_sims: int = 6000) -> dict:
    market = fair_from_american(row["home_odds"], row["away_odds"])
    p_poi, _ = home_probability(row["lambda_home"], row["lambda_away"])
    if "p_lgb" in row:
        p_lgb = float(row["p_lgb"])
    else:
        p_lgb = 1.0 - float(row["p_lgb_away_team"])
    if "p_poisson" in row:
        p_poi = float(row["p_poisson"])
    elif "p_poisson_away_team" in row:
        p_poi = 1.0 - float(row["p_poisson_away_team"])
    elo = _book_for(row).p_home(
        row["home_id"], row["away_id"], row["home_starter"], row["away_starter"]
    )
    markov = markov_p_home(row["lambda_home"], row["lambda_away"], sims=markov_sims)
    decision = decide(
        p_lgb=p_lgb,
        p_poisson=p_poi,
        p_market=market,
        p_elo=elo,
        p_home_official=p_lgb,
        p_markov=markov,
    )
    features = residual_features(row["lambda_home"], row["lambda_away"], market, p_lgb)
    return {
        "game_id": row["game_id"],
        "matchup": f"{row['away_team']} @ {row['home_team']}",
        "p_market": market,
        "p_elo": elo,
        "p_markov": markov,
        **decision.as_dict(),
        "residual_features": features,
        "capped_lgb": bayes_update(market, p_lgb, kappa=0.25),
    }


def tonight(markov_sims: int = 6000) -> list[dict]:
    return [score_row(row, markov_sims=markov_sims) for row in TONIGHT]


def main() -> None:
    import json
    print(json.dumps(tonight(), indent=2, default=str))


if __name__ == "__main__":
    main()
