"""Deterministic identities, normalization, and slot canonicalization."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value: str | datetime) -> str:
    return parse_utc(value).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_event_key(sport_key: str, event_id: str) -> str:
    if not sport_key.startswith("soccer_"):
        raise ValueError(f"non-soccer sport key rejected: {sport_key}")
    if not event_id:
        raise ValueError("event id is required")
    return f"EVENT#{sport_key}#{event_id}"


def floor_slot(observed_at: str | datetime, seconds: int = 60) -> datetime:
    if seconds <= 0:
        raise ValueError("slot size must be positive")
    value = parse_utc(observed_at)
    epoch = int(value.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


def scope_hash(*, bookmakers: Sequence[str], markets: Sequence[str]) -> str:
    return digest(
        {
            "bookmakers": sorted(set(bookmakers)),
            "markets": sorted(set(markets)),
        }
    )[:20]


def _clean_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_event_odds(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one event-odds response without discarding optional fields."""
    books: list[dict[str, Any]] = []
    for book in payload.get("bookmakers") or []:
        book_key = str(book.get("key") or "").strip()
        if not book_key:
            continue
        markets: list[dict[str, Any]] = []
        for market in book.get("markets") or []:
            market_key = str(market.get("key") or "").strip()
            if not market_key:
                continue
            outcomes: list[dict[str, Any]] = []
            for outcome in market.get("outcomes") or []:
                row = {
                    key: outcome.get(key)
                    for key in ("name", "description", "sid", "link")
                    if outcome.get(key) is not None
                }
                price = _clean_number(outcome.get("price"))
                if price is None or price <= 1.0:
                    continue
                row["price"] = price
                for field in ("point", "limit", "multiplier"):
                    number = _clean_number(outcome.get(field))
                    if number is not None:
                        row[field] = number
                outcomes.append(row)
            outcomes.sort(
                key=lambda row: (
                    str(row.get("name") or ""),
                    str(row.get("description") or ""),
                    float(row.get("point") or 0.0),
                    float(row["price"]),
                )
            )
            if outcomes:
                markets.append(
                    {
                        "key": market_key,
                        "last_update": market.get("last_update") or book.get("last_update"),
                        **({"link": market["link"]} if market.get("link") else {}),
                        **({"sid": market["sid"]} if market.get("sid") else {}),
                        "outcomes": outcomes,
                    }
                )
        markets.sort(key=lambda row: (row["key"], str(row.get("last_update") or "")))
        if markets:
            books.append(
                {
                    "key": book_key,
                    "title": book.get("title") or book_key,
                    **({"link": book["link"]} if book.get("link") else {}),
                    **({"sid": book["sid"]} if book.get("sid") else {}),
                    "markets": markets,
                }
            )
    books.sort(key=lambda row: row["key"])
    return {
        "id": payload.get("id"),
        "sport_key": payload.get("sport_key"),
        "sport_title": payload.get("sport_title"),
        "commence_time": payload.get("commence_time"),
        "home_team": payload.get("home_team"),
        "away_team": payload.get("away_team"),
        "bookmakers": books,
    }


def merge_event_payloads(payloads: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Union partial book/market responses with last-update authority.

    The merge key includes the full outcome identity.  A later market update wins;
    ties are broken by payload digest, making retry ordering irrelevant.
    """
    normalized = [normalize_event_odds(payload) for payload in payloads]
    if not normalized:
        return {"bookmakers": []}
    identity_fields = ("id", "sport_key", "sport_title", "commence_time", "home_team", "away_team")
    result = {field: normalized[0].get(field) for field in identity_fields}
    books: dict[str, dict[str, Any]] = {}
    for payload in normalized:
        for field in ("id", "sport_key", "commence_time", "home_team", "away_team"):
            if result.get(field) and payload.get(field) and result[field] != payload[field]:
                raise ValueError(f"event identity mismatch for {field}")
        for book in payload["bookmakers"]:
            target = books.setdefault(
                book["key"],
                {key: value for key, value in book.items() if key != "markets"} | {"_markets": {}},
            )
            for market in book["markets"]:
                current = target["_markets"].get(market["key"])
                candidate_rank = (str(market.get("last_update") or ""), digest(market))
                current_rank = (
                    str(current.get("last_update") or ""),
                    digest(current),
                ) if current else ("", "")
                if current is None or candidate_rank > current_rank:
                    target["_markets"][market["key"]] = market
    output_books = []
    for key in sorted(books):
        row = books[key]
        markets = [row["_markets"][name] for name in sorted(row["_markets"])]
        output_books.append({k: v for k, v in row.items() if k != "_markets"} | {"markets": markets})
    result["bookmakers"] = output_books
    return result


@dataclass(frozen=True)
class SnapshotAttempt:
    attempt_id: str
    observed_at: str
    commence_time: str
    raw_uri: str
    payload_sha256: str
    bookmaker_count: int
    market_count: int
    valid: bool = True

    @property
    def observed(self) -> datetime:
        return parse_utc(self.observed_at)

    @property
    def commence(self) -> datetime:
        return parse_utc(self.commence_time)


def attempt_rank(attempt: SnapshotAttempt) -> tuple[int, int, float, str]:
    return (
        int(attempt.bookmaker_count),
        int(attempt.market_count),
        attempt.observed.timestamp(),
        attempt.payload_sha256,
    )


def choose_canonical_attempt(
    attempts: Iterable[SnapshotAttempt],
    *,
    slot_start: str | datetime,
    slot_seconds: int,
    grace_seconds: int = 20,
) -> SnapshotAttempt | None:
    slot = parse_utc(slot_start)
    deadline = slot + timedelta(seconds=slot_seconds + grace_seconds)
    eligible = [
        attempt
        for attempt in attempts
        if attempt.valid
        and slot <= attempt.observed <= deadline
        and attempt.observed < attempt.commence
    ]
    return max(eligible, key=attempt_rank) if eligible else None


def is_prematch(observed_at: str, commence_time: str) -> bool:
    return parse_utc(observed_at) < parse_utc(commence_time)

