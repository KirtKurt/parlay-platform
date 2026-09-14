"""Separate goals-training path alongside the existing market-model schedule.

All models remain shadow-only. No champion/public-authority writes occur here.
"""
from __future__ import annotations

import json
import os
from typing import Any

from botocore.exceptions import ClientError

from .canonical import digest, iso_utc, parse_utc
from .kss1_bbd import BbdClient, BbdError, extract_match_xg
from .kss1_features import HistoryIndex, build_training_table, normalize_history
from .kss1_goals_model import train_and_validate
from .kss1_identity import classify_competition, map_event
from .settlement import settlement_training_admissible, settlement_training_views
from .storage import SoccerStore, ddb_safe, now_utc, plain

MODEL_PK = "MODEL#kss1_goals#global"
CONTEXT_KEY = {"PK": MODEL_PK, "SK": "SHADOW_CONTEXT"}


def source_history(store) -> list[dict[str, Any]]:
    from .trainer import _settlement_conflict_events
    conflicts = _settlement_conflict_events(store)
    rows = []
    signed = settlement_training_views(list(store.scan_all(store.settlements, ConsistentRead=True)))
    for row in signed:
        if row["event_key"] in conflicts or not settlement_training_admissible(row):
            continue
        if not classify_competition(row["sport_key"])["goals_model_eligible"]:
            continue
        certificate = row.get("training_admissibility_certificate") or {}
        available = max(parse_utc(row["observed_at"]), parse_utc(row.get("completed_at") or row["observed_at"]), parse_utc(certificate.get("observed_at") or row["observed_at"]))
        rows.append({key: row[key] for key in ("event_key", "sport_key", "home_team", "away_team", "commence_time", "home_score", "away_score")} | {
            "available_at": iso_utc(available), "source_receipt": digest(row),
            "source": "the_odds_api_signed_settlement", "provenance_mode": "VERIFIED_RECEIPT",
            "home_xg": None, "away_xg": None,
        })
    return normalize_history(rows)


def load_context(store) -> dict[str, Any] | None:
    pointer = plain(store.models.get_item(Key=CONTEXT_KEY, ConsistentRead=True).get("Item"))
    if not pointer:
        return None
    uri = str(pointer.get("artifact_uri") or "")
    if not uri.startswith(f"s3://{store.artifact_bucket}/artifacts/kss1/"):
        raise ValueError("goals context must use the isolated artifact bucket")
    payload = store.read_json(uri)
    if digest(payload) != pointer.get("artifact_digest"):
        raise ValueError("goals context artifact digest mismatch")
    if payload.get("automatic_prediction_allowed") is not False:
        raise ValueError("goals context must remain shadow-only")
    return payload


def _provider_match(row):
    def name(value):
        return value.get("name") if isinstance(value, dict) else value
    return {"id": row.get("id") or row.get("match_id"),
            "home": name(row.get("home") or row.get("home_team")),
            "away": name(row.get("away") or row.get("away_team")),
            "kickoff_utc": row.get("kickoff_utc") or row.get("kickoff") or row.get("commence_time") or row.get("start_time")}


def enrich_xg(store, rows, previous, token, *, limit=20):
    cached = {r["event_key"]: r for r in (previous or {}).get("history", [])}
    for row in rows:
        old = cached.get(row["event_key"])
        if old and old["source_receipt"] == row["source_receipt"] and old.get("home_xg") is not None:
            for key in ("home_xg", "away_xg", "xg_available_at", "xg_source_receipt", "xg_receipt_uri"):
                if key in old:
                    row[key] = old[key]
    status = {"configured": bool(token), "fetched": 0, "unmapped": 0, "missing_xg": 0, "errors": 0}
    if not token:
        status["reason"] = "BBD_CREDENTIAL_NOT_CONFIGURED"
        return rows, status
    client = BbdClient(token)
    competitions = {}
    attempted = 0
    # Oldest remaining rows are not repeatedly allowed to starve recent games.
    # Existing retained receipts are reused; every new receipt is timestamped now.
    for row in reversed(rows):
        if row.get("home_xg") is not None:
            continue
        if attempted >= limit:
            break
        league = classify_competition(row["sport_key"])["bbd_league"]
        if not league:
            continue
        try:
            if league not in competitions:
                competitions[league] = []
                competitions[league] = [_provider_match(r) for r in client.list_matches(league, limit=50) if isinstance(r, dict)]
            match = map_event(odds_event_id=row["event_key"], sport_key=row["sport_key"], home_team=row["home_team"], away_team=row["away_team"], commence_time=row["commence_time"], bbd_matches=competitions[league])
            if match["status"] != "mapped":
                status["unmapped"] += 1
                continue
            attempted += 1
            payload = client.match_stats(match["bbd_match_id"])
            observed = iso_utc(now_utc())
            xg = extract_match_xg(payload)
            if not xg["has_xg"]:
                status["missing_xg"] += 1
                continue
            receipt = {"provider": "BBD", "path": f'/v1/stored/matches/{match["bbd_match_id"]}/stats', "identity": match, "observed_at": observed, "response": payload}
            receipt_hash = digest(receipt)
            uri = store.write_artifact("kss1/bbd_receipts", receipt, receipt_hash)
            row.update(home_xg=xg["xg_home"], away_xg=xg["xg_away"], xg_available_at=observed, xg_source_receipt=receipt_hash, xg_receipt_uri=uri)
            status["fetched"] += 1
        except (BbdError, TypeError, ValueError, json.JSONDecodeError):
            status["errors"] += 1
            # Do not include provider exception text or credentials in reports.
    return normalize_history(rows), status


def train_goals_shadow(store, *, token="") -> dict[str, Any]:
    started = iso_utc(now_utc())
    previous = load_context(store)
    history = source_history(store)
    history, bbd = enrich_xg(store, history, previous, token)
    history = history[-5000:]
    index = HistoryIndex(history)
    table = build_training_table(index)
    report = train_and_validate(table, min_train=500, min_test=100)
    feature_digest = digest({"rows": table})
    table_uri = store.write_artifact("kss1/training_tables", {"rows": table}, feature_digest)
    model = report.get("model")
    if not model and previous:
        model = previous.get("model")
    # A failed retrospective candidate cannot replace a previously tested one.
    if model and report.get("trained") and not report["lower_brier_no_worse_log_loss"]:
        model = (previous or {}).get("model")
    context = {"context_as_of": started, "created_at": iso_utc(now_utc()),
               "history": history, "model": model, "training_report": report,
               "bbd": bbd, "training_table_uri": table_uri,
               "automatic_prediction_allowed": False}
    context_digest = digest(context)
    uri = store.write_artifact("kss1/contexts", context, context_digest)
    pointer = {**CONTEXT_KEY, "entity_type": "KSS1_GOALS_SHADOW_CONTEXT", "context_as_of": started,
               "artifact_uri": uri, "artifact_digest": context_digest,
               "training_rows": len(table), "bbd": bbd, "trained": report["trained"],
               "model_digest": model["model_digest"] if model else None,
               "candidate_model_digest": report.get("model", {}).get("model_digest"),
               "candidate_holdout": report.get("holdout"),
               "candidate_baseline": report.get("baseline"),
               "qualification_blockers": report.get("qualification_blockers", [report.get("reason")]),
               "automatic_prediction_allowed": False}
    try:
        store.models.put_item(Item=ddb_safe(pointer), ConditionExpression="attribute_not_exists(context_as_of) OR context_as_of < :cutoff", ExpressionAttributeValues={":cutoff": started})
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return {"trained": report["trained"], "published_context": False, "reason": "NEWER_GOALS_CONTEXT_EXISTS"}
    return {"trained": report["trained"], "published_context": True, "training_rows": len(table),
            "reason": report.get("reason"), "bbd": bbd, "artifact_uri": uri,
            "model_digest": pointer["model_digest"], "automatic_prediction_allowed": False}


def trainer_handler(event, context):
    # Separate scheduled Lambda: a market-trainer timeout cannot starve goals.
    result = train_goals_shadow(SoccerStore(), token=os.getenv("SOCCER_AUTO_BBD_API_KEY", ""))
    return {"ok": True, "system": "soccer_auto", "goals_training": result}


def goals_status(store):
    row = plain(store.models.get_item(Key=CONTEXT_KEY, ConsistentRead=True).get("Item"))
    return {"ok": True, "system": "kss1_goals", "authority": "SHADOW_LEARNING",
            "automatic_prediction_allowed": False, "training_context": row,
            "reason": "GOALS_TRAINER_HAS_NOT_COMPLETED" if row is None else None}
