"""Shadow-only persistence for 2stackMLB.

Optional daily hook (does nothing unless KS1_STACK2MLB_SHADOW=1):

    from stack2mlb.shadow import maybe_from_env
    maybe_from_env(frame, output)

Never writes mlb/ks1/predictions-v1/. Never sets promoted=True.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from stack2mlb import VERSION
from stack2mlb.calibrate import brier, ece, reliability, wilson_lower
from stack2mlb.elo_history import book_from_grades
from stack2mlb.live import attach

SHADOW_PREFIX = "mlb/ks1/stack2mlb-shadow-v1/"
OFFICIAL_PREFIX = "mlb/ks1/predictions-v1/"


def attach_slate(rows: list[dict], grades: list[dict] | None = None, markov_sims: int = 4000) -> list[dict]:
    book = book_from_grades(grades or [])
    out = []
    for row in rows:
        attached = attach(row, book, markov_sims=markov_sims)
        attached["elo_games_used"] = getattr(book, "_graded", 0)
        attached["version"] = VERSION
        out.append(attached)
    return out


def write_sidecar(predictions: list[dict], output: Path, grades: list[dict] | None = None) -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    shadow = attach_slate(predictions, grades)
    path = output / "stack2mlb_shadow.json"
    body = json.dumps({"system": "2stackMLB", "promoted": False, "prefix": SHADOW_PREFIX,
                       "official_prefix": OFFICIAL_PREFIX, "rows": shadow}, indent=2, default=str)
    path.write_text(body)
    report = {
        "system": "2stackMLB",
        "promoted": False,
        "rows": len(shadow),
        "bet": sum(1 for r in shadow if r.get("pick_status") == "bet"),
        "shrink": sum(1 for r in shadow if r.get("pick_status") == "shrink"),
        "pass": sum(1 for r in shadow if r.get("pick_status") == "pass"),
        "official_p_home_rewritten": False,
        "path": str(path),
    }
    (output / "stack2mlb_report.json").write_text(json.dumps(report, indent=2))
    return report


def chart(grades: list[dict], shadows: list[dict]) -> dict:
    by_id = {str(s["game_id"]): s for s in shadows if s.get("game_id")}
    y, p_official, p_stack, statuses = [], [], [], []
    for grade in grades:
        pk = str(grade.get("game_id"))
        shadow = by_id.get(pk)
        if shadow is None or grade.get("home_win") is None:
            continue
        y.append(int(grade["home_win"]))
        p_official.append(float(shadow["p_home_official"]))
        p_stack.append(float(shadow["p_stack"]))
        statuses.append(shadow.get("pick_status"))
    if not y:
        return {"system": "2stackMLB", "promoted": False, "games": 0, "note": "no overlapping grades"}
    official_rel = reliability(y, p_official)
    stack_rel = reliability(y, p_stack)
    selected = [i for i, s in enumerate(statuses) if s == "bet"]
    selected_hits = sum(y[i] for i in selected)
    return {
        "system": "2stackMLB",
        "promoted": False,
        "games": len(y),
        "official_brier": brier(y, p_official),
        "stack_brier": brier(y, p_stack),
        "official_ece": ece(official_rel),
        "stack_ece": ece(stack_rel),
        "official_reliability": official_rel,
        "stack_reliability": stack_rel,
        "bet_n": len(selected),
        "bet_hits": selected_hits,
        "bet_wilson_lower": wilson_lower(selected_hits, len(selected)) if selected else 0.0,
        "promotion_allowed": False,
        "promotion_reason": "chart exists; promotion still forbidden until a frozen holdout clears the bar",
    }


def from_daily_frame(frame, output: Path, grades: list[dict] | None = None) -> dict:
    return write_sidecar(frame.to_dict("records"), Path(output), grades)


def maybe_from_env(frame, output: Path, grades: list[dict] | None = None):
    if os.environ.get("KS1_STACK2MLB_SHADOW") != "1":
        return None
    return from_daily_frame(frame, output, grades)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Build a 2stackMLB shadow sidecar from KS1 predictions.")
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if str(args.predictions).endswith(".parquet"):
        import pandas as pd
        rows = pd.read_parquet(args.predictions).to_dict("records")
    else:
        payload = json.loads(args.predictions.read_text())
        rows = payload if isinstance(payload, list) else payload.get("rows") or payload.get("predictions")
    grades = []
    if args.ledger:
        ledger = json.loads(args.ledger.read_text())
        grades = ledger.get("rows", ledger if isinstance(ledger, list) else [])
    print(json.dumps(write_sidecar(rows, args.output, grades), indent=2))


if __name__ == "__main__":
    main()
