"""Live KSS1 shadow writer.

The freeze Lambda entrypoint wraps soccer_auto.inference.freeze_handler so
existing lock/prediction contract tests stay on the original function.
"""
from __future__ import annotations

from datetime import timedelta
import json
from typing import Any, Mapping

from soccer_auto.canonical import digest, iso_utc, parse_utc
from soccer_auto.kss1_features import HistoryIndex
from soccer_auto.inference import freeze_handler as _freeze_handler
from soccer_auto.kss1_engine import ENGINE_ID, predict_match
from soccer_auto.kss1_markets import settle_regulation
from soccer_auto.storage import SoccerStore, now_utc

# T-60 means "no later than 60 minutes before kickoff", not "only inside the
# last 90 minutes". Deploy-time and morning freezes must see today's slate.
KSS1_SHADOW_LOOKAHEAD = timedelta(hours=36)


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


def kss1_prediction_sk(lock: Mapping[str, Any], model_digest: str = ENGINE_ID, *, goals_context_as_of=None) -> str:
    context_suffix = f"#CONTEXT#{digest(goals_context_as_of)}" if goals_context_as_of is not None else ""
    return (
        f"PRED#KSS1#REV#{int(lock.get('schedule_revision') or 0)}"
        f"#TARGET#kss1_book#MODEL#{model_digest}{context_suffix}"
    )


def shadow_source_from_event(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_key": row.get("event_key"),
        "event_id": row.get("event_id") or row.get("odds_event_id"),
        "sport_key": row.get("sport_key"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "commence_time": row.get("commence_time"),
        "schedule_revision": int(row.get("schedule_revision") or 0),
        "lock_at": row.get("lock_at"),
        "feature_hash": row.get("feature_hash"),
        "frozen_features": row.get("frozen_features") or {},
        "prediction_eligible": False,
    }


def build_kss1_shadow_item(lock: Mapping[str, Any], observed_at: str, *, goals_context=None, history_index=None) -> dict[str, Any]:
    features = None
    model = None
    goal_inputs = {}
    if goals_context is not None:
        deadline = parse_utc(lock["commence_time"]) - timedelta(minutes=60)
        cutoff = min(parse_utc(observed_at), deadline)
        if parse_utc(goals_context["created_at"]) > cutoff:
            raise ValueError("goals context unavailable at prediction cutoff")
        index = history_index or HistoryIndex(goals_context["history"])
        if any(row["provenance_mode"] != "VERIFIED_RECEIPT" for row in index.rows):
            raise ValueError("research-only history cannot serve live predictions")
        features = index.features(lock, iso_utc(cutoff))
        model = goals_context.get("model")
        if model and model.get("research_only"):
            raise ValueError("research model cannot serve live predictions")
        goal_inputs = features["values"]
    book = predict_match(
        {
            "odds_event_id": lock.get("event_id"),
            "sport_key": lock.get("sport_key"),
            "home_team": lock.get("home_team"),
            "away_team": lock.get("away_team"),
            "commence_time": lock.get("commence_time"),
            "observed_at": observed_at,
            "market_1x2": market_prior_from_lock(lock),
            **goal_inputs,
        },
        goals_model=model,
        goals_features=features,
    )
    model_digest = model["model_digest"] if model else ENGINE_ID
    context_as_of = goals_context["context_as_of"] if goals_context else None
    return {
        "PK": lock["event_key"],
        "SK": kss1_prediction_sk(lock, model_digest, goals_context_as_of=context_as_of),
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
        "model_digest": model_digest,
        "model_authority": "SHADOW",
        "prediction_status": "SHADOW",
        "automatic_prediction_allowed": False,
        "kss1": book,
        "goals_features": features,
        "goals_context_as_of": context_as_of,
        "immutable": True,
        "created_at": observed_at,
    }


def write_kss1_shadow(store: SoccerStore, lock: Mapping[str, Any], observed_at: str, *, goals_context=None, history_index=None) -> dict[str, Any]:
    if not lock.get("event_key") or not lock.get("home_team") or not lock.get("away_team"):
        return {"written": False, "reason": "EVENT_IDENTITY_INCOMPLETE"}
    if goals_context is not None and parse_utc(observed_at) > parse_utc(lock["commence_time"]) - timedelta(minutes=60):
        return {"written": False, "reason": "MISSED_T60_GOALS_SHADOW"}
    item = build_kss1_shadow_item(lock, observed_at, goals_context=goals_context, history_index=history_index)
    if len(json.dumps(item, ensure_ascii=False).encode("utf-8")) > 300_000:
        features = item.pop("goals_features")
        item["goals_features_uri"] = store.write_artifact("kss1/prediction_features", features, digest(features))
        item["goals_feature_digest"] = features["feature_digest"]
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
    from soccer_auto.kss1_training_runtime import load_context
    goals_context = None
    history_index = None
    try:
        goals_context = load_context(store)
        if goals_context:
            history_index = HistoryIndex(goals_context["history"])
    except Exception as exc:
        result["kss1_context_error"] = type(exc).__name__
    try:
        events = store.active_events_between(
            iso_utc(observed),
            iso_utc(observed + KSS1_SHADOW_LOOKAHEAD),
        )
        result["kss1_events_considered"] = len(events)
        for row in events:
            revision = int(row.get("schedule_revision") or 0)
            lock = None
            if revision > 0:
                lock = store.get_lock(
                    row["event_key"],
                    schedule_revision=revision,
                    horizon="T10",
                )
            source = lock or shadow_source_from_event(row)
            try:
                outcome = write_kss1_shadow(store, source, observed_at, goals_context=goals_context, history_index=history_index)
            except (TypeError, ValueError) as exc:
                result["kss1_feature_errors"] = result.get("kss1_feature_errors", 0) + 1
                skipped += 1
                continue
            written += int(bool(outcome.get("written")))
            skipped += int(not outcome.get("written"))
    except Exception as exc:
        result["kss1_shadow_error"] = str(exc)[:500]
    result["kss1_engine"] = ENGINE_ID
    result["kss1_shadow_written"] = written
    result["kss1_shadow_skipped"] = skipped
    return result
