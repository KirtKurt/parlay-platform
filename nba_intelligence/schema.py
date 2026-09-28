"""Canonical point-in-time NBA records. Missing data stays None."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

@dataclass(frozen=True)
class Provenance:
    source: str
    retrieved_at: datetime
    effective_at: datetime
    feature_version: str
    game_id: str
    entity_id: Optional[str] = None

    def assert_available(self, as_of: datetime) -> None:
        if self.effective_at > as_of or self.retrieved_at > as_of:
            raise ValueError("future-data leakage: provenance is later than as_of")

@dataclass(frozen=True)
class Game:
    game_id: str
    season: str
    tip_time: datetime
    home_team_id: str
    away_team_id: str
    venue: Optional[str] = None
    final_home: Optional[int] = None
    final_away: Optional[int] = None
    overtime_periods: Optional[int] = None

@dataclass(frozen=True)
class Observation:
    game_id: str
    entity_id: str
    name: str
    value: Optional[float]
    as_of: datetime
    provenance: Provenance
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.provenance.assert_available(self.as_of)
