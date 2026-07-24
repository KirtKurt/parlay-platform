"""Historical MLB optimizer whose objective is whole-day slate accuracy.

The module consumes immutable historical The Odds API snapshots requested every
15 minutes from 01:00 America/New_York through T-minus-45 for the final game on
the slate.  Each game's feature series is independently clipped at that game's
own T-minus-45 boundary, so later-game history is retained without leaking any
post-lock observation into an earlier game.  Whole slate dates never cross
train, walk-forward validation, or untouched-audit partitions.

The search is deterministic and intentionally aggressive, but promotion remains
fail-closed: at least 1,000 training games plus 200 walk-forward games and 200
untouched-audit games, exact full-slate prediction coverage, 80% or better on
every validation and audit day, non-degrading probabilistic quality, and explicit
overfit checks are all mandatory.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import mlb_historical_policy_v1 as policy_runtime


VERSION = "MLB-HISTORICAL-DAILY-OPTIMIZER-v1.2-compiled-search-1000-train-200-validation-200-audit"
DATASET_VERSION = "MLB-HISTORICAL-DAILY-DATASET-v1.1-per-game-t45-clipped"
SNAPSHOT_GRID_VERSION = "MLB-HISTORICAL-SNAPSHOT-GRID-v1.1-01ET-15m-to-last-game-t45"
SEARCH_VERSION = "MLB-HISTORICAL-AGGRESSIVE-SEARCH-v1.2-25000-compiled-prefilter-rich-proof"
SPORT_KEY = "baseball_mlb"
EASTERN = ZoneInfo("America/New_York")
PULL_START_ET = "01:00"
PULL_INTERVAL_MINUTES = 15
FULL_SLATE_LOCK_MINUTES = 45
MAX_PROVIDER_TIMESTAMP_LAG_MINUTES = 15
MIN_PULLS_PER_GAME = 4


class HistoricalOptimizerError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotGrid:
    slate_date_et: str
    start_at_et: str
    interval_minutes: int
    first_game_start_utc: str
    last_game_start_utc: str
    first_game_lock_at_utc: str
    lock_at_utc: str
    timestamps_utc: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": SNAPSHOT_GRID_VERSION,
            "slateDateEt": self.slate_date_et,
            "startAtEt": self.start_at_et,
            "intervalMinutes": self.interval_minutes,
            "firstGameStartUtc": self.first_game_start_utc,
            "lastGameStartUtc": self.last_game_start_utc,
            "firstGameLockAtUtc": self.first_game_lock_at_utc,
            "lastGameLockAtUtc": self.lock_at_utc,
            "lockAtUtc": self.lock_at_utc,
            "timestampsUtc": list(self.timestamps_utc),
            "slotCount": len(self.timestamps_utc),
            "perGameFeatureCutoff": "each_game_commence_time_minus_45_minutes",
        }


@dataclass(frozen=True)
class SearchConfig:
    minimum_training_games: int = policy_runtime.MIN_TRAINING_GAMES
    minimum_walk_forward_games: int = policy_runtime.MIN_WALK_FORWARD_GAMES
    minimum_untouched_holdout_games: int = policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES
    minimum_settled_games: int = policy_runtime.MIN_TOTAL_SETTLED_GAMES
    minimum_daily_accuracy: float = policy_runtime.MIN_DAILY_ACCURACY
    target_daily_accuracy_high: float = policy_runtime.TARGET_DAILY_ACCURACY_HIGH
    minimum_walk_forward_days: int = policy_runtime.MIN_WALK_FORWARD_DAYS
    minimum_holdout_days: int = policy_runtime.MIN_UNTOUCHED_HOLDOUT_DAYS
    maximum_candidates: int = 25000
    random_seed: int = 1541
    maximum_train_validation_accuracy_gap: float = 0.10
    maximum_brier_degradation: float = 0.005
    maximum_log_loss_degradation: float = 0.01

    def validate(self) -> "SearchConfig":
        if self.minimum_training_games < policy_runtime.MIN_TRAINING_GAMES:
            raise ValueError("minimum_training_games cannot be below 1000")
        if self.minimum_walk_forward_games < policy_runtime.MIN_WALK_FORWARD_GAMES:
            raise ValueError("minimum_walk_forward_games cannot be below 200")
        if self.minimum_untouched_holdout_games < policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES:
            raise ValueError("minimum_untouched_holdout_games cannot be below 200")
        required_total = (
            self.minimum_training_games
            + self.minimum_walk_forward_games
            + self.minimum_untouched_holdout_games
        )
        if self.minimum_settled_games < required_total:
            raise ValueError(
                "minimum_settled_games must cover training, walk-forward, and untouched audit"
            )
        if self.minimum_daily_accuracy < 0.80 or self.minimum_daily_accuracy > 1.0:
            raise ValueError("minimum_daily_accuracy cannot be below 0.80")
        if self.minimum_holdout_days < 15:
            raise ValueError("minimum_holdout_days cannot be below 15")
        if self.minimum_walk_forward_days < 20:
            raise ValueError("minimum_walk_forward_days cannot be below 20")
        if self.maximum_candidates < 100:
            raise ValueError("maximum_candidates must be at least 100")
        return self


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


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_hhmm(value: str) -> Tuple[int, int]:
    try:
        hour, minute = [int(part) for part in str(value).split(":", 1)]
    except Exception as exc:
        raise ValueError("start_at_et must be HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("start_at_et is outside a valid day")
    return hour, minute


def build_snapshot_grid(
    slate_date_et: str,
    game_starts: Any,
    *,
    start_at_et: str = PULL_START_ET,
    interval_minutes: int = PULL_INTERVAL_MINUTES,
    lock_minutes: int = FULL_SLATE_LOCK_MINUTES,
) -> SnapshotGrid:
    """Return paid request timestamps from 01:00 ET to the last game T-minus-45.

    One sport-level historical snapshot covers all games.  The final grid runs
    through the latest game's cutoff, while ``build_slate_dataset`` clips each
    individual game at its own cutoff.
    """

    try:
        day = date.fromisoformat(str(slate_date_et))
    except Exception as exc:
        raise ValueError("slate_date_et must be YYYY-MM-DD") from exc
    raw_starts = (
        list(game_starts)
        if isinstance(game_starts, (list, tuple, set, frozenset))
        else [game_starts]
    )
    starts = [_parse_dt(value) for value in raw_starts]
    if not starts or any(value is None for value in starts):
        raise ValueError("game_starts must contain valid ISO-8601 datetimes")
    parsed_starts = sorted(value for value in starts if value is not None)
    if any(value.astimezone(EASTERN).date() != day for value in parsed_starts):
        raise ValueError("every game must belong to the requested Eastern slate date")
    if interval_minutes != 15:
        raise ValueError("historical pull interval is locked to 15 minutes")
    if lock_minutes != 45:
        raise ValueError("historical game lock is locked to T-minus-45")
    hour, minute = _parse_hhmm(start_at_et)
    if (hour, minute) != (1, 0):
        raise ValueError("historical game-day pulls must begin at 01:00 ET")
    start_local = datetime.combine(day, dt_time(hour, minute), tzinfo=EASTERN)
    first = parsed_starts[0]
    last = parsed_starts[-1]
    first_lock_utc = first - timedelta(minutes=lock_minutes)
    final_lock_utc = last - timedelta(minutes=lock_minutes)
    start_utc = start_local.astimezone(timezone.utc)
    if final_lock_utc < start_utc:
        raise ValueError("last game lock occurs before the required 01:00 ET start")
    timestamps: List[str] = []
    cursor = start_utc
    while cursor <= final_lock_utc:
        timestamps.append(_iso_z(cursor))
        cursor += timedelta(minutes=interval_minutes)
    if not timestamps:
        raise ValueError("snapshot grid contains no timestamps")
    return SnapshotGrid(
        slate_date_et=day.isoformat(),
        start_at_et=start_at_et,
        interval_minutes=interval_minutes,
        first_game_start_utc=_iso_z(first),
        last_game_start_utc=_iso_z(last),
        first_game_lock_at_utc=_iso_z(first_lock_utc),
        lock_at_utc=_iso_z(final_lock_utc),
        timestamps_utc=tuple(timestamps),
    )


def american_implied_probability(value: Any) -> Optional[float]:
    try:
        price = float(value)
    except Exception:
        return None
    if not math.isfinite(price) or price == 0.0:
        return None
    return abs(price) / (abs(price) + 100.0) if price < 0 else 100.0 / (price + 100.0)


def devig_pair(home_price: Any, away_price: Any) -> Optional[Tuple[float, float]]:
    home = american_implied_probability(home_price)
    away = american_implied_probability(away_price)
    if home is None or away is None or home + away <= 0:
        return None
    total = home + away
    return home / total, away / total


def _normalize_team(value: Any) -> str:
    text = " ".join(str(value or "").lower().strip().split())
    aliases = {
        "a's": "athletics",
        "oakland a's": "athletics",
        "oakland athletics": "athletics",
        "athletics athletics": "athletics",
        "az diamondbacks": "arizona diamondbacks",
        "chi cubs": "chicago cubs",
        "chi white sox": "chicago white sox",
        "la angels": "los angeles angels",
        "la dodgers": "los angeles dodgers",
        "ny mets": "new york mets",
        "ny yankees": "new york yankees",
        "sd padres": "san diego padres",
        "sf giants": "san francisco giants",
        "tb rays": "tampa bay rays",
    }
    return aliases.get(text, text)


def _historical_payload_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, Mapping):
        rows = payload.get("data")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise HistoricalOptimizerError("historical odds payload data is not a list")
    if any(not isinstance(row, Mapping) for row in rows):
        raise HistoricalOptimizerError("historical odds payload contains a non-object event")
    return [dict(row) for row in rows]


def _book_h2h(book: Mapping[str, Any], home_team: str, away_team: str) -> Optional[Dict[str, Any]]:
    for market in book.get("markets") or []:
        if not isinstance(market, Mapping) or market.get("key") != "h2h":
            continue
        prices: Dict[str, float] = {}
        for outcome in market.get("outcomes") or []:
            if not isinstance(outcome, Mapping):
                continue
            name = _normalize_team(outcome.get("name"))
            try:
                prices[name] = float(outcome.get("price"))
            except Exception:
                continue
        home_price = prices.get(_normalize_team(home_team))
        away_price = prices.get(_normalize_team(away_team))
        pair = devig_pair(home_price, away_price)
        if pair:
            return {
                "homePrice": home_price,
                "awayPrice": away_price,
                "homeFair": pair[0],
                "awayFair": pair[1],
                "lastUpdate": market.get("last_update") or book.get("last_update"),
            }
    return None


def normalize_historical_snapshot(payload: Any, requested_at: Any) -> Dict[str, Any]:
    requested = _parse_dt(requested_at)
    if requested is None:
        raise ValueError("requested_at is invalid")
    provider = _parse_dt(payload.get("timestamp") if isinstance(payload, Mapping) else requested)
    if provider is None:
        raise HistoricalOptimizerError("historical response timestamp is invalid")
    if provider > requested + timedelta(seconds=1):
        raise HistoricalOptimizerError("historical response timestamp is after the request")
    lag_minutes = (requested - provider).total_seconds() / 60.0
    if lag_minutes > MAX_PROVIDER_TIMESTAMP_LAG_MINUTES + 1e-9:
        raise HistoricalOptimizerError("historical response is too stale for a 15-minute grid")
    events = []
    for raw in _historical_payload_rows(payload):
        home = raw.get("home_team")
        away = raw.get("away_team")
        commence = _parse_dt(raw.get("commence_time"))
        provider_id = str(raw.get("id") or "")
        if not home or not away or commence is None or not provider_id:
            continue
        by_book: Dict[str, Dict[str, Any]] = {}
        for book in raw.get("bookmakers") or []:
            if not isinstance(book, Mapping):
                continue
            parsed = _book_h2h(book, str(home), str(away))
            if parsed:
                by_book[str(book.get("key") or book.get("title") or "unknown")] = parsed
        if not by_book:
            continue
        home_values = [float(row["homeFair"]) for row in by_book.values()]
        away_values = [float(row["awayFair"]) for row in by_book.values()]
        primary = by_book.get("fanduel") or next(iter(by_book.values()))
        events.append(
            {
                "providerEventId": provider_id,
                "homeTeam": str(home),
                "awayTeam": str(away),
                "commenceTime": commence.isoformat(),
                "homeFair": sum(home_values) / len(home_values),
                "awayFair": sum(away_values) / len(away_values),
                "bookCount": len(by_book),
                "bookDivergence": max(home_values) - min(home_values) if len(home_values) > 1 else 0.0,
                "homePrice": primary.get("homePrice"),
                "awayPrice": primary.get("awayPrice"),
                "books": by_book,
            }
        )
    return {
        "version": DATASET_VERSION,
        "requestedAtUtc": requested.isoformat(),
        "providerTimestampUtc": provider.isoformat(),
        "providerLagMinutes": round(lag_minutes, 6),
        "events": sorted(events, key=lambda row: (row["commenceTime"], row["providerEventId"])),
    }


def _official_game(raw: Mapping[str, Any]) -> Dict[str, Any]:
    official_pk = str(raw.get("officialGamePk") or raw.get("official_game_pk") or "")
    home = raw.get("homeTeam") or raw.get("home_team")
    away = raw.get("awayTeam") or raw.get("away_team")
    commence = _parse_dt(raw.get("gameDate") or raw.get("commenceTime") or raw.get("commence_time"))
    winner = raw.get("winner")
    winner_is_valid = _normalize_team(winner) in {
        _normalize_team(home),
        _normalize_team(away),
    }
    # ``build_slate_dataset`` normalizes raw official rows once and then passes
    # those rows through the crosswalk helper. Accept that idempotent internal
    # representation only when it carries a binary settled label and a valid
    # winner; arbitrary incomplete upstream rows remain rejected.
    normalized_settled = raw.get("homeWon") in {0, 1} and winner_is_valid
    completed = raw.get("completed") is True or normalized_settled
    if not official_pk or not home or not away or commence is None:
        raise HistoricalOptimizerError("official final row is missing identity/start fields")
    if not completed or not winner_is_valid:
        raise HistoricalOptimizerError(f"official game {official_pk} is not a settled non-tie final")
    return {
        "officialGamePk": official_pk,
        "homeTeam": str(home),
        "awayTeam": str(away),
        "commenceTime": commence.isoformat(),
        "winner": str(winner),
        "homeWon": 1 if _normalize_team(winner) == _normalize_team(home) else 0,
    }


def crosswalk_snapshot(
    official_games: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    official = [_official_game(row) for row in official_games]
    candidates = []
    for game in official:
        game_start = _parse_dt(game["commenceTime"])
        pair = (_normalize_team(game["awayTeam"]), _normalize_team(game["homeTeam"]))
        for event in events:
            event_pair = (_normalize_team(event.get("awayTeam")), _normalize_team(event.get("homeTeam")))
            event_start = _parse_dt(event.get("commenceTime"))
            if pair != event_pair or game_start is None or event_start is None:
                continue
            candidates.append(
                (
                    abs((event_start - game_start).total_seconds()),
                    game["officialGamePk"],
                    str(event.get("providerEventId") or ""),
                    event,
                )
            )
    candidates.sort(key=lambda row: row[:3])
    matched: Dict[str, Dict[str, Any]] = {}
    used_events = set()
    for drift, official_pk, event_id, event in candidates:
        if drift > 12 * 60 * 60 or official_pk in matched or not event_id or event_id in used_events:
            continue
        matched[official_pk] = copy.deepcopy(dict(event))
        used_events.add(event_id)
    return matched


def _intervals(points: Sequence[Tuple[datetime, float]]) -> List[Tuple[float, float, float]]:
    rows = []
    if len(points) < 2:
        return rows
    origin = points[0][0]
    for previous, current in zip(points, points[1:]):
        hours = (current[0] - previous[0]).total_seconds() / 3600.0
        if hours <= 0:
            continue
        change_pp = (current[1] - previous[1]) * 100.0
        midpoint_hours = ((previous[0] + (current[0] - previous[0]) / 2) - origin).total_seconds() / 3600.0
        rows.append((change_pp, change_pp / hours, midpoint_hours))
    return rows


def _slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 2 or len(xs) != len(ys):
        return 0.0
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((value - x_mean) ** 2 for value in xs)
    if denominator <= 0:
        return 0.0
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / len(values))


def _reversals(values: Sequence[float]) -> int:
    signs = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        signs.append(1 if change > 0.0005 else -1 if change < -0.0005 else 0)
    nonzero = [value for value in signs if value]
    return sum(previous != current for previous, current in zip(nonzero, nonzero[1:]))


def _window(points: Sequence[Tuple[datetime, float]], minutes: Optional[int]) -> List[Tuple[datetime, float]]:
    if not points or minutes is None:
        return list(points)
    threshold = points[-1][0] - timedelta(minutes=minutes)
    selected = [point for point in points if point[0] >= threshold]
    anchors = [point for point in points if point[0] <= threshold]
    if anchors and (not selected or anchors[-1][0] < selected[0][0]):
        selected.insert(0, anchors[-1])
    return selected


def _temporal_summary(points: Sequence[Tuple[datetime, float]], expected_slots: int) -> Dict[str, Any]:
    def summarize(window_points: Sequence[Tuple[datetime, float]]) -> Dict[str, Any]:
        intervals = _intervals(window_points)
        duration = (
            (window_points[-1][0] - window_points[0][0]).total_seconds() / 60.0
            if len(window_points) >= 2
            else 0.0
        )
        changes = [row[0] for row in intervals]
        velocities = [row[1] for row in intervals]
        midpoints = [row[2] for row in intervals]
        gaps = [
            (current[0] - previous[0]).total_seconds() / 60.0
            for previous, current in zip(window_points, window_points[1:])
        ]
        expected_window = int(round(duration / 15.0)) + 1 if window_points else 0
        return {
            "pullCount": len(window_points),
            "durationMinutes": round(duration, 6),
            "coverageRatio": round(min(1.0, len(window_points) / max(1, expected_window)), 8)
            if window_points
            else 0.0,
            "maxGapMinutes": round(max(gaps), 6) if gaps else 0.0,
            "velocityPpHr": round(
                ((window_points[-1][1] - window_points[0][1]) * 100.0) / (duration / 60.0), 8
            )
            if duration > 0
            else 0.0,
            "accelerationPpHr2": round(_slope(midpoints, velocities), 8),
            "volatilityPpPerPull": round(_std(changes), 8),
            "reversalCount": _reversals([point[1] for point in window_points]),
        }

    full = summarize(points)
    full["coverageRatio"] = round(min(1.0, len(points) / max(1, expected_slots)), 8)
    return {
        "version": "MLB-TEMPORAL-FEATURES-v1-lock-bounded-multi-horizon",
        "available": bool(points),
        "sourcePointCount": len(points),
        "horizons": {
            "15m": summarize(_window(points, 15)),
            "60m": summarize(_window(points, 60)),
            "180m": summarize(_window(points, 180)),
            "full": full,
        },
    }


def _market_side(price: Any) -> str:
    try:
        value = float(price)
    except Exception:
        return "unknown"
    if value <= -120:
        return "favorite"
    if value >= 105:
        return "underdog"
    return "pickem"


def _signal(
    game: Mapping[str, Any], observations: Sequence[Mapping[str, Any]], side: str, expected_slots: int
) -> Dict[str, Any]:
    points = [
        (_parse_dt(row.get("providerTimestampUtc")), float(row[f"{side}Fair"]))
        for row in observations
        if _parse_dt(row.get("providerTimestampUtc")) is not None
        and row.get(f"{side}Fair") not in (None, "")
    ]
    points = sorted([(at, value) for at, value in points if at is not None], key=lambda row: row[0])
    latest = observations[-1]
    fair_latest = points[-1][1]
    fair_start = points[0][1]
    delta = fair_latest - fair_start
    temporal = _temporal_summary(points, expected_slots)
    reversals = int((temporal.get("horizons") or {}).get("full", {}).get("reversalCount") or 0)
    divergence = float(latest.get("bookDivergence") or 0.0)
    price = latest.get(f"{side}Price")
    side_type = _market_side(price)
    tags = []
    if len(points) < MIN_PULLS_PER_GAME:
        tags.append("LOW_PULL_DEPTH")
    if delta >= 0.006:
        tags.append("POSITIVE_MOVE")
    if delta <= -0.006:
        tags.append("NEGATIVE_MOVE")
    if divergence <= 0.02:
        tags.append("BOOK_AGREEMENT")
    if divergence > 0.04:
        tags.append("BOOK_DIVERGENCE")
    if reversals:
        tags.append("REVERSAL")
    tags.append(side_type.upper())
    velocity60 = float((temporal["horizons"].get("60m") or {}).get("velocityPpHr") or 0.0)
    volatility60 = float((temporal["horizons"].get("60m") or {}).get("volatilityPpPerPull") or 0.0)
    reversals60 = int((temporal["horizons"].get("60m") or {}).get("reversalCount") or 0)
    if delta >= 0.01 and velocity60 > 0 and divergence <= 0.02 and reversals <= 1:
        tags.append("STEAM")
    if volatility60 >= 0.8 or reversals60 >= 2:
        tags.append("LATE_INSTABILITY")
    return {
        "side": side,
        "team": game["homeTeam"] if side == "home" else game["awayTeam"],
        "fairProbability": fair_latest,
        "marketConsensusProbability": fair_latest,
        "probStart": fair_start,
        "probLatest": fair_latest,
        "delta": delta,
        "bookCount": int(latest.get("bookCount") or 0),
        "bookDivergence": divergence,
        "reversalCount": reversals,
        "americanOdds": price,
        "marketSide": side_type,
        "pullCountForGame": len(points),
        "temporalFeatures": temporal,
        "tags": sorted(set(tags)),
    }


def build_slate_dataset(
    slate_date_et: str,
    official_final_rows: Sequence[Mapping[str, Any]],
    historical_snapshots: Sequence[Mapping[str, Any]],
    grid: SnapshotGrid,
) -> Dict[str, Any]:
    official = [_official_game(row) for row in official_final_rows]
    if not official:
        raise HistoricalOptimizerError("official slate has no settled games")
    if grid.slate_date_et != slate_date_et:
        raise HistoricalOptimizerError("snapshot grid slate date mismatch")
    lock_by_game = {
        game["officialGamePk"]: _parse_dt(game["commenceTime"])
        - timedelta(minutes=FULL_SLATE_LOCK_MINUTES)
        for game in official
    }
    final_grid_lock = _parse_dt(grid.lock_at_utc)
    if final_grid_lock is None or abs(
        (final_grid_lock - max(lock_by_game.values())).total_seconds()
    ) > 1.0:
        raise HistoricalOptimizerError("snapshot grid does not end at the final game T-minus-45")
    normalized = []
    expected_requests = list(grid.timestamps_utc)
    by_requested = {str(row.get("requestedAtUtc") or ""): row for row in historical_snapshots}
    missing_requests = [value for value in expected_requests if value not in by_requested]
    if missing_requests:
        raise HistoricalOptimizerError("historical request ledger is incomplete")
    series: Dict[str, List[Dict[str, Any]]] = {game["officialGamePk"]: [] for game in official}
    for requested in expected_requests:
        snapshot = normalize_historical_snapshot(
            by_requested[requested].get("payload", by_requested[requested]), requested
        )
        provider_at = _parse_dt(snapshot["providerTimestampUtc"])
        if provider_at is None or provider_at > final_grid_lock + timedelta(seconds=1):
            raise HistoricalOptimizerError("post-final-lock historical observation detected")
        matches = crosswalk_snapshot(official, snapshot["events"])
        accepted_for_games = 0
        for game in official:
            game_pk = game["officialGamePk"]
            event = matches.get(game_pk)
            # This is the no-leakage boundary: a later sport-level snapshot may
            # contain an early game, but it never enters that game's features.
            if event and provider_at <= lock_by_game[game_pk] + timedelta(seconds=1):
                series[game_pk].append(
                    {
                        **event,
                        "requestedAtUtc": snapshot["requestedAtUtc"],
                        "providerTimestampUtc": snapshot["providerTimestampUtc"],
                        "providerLagMinutes": snapshot["providerLagMinutes"],
                    }
                )
                accepted_for_games += 1
        normalized.append(
            {
                "requestedAtUtc": snapshot["requestedAtUtc"],
                "providerTimestampUtc": snapshot["providerTimestampUtc"],
                "matchedOfficialGames": len(matches),
                "acceptedBeforePerGameLock": accepted_for_games,
                "providerEventCount": len(snapshot["events"]),
            }
        )

    records = []
    exclusions = []
    parsed_requested = [_parse_dt(value) for value in expected_requests]
    for game in official:
        game_pk = game["officialGamePk"]
        game_lock = lock_by_game[game_pk]
        observations = sorted(
            series[game_pk], key=lambda row: str(row.get("providerTimestampUtc") or "")
        )
        if any(
            (_parse_dt(row.get("providerTimestampUtc")) or datetime.max.replace(tzinfo=timezone.utc))
            > game_lock + timedelta(seconds=1)
            for row in observations
        ):
            raise HistoricalOptimizerError("post-game-lock historical observation detected")
        expected_game_slots = sum(
            value is not None and value <= game_lock + timedelta(seconds=1)
            for value in parsed_requested
        )
        if len(observations) < MIN_PULLS_PER_GAME:
            exclusions.append(
                {
                    "officialGamePk": game_pk,
                    "reason": "insufficient_game_lock_bounded_pull_depth",
                    "pullCount": len(observations),
                    "predictionLockAtUtc": _iso_z(game_lock),
                }
            )
            continue
        home = _signal(game, observations, "home", expected_game_slots)
        away = _signal(game, observations, "away", expected_game_slots)
        records.append(
            {
                "version": DATASET_VERSION,
                "slateDateEt": slate_date_et,
                "officialGamePk": game_pk,
                "homeTeam": game["homeTeam"],
                "awayTeam": game["awayTeam"],
                "commenceTime": game["commenceTime"],
                "winner": game["winner"],
                "homeWon": game["homeWon"],
                "homeSignal": home,
                "awaySignal": away,
                "requestedSlotCount": expected_game_slots,
                "observedHomePullCount": home["pullCountForGame"],
                "observedAwayPullCount": away["pullCountForGame"],
                "predictionLockAtUtc": _iso_z(game_lock),
                "postLockDataExcluded": True,
                "gameSpecificLockClipping": True,
            }
        )
    official_count = len(official)
    coverage = len(records) / official_count if official_count else 0.0
    return {
        "version": DATASET_VERSION,
        "slateDateEt": slate_date_et,
        "grid": grid.to_dict(),
        "officialGameCount": official_count,
        "eligibleGameCount": len(records),
        "exactSlateCoverage": round(coverage, 8),
        "completeSlate": len(records) == official_count and not exclusions,
        "records": records,
        "exclusions": exclusions,
        "snapshotAudit": normalized,
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
        "fingerprint": dataset_fingerprint(records),
    }


def dataset_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    material = [
        {
            "slateDateEt": row.get("slateDateEt"),
            "officialGamePk": row.get("officialGamePk"),
            "winner": row.get("winner"),
            "homeSignal": row.get("homeSignal"),
            "awaySignal": row.get("awaySignal"),
            "predictionLockAtUtc": row.get("predictionLockAtUtc"),
        }
        for row in sorted(
            records,
            key=lambda item: (str(item.get("slateDateEt") or ""), str(item.get("officialGamePk") or "")),
        )
    ]
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def predict_record(record: Mapping[str, Any], policy: Mapping[str, Any]) -> Dict[str, Any]:
    selected, home, away = policy_runtime.select_winner(
        record.get("homeSignal") or {}, record.get("awaySignal") or {}, policy
    )
    side = str(selected.get("side") or "")
    home_prediction = 1 if side == "home" else 0
    home_probability, _ = policy_runtime.complementary_probabilities(home, away)
    probability = float(home_probability)
    correct = home_prediction == int(record.get("homeWon") or 0)
    return {
        "slateDateEt": record.get("slateDateEt"),
        "officialGamePk": record.get("officialGamePk"),
        "homeTeam": record.get("homeTeam"),
        "awayTeam": record.get("awayTeam"),
        "predictedSide": side,
        "predictedWinner": selected.get("team"),
        "homeWinProbability": probability,
        "homeWon": int(record.get("homeWon") or 0),
        "correct": bool(correct),
        "policyDigest": policy_runtime.policy_digest(policy),
    }


def _log_loss(rows: Sequence[Mapping[str, Any]]) -> float:
    if not rows:
        return 1.0
    total = 0.0
    for row in rows:
        probability = min(1 - 1e-9, max(1e-9, float(row.get("homeWinProbability") or 0.5)))
        outcome = int(row.get("homeWon") or 0)
        total += -(outcome * math.log(probability) + (1 - outcome) * math.log(1 - probability))
    return total / len(rows)


def _brier(rows: Sequence[Mapping[str, Any]]) -> float:
    if not rows:
        return 1.0
    return sum(
        (float(row.get("homeWinProbability") or 0.5) - int(row.get("homeWon") or 0)) ** 2
        for row in rows
    ) / len(rows)


def evaluate_policy(
    records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    dates: Optional[Iterable[str]] = None,
    *,
    daily_target: float = policy_runtime.MIN_DAILY_ACCURACY,
) -> Dict[str, Any]:
    date_filter = {str(value) for value in dates} if dates is not None else None
    selected_records = [
        row for row in records if date_filter is None or str(row.get("slateDateEt") or "") in date_filter
    ]
    predictions = [predict_record(row, policy) for row in selected_records]
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    expected_by_date: Dict[str, int] = {}
    for row in selected_records:
        day = str(row.get("slateDateEt") or "")
        expected_by_date[day] = expected_by_date.get(day, 0) + 1
    for row in predictions:
        by_date.setdefault(str(row.get("slateDateEt") or ""), []).append(row)
    daily = []
    for day in sorted(expected_by_date):
        rows = by_date.get(day, [])
        expected = expected_by_date[day]
        correct = sum(row.get("correct") is True for row in rows)
        coverage = len(rows) / expected if expected else 0.0
        accuracy = correct / expected if expected else 0.0
        daily.append(
            {
                "slateDateEt": day,
                "officialGameCount": expected,
                "predictionCount": len(rows),
                "correct": correct,
                "accuracy": round(accuracy, 8),
                "coverage": round(coverage, 8),
                "dayPassed": coverage >= 1.0 - 1e-12 and accuracy + 1e-12 >= daily_target,
            }
        )
    accuracies = [row["accuracy"] for row in daily]
    coverage_values = [row["coverage"] for row in daily]
    return {
        "policyDigest": policy_runtime.policy_digest(policy),
        "gameCount": len(predictions),
        "dayCount": len(daily),
        "correct": sum(row.get("correct") is True for row in predictions),
        "overallAccuracy": round(
            sum(row.get("correct") is True for row in predictions) / len(predictions), 8
        )
        if predictions
        else 0.0,
        "minimumDailyAccuracy": min(accuracies) if accuracies else 0.0,
        "meanDailyAccuracy": sum(accuracies) / len(accuracies) if accuracies else 0.0,
        "dailyPassRate": sum(row["dayPassed"] for row in daily) / len(daily) if daily else 0.0,
        "minimumSlateCoverage": min(coverage_values) if coverage_values else 0.0,
        "meanSlateCoverage": sum(coverage_values) / len(coverage_values) if coverage_values else 0.0,
        "brierScore": _brier(predictions),
        "logLoss": _log_loss(predictions),
        "daily": daily,
    }



# Search-time representation. The public evaluator above intentionally returns
# rich per-game dictionaries, but using it for 25,000 candidates repeatedly
# deep-copies signals, validates the same policy, and hashes it for every game.
# These compact tuples preserve the exact production formula while moving all
# invariant parsing/tag logic out of the candidate loop.
#
# Signal tuple fields:
# fair, delta, divergence, reversals, pull_count, coverage, velocity60,
# acceleration180, volatility180, price, market_side_code, is_home, decimal,
# fixed_rule_adjustment, market_probability, directional_override_base.
_COMPILED_SIGNAL_SIZE = 16


def _compile_signal_for_search(signal: Mapping[str, Any]) -> Tuple[Any, ...]:
    fair = policy_runtime._f(
        signal.get("fairProbability", signal.get("probLatest")), 0.5
    )
    fair = min(0.999, max(0.001, fair))
    delta = policy_runtime._f(signal.get("delta"), 0.0)
    divergence = max(0.0, policy_runtime._f(signal.get("bookDivergence"), 0.0))
    reversals = max(0, int(policy_runtime._f(signal.get("reversalCount"), 0.0)))
    pull_count = max(
        0,
        int(
            policy_runtime._f(
                signal.get(
                    "pullCountForGame",
                    policy_runtime._nested(signal, "temporalFeatures", "sourcePointCount"),
                ),
                0.0,
            )
        ),
    )
    coverage = min(
        1.0,
        max(
            0.0,
            policy_runtime._f(
                policy_runtime._nested(
                    signal, "temporalFeatures", "horizons", "full", "coverageRatio"
                ),
                0.0,
            ),
        ),
    )
    velocity60 = policy_runtime._f(
        policy_runtime._nested(
            signal, "temporalFeatures", "horizons", "60m", "velocityPpHr"
        ),
        0.0,
    )
    acceleration180 = policy_runtime._f(
        policy_runtime._nested(
            signal, "temporalFeatures", "horizons", "180m", "accelerationPpHr2"
        ),
        0.0,
    )
    volatility180 = max(
        0.0,
        policy_runtime._f(
            policy_runtime._nested(
                signal,
                "temporalFeatures",
                "horizons",
                "180m",
                "volatilityPpPerPull",
            ),
            0.0,
        ),
    )
    price_value = signal.get("americanOdds")
    price = policy_runtime._f(price_value, 0.0)
    side_type = str(
        signal.get("marketSide") or policy_runtime._market_side(price_value)
    ).lower()
    side_code = -1 if side_type == "favorite" else 1 if side_type == "underdog" else 0
    is_home = str(signal.get("side") or "").lower() == "home"
    decimal = policy_runtime._american_decimal(price_value) or 0.0
    fixed_adjustment = policy_runtime.fixed_rule_adjustment(signal)
    market_probability = policy_runtime._market_probability(signal)
    tags = {str(value) for value in signal.get("tags") or []}
    bad_or_unstable = {
        "LOW_PULL_DEPTH",
        "SINGLE_PULL_BASELINE",
        "BOOK_DIVERGENCE",
        "LATE_INSTABILITY",
        "COMPRESSED_MARKET",
        "UNCONFIRMED_RUN_LINE_MOVE",
        "RESISTANCE",
    }
    directional_override_base = bool(
        market_probability >= 0.42
        and delta >= 0.01
        and reversals <= 1
        and "BOOK_AGREEMENT" in tags
        and bool({"STEAM", "RUN_LINE_CONFIRMATION"} & tags)
        and not (tags & bad_or_unstable)
    )
    value = (
        fair,
        delta,
        divergence,
        reversals,
        pull_count,
        coverage,
        velocity60,
        acceleration180,
        volatility180,
        price,
        side_code,
        is_home,
        decimal,
        fixed_adjustment,
        market_probability,
        directional_override_base,
    )
    if len(value) != _COMPILED_SIGNAL_SIZE:
        raise HistoricalOptimizerError("compiled search signal schema changed")
    return value


def _compile_policy_for_search(policy: Mapping[str, Any]) -> Tuple[float, ...]:
    errors = policy_runtime.validate_policy(policy)
    if errors:
        raise HistoricalOptimizerError("invalid search policy: " + ",".join(errors))
    f = policy_runtime._f
    return (
        f(policy.get("movementWeight")),
        f(policy.get("movementClip")),
        f(policy.get("underdogMovementWeight")),
        f(policy.get("underdogMovementCap")),
        f(policy.get("heavyFavoritePenalty")),
        f(policy.get("heavyFavoritePrice")),
        f(policy.get("divergenceStart")),
        f(policy.get("divergenceWeight")),
        f(policy.get("divergenceCap")),
        f(policy.get("reversalPenalty")),
        f(policy.get("reversalCap")),
        f(policy.get("lowPullDepthMinimum")),
        f(policy.get("lowPullDepthMultiplier")),
        f(policy.get("velocity60mWeight")),
        f(policy.get("acceleration180mWeight")),
        f(policy.get("volatility180mPenalty")),
        f(policy.get("coverageShortfallPenalty")),
        f(policy.get("homeBias")),
        f(policy.get("favoriteBias")),
        f(policy.get("underdogBias")),
        f(policy.get("scoreEdgeWeight")),
        f(policy.get("scoreEvWeight")),
        f(policy.get("scoreDeltaWeight")),
        f(policy.get("scoreDivergencePenalty")),
        f(policy.get("scoreReversalPenalty")),
        f(policy.get("scorePositiveValueBonus")),
        f(policy.get("scoreHeavyFavoritePenalty")),
    )


def _score_compiled_signal(
    signal: Tuple[Any, ...], policy: Tuple[float, ...]
) -> Tuple[float, float]:
    (
        fair,
        delta,
        divergence,
        reversals,
        pull_count,
        coverage,
        velocity60,
        acceleration180,
        volatility180,
        price,
        side_code,
        is_home,
        decimal,
        fixed_adjustment,
        _market_probability_value,
        _directional_override_base,
    ) = signal
    (
        movement_weight,
        movement_clip,
        underdog_movement_weight,
        underdog_movement_cap,
        heavy_favorite_penalty,
        heavy_favorite_price,
        divergence_start,
        divergence_weight,
        divergence_cap,
        reversal_penalty,
        reversal_cap,
        low_pull_depth_minimum,
        low_pull_depth_multiplier,
        velocity60_weight,
        acceleration180_weight,
        volatility180_penalty,
        coverage_shortfall_penalty,
        home_bias,
        favorite_bias,
        underdog_bias,
        score_edge_weight,
        score_ev_weight,
        score_delta_weight,
        score_divergence_penalty,
        score_reversal_penalty,
        score_positive_value_bonus,
        score_heavy_favorite_penalty,
    ) = policy

    movement = delta * movement_weight
    if movement > movement_clip:
        movement = movement_clip
    elif movement < -movement_clip:
        movement = -movement_clip
    if side_code == 1 and delta > 0.0:
        extra = delta * underdog_movement_weight
        movement += underdog_movement_cap if extra > underdog_movement_cap else extra
    if side_code == -1 and price <= heavy_favorite_price:
        movement -= heavy_favorite_penalty
    if divergence > divergence_start:
        penalty = (divergence - divergence_start) * divergence_weight
        movement -= divergence_cap if penalty > divergence_cap else penalty
    if reversals:
        penalty = reversals * reversal_penalty
        movement -= reversal_cap if penalty > reversal_cap else penalty
    movement += velocity60 * velocity60_weight
    movement += acceleration180 * acceleration180_weight
    movement -= volatility180 * volatility180_penalty
    movement -= (1.0 - coverage) * coverage_shortfall_penalty
    if pull_count and pull_count < int(low_pull_depth_minimum):
        movement *= low_pull_depth_multiplier
    if is_home:
        movement += home_bias
    if side_code == -1:
        movement += favorite_bias
    elif side_code == 1:
        movement += underdog_bias

    probability = fair + movement
    if probability > 0.95:
        probability = 0.95
    elif probability < 0.05:
        probability = 0.05
    edge = probability - fair
    expected_value = probability * decimal - 1.0 if decimal else -1.0
    score = (
        50.0
        + edge * score_edge_weight
        + expected_value * score_ev_weight
        + delta * score_delta_weight
        - divergence * score_divergence_penalty
        - reversals * score_reversal_penalty
    )
    if side_code in {0, 1} and edge > 0.0 and expected_value > 0.0:
        score += score_positive_value_bonus
    if side_code == -1 and price <= heavy_favorite_price:
        score -= score_heavy_favorite_penalty
    if score > 100.0:
        score = 100.0
    elif score < 0.0:
        score = 0.0
    # Match production serialization exactly: apply_policy_to_signal stores the
    # raw score at four decimals before the fixed winner-rule adjustment reads
    # it, then production stores the optimized score at four decimals and its
    # sigmoid probability at eight decimals.
    raw_score = round(score, 4)
    optimized_score_unrounded = raw_score + fixed_adjustment
    if optimized_score_unrounded > 100.0:
        optimized_score_unrounded = 100.0
    elif optimized_score_unrounded < 0.0:
        optimized_score_unrounded = 0.0
    probability_from_score = 1.0 / (
        1.0 + math.exp(-(optimized_score_unrounded - 50.0) / 12.0)
    )
    if probability_from_score > 0.95:
        probability_from_score = 0.95
    elif probability_from_score < 0.05:
        probability_from_score = 0.05
    return round(optimized_score_unrounded, 4), round(probability_from_score, 8)


def _compile_partition_for_search(
    records: Sequence[Mapping[str, Any]], dates: Iterable[str]
) -> Dict[str, Any]:
    selected_dates = sorted({str(value) for value in dates})
    day_index = {day: index for index, day in enumerate(selected_dates)}
    counts = [0] * len(selected_dates)
    games: List[Tuple[Any, ...]] = []
    for row in records:
        day = str(row.get("slateDateEt") or "")
        index = day_index.get(day)
        if index is None:
            continue
        counts[index] += 1
        games.append(
            (
                index,
                int(row.get("homeWon") or 0),
                _compile_signal_for_search(row.get("homeSignal") or {}),
                _compile_signal_for_search(row.get("awaySignal") or {}),
            )
        )
    return {"dates": selected_dates, "counts": counts, "games": games}


def _evaluate_compiled_partition(
    compiled: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    daily_target: float,
) -> Dict[str, Any]:
    policy_values = _compile_policy_for_search(policy)
    dates = list(compiled.get("dates") or [])
    counts = list(compiled.get("counts") or [])
    correct_by_day = [0] * len(dates)
    total_correct = 0
    brier_total = 0.0
    log_loss_total = 0.0
    games = compiled.get("games") or []
    for day_index, home_won, home_signal, away_signal in games:
        home_score, home_raw_probability = _score_compiled_signal(
            home_signal, policy_values
        )
        away_score, away_raw_probability = _score_compiled_signal(
            away_signal, policy_values
        )
        home_market = home_signal[14]
        away_market = away_signal[14]
        anchor_is_home = home_market >= away_market
        candidate_is_home = home_score >= away_score
        selected_is_home = candidate_is_home
        if candidate_is_home != anchor_is_home:
            candidate_signal = home_signal if candidate_is_home else away_signal
            candidate_score = home_score if candidate_is_home else away_score
            anchor_score = home_score if anchor_is_home else away_score
            selected_is_home = bool(
                candidate_signal[15]
                and candidate_score >= 64.0
                and candidate_score - anchor_score >= 8.0
            )
            if not selected_is_home:
                selected_is_home = anchor_is_home
        home_probability = home_raw_probability / (
            home_raw_probability + away_raw_probability
        )
        correct = int(selected_is_home) == home_won
        if correct:
            total_correct += 1
            correct_by_day[day_index] += 1
        delta_probability = home_probability - home_won
        brier_total += delta_probability * delta_probability
        bounded = min(1.0 - 1e-9, max(1e-9, home_probability))
        log_loss_total += -(
            home_won * math.log(bounded)
            + (1 - home_won) * math.log(1.0 - bounded)
        )

    daily = []
    accuracies = []
    for index, day in enumerate(dates):
        expected = counts[index]
        correct = correct_by_day[index]
        accuracy = correct / expected if expected else 0.0
        rounded_accuracy = round(accuracy, 8)
        accuracies.append(rounded_accuracy)
        daily.append(
            {
                "slateDateEt": day,
                "officialGameCount": expected,
                "predictionCount": expected,
                "correct": correct,
                "accuracy": rounded_accuracy,
                "coverage": 1.0 if expected else 0.0,
                "dayPassed": bool(expected and accuracy + 1e-12 >= daily_target),
            }
        )
    game_count = len(games)
    return {
        "policyDigest": policy_runtime.policy_digest(policy),
        "gameCount": game_count,
        "dayCount": len(daily),
        "correct": total_correct,
        "overallAccuracy": round(total_correct / game_count, 8) if game_count else 0.0,
        "minimumDailyAccuracy": min(accuracies) if accuracies else 0.0,
        "meanDailyAccuracy": sum(accuracies) / len(accuracies) if accuracies else 0.0,
        "dailyPassRate": (
            sum(row["dayPassed"] for row in daily) / len(daily) if daily else 0.0
        ),
        "minimumSlateCoverage": 1.0 if daily and all(counts) else 0.0,
        "meanSlateCoverage": 1.0 if daily and all(counts) else 0.0,
        "brierScore": brier_total / game_count if game_count else 1.0,
        "logLoss": log_loss_total / game_count if game_count else 1.0,
        "daily": daily,
    }


def chronological_partitions(
    records: Sequence[Mapping[str, Any]],
    config: SearchConfig,
    untouched_holdout_dates: Optional[Iterable[str]] = None,
) -> Dict[str, List[str]]:
    """Build whole-slate partitions with an optional fresh audit window.

    The first search reserves the latest 200+ games as an untouched audit.  If
    that round is rejected, the orchestrator may collect a strictly later block
    and pass those dates here.  Prior audit labels may then join development,
    while the new block remains unseen until the next single audit evaluation.
    """

    counts: Dict[str, int] = {}
    for row in records:
        day = str(row.get("slateDateEt") or "")
        if day:
            counts[day] = counts.get(day, 0) + 1
    dates = sorted(counts)
    required_days = config.minimum_walk_forward_days + config.minimum_holdout_days + 1
    if len(dates) < required_days:
        raise HistoricalOptimizerError("not enough complete slate dates for chronological validation")

    def suffix(available: Sequence[str], minimum_games: int, minimum_days: int) -> List[str]:
        selected: List[str] = []
        game_count = 0
        for day in reversed(list(available)):
            selected.append(day)
            game_count += counts[day]
            if game_count >= minimum_games and len(selected) >= minimum_days:
                return sorted(selected)
        raise HistoricalOptimizerError("not enough whole slate dates for a required partition")

    explicit = sorted({str(value) for value in (untouched_holdout_dates or []) if str(value)})
    if explicit:
        unknown = sorted(set(explicit) - set(dates))
        if unknown:
            raise HistoricalOptimizerError("explicit untouched audit contains unknown slate dates")
        if len(explicit) < config.minimum_holdout_days:
            raise HistoricalOptimizerError("explicit untouched audit has too few complete slate dates")
        untouched_game_count = sum(counts[day] for day in explicit)
        if untouched_game_count < config.minimum_untouched_holdout_games:
            raise HistoricalOptimizerError("explicit untouched audit has too few settled games")
        explicit_set = set(explicit)
        before_untouched = [day for day in dates if day not in explicit_set]
        if not before_untouched or max(before_untouched) >= min(explicit):
            raise HistoricalOptimizerError(
                "explicit untouched audit is not strictly after development dates"
            )
        untouched = explicit
    else:
        untouched = suffix(
            dates, config.minimum_untouched_holdout_games, config.minimum_holdout_days
        )
        before_untouched = dates[: dates.index(untouched[0])]

    walk_forward = suffix(
        before_untouched, config.minimum_walk_forward_games, config.minimum_walk_forward_days
    )
    train = before_untouched[: before_untouched.index(walk_forward[0])]
    train_count = sum(counts[day] for day in train)
    if train_count < config.minimum_training_games:
        raise HistoricalOptimizerError(
            f"training partition has {train_count} games; {config.minimum_training_games} required"
        )
    return {
        "train": train,
        "walkForward": walk_forward,
        "untouchedHoldout": untouched,
    }


def _candidate_policy(rng: random.Random) -> Dict[str, Any]:
    policy = copy.deepcopy(policy_runtime.BASELINE_POLICY)
    choices: Dict[str, Sequence[float]] = {
        "movementWeight": (0.30, 0.50, 0.70, 0.90, 1.10, 1.30),
        "movementClip": (0.020, 0.030, 0.040, 0.050),
        "underdogMovementWeight": (0.0, 0.20, 0.35, 0.50, 0.70),
        "underdogMovementCap": (0.0, 0.004, 0.008, 0.012, 0.016),
        "heavyFavoritePenalty": (0.0, 0.002, 0.004, 0.006, 0.008),
        "divergenceStart": (0.015, 0.025, 0.035, 0.045, 0.055),
        "divergenceWeight": (0.0, 0.25, 0.50, 0.75, 1.0),
        "divergenceCap": (0.006, 0.012, 0.018, 0.024),
        "reversalPenalty": (0.0, 0.002, 0.004, 0.006, 0.008),
        "reversalCap": (0.008, 0.018, 0.028, 0.038),
        "lowPullDepthMultiplier": (0.35, 0.55, 0.75, 1.0),
        "velocity60mWeight": (-0.004, -0.002, 0.0, 0.002, 0.004),
        "acceleration180mWeight": (-0.002, -0.001, 0.0, 0.001, 0.002),
        "volatility180mPenalty": (0.0, 0.002, 0.004, 0.008),
        "coverageShortfallPenalty": (0.0, 0.01, 0.02, 0.04),
        "homeBias": (-0.005, 0.0, 0.005),
        "favoriteBias": (-0.005, 0.0, 0.005, 0.010),
        "underdogBias": (-0.010, -0.005, 0.0, 0.005),
        "scoreEdgeWeight": (500.0, 700.0, 900.0, 1200.0, 1500.0),
        "scoreEvWeight": (0.0, 100.0, 220.0, 350.0, 500.0),
        "scoreDeltaWeight": (0.0, 130.0, 260.0, 400.0, 600.0),
        "scoreDivergencePenalty": (50.0, 110.0, 180.0, 260.0),
        "scoreReversalPenalty": (0.0, 1.5, 2.5, 4.0, 6.0),
        "scorePositiveValueBonus": (0.0, 2.0, 4.0, 6.0, 8.0),
        "scoreHeavyFavoritePenalty": (0.0, 2.0, 4.0, 6.0, 8.0),
    }
    for name, values in choices.items():
        policy[name] = rng.choice(values)
    return policy


def candidate_policies(config: SearchConfig) -> Iterator[Dict[str, Any]]:
    yielded = set()

    def emit(value: Dict[str, Any]):
        identity = policy_runtime.policy_digest(value)
        if identity in yielded:
            return None
        yielded.add(identity)
        return value

    baseline = emit(copy.deepcopy(policy_runtime.BASELINE_POLICY))
    if baseline:
        yield baseline
    # Structured market-only incumbents are always searched before randomized
    # movement/temporal combinations.
    for movement in (0.0, 0.35, 0.70, 1.05):
        for favorite_bias in (0.0, 0.005, 0.010):
            value = copy.deepcopy(policy_runtime.BASELINE_POLICY)
            value["movementWeight"] = movement
            value["favoriteBias"] = favorite_bias
            value["velocity60mWeight"] = 0.0
            value["acceleration180mWeight"] = 0.0
            candidate = emit(value)
            if candidate:
                yield candidate
    rng = random.Random(config.random_seed)
    while len(yielded) < config.maximum_candidates:
        candidate = emit(_candidate_policy(rng))
        if candidate:
            yield candidate


def _rank(metrics: Mapping[str, Any]) -> Tuple[float, ...]:
    return (
        float(metrics.get("dailyPassRate") or 0.0),
        float(metrics.get("minimumDailyAccuracy") or 0.0),
        float(metrics.get("meanDailyAccuracy") or 0.0),
        float(metrics.get("overallAccuracy") or 0.0),
        -float(metrics.get("brierScore") or 1.0),
        -float(metrics.get("logLoss") or 10.0),
    )


def _overfit_checks(
    train: Mapping[str, Any], validation: Mapping[str, Any], baseline_validation: Mapping[str, Any], config: SearchConfig
) -> Dict[str, Any]:
    accuracy_gap = abs(
        float(train.get("meanDailyAccuracy") or 0.0)
        - float(validation.get("meanDailyAccuracy") or 0.0)
    )
    brier_delta = float(validation.get("brierScore") or 1.0) - float(
        baseline_validation.get("brierScore") or 1.0
    )
    log_loss_delta = float(validation.get("logLoss") or 10.0) - float(
        baseline_validation.get("logLoss") or 10.0
    )
    passed = bool(
        accuracy_gap <= config.maximum_train_validation_accuracy_gap + 1e-12
        and brier_delta <= config.maximum_brier_degradation + 1e-12
        and log_loss_delta <= config.maximum_log_loss_degradation + 1e-12
        and float(validation.get("minimumSlateCoverage") or 0.0) >= 1.0 - 1e-12
    )
    return {
        "passed": passed,
        "trainValidationMeanDailyAccuracyGap": round(accuracy_gap, 8),
        "maximumAllowedAccuracyGap": config.maximum_train_validation_accuracy_gap,
        "brierDeltaVsBaseline": round(brier_delta, 8),
        "maximumAllowedBrierDegradation": config.maximum_brier_degradation,
        "logLossDeltaVsBaseline": round(log_loss_delta, 8),
        "maximumAllowedLogLossDegradation": config.maximum_log_loss_degradation,
    }


def search(
    records: Sequence[Mapping[str, Any]],
    config: Optional[SearchConfig] = None,
    *,
    untouched_holdout_dates: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    config = (config or SearchConfig()).validate()
    clean = [copy.deepcopy(dict(row)) for row in records]
    if len(clean) < config.minimum_settled_games:
        return {
            "ok": False,
            "version": VERSION,
            "status": "ACCUMULATING_HISTORICAL_GAMES",
            "settledGameCount": len(clean),
            "required": config.minimum_settled_games,
            "requiredTrainingGames": config.minimum_training_games,
            "requiredWalkForwardGames": config.minimum_walk_forward_games,
            "requiredUntouchedAuditGames": config.minimum_untouched_holdout_games,
        }
    try:
        partitions = chronological_partitions(
            clean, config, untouched_holdout_dates=untouched_holdout_dates
        )
    except HistoricalOptimizerError as exc:
        return {
            "ok": False,
            "version": VERSION,
            "status": "ACCUMULATING_HISTORICAL_GAMES",
            "settledGameCount": len(clean),
            "required": config.minimum_settled_games,
            "requiredTrainingGames": config.minimum_training_games,
            "requiredWalkForwardGames": config.minimum_walk_forward_games,
            "requiredUntouchedAuditGames": config.minimum_untouched_holdout_games,
            "partitionReason": str(exc),
        }
    compiled_partitions = {
        name: _compile_partition_for_search(clean, dates)
        for name, dates in partitions.items()
    }
    baseline = copy.deepcopy(policy_runtime.BASELINE_POLICY)
    baseline_train_compiled = _evaluate_compiled_partition(
        compiled_partitions["train"],
        baseline,
        daily_target=config.minimum_daily_accuracy,
    )
    baseline_validation_compiled = _evaluate_compiled_partition(
        compiled_partitions["walkForward"],
        baseline,
        daily_target=config.minimum_daily_accuracy,
    )
    incumbent_policy = baseline
    incumbent_train_compiled = baseline_train_compiled
    incumbent_validation_compiled = baseline_validation_compiled
    incumbent_overfit_compiled = _overfit_checks(
        baseline_train_compiled,
        baseline_validation_compiled,
        baseline_validation_compiled,
        config,
    )
    improvements = []
    evaluated = 0
    rejected_overfit = 0
    for candidate in candidate_policies(config):
        evaluated += 1
        if candidate == baseline:
            continue
        validation = _evaluate_compiled_partition(
            compiled_partitions["walkForward"],
            candidate,
            daily_target=config.minimum_daily_accuracy,
        )
        if _rank(validation) <= _rank(incumbent_validation_compiled):
            continue
        train = _evaluate_compiled_partition(
            compiled_partitions["train"],
            candidate,
            daily_target=config.minimum_daily_accuracy,
        )
        overfit = _overfit_checks(
            train, validation, baseline_validation_compiled, config
        )
        if not overfit["passed"]:
            rejected_overfit += 1
            continue
        incumbent_policy = candidate
        incumbent_train_compiled = train
        incumbent_validation_compiled = validation
        incumbent_overfit_compiled = overfit
        improvements.append(
            {
                "candidateNumber": evaluated,
                "policyDigest": policy_runtime.policy_digest(candidate),
                "walkForwardDailyPassRate": validation["dailyPassRate"],
                "walkForwardMinimumDailyAccuracy": validation["minimumDailyAccuracy"],
                "walkForwardMeanDailyAccuracy": validation["meanDailyAccuracy"],
                "walkForwardBrierScore": validation["brierScore"],
            }
        )
    # Re-run the selected incumbent and baseline through the rich public runtime
    # formula before evaluating the untouched holdout or any promotion gate. The
    # compiled loop is a speed optimization only; it cannot become proof.
    baseline_train = evaluate_policy(
        clean, baseline, partitions["train"], daily_target=config.minimum_daily_accuracy
    )
    baseline_validation = evaluate_policy(
        clean,
        baseline,
        partitions["walkForward"],
        daily_target=config.minimum_daily_accuracy,
    )
    incumbent_train = evaluate_policy(
        clean,
        incumbent_policy,
        partitions["train"],
        daily_target=config.minimum_daily_accuracy,
    )
    incumbent_validation = evaluate_policy(
        clean,
        incumbent_policy,
        partitions["walkForward"],
        daily_target=config.minimum_daily_accuracy,
    )
    incumbent_overfit = _overfit_checks(
        incumbent_train, incumbent_validation, baseline_validation, config
    )

    # This is the first and only use of the untouched holdout in this search.
    # It is evaluated only after the policy has been frozen by development data.
    holdout = evaluate_policy(
        clean,
        incumbent_policy,
        partitions["untouchedHoldout"],
        daily_target=config.minimum_daily_accuracy,
    )
    baseline_holdout = evaluate_policy(
        clean,
        baseline,
        partitions["untouchedHoldout"],
        daily_target=config.minimum_daily_accuracy,
    )
    gate_errors = []
    partition_game_counts = {
        "train": incumbent_train["gameCount"],
        "walkForward": incumbent_validation["gameCount"],
        "untouchedHoldout": holdout["gameCount"],
    }
    if len(clean) < config.minimum_settled_games:
        gate_errors.append("settled_game_floor_not_met")
    if partition_game_counts["train"] < config.minimum_training_games:
        gate_errors.append("training_game_floor_not_met")
    if partition_game_counts["walkForward"] < config.minimum_walk_forward_games:
        gate_errors.append("walk_forward_game_floor_not_met")
    if partition_game_counts["untouchedHoldout"] < config.minimum_untouched_holdout_games:
        gate_errors.append("untouched_audit_game_floor_not_met")
    if incumbent_validation["dayCount"] < config.minimum_walk_forward_days:
        gate_errors.append("walk_forward_day_floor_not_met")
    if holdout["dayCount"] < config.minimum_holdout_days:
        gate_errors.append("untouched_holdout_day_floor_not_met")
    for name, metrics in (("walk_forward", incumbent_validation), ("untouched_holdout", holdout)):
        if metrics["minimumSlateCoverage"] < 1.0 - 1e-12:
            gate_errors.append(f"{name}_exact_slate_coverage_failed")
        if metrics["dailyPassRate"] < 1.0 - 1e-12:
            gate_errors.append(f"{name}_contains_day_below_80_percent")
        if metrics["minimumDailyAccuracy"] + 1e-12 < config.minimum_daily_accuracy:
            gate_errors.append(f"{name}_minimum_daily_accuracy_failed")
        if metrics["meanDailyAccuracy"] + 1e-12 < config.minimum_daily_accuracy:
            gate_errors.append(f"{name}_mean_daily_accuracy_failed")
    if not all(row.get("postLockDataExcluded") is True for row in clean):
        gate_errors.append("post_lock_exclusion_proof_missing")
    if not all(row.get("gameSpecificLockClipping") is True for row in clean):
        gate_errors.append("game_specific_lock_clipping_proof_missing")
    if not incumbent_overfit.get("passed"):
        gate_errors.append("overfit_checks_failed")
    if _rank(incumbent_validation) <= _rank(baseline_validation):
        gate_errors.append("candidate_did_not_improve_walk_forward_daily_objective")
    if holdout["brierScore"] > baseline_holdout["brierScore"] + config.maximum_brier_degradation:
        gate_errors.append("untouched_holdout_brier_degraded")
    if holdout["logLoss"] > baseline_holdout["logLoss"] + config.maximum_log_loss_degradation:
        gate_errors.append("untouched_holdout_log_loss_degraded")
    gate = {
        "version": policy_runtime.PROMOTION_GATE_VERSION,
        "passed": not gate_errors,
        "errors": sorted(set(gate_errors)),
        "settledGameCount": len(clean),
        "requiredSettledGameCount": config.minimum_settled_games,
        "trainingGameCount": partition_game_counts["train"],
        "requiredTrainingGameCount": config.minimum_training_games,
        "walkForwardGameCount": partition_game_counts["walkForward"],
        "requiredWalkForwardGameCount": config.minimum_walk_forward_games,
        "untouchedHoldoutGameCount": partition_game_counts["untouchedHoldout"],
        "requiredUntouchedHoldoutGameCount": config.minimum_untouched_holdout_games,
        "walkForwardDayCount": incumbent_validation["dayCount"],
        "untouchedHoldoutDayCount": holdout["dayCount"],
        "walkForwardMinimumDailyAccuracy": incumbent_validation["minimumDailyAccuracy"],
        "walkForwardMeanDailyAccuracy": incumbent_validation["meanDailyAccuracy"],
        "untouchedHoldoutMinimumDailyAccuracy": holdout["minimumDailyAccuracy"],
        "untouchedHoldoutMeanDailyAccuracy": holdout["meanDailyAccuracy"],
        "walkForwardSlateCoverage": incumbent_validation["minimumSlateCoverage"],
        "untouchedHoldoutSlateCoverage": holdout["minimumSlateCoverage"],
        "dailyAccuracyRequirement": config.minimum_daily_accuracy,
        "dailyAccuracyTargetHigh": config.target_daily_accuracy_high,
        "walkForwardReached90PctMean": incumbent_validation["meanDailyAccuracy"] + 1e-12
        >= config.target_daily_accuracy_high,
        "untouchedHoldoutReached90PctMean": holdout["meanDailyAccuracy"] + 1e-12
        >= config.target_daily_accuracy_high,
        "holdoutWasUntouchedDuringSearch": True,
        "chronologicalWholeSlateSplits": True,
        "postLockDataExcluded": all(row.get("postLockDataExcluded") is True for row in clean),
        "gameSpecificLockClipping": all(
            row.get("gameSpecificLockClipping") is True for row in clean
        ),
        "overfitChecksPassed": incumbent_overfit.get("passed") is True,
        "overfitChecks": incumbent_overfit,
        "candidateImprovedWalkForwardDailyObjective": _rank(incumbent_validation)
        > _rank(baseline_validation),
    }
    return {
        "ok": True,
        "version": VERSION,
        "searchVersion": SEARCH_VERSION,
        "status": "PROMOTION_GATE_PASSED" if gate["passed"] else "CANDIDATE_REJECTED",
        "datasetFingerprint": dataset_fingerprint(clean),
        "settledGameCount": len(clean),
        "slateDateCount": len({str(row.get("slateDateEt") or "") for row in clean}),
        "partitions": partitions,
        "holdoutLabelReadPolicy": "winner selected before first holdout evaluation; holdout labels never enter search",
        "holdoutDefinition": {
            "explicitFreshWindow": bool(untouched_holdout_dates),
            "dates": list(partitions["untouchedHoldout"]),
            "strictlyAfterDevelopment": bool(
                partitions["untouchedHoldout"]
                and partitions["walkForward"]
                and min(partitions["untouchedHoldout"]) > max(partitions["walkForward"])
            ),
        },
        "candidateCountEvaluated": evaluated,
        "compiledCandidateEvaluations": max(0, evaluated - 1),
        "richProofPolicyEvaluations": 6,
        "searchAcceleration": "compiled_prefilter_with_rich_runtime_equivalent_proof",
        "compiledIncumbentDiagnostics": {
            "train": incumbent_train_compiled,
            "walkForward": incumbent_validation_compiled,
            "overfitChecks": incumbent_overfit_compiled,
        },
        "overfitCandidateCountRejected": rejected_overfit,
        "retainedImprovementCount": len(improvements),
        "retainedImprovements": improvements[-50:],
        "baseline": {
            "policy": baseline,
            "policyDigest": policy_runtime.policy_digest(baseline),
            "train": baseline_train,
            "walkForward": baseline_validation,
            "untouchedHoldout": baseline_holdout,
        },
        "candidate": {
            "policy": incumbent_policy,
            "policyDigest": policy_runtime.policy_digest(incumbent_policy),
            "train": incumbent_train,
            "walkForward": incumbent_validation,
            "untouchedHoldout": holdout,
        },
        "promotionGate": gate,
    }


def champion_payload(search_result: Mapping[str, Any], artifact: Mapping[str, Any], activated_at_utc: str) -> Dict[str, Any]:
    gate = copy.deepcopy(search_result.get("promotionGate") or {})
    candidate = search_result.get("candidate") or {}
    policy = copy.deepcopy(candidate.get("policy") or {})
    if gate.get("passed") is not True:
        raise HistoricalOptimizerError("candidate cannot be activated before the promotion gate passes")
    errors = policy_runtime.validate_policy(policy)
    if errors:
        raise HistoricalOptimizerError("candidate policy is invalid: " + ",".join(errors))
    payload = {
        "version": policy_runtime.VERSION,
        "recordType": policy_runtime.CHAMPION_RECORD_TYPE,
        "liveAuthorityEnabled": True,
        "shadowOnly": False,
        "activatedAtUtc": activated_at_utc,
        "policy": policy,
        "policyDigest": policy_runtime.policy_digest(policy),
        "artifact": copy.deepcopy(dict(artifact)),
        "promotionGate": gate,
        "datasetFingerprint": search_result.get("datasetFingerprint"),
        "searchVersion": search_result.get("searchVersion"),
        "objective": "whole_day_complete_mlb_slate_accuracy",
        "evidencePartitionContract": {
            "trainingGames": policy_runtime.MIN_TRAINING_GAMES,
            "walkForwardGames": policy_runtime.MIN_WALK_FORWARD_GAMES,
            "untouchedAuditGames": policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES,
        },
        "requiredDailyAccuracyBand": {
            "minimum": policy_runtime.MIN_DAILY_ACCURACY,
            "targetHigh": policy_runtime.TARGET_DAILY_ACCURACY_HIGH,
            "ceilingIsNotARejectionRule": True,
        },
    }
    validation = policy_runtime.validate_champion(
        {"record_type": policy_runtime.CHAMPION_RECORD_TYPE, "data": payload}
    )
    if not validation.ok:
        raise HistoricalOptimizerError("champion payload failed runtime validation: " + ",".join(validation.errors))
    return payload
