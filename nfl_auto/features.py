"""Leakage-safe NFL game parsing, play aggregation, and frozen feature rows."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from .canonical import (
    digest,
    first_present,
    game_id as canonical_game_id,
    normalize_team,
    parse_bbd_kickoff,
)
from .config import PUBLIC_DECISION_HORIZON_MINUTES, parse_utc
from .markets import consensus_is_eligible

FEATURE_SCHEMA_VERSION = "nfl-auto-features-v1"
FEATURE_NAMES = (
    "home_off_epa",
    "away_off_epa",
    "off_epa_delta",
    "home_def_epa_allowed",
    "away_def_epa_allowed",
    "def_epa_edge_home",
    "pass_epa_delta",
    "rush_epa_delta",
    "success_rate_delta",
    "explosive_rate_delta",
    "turnover_rate_edge_home",
    "early_down_pass_rate_delta",
    "third_down_success_delta",
    "rest_days_delta",
    "home_games_available",
    "away_games_available",
    "market_line",
    "market_dispersion",
    "bookmaker_count_scaled",
    "probability_move_24h_to_t10",
    "probability_move_60m_to_t10",
    "line_move_24h_to_t10",
    "line_move_60m_to_t10",
)


@dataclass(frozen=True)
class Game:
    game_id: str
    season: int
    week: int
    game_type: str
    kickoff_utc: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    home_rest: float
    away_rest: float
    stadium: str | None = None
    roof: str | None = None
    surface: str | None = None

    @property
    def kickoff(self) -> datetime:
        return parse_utc(self.kickoff_utc)


@dataclass(frozen=True)
class TeamGameStats:
    team: str
    game_id: str
    offensive_epa: float
    pass_epa: float
    rush_epa: float
    success_rate: float
    explosive_rate: float
    turnover_rate: float
    early_down_pass_rate: float
    third_down_success: float
    defensive_epa_allowed: float
    defensive_success_allowed: float
    plays: int


@dataclass(frozen=True)
class FrozenFeatureRow:
    target: str
    event_key: str
    season: int
    week: int
    kickoff_utc: str
    features: tuple[float, ...]
    market_prior: float
    label: int
    feature_hash: str
    bbd_digest: str
    odds_digest: str
    decision_horizon_minutes: int = PUBLIC_DECISION_HORIZON_MINUTES

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "event_key": self.event_key,
            "season": self.season,
            "week": self.week,
            "kickoff_utc": self.kickoff_utc,
            "features": list(self.features),
            "feature_names": list(FEATURE_NAMES),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "market_prior": self.market_prior,
            "label": self.label,
            "feature_hash": self.feature_hash,
            "bbd_digest": self.bbd_digest,
            "odds_digest": self.odds_digest,
            "decision_horizon_minutes": self.decision_horizon_minutes,
            "training_eligible": True,
        }


def _number(row: Mapping[str, Any], *names: str, default: float = 0.0) -> float:
    value = first_present(row, *names, default=default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def parse_bbd_game(row: Mapping[str, Any]) -> Game:
    season = int(first_present(row, "season", "season_year"))
    week = int(first_present(row, "week", "week_number"))
    game_type = str(first_present(row, "game_type", "season_type", "type", default="")).upper()
    if game_type not in {"REG", "POST"}:
        raise ValueError("NFL_GAME_TYPE_NOT_TRAINING_ELIGIBLE")
    home_raw = first_present(row, "home_team", "home")
    away_raw = first_present(row, "away_team", "away")
    home = normalize_team(home_raw)
    away = normalize_team(away_raw)
    raw_id = first_present(row, "game_id", "id")
    identifier = str(raw_id or canonical_game_id(season, week, away, home))
    score = row.get("score") if isinstance(row.get("score"), Mapping) else {}
    scores = row.get("scores") if isinstance(row.get("scores"), Mapping) else {}
    scores_value = scores.get("value") if isinstance(scores.get("value"), Mapping) else {}
    home_score = int(
        first_present(
            row,
            "home_score",
            default=first_present(score, "home", default=first_present(scores_value, "home", default=0)),
        )
    )
    away_score = int(
        first_present(
            row,
            "away_score",
            default=first_present(score, "away", default=first_present(scores_value, "away", default=0)),
        )
    )
    return Game(
        game_id=identifier,
        season=season,
        week=week,
        game_type=game_type,
        kickoff_utc=parse_bbd_kickoff(row),
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        home_rest=_number(row, "home_rest", "home_rest_days", default=7.0),
        away_rest=_number(row, "away_rest", "away_rest_days", default=7.0),
        stadium=str(first_present(row, "stadium", default="")) or None,
        roof=str(first_present(row, "roof", default="")) or None,
        surface=str(first_present(row, "surface", default="")) or None,
    )


def _is_play(play: Mapping[str, Any]) -> bool:
    play_type = str(play.get("play_type") or "").lower()
    return play_type in {"pass", "run", "rush"} or play.get("epa") is not None


def _binary(play: Mapping[str, Any], *names: str) -> float:
    for name in names:
        value = play.get(name)
        if value is not None:
            try:
                return 1.0 if float(value) != 0.0 else 0.0
            except (TypeError, ValueError):
                text = str(value).lower()
                return 1.0 if text in {"true", "yes", "success"} else 0.0
    return 0.0


def aggregate_game_plays(game: Game, plays: Sequence[Mapping[str, Any]]) -> dict[str, TeamGameStats]:
    teams = (game.home_team, game.away_team)
    accum: dict[str, dict[str, float]] = {
        team: {
            "plays": 0.0,
            "epa": 0.0,
            "pass_plays": 0.0,
            "pass_epa": 0.0,
            "rush_plays": 0.0,
            "rush_epa": 0.0,
            "successes": 0.0,
            "explosive": 0.0,
            "turnovers": 0.0,
            "early_down_plays": 0.0,
            "early_down_passes": 0.0,
            "third_down_plays": 0.0,
            "third_down_successes": 0.0,
            "def_plays": 0.0,
            "def_epa": 0.0,
            "def_successes": 0.0,
        }
        for team in teams
    }
    for play in plays:
        if not isinstance(play, Mapping) or not _is_play(play):
            continue
        try:
            offense = normalize_team(play.get("posteam") or play.get("offense") or play.get("possession_team"))
            defense = normalize_team(play.get("defteam") or play.get("defense") or play.get("defensive_team"))
        except ValueError:
            continue
        if offense not in accum or defense not in accum:
            continue
        epa = _number(play, "epa", default=0.0)
        play_type = str(play.get("play_type") or "").lower()
        is_pass = play_type == "pass" or _binary(play, "pass_attempt") == 1.0
        is_rush = play_type in {"run", "rush"} or _binary(play, "rush_attempt") == 1.0
        yards = _number(play, "yards_gained", "yards", default=0.0)
        success = _binary(play, "success") if play.get("success") is not None else float(epa > 0.0)
        turnover = max(
            _binary(play, "interception", "interception_thrown"),
            _binary(play, "fumble_lost", "lost_fumble"),
        )
        down = int(_number(play, "down", default=0.0))
        third_success = success if down == 3 else 0.0
        row = accum[offense]
        row["plays"] += 1.0
        row["epa"] += epa
        row["successes"] += success
        row["turnovers"] += turnover
        row["explosive"] += float((is_pass and yards >= 20.0) or (is_rush and yards >= 10.0))
        if is_pass:
            row["pass_plays"] += 1.0
            row["pass_epa"] += epa
        if is_rush:
            row["rush_plays"] += 1.0
            row["rush_epa"] += epa
        if down in {1, 2}:
            row["early_down_plays"] += 1.0
            row["early_down_passes"] += float(is_pass)
        if down == 3:
            row["third_down_plays"] += 1.0
            row["third_down_successes"] += third_success
        defense_row = accum[defense]
        defense_row["def_plays"] += 1.0
        defense_row["def_epa"] += epa
        defense_row["def_successes"] += success

    result: dict[str, TeamGameStats] = {}
    for team in teams:
        row = accum[team]
        plays_count = max(1.0, row["plays"])
        pass_count = max(1.0, row["pass_plays"])
        rush_count = max(1.0, row["rush_plays"])
        def_count = max(1.0, row["def_plays"])
        early_count = max(1.0, row["early_down_plays"])
        third_count = max(1.0, row["third_down_plays"])
        result[team] = TeamGameStats(
            team=team,
            game_id=game.game_id,
            offensive_epa=row["epa"] / plays_count,
            pass_epa=row["pass_epa"] / pass_count,
            rush_epa=row["rush_epa"] / rush_count,
            success_rate=row["successes"] / plays_count,
            explosive_rate=row["explosive"] / plays_count,
            turnover_rate=row["turnovers"] / plays_count,
            early_down_pass_rate=row["early_down_passes"] / early_count,
            third_down_success=row["third_down_successes"] / third_count,
            defensive_epa_allowed=row["def_epa"] / def_count,
            defensive_success_allowed=row["def_successes"] / def_count,
            plays=int(row["plays"]),
        )
    return result


_ROLLING_FIELDS = (
    "offensive_epa",
    "pass_epa",
    "rush_epa",
    "success_rate",
    "explosive_rate",
    "turnover_rate",
    "early_down_pass_rate",
    "third_down_success",
    "defensive_epa_allowed",
    "defensive_success_allowed",
)


def _rolling_average(rows: Sequence[TeamGameStats], *, max_games: int = 8, decay: float = 0.82) -> dict[str, float]:
    selected = list(rows[-max_games:])
    if not selected:
        return {**{name: 0.0 for name in _ROLLING_FIELDS}, "games_available": 0.0}
    weights = [decay ** (len(selected) - 1 - index) for index in range(len(selected))]
    total = sum(weights)
    result = {
        name: sum(getattr(row, name) * weight for row, weight in zip(selected, weights)) / total
        for name in _ROLLING_FIELDS
    }
    result["games_available"] = float(len(selected))
    return result


def pregame_team_features(
    games: Sequence[Game],
    stats_by_game: Mapping[str, Mapping[str, TeamGameStats]],
) -> dict[str, dict[str, dict[str, float]]]:
    history: dict[str, list[TeamGameStats]] = {}
    output: dict[str, dict[str, dict[str, float]]] = {}
    for game in sorted(games, key=lambda row: (row.kickoff, row.game_id)):
        output[game.game_id] = {
            game.home_team: _rolling_average(history.get(game.home_team, [])),
            game.away_team: _rolling_average(history.get(game.away_team, [])),
        }
        current = stats_by_game.get(game.game_id) or {}
        for team in (game.home_team, game.away_team):
            row = current.get(team)
            if row is not None:
                history.setdefault(team, []).append(row)
    return output


def _market_fields(target: str, consensus: Mapping[str, Any]) -> tuple[str, str, str]:
    if target == "moneyline_home_win":
        return "moneyline", "home_probability", "home_probability"
    if target == "spread_home_cover":
        return "spread", "home_probability", "home_line"
    if target == "total_over":
        return "total", "over_probability", "total_line"
    raise ValueError("NFL_TARGET_UNSUPPORTED")


def _target_label(game: Game, target: str, line: float) -> int | None:
    if target == "moneyline_home_win":
        if game.home_score == game.away_score:
            return None
        return int(game.home_score > game.away_score)
    if target == "spread_home_cover":
        adjusted = game.home_score + line - game.away_score
        if abs(adjusted) < 1e-9:
            return None
        return int(adjusted > 0.0)
    if target == "total_over":
        adjusted = game.home_score + game.away_score - line
        if abs(adjusted) < 1e-9:
            return None
        return int(adjusted > 0.0)
    raise ValueError("NFL_TARGET_UNSUPPORTED")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default



def build_inference_features(
    *,
    game: Game,
    rolling: Mapping[str, Mapping[str, float]],
    snapshots: Mapping[int, Mapping[str, Any]],
    target: str,
    min_bookmakers: int,
) -> tuple[tuple[float, ...], float, float]:
    """Build the same feature schema as training without reading a result label."""
    if game.game_type != "REG":
        raise ValueError("NFL_LIVE_PREDICTIONS_REGULAR_SEASON_ONLY")
    if 10 not in snapshots:
        raise ValueError("NFL_T10_SNAPSHOT_MISSING")
    final_snapshot_at = snapshots[10].get("snapshot_at")
    if not final_snapshot_at:
        raise ValueError("NFL_T10_SNAPSHOT_TIMESTAMP_MISSING")
    if (game.kickoff - parse_utc(str(final_snapshot_at))).total_seconds() < PUBLIC_DECISION_HORIZON_MINUTES * 60:
        raise ValueError("NFL_T10_SNAPSHOT_TOO_LATE")
    market, probability_field, line_field = _market_fields(target, snapshots[10])
    if not consensus_is_eligible(snapshots[10], market, min_bookmakers):
        raise ValueError("NFL_T10_MARKET_INELIGIBLE")
    final_market = snapshots[10][market]
    prior = _safe_float(final_market.get(probability_field), -1.0)
    if not 0.0 < prior < 1.0:
        raise ValueError("NFL_MARKET_PRIOR_INVALID")
    line = 0.0 if target == "moneyline_home_win" else _safe_float(final_market.get(line_field))
    earlier_24 = (snapshots.get(1440) or {}).get(market) or {}
    earlier_60 = (snapshots.get(60) or {}).get(market) or {}
    p24 = _safe_float(earlier_24.get(probability_field), prior)
    p60 = _safe_float(earlier_60.get(probability_field), prior)
    line24 = 0.0 if target == "moneyline_home_win" else _safe_float(earlier_24.get(line_field), line)
    line60 = 0.0 if target == "moneyline_home_win" else _safe_float(earlier_60.get(line_field), line)
    home = rolling.get(game.home_team) or {}
    away = rolling.get(game.away_team) or {}
    values = (
        _safe_float(home.get("offensive_epa")),
        _safe_float(away.get("offensive_epa")),
        _safe_float(home.get("offensive_epa")) - _safe_float(away.get("offensive_epa")),
        _safe_float(home.get("defensive_epa_allowed")),
        _safe_float(away.get("defensive_epa_allowed")),
        _safe_float(away.get("defensive_epa_allowed")) - _safe_float(home.get("defensive_epa_allowed")),
        _safe_float(home.get("pass_epa")) - _safe_float(away.get("pass_epa")),
        _safe_float(home.get("rush_epa")) - _safe_float(away.get("rush_epa")),
        _safe_float(home.get("success_rate")) - _safe_float(away.get("success_rate")),
        _safe_float(home.get("explosive_rate")) - _safe_float(away.get("explosive_rate")),
        _safe_float(away.get("turnover_rate")) - _safe_float(home.get("turnover_rate")),
        _safe_float(home.get("early_down_pass_rate")) - _safe_float(away.get("early_down_pass_rate")),
        _safe_float(home.get("third_down_success")) - _safe_float(away.get("third_down_success")),
        game.home_rest - game.away_rest,
        _safe_float(home.get("games_available")),
        _safe_float(away.get("games_available")),
        line,
        _safe_float(final_market.get("dispersion")),
        min(1.0, _safe_float(final_market.get("bookmaker_count")) / 12.0),
        prior - p24,
        prior - p60,
        line - line24,
        line - line60,
    )
    return tuple(float(value) for value in values), prior, line


def materialize_game_rows(
    *,
    game: Game,
    rolling: Mapping[str, Mapping[str, float]],
    snapshots: Mapping[int, Mapping[str, Any]],
    bbd_provenance: Mapping[str, Any],
    odds_provenance: Mapping[str, Any],
    min_bookmakers: int,
) -> tuple[list[FrozenFeatureRow], dict[str, str]]:
    """Create one immutable T-10 row per eligible market target.

    `snapshots` maps minutes-before-kickoff to a consensus object. Every stored
    snapshot must carry `snapshot_at`; the final one must be at or before T-10.
    """
    excluded: dict[str, str] = {}
    if game.game_type not in {"REG", "POST"}:
        return [], {target: "PRESEASON_OR_UNKNOWN_GAME_TYPE" for target in ("moneyline_home_win", "spread_home_cover", "total_over")}
    if 10 not in snapshots:
        return [], {target: "T10_SNAPSHOT_MISSING" for target in ("moneyline_home_win", "spread_home_cover", "total_over")}
    final_snapshot_at = snapshots[10].get("snapshot_at")
    if not final_snapshot_at:
        return [], {target: "T10_SNAPSHOT_TIMESTAMP_MISSING" for target in ("moneyline_home_win", "spread_home_cover", "total_over")}
    seconds_before = (game.kickoff - parse_utc(str(final_snapshot_at))).total_seconds()
    if seconds_before < PUBLIC_DECISION_HORIZON_MINUTES * 60:
        return [], {target: "T10_SNAPSHOT_TOO_LATE" for target in ("moneyline_home_win", "spread_home_cover", "total_over")}
    if not bbd_provenance or not odds_provenance:
        return [], {target: "DUAL_PROVIDER_PROVENANCE_MISSING" for target in ("moneyline_home_win", "spread_home_cover", "total_over")}

    home = rolling.get(game.home_team) or {}
    away = rolling.get(game.away_team) or {}
    rows: list[FrozenFeatureRow] = []
    for target in ("moneyline_home_win", "spread_home_cover", "total_over"):
        market, probability_field, line_field = _market_fields(target, snapshots[10])
        if not consensus_is_eligible(snapshots[10], market, min_bookmakers):
            excluded[target] = "INSUFFICIENT_T10_BOOKMAKERS"
            continue
        final_market = snapshots[10][market]
        prior = _safe_float(final_market.get(probability_field), -1.0)
        if not 0.0 < prior < 1.0:
            excluded[target] = "INVALID_MARKET_PRIOR"
            continue
        line = 0.0 if target == "moneyline_home_win" else _safe_float(final_market.get(line_field))
        label = _target_label(game, target, line)
        if label is None:
            excluded[target] = "PUSH_OR_TIE"
            continue

        earlier_24 = (snapshots.get(1440) or {}).get(market) or {}
        earlier_60 = (snapshots.get(60) or {}).get(market) or {}
        p24 = _safe_float(earlier_24.get(probability_field), prior)
        p60 = _safe_float(earlier_60.get(probability_field), prior)
        line24 = 0.0 if target == "moneyline_home_win" else _safe_float(earlier_24.get(line_field), line)
        line60 = 0.0 if target == "moneyline_home_win" else _safe_float(earlier_60.get(line_field), line)

        values = (
            _safe_float(home.get("offensive_epa")),
            _safe_float(away.get("offensive_epa")),
            _safe_float(home.get("offensive_epa")) - _safe_float(away.get("offensive_epa")),
            _safe_float(home.get("defensive_epa_allowed")),
            _safe_float(away.get("defensive_epa_allowed")),
            _safe_float(away.get("defensive_epa_allowed")) - _safe_float(home.get("defensive_epa_allowed")),
            _safe_float(home.get("pass_epa")) - _safe_float(away.get("pass_epa")),
            _safe_float(home.get("rush_epa")) - _safe_float(away.get("rush_epa")),
            _safe_float(home.get("success_rate")) - _safe_float(away.get("success_rate")),
            _safe_float(home.get("explosive_rate")) - _safe_float(away.get("explosive_rate")),
            _safe_float(away.get("turnover_rate")) - _safe_float(home.get("turnover_rate")),
            _safe_float(home.get("early_down_pass_rate")) - _safe_float(away.get("early_down_pass_rate")),
            _safe_float(home.get("third_down_success")) - _safe_float(away.get("third_down_success")),
            game.home_rest - game.away_rest,
            _safe_float(home.get("games_available")),
            _safe_float(away.get("games_available")),
            line,
            _safe_float(final_market.get("dispersion")),
            min(1.0, _safe_float(final_market.get("bookmaker_count")) / 12.0),
            prior - p24,
            prior - p60,
            line - line24,
            line - line60,
        )
        bbd_digest = digest(bbd_provenance)
        odds_digest = digest(odds_provenance)
        hash_payload = {
            "schema": FEATURE_SCHEMA_VERSION,
            "target": target,
            "event_key": game.game_id,
            "kickoff_utc": game.kickoff_utc,
            "features": values,
            "market_prior": prior,
            "decision_horizon_minutes": PUBLIC_DECISION_HORIZON_MINUTES,
            "bbd_digest": bbd_digest,
            "odds_digest": odds_digest,
        }
        rows.append(
            FrozenFeatureRow(
                target=target,
                event_key=game.game_id,
                season=game.season,
                week=game.week,
                kickoff_utc=game.kickoff_utc,
                features=tuple(float(value) for value in values),
                market_prior=prior,
                label=label,
                feature_hash=digest(hash_payload),
                bbd_digest=bbd_digest,
                odds_digest=odds_digest,
            )
        )
    return rows, excluded
