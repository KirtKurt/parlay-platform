"""Train/evaluate KSS1 goals from an explicit receipt-bearing history file."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soccer_auto.kss1_features import HistoryIndex, build_training_table
from soccer_auto.kss1_goals_model import train_and_validate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--allow-research", action="store_true")
    args = parser.parse_args()
    index = HistoryIndex(json.loads(Path(args.history).read_text()))
    if not args.allow_research and any(r["provenance_mode"] != "VERIFIED_RECEIPT" for r in index.rows):
        raise ValueError("research archives require --allow-research and cannot qualify production")
    target = Path(args.out)
    target.mkdir(parents=True, exist_ok=True)
    table = build_training_table(index)
    report = train_and_validate(table)
    (target / "training-table.json").write_text(json.dumps(table, sort_keys=True, allow_nan=False))
    (target / "validation.json").write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    if report.get("trained"):
        (target / "model.json").write_text(json.dumps(report["model"], sort_keys=True, indent=2, allow_nan=False))
    else:
        (target / "model.json").unlink(missing_ok=True)
    print(json.dumps({k: v for k, v in report.items() if k not in {"model", "candidates"}}, indent=2))
    if not report.get("trained"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
