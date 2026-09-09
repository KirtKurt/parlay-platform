from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Any


@dataclass(frozen=True)
class RepairAction:
    action: str
    reason: str
    destructive: bool = False


def diagnose(state: Mapping[str, Any], now: datetime | None = None) -> list[RepairAction]:
    now = now or datetime.now(timezone.utc)
    actions: list[RepairAction] = []
    stale_minutes = float(state.get('minutes_since_last_pull') or 0)
    expected = float(state.get('expected_interval_minutes') or 15)
    if stale_minutes > max(expected * 2, 20):
        actions.append(RepairAction('FORCE_INGEST', 'STALE_PULLS'))
    if float(state.get('missing_market_fraction') or 0) >= .25:
        actions.append(RepairAction('REDISCOVER_EVENT_MARKETS', 'INCOMPLETE_MARKET_COVERAGE'))
    if int(state.get('unsettled_completed_games') or 0) > 0:
        actions.append(RepairAction('RUN_SETTLEMENT', 'SETTLEMENT_BACKLOG'))
    if state.get('champion_corrupt'):
        actions.append(RepairAction('QUARANTINE_CHAMPION_AND_FALLBACK', 'CORRUPT_MODEL_ARTIFACT'))
    if int(state.get('new_training_examples') or 0) >= int(state.get('min_new_examples') or 25):
        actions.append(RepairAction('RUN_TRAINING', 'NEW_TRAINING_DATA'))
    if state.get('training_stalled'):
        actions.append(RepairAction('RETRY_TRAINING_WITH_LAST_GOOD_DATA', 'TRAINING_STALLED'))
    return actions


ALLOWED_SELF_REPAIRS = {
    'FORCE_INGEST',
    'REDISCOVER_EVENT_MARKETS',
    'RUN_SETTLEMENT',
    'RUN_TRAINING',
    'RETRY_TRAINING_WITH_LAST_GOOD_DATA',
    'QUARANTINE_CHAMPION_AND_FALLBACK',
}


def validate_self_repair(action: RepairAction) -> None:
    if action.action not in ALLOWED_SELF_REPAIRS or action.destructive:
        raise RuntimeError('MLB_AUTO_REPAIR_OUTSIDE_ALLOWED_BOUNDARY')
