from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "mlb_signal_policy_v12", ROOT / "hello_world" / "mlb_signal_policy_v12.py"
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def row(tags):
    return {
        "predictedSide": "home",
        "predictedWinner": "Home",
        "homeSignal": {
            "marketConsensusProbability": 0.55,
            "delta": 0.04,
            "reversalCount": 3,
            "tags": tags,
            "temporalFeatures": {
                "horizons": {
                    "15m": {"velocityPpHr": 1.0},
                    "60m": {"velocityPpHr": 1.0, "reversalCount": 2},
                    "180m": {"velocityPpHr": 1.0, "reversalCount": 5},
                    "full": {"reversalCount": 10},
                }
            },
        },
        "awaySignal": {"marketConsensusProbability": 0.45},
    }


def main():
    agreement_only = module._signal_risk_gate_reasons(row(["BOOK_AGREEMENT"]))
    assert "positive_move_high_reversal_without_confirmation" in agreement_only
    assert "multi_horizon_reversal_instability" in agreement_only

    steam_only = module._signal_risk_gate_reasons(row(["STEAM"]))
    assert "multi_horizon_reversal_instability" in steam_only

    agreement_and_steam = module._signal_risk_gate_reasons(row(["BOOK_AGREEMENT", "STEAM"]))
    assert agreement_and_steam == []

    agreement_and_run_line = module._signal_risk_gate_reasons(row(["BOOK_AGREEMENT", "RUN_LINE_CONFIRMATION"]))
    assert agreement_and_run_line == []

    components = module._components(row(["BOOK_AGREEMENT"]))
    names = {item["name"] for item in components}
    assert "large_unconfirmed_reversal_move_penalty" in names
    print("MLB book-agreement confirmation gate PASS")


if __name__ == "__main__":
    main()
