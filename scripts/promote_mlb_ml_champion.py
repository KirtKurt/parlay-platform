#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "runtime_reports"
PATHS = {
    "outcome": (REPORTS / "mlb_ml_outcome_challenger_latest.json", REPORTS / "mlb_ml_outcome_champion.json"),
    "reliability": (REPORTS / "mlb_ml_reliability_challenger_latest.json", REPORTS / "mlb_ml_model_latest.json"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote a reviewed MLB ML challenger to production champion.")
    parser.add_argument("--role", choices=sorted(PATHS), required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != "PROMOTE_REVIEWED_CHALLENGER":
        raise SystemExit("Promotion confirmation phrase was not supplied exactly.")
    challenger_path, champion_path = PATHS[args.role]
    challenger = json.loads(challenger_path.read_text(encoding="utf-8"))
    if challenger.get("promotionRecommended") is not True:
        raise SystemExit(f"{args.role} challenger has not passed promotion gates")
    if challenger.get("cleanCohort") is not True or challenger.get("validationProtocol") != "chronological_train_validation_test_v1":
        raise SystemExit("challenger is not a clean chronological model")
    champion = dict(challenger)
    champion.update({
        "productionApproved": True,
        "manualChampionPromotionRequired": False,
        "approvedAtUtc": datetime.now(timezone.utc).isoformat(),
        "approvedByWorkflow": "manual_reviewed_challenger_promotion",
        "sourceChallengerPath": str(challenger_path.relative_to(ROOT)),
    })
    champion_path.write_text(json.dumps(champion, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "role": args.role, "championPath": str(champion_path.relative_to(ROOT)), "version": champion.get("version")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
