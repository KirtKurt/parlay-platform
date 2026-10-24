"""Experiment registry. Failed ideas stay written down so we do not rerun them."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass
class Experiment:
    experiment_id: str
    date: str
    hypothesis: str
    features_changed: str
    model_changed: str
    training_range: str
    validation_range: str
    sample_size: int
    accuracy: float | None
    brier: float | None
    log_loss: float | None
    calibration_ece: float | None
    result: str
    promotion_decision: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


REGISTRY = [
    Experiment(
        experiment_id="EXP-2026-09-11-A",
        date="2026-09-11",
        hypothesis="A second LightGBM with a new seed is a useful third engine.",
        features_changed="none",
        model_changed="rejected-gbdt-clone",
        training_range="n/a",
        validation_range="n/a",
        sample_size=0,
        accuracy=None, brier=None, log_loss=None, calibration_ece=None,
        result="rejected_a_priori",
        promotion_decision="reject",
        reason="Correlated errors with the champion. Architecture diversity required.",
    ),
    Experiment(
        experiment_id="EXP-2026-09-11-B",
        date="2026-09-11",
        hypothesis="Agreement gate + market-capped stack reduces 77% fantasies without rewriting p_home.",
        features_changed="none; serving-only",
        model_changed="2stackMLB-v1 shadow",
        training_range="none (no refit)",
        validation_range="contract fixtures NYM@NYY and CWS@STL 2026-09-11",
        sample_size=2,
        accuracy=None, brier=None, log_loss=None, calibration_ece=None,
        result="gate_sits_both_disputed_games",
        promotion_decision="shadow_only",
        reason="n=2 is not a holdout. Promotion forbidden until walk-forward on graded locks.",
    ),
]


def lookup(experiment_id: str):
    for item in REGISTRY:
        if item.experiment_id == experiment_id:
            return item
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
