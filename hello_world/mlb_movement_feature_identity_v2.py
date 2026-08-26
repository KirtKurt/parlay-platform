"""Derive identity-stable MLB movement features from immutable pregame pulls.

This module is intentionally source-honest. It never manufactures flat movement,
never reads outcomes, and never mutates predictions, locks, labels, models, or
promotion state. A feature is returned only when two real, pre-first-pitch
moneyline observations for the same official game can be compared.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "MLB-MOVEMENT-FEATURE-IDENTITY-v2-official-pregame-only"


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
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


def stable_identity(game: Mapping[str, Any]) -> Optional[str]:
    """Return an identity that survives provider event retirement after start."""
    official_pk = game.get("official_game_pk") or game.get("officialGamePk")
    if official_pk not in (None, ""):
        return f"official:{official_pk}"

    away = game.get("away_team") or game.get("awayTeam")
    home = game.get("home_team") or game.get("homeTeam")
    start = (
        game.get("official_commence_time")
        or game.get("officialCommenceTime")
        or game.get("commence_time")
        or game.get("commenceTime")
    )
    start_dt = _parse_dt(start)
    if away and home and start_dt is not None:
        return (
            f"teams:{_norm(away)}|{_norm(home)}|"
            f"start:{start_dt.isoformat()}"
        )
    return None


def _snapshot_games(snapshot: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    data = snapshot.get("data")
    if not isinstance(data, Mapping):
        return ()
    games = data.get("games")
    if not isinstance(games, Sequence) or isinstance(games, (str, bytes)):
        return ()
    return tuple(game for game in games if isinstance(game, Mapping))


def _observation_start(game: Mapping[str, Any]) -> Optional[datetime]:
    return _parse_dt(
        game.get("official_commence_time")
        or game.get("officialCommenceTime")
        or game.get("commence_time")
        or game.get("commenceTime")
    )


def _snapshot_asof(snapshot: Mapping[str, Any]) -> Optional[datetime]:
    return _parse_dt(
        snapshot.get("asof")
        or snapshot.get("created_at")
        or snapshot.get("createdAtUtc")
    )


def pregame_observations(
    snapshots: Iterable[Mapping[str, Any]],
) -> Dict[str, List[Tuple[datetime, Mapping[str, Any]]]]:
    """Group only observations captured strictly before the official start."""
    grouped: Dict[str, List[Tuple[datetime, Mapping[str, Any]]]] = defaultdict(list)
    seen: set[Tuple[str, str]] = set()
    for snapshot in snapshots:
        asof = _snapshot_asof(snapshot)
        if asof is None:
            continue
        for game in _snapshot_games(snapshot):
            identity = stable_identity(game)
            start = _observation_start(game)
            if identity is None or start is None or asof >= start:
                continue
            fingerprint = (identity, asof.isoformat())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            grouped[identity].append((asof, game))
    for rows in grouped.values():
        rows.sort(key=lambda value: value[0])
    return dict(grouped)


def derive_latest_features(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    delta_for_game: Callable[[Dict[str, Any], Dict[str, Any]], Mapping[str, Any]],
    movement_strength: Callable[[float, int, int], str],
) -> List[Dict[str, Any]]:
    """Return the latest valid real movement pair for each stable game identity."""
    derived: List[Dict[str, Any]] = []
    for identity, observations in sorted(pregame_observations(snapshots).items()):
        if len(observations) < 2:
            continue
        latest_valid: Optional[Tuple[datetime, datetime, Mapping[str, Any], Mapping[str, Any]]] = None
        for (previous_asof, previous), (latest_asof, latest) in zip(
            observations,
            observations[1:],
        ):
            row = delta_for_game(
                {**dict(previous), "_snapshot_asof": previous_asof.isoformat()},
                {**dict(latest), "_snapshot_asof": latest_asof.isoformat()},
            )
            if row.get("ok") is not True:
                continue
            latest_valid = (previous_asof, latest_asof, latest, row)
        if latest_valid is None:
            continue

        previous_asof, latest_asof, latest_game, row = latest_valid
        agreement = row.get("book_agreement") or {}
        try:
            hot_delta = float(row.get("hot_delta") or 0.0)
            agreeing = int(agreement.get("agreeing_books") or 0)
            disagreeing = int(agreement.get("disagreeing_books") or 0)
        except (TypeError, ValueError):
            continue
        strength = movement_strength(hot_delta, agreeing, disagreeing)
        favorite = row.get("favorite") or {}
        tags = list(row.get("reason_codes") or [])
        if strength != "FLAT":
            tags.append(f"hot_move_{strength.lower()}")

        derived.append(
            {
                "stable_identity": identity,
                "identity_version": VERSION,
                "official_game_pk": latest_game.get("official_game_pk")
                or latest_game.get("officialGamePk"),
                "game_id": latest_game.get("game_id")
                or latest_game.get("gameId")
                or latest_game.get("id"),
                "game_key": latest_game.get("game_key")
                or latest_game.get("gameKey"),
                "provider_event_id": latest_game.get("provider_event_id")
                or latest_game.get("providerEventId")
                or latest_game.get("id"),
                "home_team": latest_game.get("home_team")
                or latest_game.get("homeTeam"),
                "away_team": latest_game.get("away_team")
                or latest_game.get("awayTeam"),
                "commence_time": latest_game.get("official_commence_time")
                or latest_game.get("officialCommenceTime")
                or latest_game.get("commence_time")
                or latest_game.get("commenceTime"),
                "previous_asof": previous_asof.isoformat(),
                "latest_asof": latest_asof.isoformat(),
                "hot_team": row.get("hot_team"),
                "hot_delta": hot_delta,
                "movement_strength": strength,
                "favorite_side": favorite.get("side"),
                "favorite_team": favorite.get("team"),
                "dog_side": favorite.get("dog_side"),
                "dog_team": favorite.get("dog_team"),
                "book_agreement": agreement,
                "latest_consensus": row.get("latest_consensus"),
                "previous_consensus": row.get("previous_consensus"),
                "prediction_status_at_feature_time": row.get("prediction_status"),
                "signal_tags": sorted(set(tags)),
                "derived_from_immutable_pregame_snapshots": True,
                "outcome_data_used": False,
                "post_start_observation_used": False,
                "source_observation_count": len(observations),
            }
        )
    return derived
