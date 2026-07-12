#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

import boto3
from boto3.dynamodb.conditions import Key

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "runtime_reports" / "mlb_rolling_24h_audit_latest.json"
OUTPUT = ROOT / "runtime_reports" / "mlb_missing_settlement_diagnostics_latest.json"
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "parlay_platform_snapshots")
QUERY_ERRORS: Dict[str, str] = {}


def safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe(item) for item in value]
    return value


def walk_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                yield from walk_dicts(child)


def provider_ids(row: Dict[str, Any]) -> Set[str]:
    keys = {
        "id", "gameId", "game_id", "providerGameId", "provider_game_id",
        "eventId", "event_id", "providerEventId", "provider_event_id",
        "oddsApiEventId", "odds_api_event_id",
    }
    out: Set[str] = set()
    for container in walk_dicts(row):
        for key in keys:
            value = container.get(key)
            if value not in (None, ""):
                out.add(str(value).strip())
    return {value for value in out if value}


def norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace(".", " ").replace("'", "").split())


def query_partition(slate_date: str) -> List[Dict[str, Any]]:
    if not TABLE_NAME:
        QUERY_ERRORS[slate_date] = "SNAPSHOTS_TABLE_NOT_CONFIGURED"
        return []
    try:
        table = boto3.resource("dynamodb").Table(TABLE_NAME)
        rows: List[Dict[str, Any]] = []
        start_key = None
        while True:
            args: Dict[str, Any] = {
                "KeyConditionExpression": Key("PK").eq(f"GAME_WINNERS#mlb#{slate_date}"),
                "ConsistentRead": True,
            }
            if start_key:
                args["ExclusiveStartKey"] = start_key
            response = table.query(**args)
            for item in response.get("Items") or []:
                data = item.get("data") if isinstance(item.get("data"), dict) else item
                if isinstance(data, dict):
                    rows.append(safe(data))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return rows
    except Exception as exc:
        QUERY_ERRORS[slate_date] = f"{type(exc).__name__}: {exc}"
        return []


def summary(row: Dict[str, Any]) -> Dict[str, Any]:
    audit = row.get("lockedCardAudit") or {}
    lock = row.get("slatePredictionLock") or row.get("lastPossiblePredictionGate") or {}
    return {
        "providerIds": sorted(provider_ids(row)),
        "gameId": row.get("gameId") or row.get("game_id") or row.get("id"),
        "awayTeam": row.get("awayTeam") or row.get("away_team"),
        "homeTeam": row.get("homeTeam") or row.get("home_team"),
        "commenceTime": row.get("commenceTime") or row.get("commence_time"),
        "predictedWinner": row.get("predictedWinner"),
        "officialPrediction": row.get("officialPrediction"),
        "officialPredictionStatus": row.get("officialPredictionStatus"),
        "lockedPrediction": row.get("lockedPrediction"),
        "lockAtUtc": audit.get("lockAtUtc") or lock.get("lockAtUtc"),
        "createdAt": row.get("createdAt") or row.get("created_at"),
        "tags": row.get("tags") or [],
        "hasFrozenFeatureVector": bool(row.get("frozenFeatureVector")),
        "lockedAmericanOdds": row.get("lockedAmericanOdds"),
        "priceBook": row.get("priceBook"),
        "priceSource": row.get("priceSource"),
    }


def main() -> int:
    report = json.loads(AUDIT.read_text(encoding="utf-8"))
    rows = list(report.get("rows") or [])
    missing = []
    partition_cache: Dict[str, List[Dict[str, Any]]] = {}

    for row in rows:
        if row.get("status") not in {"MISSING_LOCKED_PREDICTION", "MISSING_PREDICTION"}:
            continue
        audit = row.get("lockedCardAudit") or {}
        slate = str(row.get("slateDateEt") or "")
        partition = partition_cache.setdefault(slate, query_partition(slate))
        wanted_ids = provider_ids(row)
        away = norm(row.get("awayTeam"))
        home = norm(row.get("homeTeam"))
        id_matches = [candidate for candidate in partition if wanted_ids.intersection(provider_ids(candidate))]
        matchup_matches = [
            candidate for candidate in partition
            if norm(candidate.get("awayTeam") or candidate.get("away_team")) == away
            and norm(candidate.get("homeTeam") or candidate.get("home_team")) == home
        ]
        missing.append({
            "status": row.get("status"),
            "id": row.get("id"),
            "gameId": row.get("gameId"),
            "provider_game_id": row.get("provider_game_id"),
            "slateDateEt": slate,
            "matchup": row.get("matchup"),
            "awayTeam": row.get("awayTeam"),
            "homeTeam": row.get("homeTeam"),
            "commenceTime": row.get("commenceTime"),
            "winner": row.get("winner"),
            "homeScore": row.get("homeScore"),
            "awayScore": row.get("awayScore"),
            "missingReason": audit.get("missingReason"),
            "finalProviderIds": audit.get("finalProviderIds"),
            "finalCommenceTime": audit.get("finalCommenceTime"),
            "matchupCandidateCount": audit.get("matchupCandidateCount"),
            "candidateDiagnostics": audit.get("candidateDiagnostics") or [],
            "selectionPolicy": audit.get("selectionPolicy"),
            "auditVersion": audit.get("version"),
            "ddbPartition": f"GAME_WINNERS#mlb#{slate}",
            "ddbQueryError": QUERY_ERRORS.get(slate),
            "ddbPartitionRowCount": len(partition),
            "ddbProviderIdMatchCount": len(id_matches),
            "ddbMatchupMatchCount": len(matchup_matches),
            "ddbProviderIdMatches": [summary(candidate) for candidate in id_matches],
            "ddbMatchupMatches": [summary(candidate) for candidate in matchup_matches],
            "ddbAllStoredRows": [summary(candidate) for candidate in partition],
        })
    payload = {
        "ok": len(missing) == 0,
        "proofType": "MLB_MISSING_SETTLEMENT_DIAGNOSTICS",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "auditCreatedAt": report.get("createdAt"),
        "snapshotsTableConfigured": bool(TABLE_NAME),
        "ddbQueryErrors": QUERY_ERRORS,
        "completedFinalGames": (report.get("summary") or {}).get("completedFinalGames"),
        "gradedPredictionCount": (report.get("summary") or {}).get("gradedPredictionCount"),
        "missingPredictionCount": len(missing),
        "missingRows": missing,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
