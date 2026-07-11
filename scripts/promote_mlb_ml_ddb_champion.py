#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_champion_challenger_v1 as champion
import mlb_ml_manual_promotion_only_v1 as manual_only

manual_only.apply(champion)


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote a reviewed MLB ML v3 DDB challenger bundle.")
    parser.add_argument("--authority", choices=["direction", "playability", "both"], required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()

    if args.confirm != "PROMOTE_REVIEWED_DDB_CHALLENGER":
        raise SystemExit("Promotion confirmation phrase was not supplied exactly.")
    if not os.environ.get("SNAPSHOTS_TABLE"):
        raise SystemExit("SNAPSHOTS_TABLE is required for DDB champion promotion.")

    result = champion.promote_reviewed_latest(args.authority)
    print(json.dumps(result, indent=2, default=str))
    if not result.get("ok") or result.get("promoted") is not True:
        raise SystemExit("DDB champion was not promoted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
