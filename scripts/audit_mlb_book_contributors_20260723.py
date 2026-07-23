#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

SLATE = os.environ.get("SLATE_DATE", "2026-07-23")
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "parlay_platform_snapshots")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
OUTPUT = Path(os.environ.get("OUTPUT_PATH", "/tmp/mlb-book-contributors/mlb_book_contributors.json"))


def plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def parse_dt(value: Any):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def american_prob(value: Any):
    try:
        odds = float(value)
    except Exception:
        return None
    if odds == 0:
        return None
    return abs(odds) / (abs(odds) + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def devig_pair(home: Any, away: Any):
    home_prob = american_prob(home)
    away_prob = american_prob(away)
    if home_prob is None or away_prob is None or home_prob + away_prob <= 0:
        return None
    total = home_prob + away_prob
    return home_prob / total, away_prob / total


def query(table, pk: str, prefix: str | None = None):
    expression = Key("PK").eq(pk)
    if prefix:
        expression = expression & Key("SK").begins_with(prefix)
    rows = []
    start = None
    while True:
        kwargs = {
            "KeyConditionExpression": expression,
            "ConsistentRead": True,
            "ScanIndexForward": True,
        }
        if start:
            kwargs["ExclusiveStartKey"] = start
        response = table.query(**kwargs)
        rows.extend(plain(response.get("Items") or []))
        start = response.get("LastEvaluatedKey")
        if not start:
            return rows


def game_matches(game: dict[str, Any], prediction: dict[str, Any]) -> bool:
    game_pk = str(game.get("official_game_pk") or game.get("officialGamePk") or "")
    prediction_pk = str(prediction.get("officialGamePk") or prediction.get("official_game_pk") or "")
    if game_pk and prediction_pk:
        return game_pk == prediction_pk
    game_id = str(game.get("game_id") or game.get("gameId") or game.get("id") or "")
    prediction_id = str(prediction.get("gameId") or prediction.get("game_id") or prediction.get("id") or "")
    return bool(game_id and prediction_id and game_id == prediction_id)


def main() -> int:
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
    pull_items = query(table, f"PULLS#mlb#{SLATE}")
    prediction_items = query(table, f"GAME_WINNERS#mlb#{SLATE}", "GAME#")

    pulls = []
    for item in pull_items:
        if item.get("record_type") != "pull_run" or not isinstance(item.get("data"), dict):
            continue
        row = item["data"]
        row["_itemSk"] = item.get("SK")
        pulls.append(row)
    pulls.sort(
        key=lambda row: (
            parse_dt(row.get("pulled_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(row.get("pull_id") or ""),
        )
    )
    pulls_by_id = {str(row.get("pull_id")): row for row in pulls if row.get("pull_id")}

    games = []
    union_books: set[str] = set()
    for item in prediction_items:
        if item.get("record_type") != "mlb_single_game_moneyline_prediction":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        source_id = str(data.get("predictionSourcePullId") or "")
        source_at = parse_dt(data.get("predictionSourcePullAt"))
        source_pull = pulls_by_id.get(source_id)
        if source_pull is None and source_at:
            eligible = [
                row
                for row in pulls
                if (parse_dt(row.get("pulled_at")) or datetime.min.replace(tzinfo=timezone.utc))
                <= source_at
            ]
            source_pull = eligible[-1] if eligible else None

        source_game = None
        if source_pull:
            source_game = next(
                (
                    game
                    for game in source_pull.get("games") or []
                    if isinstance(game, dict) and game_matches(game, data)
                ),
                None,
            )

        books = (source_game or {}).get("books") or {}
        complete_moneyline_books = []
        partial_moneyline_books = []
        spread_books = []
        total_books = []
        per_book = []
        home_probabilities = []
        away_probabilities = []
        for book, payload in sorted(books.items()):
            payload = payload if isinstance(payload, dict) else {}
            moneyline = payload.get("ml") or payload.get("moneyline") or {}
            pair = devig_pair(moneyline.get("home"), moneyline.get("away")) if isinstance(moneyline, dict) else None
            if pair:
                complete_moneyline_books.append(book)
                union_books.add(book)
                home_probabilities.append(pair[0])
                away_probabilities.append(pair[1])
            elif isinstance(moneyline, dict) and (
                moneyline.get("home") is not None or moneyline.get("away") is not None
            ):
                partial_moneyline_books.append(book)
            if isinstance(payload.get("spread"), dict):
                spread_books.append(book)
            if isinstance(payload.get("total"), dict):
                total_books.append(book)
            per_book.append(
                {
                    "book": book,
                    "moneylineHome": moneyline.get("home") if isinstance(moneyline, dict) else None,
                    "moneylineAway": moneyline.get("away") if isinstance(moneyline, dict) else None,
                    "deVigHomeProbabilityPct": round(pair[0] * 100.0, 4) if pair else None,
                    "deVigAwayProbabilityPct": round(pair[1] * 100.0, 4) if pair else None,
                    "spreadAvailable": isinstance(payload.get("spread"), dict),
                    "totalAvailable": isinstance(payload.get("total"), dict),
                }
            )

        selected = data.get("homeSignal") if data.get("predictedSide") == "home" else data.get("awaySignal")
        selected = selected if isinstance(selected, dict) else {}
        consensus_home = sum(home_probabilities) / len(home_probabilities) if home_probabilities else None
        consensus_away = sum(away_probabilities) / len(away_probabilities) if away_probabilities else None
        games.append(
            {
                "officialGamePk": data.get("officialGamePk"),
                "awayTeam": data.get("awayTeam"),
                "homeTeam": data.get("homeTeam"),
                "predictedWinner": data.get("predictedWinner"),
                "predictedSide": data.get("predictedSide"),
                "predictionSourcePullId": source_id or None,
                "predictionSourcePullAt": data.get("predictionSourcePullAt"),
                "sourcePullResolved": source_pull is not None,
                "sourceGameResolved": source_game is not None,
                "completeMoneylineBookCount": len(complete_moneyline_books),
                "completeMoneylineBooks": complete_moneyline_books,
                "partialMoneylineBooks": partial_moneyline_books,
                "spreadBooks": spread_books,
                "totalBooks": total_books,
                "referencePriceBook": selected.get("priceBook"),
                "referenceAmericanOdds": selected.get("americanOdds"),
                "storedBookCount": selected.get("bookCount"),
                "storedFairProbabilityPct": selected.get("fairProbabilityPct"),
                "recomputedConsensusHomeProbabilityPct": round(consensus_home * 100.0, 4)
                if consensus_home is not None
                else None,
                "recomputedConsensusAwayProbabilityPct": round(consensus_away * 100.0, 4)
                if consensus_away is not None
                else None,
                "allBookConsensusMatchesStoredCount": int(selected.get("bookCount") or 0)
                == len(complete_moneyline_books),
                "perBook": per_book,
            }
        )

    games.sort(key=lambda row: str(row.get("officialGamePk") or ""))
    report = {
        "ok": len(games) == 5 and all(game["sourceGameResolved"] for game in games),
        "readOnly": True,
        "productionWritesPerformed": False,
        "slateDateEt": SLATE,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "gameCount": len(games),
        "unionOfCompleteMoneylineBooks": sorted(union_books),
        "unionBookCount": len(union_books),
        "games": games,
        "policy": (
            "Consensus probabilities and movement use every sportsbook with a complete paired "
            "moneyline in the exact prediction-source pull. The reference-price book is separate "
            "and is used for displayed odds and EV."
        ),
        "secretExposed": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "unionBookCount": report["unionBookCount"],
                "unionOfCompleteMoneylineBooks": report["unionOfCompleteMoneylineBooks"],
                "games": [
                    {
                        "game": f"{game['awayTeam']} at {game['homeTeam']}",
                        "pick": game["predictedWinner"],
                        "books": game["completeMoneylineBooks"],
                        "referencePriceBook": game["referencePriceBook"],
                        "storedCountMatches": game["allBookConsensusMatchesStoredCount"],
                    }
                    for game in games
                ],
            },
            indent=2,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
