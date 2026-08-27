from __future__ import annotations

"""Authoritative timing and measurement policy for the autonomous MLB card.

The 70% figure is a transparent performance goal, never a probability override
or a guarantee. Every official MLB game must be scored in the denominator.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo


POLICY_VERSION = "MLB-THREE-API-AUTONOMY-v1"
SLATE_TIMEZONE = ZoneInfo("America/New_York")
PREDICTION_LEAD_MINUTES = 45
DAILY_ACCURACY_GOAL = 0.70

SOURCE_ROLES = {
    "mlb_stats_api": (
        "authoritative schedule, gamePk identity, official start time, game "
        "status, probable pitchers, venue, and final-result labels"
    ),
    "the_odds_api": (
        "sportsbook prices, bookmaker consensus/disagreement, movement, "
        "featured markets, period markets, alternates, team totals and props"
    ),
    "big_balls_data_pro": (
        "pregame baseball context including lineups, injuries, starters, "
        "team/player form and statistics, standings and supplemental signals"
    ),
}


class CardPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CardDeadlines:
    slate_date_et: str
    first_game_start_utc: datetime
    second_game_start_utc: datetime
    first_game_prediction_deadline_utc: datetime
    complete_card_deadline_utc: datetime
    per_game_deadline_utc: Dict[str, datetime]


def parse_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _game_id(game: Dict[str, Any]) -> str:
    for key in (
        "official_game_pk",
        "officialGamePk",
        "official_game_id",
        "officialGameId",
        "provider_event_id",
        "providerEventId",
        "game_id",
        "gameId",
        "id",
    ):
        value = game.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _game_start(game: Dict[str, Any]) -> Optional[datetime]:
    for key in (
        "official_commence_time",
        "officialCommenceTime",
        "commence_time",
        "commenceTime",
        "gameDate",
    ):
        parsed = parse_utc(game.get(key))
        if parsed is not None:
            return parsed
    return None


def ordered_games(games: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Tuple[datetime, str, Dict[str, Any]]] = []
    for raw in games or []:
        if not isinstance(raw, dict):
            continue
        game_id = _game_id(raw)
        start = _game_start(raw)
        if not game_id or start is None:
            raise CardPolicyError("OFFICIAL_GAME_ID_OR_START_MISSING")
        rows.append((start, game_id, raw))
    rows.sort(key=lambda item: (item[0], item[1]))
    if not rows:
        raise CardPolicyError("OFFICIAL_MLB_SLATE_EMPTY_OR_UNVERIFIED")
    if len({game_id for _, game_id, _ in rows}) != len(rows):
        raise CardPolicyError("DUPLICATE_OFFICIAL_GAME_ID")
    return [row for _, _, row in rows]


def card_deadlines(games: Iterable[Dict[str, Any]]) -> CardDeadlines:
    ordered = ordered_games(games)
    starts = [_game_start(game) for game in ordered]
    assert all(start is not None for start in starts)
    first_start = starts[0]
    second_start = starts[1] if len(starts) > 1 else first_start
    assert first_start is not None and second_start is not None

    first_deadline = first_start - timedelta(minutes=PREDICTION_LEAD_MINUTES)
    complete_deadline = second_start - timedelta(minutes=PREDICTION_LEAD_MINUTES)

    # The first game is independently protected at T-45. Every remaining game
    # must be locked no later than the complete-card deadline, while never later
    # than its own T-45. This produces a full-day card by second-game T-45 and
    # never permits a late prediction for any individual game.
    per_game: Dict[str, datetime] = {}
    for index, game in enumerate(ordered):
        game_id = _game_id(game)
        start = _game_start(game)
        assert start is not None
        own_deadline = start - timedelta(minutes=PREDICTION_LEAD_MINUTES)
        per_game[game_id] = (
            first_deadline if index == 0 else min(own_deadline, complete_deadline)
        )

    slate_date = first_start.astimezone(SLATE_TIMEZONE).date().isoformat()
    return CardDeadlines(
        slate_date_et=slate_date,
        first_game_start_utc=first_start,
        second_game_start_utc=second_start,
        first_game_prediction_deadline_utc=first_deadline,
        complete_card_deadline_utc=complete_deadline,
        per_game_deadline_utc=per_game,
    )


def _team(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _game_teams(game: Dict[str, Any]) -> Tuple[str, str]:
    home = _team(game.get("home_team") or game.get("homeTeam"))
    away = _team(game.get("away_team") or game.get("awayTeam"))
    if not home or not away or home == away:
        raise CardPolicyError(f"INVALID_OFFICIAL_TEAMS:{_game_id(game)}")
    return home, away


def _pick_identity(pick: Dict[str, Any]) -> str:
    return _game_id(pick)


def _pick_locked_at(pick: Dict[str, Any], card_published_at: Any) -> Optional[datetime]:
    for key in (
        "lockedAtUtc",
        "locked_at_utc",
        "predictionPersistedAtUtc",
        "prediction_persisted_at_utc",
        "publishedAtUtc",
        "published_at_utc",
    ):
        parsed = parse_utc(pick.get(key))
        if parsed is not None:
            return parsed
    return parse_utc(card_published_at)


def validate_daily_card(
    games: Iterable[Dict[str, Any]],
    picks: Iterable[Dict[str, Any]],
    *,
    card_published_at_utc: Any,
) -> Dict[str, Any]:
    ordered = ordered_games(games)
    deadlines = card_deadlines(ordered)
    official = {_game_id(game): game for game in ordered}
    pick_rows = [pick for pick in picks or [] if isinstance(pick, dict)]
    pick_ids = [_pick_identity(pick) for pick in pick_rows]

    errors: List[str] = []
    if len(pick_rows) != len(official):
        errors.append("FULL_SLATE_PICK_COUNT_MISMATCH")
    if any(not game_id for game_id in pick_ids):
        errors.append("PICK_GAME_ID_MISSING")
    if len(set(pick_ids)) != len(pick_ids):
        errors.append("DUPLICATE_PICK_GAME_ID")
    if set(pick_ids) != set(official):
        errors.append("OFFICIAL_SLATE_AND_PICK_IDENTITIES_DIFFER")

    timing: List[Dict[str, Any]] = []
    for pick in pick_rows:
        game_id = _pick_identity(pick)
        game = official.get(game_id)
        if game is None:
            continue
        home, away = _game_teams(game)
        winner = _team(
            pick.get("predictedWinner")
            or pick.get("predicted_winner")
            or pick.get("winner")
        )
        loser = _team(
            pick.get("predictedLoser")
            or pick.get("predicted_loser")
            or pick.get("loser")
        )
        if winner not in {home, away}:
            errors.append(f"INVALID_PREDICTED_WINNER:{game_id}")
        expected_loser = away if winner == home else home if winner == away else ""
        if loser and loser != expected_loser:
            errors.append(f"INVALID_PREDICTED_LOSER:{game_id}")
        locked_at = _pick_locked_at(pick, card_published_at_utc)
        deadline = deadlines.per_game_deadline_utc[game_id]
        timely = locked_at is not None and locked_at <= deadline
        if not timely:
            errors.append(f"PREDICTION_DEADLINE_MISSED:{game_id}")
        timing.append(
            {
                "gameId": game_id,
                "lockedAtUtc": locked_at.isoformat() if locked_at else None,
                "deadlineUtc": deadline.isoformat(),
                "timely": timely,
            }
        )

    published = parse_utc(card_published_at_utc)
    complete_card_timely = bool(
        published is not None and published <= deadlines.complete_card_deadline_utc
    )
    if not complete_card_timely:
        errors.append("COMPLETE_CARD_SECOND_GAME_T45_DEADLINE_MISSED")

    return {
        "ok": not errors,
        "policyVersion": POLICY_VERSION,
        "predictionLeadMinutes": PREDICTION_LEAD_MINUTES,
        "dailyAccuracyGoal": DAILY_ACCURACY_GOAL,
        "slateDateEt": deadlines.slate_date_et,
        "firstGamePredictionDeadlineUtc": (
            deadlines.first_game_prediction_deadline_utc.isoformat()
        ),
        "completeCardDeadlineUtc": deadlines.complete_card_deadline_utc.isoformat(),
        "completeCardTimely": complete_card_timely,
        "officialGameCount": len(official),
        "predictionCount": len(pick_rows),
        "allGamesPredicted": set(pick_ids) == set(official),
        "timing": timing,
        "errors": sorted(set(errors)),
    }


def daily_accuracy(
    settled_picks: Iterable[Dict[str, Any]],
    *,
    official_game_count: Optional[int] = None,
) -> Dict[str, Any]:
    rows = [row for row in settled_picks or [] if isinstance(row, dict)]
    denominator = int(official_game_count) if official_game_count is not None else len(rows)
    def row_is_correct(row: Dict[str, Any]) -> bool:
        if row.get("correct") is True:
            return True
        if row.get("correct") is False:
            return False
        predicted = _team(row.get("predictedWinner"))
        actual = _team(row.get("actualWinner"))
        return bool(predicted and actual and predicted == actual)

    correct = sum(1 for row in rows if row_is_correct(row))
    settled = sum(
        1
        for row in rows
        if row.get("correct") in {True, False}
        or row.get("actualWinner") not in (None, "")
    )
    complete = denominator > 0 and settled == denominator
    accuracy = (correct / denominator) if denominator > 0 else None
    return {
        "policyVersion": POLICY_VERSION,
        "dailyAccuracyGoal": DAILY_ACCURACY_GOAL,
        "officialGameCount": denominator,
        "settledGameCount": settled,
        "correctPickCount": correct,
        "dailyAccuracy": accuracy,
        "goalMet": bool(complete and accuracy is not None and accuracy >= DAILY_ACCURACY_GOAL),
        "completeOfficialSlateDenominator": complete,
        "measurementPolicy": (
            "all official MLB games; no passes, exclusions, rounding or "
            "post-hoc selection"
        ),
    }
