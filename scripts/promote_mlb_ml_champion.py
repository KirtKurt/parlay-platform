#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_champion_challenger_v1 as champion


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote the latest reviewed MLB ML v3 challenger stored in DynamoDB.")
    parser.add_argument("--authority", choices=["direction", "playability", "both"], required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != "PROMOTE_REVIEWED_CHALLENGER":
        raise SystemExit("Promotion confirmation phrase was not supplied exactly.")
    result = champion.promote_reviewed_latest(args.authority)
    print(json.dumps(result, indent=2, default=str))
    if result.get("ok") is not True or result.get("promoted") is not True:
        raise SystemExit("Latest challenger was not promoted; requested authority has not passed all gates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
