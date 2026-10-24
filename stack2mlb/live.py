"""Map a live KS1 prediction row onto 2stackMLB.decide().

Official fields are read, never written. Missing market -> pass.
"""
from __future__ import annotations

from stack2mlb.elo import EloBook
from stack2mlb.markov import p_home as markov_p_home
from stack2mlb.poisson_bridge import residual_features
from stack2mlb.stack import decide


def starter_unverified(row: dict) -> bool:
    lineup = str(row.get("lineup_status") or "")
    source = str(row.get("starter_feature_source") or "")
    if lineup and lineup != "confirmed":
        return True
    if source == "team_starter_prior":
        return True
    home_s = str(row.get("home_starter_status") or "")
    away_s = str(row.get("away_starter_status") or "")
    if home_s in {"probable", "unconfirmed", ""} or away_s in {"probable", "unconfirmed", ""}:
        if lineup != "confirmed":
            return True
    return False


def attach(row: dict, book: EloBook | None = None, *, markov_sims: int = 4000) -> dict:
    official = float(row["p_home"])
    poisson = row.get("p_home_poisson")
    if poisson is None:
        from stack2mlb.poisson_bridge import home_probability
        poisson, _ = home_probability(row["lambda_home"], row["lambda_away"])
    market = row.get("market_home_prob")
    if market is None:
        decision = decide(
            p_lgb=official,
            p_poisson=float(poisson),
            p_market=0.5,
            p_elo=0.5,
            p_home_official=official,
            starter_unverified=True,
        )
        out = decision.as_dict()
        out.update(pick_status="pass", selection_reason="market_unavailable", shadow=True, promoted=False)
        return out

    book = book or EloBook()
    elo = book.p_home(
        str(row["home_id"]),
        str(row["away_id"]),
        row.get("home_starter_id"),
        row.get("away_starter_id"),
    )
    markov = markov_p_home(float(row["lambda_home"]), float(row["lambda_away"]), sims=markov_sims)
    decision = decide(
        p_lgb=official,
        p_poisson=float(poisson),
        p_market=float(market),
        p_elo=elo,
        p_home_official=official,
        p_markov=markov,
        starter_unverified=starter_unverified(row),
    )
    out = decision.as_dict()
    out.update(
        game_id=str(row["game_id"]),
        date=row.get("date"),
        shadow=True,
        promoted=False,
        official_probability_field="p_home",
        residual_features=residual_features(
            float(row["lambda_home"]),
            float(row["lambda_away"]),
            float(market),
            official,
        ),
    )
    if out["p_home_official"] != official:
        raise ValueError("2stackMLB tried to rewrite official p_home")
    return out
