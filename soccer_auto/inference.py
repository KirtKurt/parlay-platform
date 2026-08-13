"""Immutable T-45 feature locks and champion/challenger inference."""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Mapping

from .canonical import digest, iso_utc, merge_event_payloads, parse_utc
from .market_features import FEATURE_SCHEMA_VERSION, compile_features
from .model import CLASSES, ResidualSoftmaxModel
from .storage import SoccerStore, now_utc, plain


LOCK_VERSION = "soccer-auto-t45-lock-v1"
MIN_BOOKMAKERS = int(os.getenv("SOCCER_AUTO_MIN_BOOKMAKERS", "3"))
PUBLISH_CONFIDENCE = float(os.getenv("SOCCER_AUTO_PUBLISH_CONFIDENCE", "0.50"))


def _latest_and_earliest_by_scope(slots: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for row in slots:
        by_scope.setdefault(row["scope_hash"], []).append(row)
    latest = []
    earliest = []
    for rows in by_scope.values():
        ordered = sorted(rows, key=lambda row: (row["slot_start"], row["payload_sha256"]))
        earliest.append(ordered[0])
        latest.append(ordered[-1])
    return latest, earliest


def _merged_from_pointers(store: SoccerStore, pointers: list[dict[str, Any]]) -> dict[str, Any]:
    return merge_event_payloads(store.read_json(pointer["raw_uri"]) for pointer in pointers)


def build_frozen_lock(store: SoccerStore, event: Mapping[str, Any], *, observed_at: str) -> dict[str, Any]:
    event_key = str(event["event_key"])
    schedule_revision = int(event.get("schedule_revision") or 0)
    if schedule_revision <= 0:
        raise ValueError("a positive schedule_revision is required for a frozen lock")
    commence = parse_utc(str(event["commence_time"]))
    lock_at = commence - timedelta(minutes=45)
    base = {
        "PK": event_key,
        "SK": f"LOCK#T45#REV#{schedule_revision}#TARGET#result_1x2",
        "entity_type": "SOCCER_FROZEN_FEATURE_LOCK",
        "lock_version": LOCK_VERSION,
        "event_key": event_key,
        "event_id": event["event_id"],
        "sport_key": event["sport_key"],
        "commence_time": iso_utc(commence),
        "schedule_revision": schedule_revision,
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "target": "result_1x2",
        "lock_at": iso_utc(lock_at),
        "created_at": observed_at,
        "labels": None,
    }
    if parse_utc(observed_at) < lock_at:
        return {**base, "write_ready": False, "reason": "LOCK_NOT_DUE"}
    slots = store.canonical_slots_before(
        event_key,
        iso_utc(lock_at),
        schedule_revision=schedule_revision,
    )
    if not slots:
        return {
            **base,
            "write_ready": True,
            "training_eligible": False,
            "prediction_eligible": False,
            "exclusion_reasons": ["NO_FINALIZED_PRELOCK_CANONICAL_SLOTS"],
            "source_slot_ids": [],
            "source_payload_hashes": [],
        }
    latest_pointers, earliest_pointers = _latest_and_earliest_by_scope(slots)
    try:
        latest = _merged_from_pointers(store, latest_pointers)
        earliest = _merged_from_pointers(store, earliest_pointers)
        features = compile_features(
            latest,
            earliest=earliest,
            hours_to_start=(commence - lock_at).total_seconds() / 3600.0,
        )
    except Exception as exc:
        return {
            **base,
            "write_ready": True,
            "training_eligible": False,
            "prediction_eligible": False,
            "exclusion_reasons": ["FEATURE_COMPILATION_FAILED"],
            "failure_detail": str(exc),
            "source_slot_ids": [row["SK"] for row in latest_pointers],
            "source_payload_hashes": [row["payload_sha256"] for row in latest_pointers],
        }
    exclusion_reasons = []
    if int(features["book_count"]) < MIN_BOOKMAKERS:
        exclusion_reasons.append("INSUFFICIENT_THREE_WAY_BOOKMAKER_COVERAGE")
    source_hashes = [row["payload_sha256"] for row in latest_pointers]
    feature_hash = digest(
        {
            "event_key": event_key,
            "schedule_revision": schedule_revision,
            "lock_at": iso_utc(lock_at),
            "source_hashes": source_hashes,
            "features": features,
        }
    )
    return {
        **base,
        "write_ready": True,
        "training_eligible": not exclusion_reasons,
        "prediction_eligible": not exclusion_reasons,
        "exclusion_reasons": exclusion_reasons,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_hash": feature_hash,
        "frozen_features": features,
        "source_slot_ids": [row["SK"] for row in latest_pointers],
        "source_payload_hashes": source_hashes,
        "source_raw_uris": [row["raw_uri"] for row in latest_pointers],
        "source_observed_at_max": max(row["observed_at"] for row in latest_pointers),
        "source_observed_before_lock": all(parse_utc(row["observed_at"]) <= lock_at for row in latest_pointers),
    }


def _active_models(store: SoccerStore) -> list[dict[str, Any]]:
    rows = store.model_items()
    active: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("SK") == "CHAMPION":
            active[row["model_digest"]] = {**row, "authority_state": "CHAMPION"}
        elif row.get("authority_state") == "PROSPECTIVE_SHADOW":
            active.setdefault(row["model_digest"], row)
    return sorted(active.values(), key=lambda row: (row.get("authority_state") != "CHAMPION", row["model_digest"]))


def _load_model(store: SoccerStore, row: Mapping[str, Any]) -> ResidualSoftmaxModel:
    artifact = store.read_json(str(row["artifact_uri"]))
    model_payload = artifact.get("model") or artifact
    model = ResidualSoftmaxModel.from_dict(model_payload)
    if model_payload.get("model_digest") != row.get("model_digest"):
        raise ValueError("model registry and artifact digests disagree")
    return model


def _same_schedule(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        return (
            int(left.get("schedule_revision") or 0) > 0
            and int(left.get("schedule_revision") or 0)
            == int(right.get("schedule_revision") or 0)
            and iso_utc(str(left["commence_time"])) == iso_utc(str(right["commence_time"]))
        )
    except (KeyError, TypeError, ValueError):
        return False


def _champion_publish_permission(store: SoccerStore) -> tuple[bool, str, dict[str, Any]]:
    """Read the health authority immediately before champion inference.

    A missing, unreadable, degraded, or promotion-blocked state is a normal
    fail-closed condition.  The champion row is left missing so a later freeze
    cycle can retry it after authority recovers; challenger shadows are still
    written in the same cycle.
    """
    try:
        state = store.ops.get_item(
            Key={"PK": "AUTONOMY", "SK": "STATE"}, ConsistentRead=True
        ).get("Item")
        state = plain(state) if state else {}
    except Exception:
        return False, "AUTONOMY_STATE_UNAVAILABLE", {}
    allowed = bool(
        state.get("authority") == "AUTHORITATIVE"
        and state.get("automatic_prediction_allowed") is True
        and not state.get("promotion_blocked")
    )
    return allowed, "" if allowed else "AUTONOMY_PUBLISH_NOT_ALLOWED", state


def predict_lock(store: SoccerStore, lock: Mapping[str, Any], *, observed_at: str) -> dict[str, Any]:
    if not lock.get("prediction_eligible"):
        return {"models": 0, "predictions": 0, "reason": "LOCK_NOT_PREDICTION_ELIGIBLE"}
    current_event = store.get_event(str(lock["event_key"]))
    if not current_event or not _same_schedule(lock, current_event):
        return {"models": 0, "predictions": 0, "reason": "STALE_SCHEDULE_REVISION"}
    features = lock["frozen_features"]
    schedule_revision = int(lock["schedule_revision"])
    written = 0
    failures = []
    blocked = []
    active_models = _active_models(store)
    publish_allowed, publish_block_reason, autonomy = _champion_publish_permission(store)
    for model_row in active_models:
        try:
            authority = model_row["authority_state"]
            if authority == "CHAMPION" and not publish_allowed:
                blocked.append(
                    {
                        "model_digest": model_row.get("model_digest"),
                        "reason": publish_block_reason,
                        "autonomy_authority": autonomy.get("authority"),
                    }
                )
                continue
            model = _load_model(store, model_row)
            if tuple(model.feature_names) != tuple(features["feature_names"]):
                raise ValueError("feature schema mismatch")
            probabilities = model.predict_proba(features["values"], features["market_prior"])
            winner_index = max(range(3), key=lambda index: probabilities[index])
            confidence = probabilities[winner_index]
            abstention_reasons = []
            if authority != "CHAMPION":
                abstention_reasons.append("CHALLENGER_SHADOW_ONLY")
            if confidence < PUBLISH_CONFIDENCE:
                abstention_reasons.append("CONFIDENCE_BELOW_PREDECLARED_THRESHOLD")
            status = "PUBLISHED" if authority == "CHAMPION" and not abstention_reasons else "NO_PICK" if authority == "CHAMPION" else "SHADOW"
            prediction = {
                "PK": lock["event_key"],
                "SK": (
                    f"PRED#T45#REV#{schedule_revision}#TARGET#result_1x2#"
                    f"MODEL#{model_row['model_digest']}"
                ),
                "entity_type": "SOCCER_MODEL_PREDICTION",
                "event_key": lock["event_key"],
                "event_id": lock["event_id"],
                "sport_key": lock["sport_key"],
                "commence_time": lock["commence_time"],
                "schedule_revision": schedule_revision,
                "home_team": lock.get("home_team"),
                "away_team": lock.get("away_team"),
                "target": "result_1x2",
                "horizon": "T45",
                "created_at": observed_at,
                "lock_at": lock["lock_at"],
                "feature_hash": lock["feature_hash"],
                "feature_schema_version": lock["feature_schema_version"],
                "model_digest": model_row["model_digest"],
                "model_authority": authority,
                "probabilities": {CLASSES[index]: probabilities[index] for index in range(3)},
                "market_prior": {CLASSES[index]: features["market_prior"][index] for index in range(3)},
                "selection": CLASSES[winner_index] if status == "PUBLISHED" else None,
                "highest_probability_outcome": CLASSES[winner_index],
                "confidence": confidence,
                "prediction_status": status,
                "abstention_reasons": abstention_reasons,
                "immutable": True,
                "GSI1PK": "SOCCER_PREDICTIONS",
                "GSI1SK": (
                    f"{lock['commence_time']}#REV#{schedule_revision}#{observed_at}#"
                    f"{lock['event_key']}#{model_row['model_digest']}"
                ),
                "GSI2PK": f"MODEL#{model_row['model_digest']}",
                "GSI2SK": (
                    f"{lock['commence_time']}#REV#{schedule_revision}#{lock['event_key']}"
                ),
            }
            written += int(store.put_prediction(prediction))
        except Exception as exc:
            failures.append({"model_digest": model_row.get("model_digest"), "error": str(exc)})
    return {
        "models": len(active_models),
        "predictions": written,
        "failures": failures,
        "blocked": blocked,
    }


def freeze_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    store = SoccerStore()
    observed = now_utc()
    observed_at = iso_utc(observed)
    events = store.active_events_between(iso_utc(observed), iso_utc(observed + timedelta(minutes=50)))
    created = 0
    blocked = 0
    not_due = 0
    predictions = 0
    retried = 0
    publish_blocked = 0
    failures = []
    for row in events:
        try:
            schedule_revision = int(row.get("schedule_revision") or 0)
            if schedule_revision <= 0:
                raise ValueError("event is missing a positive schedule_revision")
            lock = store.get_lock(
                row["event_key"], schedule_revision=schedule_revision
            )
            if lock:
                retried += 1
            else:
                lock = build_frozen_lock(store, row, observed_at=observed_at)
                if not lock.pop("write_ready", False):
                    not_due += 1
                    continue
                if not store.put_lock(lock):
                    # A concurrent freeze may have won the immutable write.
                    # Read and infer from that exact revision instead of
                    # suppressing missing model predictions until forever.
                    lock = store.get_lock(
                        row["event_key"], schedule_revision=schedule_revision
                    )
                    if not lock:
                        continue
                    retried += 1
                else:
                    created += 1
                    if not lock.get("training_eligible"):
                        blocked += 1
            prediction_result = predict_lock(store, lock, observed_at=observed_at)
            predictions += int(prediction_result.get("predictions") or 0)
            publish_blocked += len(prediction_result.get("blocked") or [])
            failures.extend(prediction_result.get("failures") or [])
        except Exception as exc:
            failures.append({"event_key": row["event_key"], "error": str(exc)})
    return {
        "ok": not failures,
        "system": "soccer_auto",
        "events_considered": len(events),
        "locks_created": created,
        "locks_retried": retried,
        "locks_blocked": blocked,
        "not_due": not_due,
        "predictions_written": predictions,
        "champion_publish_blocked": publish_blocked,
        "failures": failures,
    }
