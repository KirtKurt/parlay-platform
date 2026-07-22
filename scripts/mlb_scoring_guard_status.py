#!/usr/bin/env python3
"""Build a read-only proof that an MLB pull produced persisted scoring output.

The existing pull guard proves that canonical 15-minute odds slots exist. This
companion guard verifies the output side of the pipeline: per-game movement
features and persisted winner predictions. It performs only DynamoDB Query
operations and writes a local JSON report.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

PROOF_TYPE = "MLB_SCORING_GUARD_READ_ONLY_PROOF"
PROOF_VERSION = "MLB-SCORING-GUARD-v1-output-side-coverage"
SLATE_TZ = ZoneInfo("America/New_York")
PULL_RECORD_TYPE = "pull_run"
PREDICTION_RECORD_TYPE = "mlb_single_game_moneyline_prediction"
MOVEMENT_ENTITY_TYPE = "HOT_PULL_MOVEMENT_FEATURE"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _start_token(value: Any) -> str:
    parsed = _parse_dt(value)
    return parsed.isoformat() if parsed else str(value or "")


def _object_tokens(row: Dict[str, Any]) -> Set[str]:
    """Return identity tokens while preserving doubleheader separation."""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    merged: Dict[str, Any] = {**row, **data}
    tokens: Set[str] = set()

    official_pk = merged.get("official_game_pk") or merged.get("officialGamePk")
    if official_pk not in (None, ""):
        tokens.add(f"official:{official_pk}")

    for field in (
        "game_id",
        "gameId",
        "id",
        "game_identity",
        "gameIdentity",
        "provider_event_id",
        "providerEventId",
    ):
        value = merged.get(field)
        if value not in (None, ""):
            text = str(value)
            tokens.add(f"id:{text}")
            if text.startswith("provider:"):
                tokens.add(f"id:{text.split(':', 1)[1]}")

    game_key = merged.get("game_key") or merged.get("gameKey")
    if game_key not in (None, ""):
        tokens.add(f"key:{game_key}")

    away = merged.get("away_team") or merged.get("awayTeam")
    home = merged.get("home_team") or merged.get("homeTeam")
    start = (
        merged.get("commence_time")
        or merged.get("commenceTime")
        or merged.get("official_commence_time")
        or merged.get("officialCommenceTime")
    )
    if away and home and start:
        tokens.add(f"teams:{_norm(away)}|{_norm(home)}|start:{_start_token(start)}")
    return tokens


def _display_identity(game: Dict[str, Any]) -> str:
    official_pk = game.get("official_game_pk") or game.get("officialGamePk")
    if official_pk not in (None, ""):
        return f"official:{official_pk}"
    for field in ("game_id", "gameId", "id", "game_key", "gameKey"):
        if game.get(field) not in (None, ""):
            return str(game[field])
    away = game.get("away_team") or game.get("awayTeam")
    home = game.get("home_team") or game.get("homeTeam")
    start = game.get("commence_time") or game.get("commenceTime")
    return f"{_norm(away)}@{_norm(home)}#{_start_token(start)}"


def _query_partition(table: Any, pk: str, sk_prefix: Optional[str] = None) -> List[Dict[str, Any]]:
    expression = Key("PK").eq(pk)
    if sk_prefix:
        expression = expression & Key("SK").begins_with(sk_prefix)
    items: List[Dict[str, Any]] = []
    start_key = None
    while True:
        kwargs: Dict[str, Any] = {
            "KeyConditionExpression": expression,
            "ConsistentRead": True,
            "ScanIndexForward": True,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        response = table.query(**kwargs)
        items.extend(_plain(response.get("Items") or []))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def _pull_rows(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for item in items:
        if item.get("record_type") != PULL_RECORD_TYPE:
            continue
        data = item.get("data")
        if isinstance(data, dict):
            rows.append(data)
    return sorted(
        rows,
        key=lambda row: (
            _parse_dt(row.get("pulled_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(row.get("pull_id") or ""),
        ),
    )


def _authoritative_roster(pulls: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    candidates: List[Tuple[int, datetime, Dict[str, Any], Dict[str, Any]]] = []
    for pull in pulls:
        manifest = pull.get("provider_schedule_manifest")
        if not isinstance(manifest, dict):
            continue
        games = manifest.get("games")
        if not isinstance(games, list) or not games:
            continue
        try:
            declared_count = int(manifest.get("gameCount"))
        except Exception:
            declared_count = -1
        if declared_count != len(games):
            continue
        observed = _parse_dt(manifest.get("observedAtUtc") or pull.get("pulled_at"))
        if observed is None:
            continue
        candidates.append((declared_count, observed, manifest, pull))
    if not candidates:
        return [], {}
    max_count = max(candidate[0] for candidate in candidates)
    selected = max(
        (candidate for candidate in candidates if candidate[0] == max_count),
        key=lambda candidate: candidate[1],
    )
    _count, observed, manifest, pull = selected
    return list(manifest.get("games") or []), {
        "pullId": manifest.get("pullId") or pull.get("pull_id"),
        "observedAtUtc": observed.isoformat(),
        "fingerprint": manifest.get("fingerprint"),
        "gameCount": max_count,
        "officialScheduleBacked": bool((manifest.get("scheduleAuthority") or {}).get("verified")),
    }


def _has_moneyline(game: Dict[str, Any]) -> bool:
    if game.get("moneyline_available") is True or game.get("moneylineAvailable") is True:
        return True
    books = game.get("books") or {}
    if not isinstance(books, dict):
        return False
    for payload in books.values():
        if not isinstance(payload, dict):
            continue
        market = payload.get("ml") or payload.get("moneyline") or payload.get("h2h") or {}
        if isinstance(market, dict) and market.get("home") is not None and market.get("away") is not None:
            return True
    return False


def _scoreable_token_sets(pulls: Sequence[Dict[str, Any]]) -> List[Set[str]]:
    unique: List[Set[str]] = []
    seen: Set[str] = set()
    for pull in pulls:
        for game in pull.get("games") or []:
            if not isinstance(game, dict) or not _has_moneyline(game):
                continue
            tokens = _object_tokens(game)
            fingerprint = "|".join(sorted(tokens))
            if tokens and fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(tokens)
    return unique


def _matching_row(tokens: Set[str], rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for row in rows:
        if tokens & _object_tokens(row):
            return row
    return None


def _feature_tokens(feature: Dict[str, Any]) -> Set[str]:
    tokens = _object_tokens(feature)
    game_key = feature.get("game_key") or feature.get("gameKey")
    if game_key not in (None, ""):
        tokens.add(f"key:{game_key}")
        tokens.add(f"id:{game_key}")
    return tokens


def _matching_feature(tokens: Set[str], rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    matches = [row for row in rows if tokens & _feature_tokens(row)]
    if not matches:
        return None
    return max(matches, key=lambda row: str(row.get("latest_asof") or row.get("created_at") or row.get("SK") or ""))


def _fundamentals_state(prediction: Optional[Dict[str, Any]]) -> str:
    if not prediction:
        return "PREDICTION_MISSING"
    data = prediction.get("data") if isinstance(prediction.get("data"), dict) else prediction
    optimizer = data.get("winnerOptimizer") or {}
    layer = data.get("fundamentalsLayer") or {}
    applied = (
        optimizer.get("fundamentalsApplied") is True
        or layer.get("applied") is True
        or data.get("fundamentalsApplied") is True
    )
    if applied:
        return "APPLIED"
    mode = str(
        optimizer.get("fundamentalsMode")
        or layer.get("mode")
        or data.get("fundamentalsMode")
        or ""
    ).upper()
    if "NEUTRAL" in mode or "MISSING" in mode or "NOT_ENABLED" in mode:
        return "NEUTRAL_OR_SOURCE_MISSING"
    return "NOT_APPLIED"


def evaluate_slate(
    *,
    slate_date: str,
    pull_items: Sequence[Dict[str, Any]],
    prediction_items: Sequence[Dict[str, Any]],
    movement_items: Sequence[Dict[str, Any]],
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    created_at = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    pulls = _pull_rows(pull_items)
    roster, roster_authority = _authoritative_roster(pulls)
    predictions = [
        item
        for item in prediction_items
        if item.get("record_type") == PREDICTION_RECORD_TYPE
        or (isinstance(item.get("data"), dict) and item["data"].get("predictedWinner"))
    ]
    movement = [
        item for item in movement_items if item.get("entity_type") == MOVEMENT_ENTITY_TYPE
    ]
    scoreable_sets = _scoreable_token_sets(pulls)

    games: List[Dict[str, Any]] = []
    missing_predictions: List[str] = []
    missing_movement: List[str] = []
    prediction_matches: Set[str] = set()
    movement_matches: Set[str] = set()
    fundamentals_applied = 0

    for game in roster:
        tokens = _object_tokens(game)
        identity = _display_identity(game)
        prediction = _matching_row(tokens, predictions)
        feature = _matching_feature(tokens, movement)
        scoreable = any(tokens & candidate for candidate in scoreable_sets)
        if prediction:
            prediction_matches.add(identity)
        else:
            missing_predictions.append(identity)
        if feature:
            movement_matches.add(identity)
        elif scoreable:
            missing_movement.append(identity)
        state = _fundamentals_state(prediction)
        if state == "APPLIED":
            fundamentals_applied += 1
        data = prediction.get("data") if prediction and isinstance(prediction.get("data"), dict) else (prediction or {})
        games.append({
            "gameIdentity": identity,
            "officialGamePk": game.get("official_game_pk") or game.get("officialGamePk"),
            "awayTeam": game.get("away_team") or game.get("awayTeam"),
            "homeTeam": game.get("home_team") or game.get("homeTeam"),
            "commenceTime": game.get("commence_time") or game.get("commenceTime"),
            "scoreableMoneylineObserved": scoreable,
            "movementFeaturePresent": feature is not None,
            "latestMovementAtUtc": (feature or {}).get("latest_asof"),
            "hotTeam": (feature or {}).get("hot_team"),
            "hotDelta": (feature or {}).get("hot_delta"),
            "movementStrength": (feature or {}).get("movement_strength"),
            "predictionPresent": prediction is not None,
            "predictedWinner": data.get("predictedWinner") or (prediction or {}).get("predicted_winner"),
            "predictionScore": data.get("score") or (prediction or {}).get("score"),
            "confidenceTier": data.get("confidenceTier") or (prediction or {}).get("confidence_tier"),
            "fundamentalsState": state,
        })

    official_count = len(roster)
    scoreable_count = sum(1 for game in games if game["scoreableMoneylineObserved"])
    blockers: List[str] = []
    if official_count <= 0:
        blockers.append("OFFICIAL_ROSTER_NOT_RESOLVED")
    if len(pulls) < 2:
        blockers.append("INSUFFICIENT_CANONICAL_PULL_HISTORY")
    if missing_predictions:
        blockers.append("PERSISTED_WINNER_PREDICTION_COVERAGE_INCOMPLETE")
    if missing_movement:
        blockers.append("MOVEMENT_FEATURE_COVERAGE_INCOMPLETE")

    latest_pull_at = _parse_dt((pulls[-1] if pulls else {}).get("pulled_at"))
    return {
        "ok": not blockers,
        "guardPassed": not blockers,
        "proofType": PROOF_TYPE,
        "version": PROOF_VERSION,
        "createdAtUtc": created_at.isoformat().replace("+00:00", "Z"),
        "createdAtEt": created_at.astimezone(SLATE_TZ).isoformat(),
        "slateDateEt": slate_date,
        "readOnly": True,
        "sourceOfTruth": {
            "canonicalPullPartition": f"PULLS#mlb#{slate_date}",
            "predictionPartition": f"GAME_WINNERS#mlb#{slate_date}",
            "movementPartition": f"ML_FEATURE#mlb#{slate_date}",
        },
        "rosterAuthority": roster_authority,
        "summary": {
            "officialGameCount": official_count,
            "canonicalPullCount": len(pulls),
            "latestCanonicalPullAtUtc": latest_pull_at.isoformat() if latest_pull_at else None,
            "scoreableGameCount": scoreable_count,
            "movementFeatureGameCount": len(movement_matches),
            "persistedPredictionGameCount": len(prediction_matches),
            "fundamentalsAppliedCount": fundamentals_applied,
            "fundamentalsNotAppliedOrMissingCount": max(official_count - fundamentals_applied, 0),
            "missingMovementCount": len(missing_movement),
            "missingPredictionCount": len(missing_predictions),
        },
        "missingMovementGameIdentities": missing_movement,
        "missingPredictionGameIdentities": missing_predictions,
        "blockers": blockers,
        "games": games,
        "secretExposed": False,
    }


def build_live_report(
    *,
    slate_date: str,
    region: str,
    snapshots_table: str,
    signal_ledger_table: str,
) -> Dict[str, Any]:
    resource = boto3.resource("dynamodb", region_name=region)
    snapshots = resource.Table(snapshots_table)
    ledger = resource.Table(signal_ledger_table)
    pull_items = _query_partition(snapshots, f"PULLS#mlb#{slate_date}")
    prediction_items = _query_partition(
        snapshots,
        f"GAME_WINNERS#mlb#{slate_date}",
        "GAME#",
    )
    movement_items = _query_partition(ledger, f"ML_FEATURE#mlb#{slate_date}")
    return evaluate_slate(
        slate_date=slate_date,
        pull_items=pull_items,
        prediction_items=prediction_items,
        movement_items=movement_items,
    )


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slate-date", default=datetime.now(SLATE_TZ).date().isoformat())
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
    parser.add_argument("--snapshots-table", default=os.environ.get("SNAPSHOTS_TABLE", "parlay_platform_snapshots"))
    parser.add_argument("--signal-ledger-table", default=os.environ.get("SIGNAL_LEDGER_TABLE", "parlay_platform_signal_ledger"))
    parser.add_argument("--output", default="runtime_reports/mlb_scoring_guard_status_latest.json")
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        report = build_live_report(
            slate_date=args.slate_date,
            region=args.region,
            snapshots_table=args.snapshots_table,
            signal_ledger_table=args.signal_ledger_table,
        )
    except Exception as exc:
        now = datetime.now(timezone.utc)
        report = {
            "ok": False,
            "guardPassed": False,
            "proofType": PROOF_TYPE,
            "version": PROOF_VERSION,
            "createdAtUtc": now.isoformat().replace("+00:00", "Z"),
            "createdAtEt": now.astimezone(SLATE_TZ).isoformat(),
            "slateDateEt": args.slate_date,
            "readOnly": True,
            "blockers": ["AWS_SCORING_GUARD_READ_FAILED"],
            "error": f"{type(exc).__name__}: {exc}",
            "secretExposed": False,
        }
    _write_report(output, report)
    print(json.dumps({
        "guardPassed": report.get("guardPassed"),
        "slateDateEt": report.get("slateDateEt"),
        "summary": report.get("summary"),
        "blockers": report.get("blockers"),
        "output": str(output),
    }, indent=2, default=str))
    return 1 if args.enforce and report.get("guardPassed") is not True else 0


if __name__ == "__main__":
    raise SystemExit(main())
