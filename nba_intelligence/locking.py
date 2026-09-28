"""Immutable NBA prediction snapshots."""
import hashlib, json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CHECKPOINTS=(60,30,10)

def canonical_digest(payload: dict[str,Any]) -> str:
    body=json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()
    return hashlib.sha256(body).hexdigest()

@dataclass(frozen=True)
class LockedPrediction:
    game_id: str
    market: str
    selection: str
    p_raw: float
    p_calibrated: float
    model_version: str
    calibration_version: str
    prediction_time: datetime
    lock_minutes: int
    feature_digest: str
    odds_digest: str
    lineup_state: str
    injury_state: str
    explanation: dict[str,Any]

    def __post_init__(self):
        if self.lock_minutes not in CHECKPOINTS: raise ValueError("unsupported lock")
        for p in (self.p_raw,self.p_calibrated):
            if not 0 <= p <= 1: raise ValueError("probability outside [0,1]")

class ImmutableLedger:
    def __init__(self): self._rows={}
    def append(self, row: LockedPrediction):
        k=(row.game_id,row.market,row.lock_minutes)
        if k in self._rows: raise RuntimeError("locked prediction is immutable")
        self._rows[k]=row
        return row
    def rows(self): return tuple(self._rows.values())
