"""Live KSS1 shadow writer.

The freeze Lambda entrypoint wraps soccer_auto.inference.freeze_handler so
existing lock/prediction contract tests stay on the original function.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from soccer_auto.canonical import iso_utc
from soccer_auto.inference import freeze_handler as _freeze_handler
from soccer_auto.kss1_engine import ENGINE_ID, predict_match
from soccer_auto.kss1_markets import settle_regulation
from soccer_auto.storage import SoccerStore, now_utc


def market_prior_from_lock(lock: Mapping[str, Any]) -> dict[str, float] | None:
    features = lock.get("frozen_features") or {}
    prior = features.get("market_prior")
    if not prior or len(prior) != 3:
        return None
    return {
        "home": float(prior[0]),
        "draw": float(prior[1]),
        "away": float(prior[2]),
    }


def kss1_prediction_sk(lock: Mapping[str, Any]) -> str:
    return (
        f"PRED#KSS1#REV#{int(lock['schedule_revision'])}"
        f"#TARGET#kss1_book#MODEL#{ENGINE_ID}"
    )


def build_kss1_shadow_item(lock: Mapping[str, Any], observed_at: str) -> dict[str, Any]:
    book = predict_match(
        {
            "odds_event_id": lock.get("event_id"),
            "sport_key": lock.get("sport_key"),
            "home_team": lock.get("home_team"),
            "away_team": lock.get("away_team"),
            "commence_time": lock.get("commence_time"),
            "observed_at": observed_at,
            "market_1x2": market_prior_from_lock(lock),
        }
    )
    return {
        "PK": lock["event_key"],
        "SK": kss1_prediction_sk(lock),
        "entity_type": "SOCCER_MODEL_PREDICTION",
        "event_key": lock["event_key"],
        "event_id": lock.get("event_id"),
        "sport_key": lock.get("sport_key"),
        "commence_time": lock.get("commence_time"),
        "schedule_revision": int(lock.get("schedule_revision") or 0),
        "home_team": lock.get("home_team"),
        "away_team": lock.get("away_team"),
        "target": "kss1_book",
        "horizon": book.get("public_horizon"),
        "lock_at": lock.get("lock_at"),
        "feature_hash": lock.get("feature_hash"),
        "model_digest": ENGINE_ID,
        "model_authority": "SHADOW",
        "prediction_status": "SHADOW",
        "automatic_prediction_allowed": False,
        "kss1": book,
        "immutable": True,
        "created_at": observed_at,
    }


def write_kss1_shadow(store: SoccerStore, lock: Mapping[str, Any], observed_at: str) -> dict[str, Any]:
    if not lock.get("prediction_eligible"):
        return {"written": False, "reason": "LOCK_NOT_PREDICTION_ELIGIBLE"}
    item = build_kss1_shadow_item(lock, observed_at)
    return {"written": bool(store.put_prediction(item)), "sk": item["SK"]}


def grade_kss1_against_settlement(prediction: Mapping[str, Any], settlement: Mapping[str, Any]) -> dict[str, Any]:
    home = int(settlement["home_score"])
    away = int(settlement["away_score"])
    actual = settle_regulation(home, away)
    markets = (prediction.get("kss1") or {}).get("markets") or {}
    dc_hits = {"1X": actual["1X"] == "hit", "12": actual["12"] == "hit", "X2": actual["X2"] == "hit"}
    graded = {}
    for market, pick, truth in (
        ("1x2", markets.get("1x2_published"), actual["1x2"]),
        ("ou25", markets.get("ou25_published"), actual["over_25"]),
        ("btts", markets.get("btts_published"), actual["btts"]),
    ):
        if pick in (None, "ABSTAIN"):
            graded[market] = "abstain"
        else:
            graded[market] = "hit" if pick == truth else "miss"
    dc_pick = markets.get("double_chance_published")
    if dc_pick in (None, "ABSTAIN"):
        graded["double_chance"] = "abstain"
    else:
        graded["double_chance"] = "hit" if dc_hits.get(dc_pick) else "miss"
    return {"actual": actual, "graded": graded}


def freeze_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    result = _freeze_handler(event, context)
    store = SoccerStore()
    observed = now_utc()
    observed_at = iso_utc(observed)
    written = 0
    skipped = 0
    try:
        events = store.active_events_between(
            iso_utc(observed),
            iso_utc(observed + timedelta(minutes=50)),
        )
        for row in events:
            revision = int(row.get("schedule_revision") or 0)
            if revision <= 0:
                skipped += 1
                continue
            lock = store.get_lock(
                row["event_key"],
                schedule_revision=revision,
                horizon="T10",
            )
            if not lock:
                skipped += 1
                continue
            outcome = write_kss1_shadow(store, lock, observed_at)
            written += int(bool(outcome.get("written")))
            skipped += int(not outcome.get("written"))
    except Exception as exc:
        result["kss1_shadow_error"] = str(exc)[:500]
    result["kss1_engine"] = ENGINE_ID
    result["kss1_shadow_written"] = written
    result["kss1_shadow_skipped"] = skipped
    return result
