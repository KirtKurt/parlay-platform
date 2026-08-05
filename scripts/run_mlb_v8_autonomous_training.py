#!/usr/bin/env python3
"""Run the supervised V8 trainer and persist explicit learning evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import mlb_v8_autonomy_v1 as autonomy
import run_mlb_supervised_shadow_v2 as runner

VERSION = "MLB-V8-AUTONOMOUS-TRAINING-ENTRYPOINT-v1"


def run(
    *,
    region: str,
    stack_name: str,
    table_name: str,
    output: Path,
) -> Dict[str, Any]:
    value = runner.run(
        region=region,
        stack_name=stack_name,
        table_name=table_name,
        output=output,
    )
    decorated = autonomy.decorate_result(value)
    decorated["autonomousTrainingEntrypointVersion"] = VERSION
    decorated["historicalBbsRequired"] = False
    decorated["providerNeutralTrainingAllowed"] = True
    decorated["automaticWagerAllowed"] = False
    decorated["resultDigest"] = autonomy._sha(
        {
            key: item
            for key, item in decorated.items()
            if key != "resultDigest"
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(decorated, indent=2, sort_keys=True) + "\n")
    return decorated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--stack-name", default="parlay-platform-mlb-historical-optimizer"
    )
    parser.add_argument("--table-name", default="parlay_platform_snapshots")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = run(
        region=args.region,
        stack_name=args.stack_name,
        table_name=args.table_name,
        output=Path(args.output),
    )
    print(
        json.dumps(
            {
                "ok": value.get("ok"),
                "learningStatus": value.get("learningStatus"),
                "learningExecution": value.get("learningExecution"),
                "autonomyDecision": value.get("autonomyDecision"),
                "promotionGate": value.get("promotionGate"),
                "recordCountLoaded": value.get("recordCountLoaded"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    if value.get("ok") is not True:
        return 1
    if (value.get("learningExecution") or {}).get("learningExecuted") is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
