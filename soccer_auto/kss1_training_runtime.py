"""Separate goals-training path alongside the existing market-model schedule.

All models remain shadow-only. No champion/public-authority writes occur here.
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .canonical import digest, iso_utc, parse_utc
from .kss1_bbd import BbdClient, BbdError, extract_match_xg
from .kss1_features import MIN_TEAM_GAMES, HistoryIndex, build_training_table, normalize_history
from .kss1_goals_model import train_and_validate
from .kss1_identity import classify_competition, map_event
from .settlement import settlement_training_admissible, settlement_training_evidence_valid, settlement_training_views
from .storage import SoccerStore, ddb_safe, now_utc, plain

MODEL_PK = "MODEL#kss1_goals#global"
CONTEXT_KEY = {"PK": MODEL_PK, "SK": "SHADOW_CONTEXT"}


def source_history(store, *, audit=None) -> list[dict[str, Any]]:
    from .trainer import _settlement_conflict_events
    conflicts = _settlement_conflict_events(store)
    rows = []
    signed = settlement_training_views(list(store.scan_all(store.settlements, ConsistentRead=True)))
    counts = {"final_score_rows": len(signed), "invalid_score_evidence": 0,
              "conflicted_scores": 0, "regulation_ineligible": 0,
              "competition_ineligible": 0, "accepted_scores": 0}
    competitions = {}
    for row in signed:
        if not settlement_training_evidence_valid(row):
            counts["invalid_score_evidence"] += 1
            continue
        if row["event_key"] in conflicts:
            counts["conflicted_scores"] += 1
            continue
        if not settlement_training_admissible(row):
            counts["regulation_ineligible"] += 1
            continue
        if not classify_competition(row["sport_key"])["goals_model_eligible"]:
            counts["competition_ineligible"] += 1
            continue
        certificate = row.get("training_admissibility_certificate") or {}
        available = max(parse_utc(row["observed_at"]), parse_utc(row.get("completed_at") or row["observed_at"]), parse_utc(certificate.get("observed_at") or row["observed_at"]))
        rows.append({key: row[key] for key in ("event_key", "sport_key", "home_team", "away_team", "commence_time", "home_score", "away_score")} | {
            "available_at": iso_utc(available), "source_receipt": digest(row),
            "source": "the_odds_api_signed_settlement", "provenance_mode": "VERIFIED_RECEIPT",
            "home_xg": None, "away_xg": None,
        })
        counts["accepted_scores"] += 1
        competitions[row["sport_key"]] = competitions.get(row["sport_key"], 0) + 1
    result = normalize_history(rows)
    if audit is not None:
        audit.update(counts, accepted_by_competition=competitions,
                     unique_history_rows=len(result),
                     source="the_odds_api_signed_settlement",
                     availability_policy="verified score and certificate receipts; never kickoff-derived",
                     oldest_kickoff=result[0]["commence_time"] if result else None,
                     newest_kickoff=result[-1]["commence_time"] if result else None)
    return result


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
        if old and old["source_receipt"] == row["source_receipt"] and old.get("xg_last_attempt_at"):
            row["xg_last_attempt_at"] = old["xg_last_attempt_at"]
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
    # Unattempted games first, newest among ties; then least recently attempted.
    # Persist attempts even for errors/missing xG so the same failures cannot
    # consume every run's budget and starve older available statistics.
    pending = sorted(rows, key=lambda r: (r.get("xg_last_attempt_at") or "", -parse_utc(r["commence_time"]).timestamp(), r["event_key"]))
    for row in pending:
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
            row["xg_last_attempt_at"] = iso_utc(now_utc())
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
    source_audit = {}
    history = source_history(store, audit=source_audit)
    history, bbd = enrich_xg(store, history, previous, token)
    history = history[-5000:]
    index = HistoryIndex(history)
    table = build_training_table(index)
    report = train_and_validate(table, min_train=500, min_test=100)
    readiness = {key: report[key] for key in (
        "input_rows", "eligible_rows", "excluded_insufficient_team_history",
        "required_minimum", "additional_eligible_rows_lower_bound",
        "required_split_counts", "split_counts", "split_boundaries", "xg_required",
    ) if key in report}
    readiness["source_audit"] = source_audit
    readiness["minimum_prior_games_per_team"] = MIN_TEAM_GAMES
    readiness["eligible_by_competition"] = {}
    for row in table:
        if row["features"]["team_strength_complete"]:
            sport = row["features"]["sport_key"]
            readiness["eligible_by_competition"][sport] = readiness["eligible_by_competition"].get(sport, 0) + 1
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
               "bbd": bbd, "training_table_uri": table_uri, "readiness": readiness,
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
               "candidate_passed_retrospective": bool(report.get("lower_brier_no_worse_log_loss") and not report.get("research_only")),
               "readiness": readiness,
               "qualification_blockers": report.get("qualification_blockers", [report.get("reason")]),
               "automatic_prediction_allowed": False}
    try:
        store.models.put_item(Item=ddb_safe(pointer), ConditionExpression="attribute_not_exists(context_as_of) OR context_as_of < :cutoff", ExpressionAttributeValues={":cutoff": started})
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return {"trained": report["trained"], "published_context": False, "reason": "NEWER_GOALS_CONTEXT_EXISTS"}
    return {"trained": report["trained"], "published_context": True, "training_rows": len(table),
            "reason": report.get("reason"), "bbd": bbd, "artifact_uri": uri, "readiness": readiness,
            "model_digest": pointer["model_digest"], "automatic_prediction_allowed": False}


def _bbd_token():
    arn = os.getenv("SOCCER_AUTO_BBD_SECRET_ARN", "").strip()
    if not arn:
        return ""
    return (boto3.client("secretsmanager").get_secret_value(SecretId=arn).get("SecretString") or "").strip()


def trainer_handler(event, context):
    # Separate scheduled Lambda: a market-trainer timeout cannot starve goals.
    result = train_goals_shadow(SoccerStore(), token=_bbd_token())
    return {"ok": True, "system": "soccer_auto", "goals_training": result}


def goals_status(store):
    row = plain(store.models.get_item(Key=CONTEXT_KEY, ConsistentRead=True).get("Item"))
    return {"ok": True, "system": "kss1_goals", "authority": "SHADOW_LEARNING",
            "automatic_prediction_allowed": False, "training_context": row,
            "reason": "GOALS_TRAINER_HAS_NOT_COMPLETED" if row is None else None}
