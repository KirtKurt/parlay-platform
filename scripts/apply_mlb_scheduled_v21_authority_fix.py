#!/usr/bin/env python3
"""Apply a narrowly scoped MLB scheduled-writer authority repair.

The production scorer is allowed to use the legacy-compatible persistence gate only
inside the protected scheduled-pull module and only when the imported engine proves
it is the deployed v2.1 MLB engine.  The previous environment value is restored in a
finally block.  Recovery, historical, public-read, and shadow engines remain unable
to obtain write authority through this shim.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "hello_world" / "mlb_manual_pull_protected.py"
TEST = ROOT / "tests" / "unit" / "test_mlb_scheduled_v21_authority_scope.py"
MARKER = "MLB-SCHEDULED-V21-AUTHORITY-COMPAT-v1"

HELPER = r'''

# MLB-SCHEDULED-V21-AUTHORITY-COMPAT-v1
# Compatibility is deliberately scoped to this protected scheduled writer.  It does
# not change the deployed model, winner direction, public API, lock rules, recovery
# challengers, or any historical engine.
def _predict_all_with_scheduled_v21_authority(engine):
    engine_name = str(getattr(engine, "ENGINE", "") or "")
    model_version = str(getattr(engine, "MODEL_VERSION", "") or "")
    if engine_name != "MLB-SINGLE-GAME-ML-PROMOTION-v2.1":
        raise RuntimeError("MLB_SCHEDULED_WRITER_ENGINE_NOT_V21")
    if model_version != "INQSI-MLB-SINGLE-GAME-ML-v2.1-aws-sam-production":
        raise RuntimeError("MLB_SCHEDULED_WRITER_MODEL_VERSION_NOT_PRODUCTION")

    previous = os.environ.get("INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED")
    os.environ["INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED"] = "true"
    try:
        return engine.predict_all(store=True)
    finally:
        if previous is None:
            os.environ.pop("INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED", None)
        else:
            os.environ["INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED"] = previous
'''

TEST_TEXT = r'''from __future__ import annotations

import os

import pytest

import mlb_manual_pull_protected as protected


class ProductionEngine:
    ENGINE = "MLB-SINGLE-GAME-ML-PROMOTION-v2.1"
    MODEL_VERSION = "INQSI-MLB-SINGLE-GAME-ML-v2.1-aws-sam-production"

    def __init__(self):
        self.seen = None

    def predict_all(self, *, store):
        self.seen = {
            "store": store,
            "authority": os.environ.get("INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED"),
        }
        return {"ok": True, "count": 1}


class ShadowEngine(ProductionEngine):
    ENGINE = "MLB-RECOVERY-SHADOW-v1"


def test_scoped_authority_is_present_only_during_production_predict_all(monkeypatch):
    monkeypatch.delenv("INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED", raising=False)
    engine = ProductionEngine()
    result = protected._predict_all_with_scheduled_v21_authority(engine)
    assert result["ok"] is True
    assert engine.seen == {"store": True, "authority": "true"}
    assert "INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED" not in os.environ


def test_previous_authority_value_is_restored(monkeypatch):
    monkeypatch.setenv("INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED", "false")
    engine = ProductionEngine()
    protected._predict_all_with_scheduled_v21_authority(engine)
    assert engine.seen["authority"] == "true"
    assert os.environ["INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED"] == "false"


def test_shadow_engine_cannot_use_scheduled_authority(monkeypatch):
    monkeypatch.delenv("INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="ENGINE_NOT_V21"):
        protected._predict_all_with_scheduled_v21_authority(ShadowEngine())
    assert "INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED" not in os.environ
'''


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    changed = False

    if "import os" not in text:
        future = "from __future__ import annotations\n"
        if future not in text:
            raise SystemExit("mlb_manual_pull_protected missing future-import anchor")
        text = text.replace(future, future + "\nimport os\n", 1)
        changed = True

    if MARKER not in text:
        anchors = [
            "def _run_game_winner_predictions",
            "def run_game_winner_predictions",
            "def lambda_handler",
        ]
        position = next((text.find(anchor) for anchor in anchors if text.find(anchor) >= 0), -1)
        if position < 0:
            raise SystemExit("scheduled-writer insertion anchor missing")
        text = text[:position] + HELPER + "\n\n" + text[position:]
        changed = True

    call_pattern = re.compile(
        r"(?P<engine>[A-Za-z_][A-Za-z0-9_\.]*)\.predict_all\(\s*store\s*=\s*True\s*\)"
    )
    if "_predict_all_with_scheduled_v21_authority(" not in text.split(MARKER, 1)[1]:
        text, replacements = call_pattern.subn(
            r"_predict_all_with_scheduled_v21_authority(\g<engine>)",
            text,
        )
        if replacements != 1:
            raise SystemExit(f"expected exactly one protected predict_all(store=True) call, found {replacements}")
        changed = True

    if changed:
        TARGET.write_text(text, encoding="utf-8")
    TEST.write_text(TEST_TEXT, encoding="utf-8")

    final = TARGET.read_text(encoding="utf-8")
    if final.count(MARKER) != 1:
        raise SystemExit("scheduled v2.1 authority marker count invalid")
    if final.count("_predict_all_with_scheduled_v21_authority(") < 2:
        raise SystemExit("protected writer was not routed through scoped authority helper")
    print({"changed": changed, "target": str(TARGET), "test": str(TEST)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
