#!/usr/bin/env python3
"""Read-only replay of exact R8 artifacts through the separate successor schema."""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hello_world"))
import mlb_successor_model_v1 as model
from mlb_challenger_benchmark import load_sources


def main():
    sources, provenance = load_sources("parlay-platform-dev")
    accepted, rejected = [], Counter()
    raw = [r for rows in sources["dataset"]["partitions"].values() for r in rows]
    before = model.fingerprint(raw)
    for row in raw:
        try: accepted.append(model.record(row, labeled=True))
        except (ValueError, TypeError, KeyError) as exc: rejected[str(exc)] += 1
    report = {"observedAtUtc": datetime.now(timezone.utc).isoformat(),
              "sourceProvenance": provenance, "sourceRowCount": len(raw),
              "successorAcceptedRowCount": len(accepted), "rejectionCounts": dict(rejected),
              "development": model.development(accepted),
              "r8HistoryUnchanged": before == model.fingerprint(raw), "readOnly": True,
              "prospectiveQualificationEvidence": False, "productionAuthorityChanged": False}
    target = Path("runtime_reports/mlb_successor_development_replay_latest.json")
    target.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    print(json.dumps(report, indent=2))
    if not accepted or not report["r8HistoryUnchanged"]:
        raise RuntimeError("successor source replay failed")


if __name__ == "__main__": main()
