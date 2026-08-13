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


def stable_row(tags):
    return {
        "predictedSide": "home",
        "predictedWinner": "Home",
        "homeSignal": {
            "marketConsensusProbability": 0.60,
            "latestGap": 0.20,
            "delta": 0.02,
            "reversalCount": 1,
            "tags": tags,
        },
        "awaySignal": {"marketConsensusProbability": 0.40},
    }


def against_market_row(tags):
    return {
        "predictedSide": "home",
        "predictedWinner": "Home",
        "playable": True,
        "homeSignal": {
            "marketConsensusProbability": 0.45,
            "latestGap": 0.10,
            "delta": -0.02,
            "reversalCount": 1,
            "tags": tags,
        },
        "awaySignal": {"marketConsensusProbability": 0.55},
    }


def large_move_extreme_acceleration_row(tags, *, acceleration_180=0.12):
    return {
        "predictedSide": "home",
        "predictedWinner": "Home",
        "playable": True,
        "homeSignal": {
            "marketConsensusProbability": 0.60,
            "latestGap": 0.20,
            "delta": 0.04,
            "reversalCount": 1,
            "tags": tags,
            "temporalFeatures": {
                "horizons": {
                    "180m": {
                        "velocityPpHr": 1.0,
                        "accelerationPpHr2": acceleration_180,
                    }
                }
            },
        },
        "awaySignal": {
            "marketConsensusProbability": 0.40,
            "delta": 0.0,
            "temporalFeatures": {
                "horizons": {
                    "180m": {
                        "velocityPpHr": 0.5,
                        "accelerationPpHr2": 0.0,
                    }
                }
            },
        },
    }


def large_move_medium_late_movement_row(tags, *, late_movement=0.02):
    return {
        "predictedSide": "home",
        "predictedWinner": "Home",
        "playable": True,
        "homeSignal": {
            "marketConsensusProbability": 0.60,
            "latestGap": 0.20,
            "delta": 0.04,
            "reversalCount": 1,
            "tags": tags,
            "marketIntelligence": {
                "curve": {
                    "lateMovement": late_movement,
                }
            },
        },
        "awaySignal": {
            "marketConsensusProbability": 0.40,
            "delta": 0.0,
            "marketIntelligence": {
                "curve": {
                    "lateMovement": 0.0,
                }
            },
        },
    }


def large_move_medium_full_acceleration_row(tags, *, full_acceleration=0.02):
    return {
        "predictedSide": "home",
        "predictedWinner": "Home",
        "playable": True,
        "homeSignal": {
            "marketConsensusProbability": 0.60,
            "latestGap": 0.20,
            "delta": 0.04,
            "reversalCount": 1,
            "tags": tags,
            "temporalFeatures": {
                "horizons": {
                    "full": {
                        "reversalCount": 1,
                        "accelerationPpHr2": full_acceleration,
                    }
                }
            },
        },
        "awaySignal": {
            "marketConsensusProbability": 0.40,
            "delta": 0.0,
            "temporalFeatures": {
                "horizons": {
                    "full": {
                        "reversalCount": 1,
                        "accelerationPpHr2": 0.0,
                    }
                }
            },
        },
    }


def component_names(value):
    return {item["name"] for item in module._components(value)}


def test_large_move_extreme_180m_acceleration_gate():
    unstable = large_move_extreme_acceleration_row([])
    reasons = module._signal_risk_gate_reasons(unstable)
    assert "large_move_extreme_180m_acceleration_without_confirmation" in reasons
    assert module._is_playable(unstable) is False
    assert "large_move_extreme_180m_acceleration_penalty" in component_names(unstable)

    confirmed = module._signal_risk_gate_reasons(
        large_move_extreme_acceleration_row(["BOOK_AGREEMENT", "STEAM"])
    )
    assert "large_move_extreme_180m_acceleration_without_confirmation" not in confirmed

    medium_acceleration = module._signal_risk_gate_reasons(
        large_move_extreme_acceleration_row([], acceleration_180=0.02)
    )
    assert "large_move_extreme_180m_acceleration_without_confirmation" not in medium_acceleration


def test_large_move_medium_late_movement_gate():
    unstable = large_move_medium_late_movement_row([])
    reasons = module._signal_risk_gate_reasons(unstable)
    assert "large_move_medium_late_movement_without_confirmation" in reasons
    assert module._is_playable(unstable) is False
    assert "large_move_medium_late_movement_penalty" in component_names(unstable)

    confirmed = module._signal_risk_gate_reasons(
        large_move_medium_late_movement_row(["BOOK_AGREEMENT", "STEAM"])
    )
    assert "large_move_medium_late_movement_without_confirmation" not in confirmed

    confirmed_run_line = module._signal_risk_gate_reasons(
        large_move_medium_late_movement_row(["BOOK_AGREEMENT", "RUN_LINE_CONFIRMATION"])
    )
    assert "large_move_medium_late_movement_without_confirmation" not in confirmed_run_line

    small_late_move = module._signal_risk_gate_reasons(
        large_move_medium_late_movement_row([], late_movement=0.005)
    )
    assert "large_move_medium_late_movement_without_confirmation" not in small_late_move

    opposite_late_move = module._signal_risk_gate_reasons(
        large_move_medium_late_movement_row([], late_movement=-0.02)
    )
    assert "large_move_medium_late_movement_without_confirmation" not in opposite_late_move


def test_large_move_medium_full_acceleration_gate():
    unstable = large_move_medium_full_acceleration_row([])
    reasons = module._signal_risk_gate_reasons(unstable)
    assert "large_move_medium_full_acceleration_without_confirmation" in reasons
    assert module._is_playable(unstable) is False
    assert "large_move_medium_full_acceleration_penalty" in component_names(unstable)

    confirmed = module._signal_risk_gate_reasons(
        large_move_medium_full_acceleration_row(["BOOK_AGREEMENT", "STEAM"])
    )
    assert "large_move_medium_full_acceleration_without_confirmation" not in confirmed

    confirmed_run_line = module._signal_risk_gate_reasons(
        large_move_medium_full_acceleration_row(["BOOK_AGREEMENT", "RUN_LINE_CONFIRMATION"])
    )
    assert "large_move_medium_full_acceleration_without_confirmation" not in confirmed_run_line

    small_acceleration = module._signal_risk_gate_reasons(
        large_move_medium_full_acceleration_row([], full_acceleration=0.005)
    )
    assert "large_move_medium_full_acceleration_without_confirmation" not in small_acceleration

    opposite_acceleration = module._signal_risk_gate_reasons(
        large_move_medium_full_acceleration_row([], full_acceleration=-0.02)
    )
    assert "large_move_medium_full_acceleration_without_confirmation" not in opposite_acceleration


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

    names = component_names(row(["BOOK_AGREEMENT"]))
    assert "large_unconfirmed_reversal_move_penalty" in names

    steam_without_agreement = component_names(stable_row(["STEAM"]))
    assert "stable_steam_boost" not in steam_without_agreement
    assert "unstable_steam_penalty" in steam_without_agreement

    steam_with_agreement = component_names(stable_row(["BOOK_AGREEMENT", "STEAM"]))
    assert "stable_steam_boost" in steam_with_agreement
    assert "unstable_steam_penalty" not in steam_with_agreement

    run_line_without_agreement = component_names(
        stable_row(["RUN_LINE_MOVEMENT", "RUN_LINE_CONFIRMATION"])
    )
    assert "aligned_run_line_boost" not in run_line_without_agreement
    assert "run_line_noise_penalty" in run_line_without_agreement

    run_line_with_agreement = component_names(
        stable_row(["BOOK_AGREEMENT", "RUN_LINE_MOVEMENT", "RUN_LINE_CONFIRMATION"])
    )
    assert "aligned_run_line_boost" in run_line_with_agreement
    assert "run_line_noise_penalty" not in run_line_with_agreement

    market_against = against_market_row([])
    reasons = module._signal_risk_gate_reasons(market_against)
    assert "market_direction_against_selection_without_confirmation" in reasons
    assert module._is_playable(market_against) is False

    agreement_only_against = module._signal_risk_gate_reasons(
        against_market_row(["BOOK_AGREEMENT"])
    )
    assert "market_direction_against_selection_without_confirmation" in agreement_only_against

    confirmed_against = module._signal_risk_gate_reasons(
        against_market_row(["BOOK_AGREEMENT", "STEAM"])
    )
    assert "market_direction_against_selection_without_confirmation" not in confirmed_against

    test_large_move_extreme_180m_acceleration_gate()
    test_large_move_medium_late_movement_gate()
    test_large_move_medium_full_acceleration_gate()

    print("MLB book-agreement confirmation gate PASS")


if __name__ == "__main__":
    main()
