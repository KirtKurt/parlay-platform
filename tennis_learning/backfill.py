from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import urllib.request
import zipfile
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Tuple

import boto3

from handler import settle, status

TABLE_NAME = os.environ["TENNIS_LEARNING_TABLE"]
BATCH_SIZE = int(os.getenv("TENNIS_BACKFILL_BATCH_SIZE", "400"))
START_YEAR = int(os.getenv("TENNIS_BACKFILL_START_YEAR", "2000"))
END_YEAR = int(os.getenv("TENNIS_BACKFILL_END_YEAR", str(datetime.now(timezone.utc).year)))
SOURCES = {
    "atp": "https://github.com/JeffSackmann/tennis_atp/archive/refs/heads/master.zip",
    "wta": "https://github.com/JeffSackmann/tennis_wta/archive/refs/heads/master.zip",
}
table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "inqis-tennis-learning/1.2"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def _rows(tour: str) -> Iterable[Dict[str, str]]:
    payload = _download(SOURCES[tour])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        wanted = []
        for name in archive.namelist():
            base = name.rsplit("/", 1)[-1]
            prefix = f"{tour}_matches_"
            if base.startswith(prefix) and base.endswith(".csv"):
                try:
                    year = int(base[len(prefix):-4])
                except ValueError:
                    continue
                if START_YEAR <= year <= END_YEAR:
                    wanted.append((year, name))
        for _, name in sorted(wanted):
            text = io.TextIOWrapper(archive.open(name), encoding="utf-8-sig", errors="replace")
            yield from csv.DictReader(text)


def _number(row: Mapping[str, str], key: str, default: float = 0.0) -> float:
    try:
        value = row.get(key, "")
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _date(row: Mapping[str, str]) -> str:
    raw = str(row.get("tourney_date") or "")
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}T00:00:00+00:00"
    return datetime.now(timezone.utc).isoformat()


def _bootstrap_probability(winner_rank: float, loser_rank: float) -> float:
    if winner_rank <= 0 or loser_rank <= 0:
        return 0.5
    delta = max(-8.0, min(8.0, (loser_rank - winner_rank) / 60.0))
    return 1.0 / (1.0 + math.exp(-delta))


def _american_from_probability(probability: float) -> float:
    probability = max(0.01, min(0.99, probability))
    if probability >= 0.5:
        return -100.0 * probability / (1.0 - probability)
    return 100.0 * (1.0 - probability) / probability


def _winner_is_player(match_id: str) -> bool:
    digest = hashlib.sha256(match_id.encode("utf-8")).digest()
    return bool(digest[0] & 1)


def _record(
    tour: str,
    row: Mapping[str, str],
    elo: Dict[str, float],
    surface_elo: Dict[Tuple[str, str], float],
    recent: Dict[str, deque],
) -> Dict[str, Any] | None:
    winner = str(row.get("winner_name") or "").strip()
    loser = str(row.get("loser_name") or "").strip()
    if not winner or not loser:
        return None

    surface = str(row.get("surface") or "Unknown")
    date = _date(row)
    event = str(row.get("tourney_id") or "event")
    match_num = str(row.get("match_num") or "0")
    match_id = f"bootstrap:{tour}:{event}:{match_num}:{date[:10]}"
    winner_rank = _number(row, "winner_rank")
    loser_rank = _number(row, "loser_rank")

    ew, el = elo[winner], elo[loser]
    sew, sel = surface_elo[(winner, surface)], surface_elo[(loser, surface)]
    rw = sum(recent[winner]) / len(recent[winner]) if recent[winner] else 0.5
    rl = sum(recent[loser]) / len(recent[loser]) if recent[loser] else 0.5
    winner_probability = _bootstrap_probability(winner_rank, loser_rank)

    winner_as_player = _winner_is_player(match_id)
    if winner_as_player:
        player, opponent, player_won = winner, loser, True
        fair_probability = winner_probability
        elo_diff = ew - el
        surface_elo_diff = sew - sel
        recent_diff = rw - rl
    else:
        player, opponent, player_won = loser, winner, False
        fair_probability = 1.0 - winner_probability
        elo_diff = el - ew
        surface_elo_diff = sel - sew
        recent_diff = rl - rw

    payload = {
        "match_id": match_id,
        "player": player,
        "opponent": opponent,
        "event_time": date,
        "player_won": player_won,
        "source": "JeffSackmann tennis_atp/tennis_wta",
        "source_mode": "historical_rank_bootstrap",
        "signals": {
            "player_odds": _american_from_probability(fair_probability),
            "opponent_odds": _american_from_probability(1.0 - fair_probability),
            "elo_diff": elo_diff,
            "surface_elo_diff": surface_elo_diff,
            "recent_win_rate_diff": recent_diff,
            "serve_points_won_diff": 0.0,
            "return_points_won_diff": 0.0,
            "break_points_saved_diff": 0.0,
            "rest_days_diff": 0.0,
            "best_of_five": str(row.get("best_of") or "3") == "5",
        },
    }

    expected = 1.0 / (1.0 + 10 ** ((el - ew) / 400.0))
    change = 24.0 * (1.0 - expected)
    elo[winner], elo[loser] = ew + change, el - change
    surface_expected = 1.0 / (1.0 + 10 ** ((sel - sew) / 400.0))
    surface_change = 28.0 * (1.0 - surface_expected)
    surface_elo[(winner, surface)] = sew + surface_change
    surface_elo[(loser, surface)] = sel - surface_change
    recent[winner].append(1)
    recent[loser].append(0)
    return payload


def run_backfill() -> Dict[str, Any]:
    cursor_item = table.get_item(
        Key={"PK": "BACKFILL", "SK": "CURSOR"}, ConsistentRead=True
    ).get("Item", {})
    skip = int(cursor_item.get("processed", 0))
    elo = defaultdict(lambda: 1500.0)
    surface_elo = defaultdict(lambda: 1500.0)
    recent = defaultdict(lambda: deque(maxlen=20))
    processed = trained = duplicates = rejected = 0
    errors: list[str] = []
    global_index = 0

    for tour in ("atp", "wta"):
        for row in _rows(tour):
            payload = _record(tour, row, elo, surface_elo, recent)
            if payload is None:
                continue
            if global_index < skip:
                global_index += 1
                continue
            if processed >= BATCH_SIZE:
                break
            global_index += 1
            processed += 1
            try:
                result = settle(payload)
                trained += int(result.get("trained", False))
                duplicates += int(result.get("duplicate", False))
            except Exception as exc:
                rejected += 1
                if len(errors) < 10:
                    errors.append(f"{payload['match_id']}: {exc}")
        if processed >= BATCH_SIZE:
            break

    complete = processed < BATCH_SIZE
    table.put_item(
        Item={
            "PK": "BACKFILL",
            "SK": "CURSOR",
            "processed": skip + processed,
            "last_batch_trained": trained,
            "last_batch_duplicates": duplicates,
            "last_batch_rejected": rejected,
            "last_errors": errors,
            "complete": complete,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sources": list(SOURCES.values()),
        }
    )
    if processed and trained == 0 and duplicates == 0:
        raise RuntimeError(
            f"historical backfill processed {processed} rows but trained none; "
            f"rejected={rejected}; errors={errors[:3]}"
        )
    return {
        "processed": processed,
        "trained": trained,
        "duplicates": duplicates,
        "rejected": rejected,
        "errors": errors,
        "cursor": skip + processed,
        "complete": complete,
        "model": status(),
    }


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    result = run_backfill()
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(result, default=str),
    }
