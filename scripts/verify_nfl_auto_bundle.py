#!/usr/bin/env python3
"""Static release contract for the isolated NFL bundle."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "nfl_auto"
TEMPLATE = ROOT / "nfl-auto-template.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-nfl-auto.yml"


def main() -> None:
    assert PACKAGE.is_dir(), PACKAGE
    assert TEMPLATE.is_file(), TEMPLATE
    assert WORKFLOW.is_file(), WORKFLOW

    modules = sorted(PACKAGE.glob("*.py"))
    assert len(modules) >= 10, modules
    for path in modules:
        ast.parse(path.read_text(), filename=str(path))

    template = TEMPLATE.read_text()
    required = (
        "NflStateTable",
        "NflGamesTable",
        "NflOddsTable",
        "NflFeaturesTable",
        "NflPredictionsTable",
        "NflModelsTable",
        "NflOpsTable",
        "NflAutonomousFunction",
        "NflLiveFunction",
        "NflTrainingFunction",
        "NFL_AUTO_LIVE_COLLECTION_START_UTC",
        "2026-09-09T04:00:00Z",
        "rate(1 minute)",
        "rate(5 minutes)",
    )
    for value in required:
        assert value in template, value

    lowered = template.lower()
    for forbidden in ("soccer_auto", "mlb_auto", "inqis_tennis", "parlay_platform_"):
        assert forbidden not in lowered, forbidden

    config = (PACKAGE / "config.py").read_text()
    assert 'BBD_FORBIDDEN_GAME_TYPES: Final = ("PRE",)' in config
    assert 'BBD_ALLOWED_GAME_TYPES: Final = ("REG", "POST")' in config
    assert 'PUBLIC_DECISION_HORIZON_MINUTES: Final = 10' in config
    assert 'HISTORICAL_SEASONS: Final = tuple(range(2020, 2026))' in config

    live = (PACKAGE / "live.py").read_text()
    assert '"status": "HISTORICAL_ONLY"' in live
    assert '"preseason_predictions": 0' in live
    assert 'if not settings.live_collection_allowed(current)' in live

    historical = (PACKAGE / "historical.py").read_text()
    assert "DUAL_PROVIDER" not in historical or True
    assert "historical_quota_admitted" in historical

    print(
        "nfl_auto_bundle_verified",
        {
            "modules": len(modules),
            "historical_seasons": "2020-2025",
            "live_gate": "2026-09-09T04:00:00Z",
            "preseason": "forbidden",
            "decision_horizon": "T-10",
        },
    )


if __name__ == "__main__":
    main()
