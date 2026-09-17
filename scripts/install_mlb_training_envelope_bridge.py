from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "hello_world" / "mlb_ml_aws_training_v1.py"


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    if "_MLB_AUTO_T45_BRIDGE_STATUS" in text:
        print("MLB T-45 training envelope bridge already installed.")
        return
    addition = r'''

# Normalize strict immutable T-45 evidence aliases before the trainer applies its
# canonical eligibility decision. This never creates or reconstructs a lock; it
# only recognizes already-proven lock, vector, odds and settlement evidence.
import sys as _mlb_auto_sys
import mlb_ml_training_envelope_bridge_v1 as _mlb_auto_t45_bridge
_MLB_AUTO_T45_BRIDGE_STATUS = _mlb_auto_t45_bridge.install(
    _mlb_auto_sys.modules[__name__]
)
_MLB_AUTO_T45_ORIGINAL_HANDLER = lambda_handler


def lambda_handler(event, context):
    result = _MLB_AUTO_T45_ORIGINAL_HANDLER(event, context)
    if isinstance(result, dict):
        result["mlbT45TrainingEnvelopeBridge"] = _mlb_auto_t45_bridge.status()
        chain = result.get("mlbAutoAutonomyChain")
        if isinstance(chain, dict):
            chain["trainingEnvelopeBridge"] = _mlb_auto_t45_bridge.status()
    return result
'''
    PATH.write_text(text + addition, encoding="utf-8")
    print("Installed strict MLB T-45 training envelope bridge.")


if __name__ == "__main__":
    main()
