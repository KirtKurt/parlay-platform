"""Admit a new archive, or reuse the already-installed S3 pointer.

Does not invent scores. Reuse is only allowed when Dynamo points at a
content-addressed bundle that load_archive can re-derive (>=5000 scores).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soccer_auto.kss1_score_archive import load_archive
from soccer_auto.storage import SoccerStore


def main() -> int:
    store = SoccerStore()
    rows, audit = load_archive(store)
    accepted = int(audit.get("accepted_scores") or 0)
    configured = bool(audit.get("configured"))
    result = {
        "configured": configured,
        "accepted_scores": accepted,
        "artifact_digest": audit.get("artifact_digest"),
        "artifact_uri": audit.get("artifact_uri"),
        "reused": False,
        "installed": False,
    }
    if configured and accepted >= 5000 and audit.get("artifact_digest"):
        result["reused"] = True
        result["installed"] = True
        print(json.dumps(result, sort_keys=True))
        return 0
    print(json.dumps({**result, "reason": "NO_REUSABLE_INSTALLED_ARCHIVE"}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
