#!/usr/bin/env python3
"""Inspect successor readiness; explicitly activate an exact qualified artifact.

Default is read-only. Activation is an operator review action, not a schedule.
No command can qualify a failed model or enable wagers/playability.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hello_world"))
import mlb_successor_runtime_v1 as runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--artifact-digest")
    parser.add_argument("--qualification-digest")
    parser.add_argument("--reviewer")
    args = parser.parse_args()
    repo = runtime.repository()
    if args.activate:
        if not all((args.artifact_digest, args.qualification_digest, args.reviewer)):
            parser.error("activation requires the exact model digest, qualification digest and reviewer")
        result = runtime.review_and_activate(repo, artifact_digest=args.artifact_digest,
            qualification_digest=args.qualification_digest, reviewer=args.reviewer,
            now=datetime.now(timezone.utc))
    else:
        frozen, evidence = runtime.frozen_candidate(repo), repo.get("QUALIFICATION")
        result = {"readOnly": True, "development": repo.get("STATUS#TRAINING"),
                  "capture": repo.get("STATUS#CAPTURE"), "frozen": frozen,
                  "qualification": runtime.qualification_summary(evidence, frozen) if evidence and frozen else None,
                  "active": repo.get("ACTIVE")}
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__": main()
