#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

from mlb_reversal_shape_v1 import VERSION as SHAPE_VERSION
from mlb_reversal_shape_v1 import analyze


VERSION = "MLB-REVERSAL-SIMILARITY-AUDIT-v1-untouched-wilson70"
MIN_SELECTED = 100
ACCURACY_FLOOR_PCT = 70.0


def _rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("rows", "predictions", "games", "gradedRows", "cleanRows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    cohort = payload.get("cohort") or payload.get("data") or {}
    return _rows(cohort) if cohort is not payload else []


def _selected_signal(row: Dict[str, Any]) -> Dict[str, Any]:
    side = str(row.get("predictedSide") or "").lower()
    signal = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return signal if isinstance(signal, dict) else {}


def _correct(row: Dict[str, Any]) -> Optional[bool]:
    for key in ("pickCorrect", "correct", "isCorrect", "winnerCorrect"):
        value = row.get(key)
        if isinstance(value, bool):
            return value
        if value in (0, 1):
            return bool(value)
    predicted = str(row.get("predictedWinner") or "").strip().lower()
    actual = str(row.get("actualWinner") or row.get("winner") or "").strip().lower()
    return predicted == actual if predicted and actual else None


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None or value == "" else float(value)
    except Exception:
        return default


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> Dict[str, Optional[float]]:
    if total <= 0:
        return {"lowPct": None, "highPct": None}
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total) / denominator
    return {
        "lowPct": round(max(0.0, center - margin) * 100.0, 2),
        "highPct": round(min(1.0, center + margin) * 100.0, 2),
    }


def _metric_summary(values: Iterable[float]) -> Dict[str, Optional[float]]:
    clean = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            clean.append(number)
    clean.sort()
    if not clean:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    middle = len(clean) // 2
    median = clean[middle] if len(clean) % 2 else (clean[middle - 1] + clean[middle]) / 2.0
    return {
        "count": len(clean),
        "mean": round(sum(clean) / len(clean), 4),
        "median": round(median, 4),
        "min": round(clean[0], 4),
        "max": round(clean[-1], 4),
    }


def build_report(rows: Iterable[Dict[str, Any]], untouched_test: bool = False) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    pattern_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    all_items: List[Dict[str, Any]] = []

    for row in rows:
        signal = _selected_signal(row)
        shape = analyze(signal, row.get("tags") or [])
        correct = _correct(row)
        item = {"row": row, "shape": shape, "correct": correct}
        all_items.append(item)
        groups[str(shape.get("similaritySignature") or "UNKNOWN")].append(item)
        for tag in shape.get("patternTags") or ["NO_REVERSAL_PATTERN_TAG"]:
            pattern_groups[str(tag)].append(item)

    def summarize(name: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        graded = [item for item in items if item["correct"] is not None]
        successes = sum(item["correct"] is True for item in graded)
        total = len(graded)
        interval = wilson(successes, total)
        qualifies = bool(
            untouched_test
            and total >= MIN_SELECTED
            and interval["lowPct"] is not None
            and interval["lowPct"] >= ACCURACY_FLOOR_PCT
        )
        shapes = [item["shape"] for item in items]
        return {
            "name": name,
            "rowCount": len(items),
            "gradedCount": total,
            "correctCount": successes,
            "accuracyPct": round(successes * 100.0 / total, 2) if total else None,
            "wilson95Pct": interval,
            "accuracy70Qualified": qualifies,
            "qualificationReasons": [] if qualifies else [
                reason
                for reason, failed in (
                    ("not_declared_untouched_test", not untouched_test),
                    (f"selected_count_below_{MIN_SELECTED}", total < MIN_SELECTED),
                    ("wilson_lower_bound_below_70pct", interval["lowPct"] is None or interval["lowPct"] < ACCURACY_FLOOR_PCT),
                )
                if failed
            ],
            "movementSizePp": _metric_summary(shape.get("representativeMovePp", 0.0) for shape in shapes),
            "reversalCount": _metric_summary(shape.get("maximumReversalCount", 0.0) for shape in shapes),
            "lateVelocityRatio": _metric_summary(shape.get("lateVelocityRatio", 0.0) for shape in shapes),
            "fullPathEfficiency": _metric_summary(
                ((shape.get("horizons") or {}).get("full") or {}).get("pathEfficiency", 0.0)
                for shape in shapes
            ),
            "fullGrossMovePp": _metric_summary(
                ((shape.get("horizons") or {}).get("full") or {}).get("grossMovePp", 0.0)
                for shape in shapes
            ),
            "reversalDensity180m": _metric_summary(
                ((shape.get("horizons") or {}).get("180m") or {}).get("reversalDensityPerHour", 0.0)
                for shape in shapes
            ),
        }

    signature_rows = [summarize(name, items) for name, items in sorted(groups.items())]
    pattern_rows = [summarize(name, items) for name, items in sorted(pattern_groups.items())]
    graded = [item for item in all_items if item["correct"] is not None]
    total_successes = sum(item["correct"] is True for item in graded)

    return {
        "ok": True,
        "version": VERSION,
        "shapeVersion": SHAPE_VERSION,
        "untouchedTestDeclared": untouched_test,
        "rowCount": len(all_items),
        "gradedCount": len(graded),
        "correctCount": total_successes,
        "accuracyPct": round(total_successes * 100.0 / len(graded), 2) if graded else None,
        "minimumSelectedForAccuracyClaim": MIN_SELECTED,
        "minimumAccuracyClaimPct": ACCURACY_FLOOR_PCT,
        "claimRule": "untouched chronological test, at least 100 selected picks, and Wilson 95% lower bound at or above 70%",
        "similaritySignatures": signature_rows,
        "patternSimilarities": pattern_rows,
        "warning": "Do not tune thresholds on a dataset declared as untouched test; use a later prospective period after any change.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit MLB reversal similarities by time, size, path, and outcome.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--untouched-test", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    report = build_report(_rows(payload), untouched_test=args.untouched_test)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
