#!/usr/bin/env python3
"""Run isolated point-in-time target-game fundamentals enrichment for MLB V8."""
from __future__ import annotations

import os

import mlb_official_point_in_time_fundamentals_v1 as official
import run_mlb_v8_historical_bbs_backfill_entrypoint as operational

VERSION = "MLB-V8-HISTORICAL-POINT-IN-TIME-BACKFILL-v2"
TARGET_POINTER_PK = "MLB_V8_HISTORICAL_BBS_TARGET#V2"
TARGET_POINTER_SK = "ACTIVE"


def install_target_manifest_isolation(module):
    """Keep target-game fundamentals independent from prior-game form manifests."""
    module.overlay.POINTER_PK = TARGET_POINTER_PK
    module.overlay.POINTER_SK = TARGET_POINTER_SK
    return module


def main() -> int:
    backfill = operational.backfill
    install_target_manifest_isolation(backfill)
    operational.install_bucket_fallback(
        backfill,
        historical_stack=os.environ.get("HISTORICAL_STACK", backfill.DEFAULT_HISTORICAL_STACK),
        fundamentals_stack=os.environ.get("FUNDAMENTALS_STACK", backfill.DEFAULT_FUNDAMENTALS_STACK),
    )
    operational.install_stored_match_surface(backfill.BigBallsDataClient)
    operational.install_historical_resource_surfaces(backfill.BigBallsDataClient)
    operational.install_newest_coverage_window(backfill)
    operational.install_safe_diagnostics(backfill)
    official.install(backfill)
    return backfill.main()


if __name__ == "__main__":
    raise SystemExit(main())
