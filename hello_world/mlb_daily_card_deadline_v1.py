from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional


VERSION = "MLB-DAILY-CARD-DEADLINE-v1-second-game-minus-45"
DEFAULT_LEAD_MINUTES = 45


def parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _status_text(game: Dict[str, Any]) -> str:
    status = game.get("official_status") or game.get("officialStatus") or game.get("status") or {}
    if not isinstance(status, dict):
        return str(status or "").lower()
    return " ".join(
        str(status.get(key) or "")
        for key in ("abstractGameState", "detailedState", "codedGameState", "statusCode")
    ).lower()


def _is_actionable_game(game: Dict[str, Any]) -> bool:
    text = _status_text(game)
    return not any(token in text for token in ("cancelled", "canceled", "postponed"))


def _start(game: Dict[str, Any]) -> Optional[datetime]:
    for key in (
        "official_commence_time",
        "officialCommenceTime",
        "commence_time",
        "commenceTime",
        "gameDate",
    ):
        parsed = parse_dt(game.get(key))
        if parsed is not None:
            return parsed
    return None


def _game_id(game: Dict[str, Any]) -> str:
    for key in (
        "official_game_pk",
        "officialGamePk",
        "provider_event_id",
        "providerEventId",
        "game_id",
        "gameId",
        "id",
    ):
        value = str(game.get(key) or "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class DailyCardDeadlines:
    slate_date: str
    lead_minutes: int
    game_count: int
    first_game_id: Optional[str]
    second_game_id: Optional[str]
    first_game_start_utc: Optional[datetime]
    second_game_start_utc: Optional[datetime]
    first_pick_deadline_utc: Optional[datetime]
    full_card_deadline_utc: Optional[datetime]

    def as_dict(self, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        checked_at = parse_dt(now) or datetime.now(timezone.utc)
        return {
            "version": VERSION,
            "slateDate": self.slate_date,
            "leadMinutes": self.lead_minutes,
            "gameCount": self.game_count,
            "firstGameId": self.first_game_id,
            "secondGameId": self.second_game_id,
            "firstGameStartUtc": self.first_game_start_utc.isoformat() if self.first_game_start_utc else None,
            "secondGameStartUtc": self.second_game_start_utc.isoformat() if self.second_game_start_utc else None,
            "firstPickDeadlineUtc": self.first_pick_deadline_utc.isoformat() if self.first_pick_deadline_utc else None,
            "fullCardDeadlineUtc": self.full_card_deadline_utc.isoformat() if self.full_card_deadline_utc else None,
            "checkedAtUtc": checked_at.isoformat(),
            "firstPickDue": bool(self.first_pick_deadline_utc and checked_at >= self.first_pick_deadline_utc),
            "fullCardDue": bool(self.full_card_deadline_utc and checked_at >= self.full_card_deadline_utc),
            "policy": "FIRST_GAME_PICK_BY_T45_AND_FULL_DAILY_CARD_BY_SECOND_GAME_T45",
        }


def compute_deadlines(
    games: Iterable[Dict[str, Any]],
    *,
    slate_date: str = "",
    lead_minutes: int = DEFAULT_LEAD_MINUTES,
) -> DailyCardDeadlines:
    lead = max(1, int(lead_minutes))
    normalized: List[tuple[datetime, str, Dict[str, Any]]] = []
    for game in games or []:
        if not isinstance(game, dict) or not _is_actionable_game(game):
            continue
        start = _start(game)
        if start is None:
            continue
        normalized.append((start, _game_id(game), game))
    normalized.sort(key=lambda row: (row[0], row[1]))

    first = normalized[0] if normalized else None
    second = normalized[1] if len(normalized) > 1 else None
    first_start = first[0] if first else None
    second_start = second[0] if second else None
    delta = timedelta(minutes=lead)

    # A one-game slate uses its only game as the full-card anchor. On a normal
    # slate, the full card must exist no later than T-45 for the second game.
    full_anchor = second_start or first_start
    return DailyCardDeadlines(
        slate_date=str(slate_date or ""),
        lead_minutes=lead,
        game_count=len(normalized),
        first_game_id=first[1] if first else None,
        second_game_id=second[1] if second else None,
        first_game_start_utc=first_start,
        second_game_start_utc=second_start,
        first_pick_deadline_utc=(first_start - delta) if first_start else None,
        full_card_deadline_utc=(full_anchor - delta) if full_anchor else None,
    )


def publication_audit(
    games: Iterable[Dict[str, Any]],
    predictions: Iterable[Dict[str, Any]],
    *,
    slate_date: str = "",
    published_at: Any = None,
    lead_minutes: int = DEFAULT_LEAD_MINUTES,
) -> Dict[str, Any]:
    game_rows = [game for game in games or [] if isinstance(game, dict) and _is_actionable_game(game) and _start(game)]
    prediction_rows = [row for row in predictions or [] if isinstance(row, dict)]
    deadlines = compute_deadlines(game_rows, slate_date=slate_date, lead_minutes=lead_minutes)
    card_time = parse_dt(published_at)

    prediction_by_id: Dict[str, Dict[str, Any]] = {}
    for row in prediction_rows:
        identity = _game_id(row)
        if identity:
            prediction_by_id[identity] = row

    missing: List[str] = []
    late: List[Dict[str, Any]] = []
    for game in game_rows:
        identity = _game_id(game)
        row = prediction_by_id.get(identity)
        if row is None:
            missing.append(identity)
            continue
        predicted_at = None
        for key in ("predictedAtUtc", "predictionAtUtc", "lockedAtUtc", "publishedAtUtc", "createdAtUtc"):
            predicted_at = parse_dt(row.get(key))
            if predicted_at:
                break
        start = _start(game)
        if predicted_at is None:
            predicted_at = card_time
        if start and (predicted_at is None or predicted_at >= start):
            late.append(
                {
                    "gameId": identity,
                    "gameStartUtc": start.isoformat(),
                    "predictionAtUtc": predicted_at.isoformat() if predicted_at else None,
                }
            )

    first_deadline_met = bool(
        deadlines.first_pick_deadline_utc
        and card_time
        and card_time <= deadlines.first_pick_deadline_utc
    )
    full_deadline_met = bool(
        deadlines.full_card_deadline_utc
        and card_time
        and card_time <= deadlines.full_card_deadline_utc
        and not missing
    )
    if deadlines.game_count == 0:
        first_deadline_met = True
        full_deadline_met = True

    return {
        **deadlines.as_dict(now=card_time or datetime.now(timezone.utc)),
        "publishedAtUtc": card_time.isoformat() if card_time else None,
        "predictionCount": len(prediction_rows),
        "missingGameIds": sorted(value for value in missing if value),
        "postStartOrUnprovenPredictions": late,
        "firstPickDeadlineMet": first_deadline_met,
        "fullCardDeadlineMet": full_deadline_met,
        "allGamesPredicted": not missing and len(prediction_rows) >= deadlines.game_count,
        "allPredictionsPregame": not late,
        "timingHealthy": bool(first_deadline_met and full_deadline_met and not late),
    }
