"""Prioritized research backlog. Highest-value bottleneck first."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Item:
    item_id: str
    hypothesis: str
    potential_value: str
    required_data: str
    difficulty: str
    validation: str
    status: str
    rank: int

    def as_dict(self) -> dict:
        return asdict(self)


BACKLOG = [
    Item("BL-01", "Walk-forward the agreement gate on graded KS1 locks.",
         "Stops betting engine fights; first real 2stackMLB sample.",
         "graded_ledger.json + locked prediction rows with lambda and market.",
         "S", "Chronological folds, Brier all vs Brier(bet), Wilson on bet.",
         "harness_ready_waiting_on_ledger", 1),
    Item("BL-02", "Individual starter features instead of team_starter_prior.",
         "Fixes CWS-style recency fights and probable-pitcher identity.",
         "Confirmed/probable pitcher IDs already on Phase 5 rows; 2025 boxes.",
         "M", "Challenger LGB on 2025 train / 2026 test. Frozen hash. No silent swap.",
         "blocked_on_challenger_train_job", 2),
    Item("BL-03", "Put Poisson lambda into a challenger LGB as features.",
         "LGB explains residual after the run model instead of fighting it.",
         "Same Phase 2 table plus lambda from frozen Poisson.",
         "M", "Walk-forward vs current LGB Brier. Promote only if ECE also holds.",
         "specified_not_trained", 3),
    Item("BL-04", "Use already-stored Statcast research rows.",
         "Hard-hit / barrel / ev unused today; KS1 inventory already lists them.",
         "s3 research-v1/statcast.json (read-only).",
         "M", "Game-aggregated pregame-only features; no same-game pitches.",
         "data_exists_features_not_built", 4),
    Item("BL-05", "Park + weather in the run model.",
         "Poisson currently omits them as unavailable-in-training.",
         "Park factors + pregame weather bound to as_of.",
         "M", "Dual-Poisson MAE on 2026 holdout vs current poisson_model.json.",
         "blocked_on_training_coverage", 5),
    Item("BL-06", "F5 moneyline from remaining-inning Markov.",
         "First extra market with a structural model, not a new booster.",
         "KS1 lambda plus starter-inning split already in markov.py.",
         "S", "Needs F5 results. Do not publish until graded F5 sample >= 200.",
         "engine_exists_market_ungraded", 6),
    Item("BL-07", "Line-move / reversal family as a risk feature only.",
         "Prior MLB audits found reversal count is ~50% and not an edge.",
         "Canonical pull history already in AWS.",
         "L", "Fail-closed. Research-only until prospective Wilson >= bar.",
         "previously_audited_do_not_rerun_as_winner_model", 7),
]


def next_item():
    open_items = [i for i in BACKLOG if i.status not in {
        "rejected", "promoted", "previously_audited_do_not_rerun_as_winner_model"}]
    return sorted(open_items, key=lambda i: i.rank)[0]
