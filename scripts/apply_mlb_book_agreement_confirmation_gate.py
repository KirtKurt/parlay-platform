from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "hello_world" / "mlb_signal_policy_v12.py"
TEST = ROOT / "tests" / "unit" / "test_mlb_book_agreement_confirmation_gate.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"missing migration marker: {label}")


def main() -> None:
    text = POLICY.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'VERSION = "MLB-SIGNAL-POLICY-v1.7-reversal-instability-gate"',
        'VERSION = "MLB-SIGNAL-POLICY-v1.8-book-agreement-requires-structural-confirmation"',
        "policy version",
    )
    old_gate = '''    confirmations = {"BOOK_AGREEMENT", "STEAM", "RUN_LINE_CONFIRMATION"}\n    independently_confirmed = bool(tags & confirmations)'''
    new_gate = '''    structural_confirmation = bool(tags & {"STEAM", "RUN_LINE_CONFIRMATION"})\n    independently_confirmed = "BOOK_AGREEMENT" in tags and structural_confirmation'''
    text = replace_once(text, old_gate, new_gate, "risk-gate confirmation")
    old_component = '    independent_confirmation = bool(tags & {"BOOK_AGREEMENT", "STEAM", "RUN_LINE_CONFIRMATION"})'
    new_component = '''    structural_confirmation = bool(tags & {"STEAM", "RUN_LINE_CONFIRMATION"})\n    independent_confirmation = "BOOK_AGREEMENT" in tags and structural_confirmation'''
    text = replace_once(text, old_component, new_component, "component confirmation")
    POLICY.write_text(text, encoding="utf-8")

    TEST.parent.mkdir(parents=True, exist_ok=True)
    TEST.write_text('''from __future__ import annotations\n\nimport importlib.util\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[2]\nSPEC = importlib.util.spec_from_file_location(\n    "mlb_signal_policy_v12", ROOT / "hello_world" / "mlb_signal_policy_v12.py"\n)\nmodule = importlib.util.module_from_spec(SPEC)\nassert SPEC and SPEC.loader\nSPEC.loader.exec_module(module)\n\n\ndef row(tags):\n    return {\n        "predictedSide": "home",\n        "predictedWinner": "Home",\n        "homeSignal": {\n            "marketConsensusProbability": 0.55,\n            "delta": 0.04,\n            "reversalCount": 3,\n            "tags": tags,\n            "temporalFeatures": {\n                "horizons": {\n                    "15m": {"velocityPpHr": 1.0},\n                    "60m": {"velocityPpHr": 1.0, "reversalCount": 2},\n                    "180m": {"velocityPpHr": 1.0, "reversalCount": 5},\n                    "full": {"reversalCount": 10},\n                }\n            },\n        },\n        "awaySignal": {"marketConsensusProbability": 0.45},\n    }\n\n\ndef main():\n    agreement_only = module._signal_risk_gate_reasons(row(["BOOK_AGREEMENT"]))\n    assert "positive_move_high_reversal_without_confirmation" in agreement_only\n    assert "multi_horizon_reversal_instability" in agreement_only\n\n    steam_only = module._signal_risk_gate_reasons(row(["STEAM"]))\n    assert "multi_horizon_reversal_instability" in steam_only\n\n    agreement_and_steam = module._signal_risk_gate_reasons(row(["BOOK_AGREEMENT", "STEAM"]))\n    assert agreement_and_steam == []\n\n    agreement_and_run_line = module._signal_risk_gate_reasons(row(["BOOK_AGREEMENT", "RUN_LINE_CONFIRMATION"]))\n    assert agreement_and_run_line == []\n\n    components = module._components(row(["BOOK_AGREEMENT"]))\n    names = {item["name"] for item in components}\n    assert "large_unconfirmed_reversal_move_penalty" in names\n    print("MLB book-agreement confirmation gate PASS")\n\n\nif __name__ == "__main__":\n    main()\n''', encoding="utf-8")
    print("Applied MLB book-agreement structural confirmation gate.")


if __name__ == "__main__":
    main()
