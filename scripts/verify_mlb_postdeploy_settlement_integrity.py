#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "runtime_reports" / "mlb_rolling_24h_audit_latest.json"
REPORT_PATH = ROOT / "runtime_reports" / "mlb_postdeploy_settlement_integrity_latest.json"
DEFAULT_CUTOFF = "2026-07-12T00:20:17+00:00"
CUTOFF = os.environ.get("INQSI_MLB_GREEN_DEPLOYMENT_AT_UTC", DEFAULT_CUTOFF)


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def lock_at(row: Dict[str, Any]) -> Optional[datetime]:
    audit = row.get("lockedCardAudit") or {}
    for value in (
        audit.get("lockAtUtc"),
        (row.get("slatePredictionLock") or {}).get("lockAtUtc"),
        (row.get("lastPossiblePredictionGate") or {}).get("lockAtUtc"),
        row.get("lockedAtUtc"),
    ):
        parsed = parse_dt(value)
        if parsed:
            return parsed
    return None


def game_id(row: Dict[str, Any]) -> str:
    return str(
        row.get("id")
        or row.get("gameId")
        or row.get("game_id")
        or row.get("providerGameId")
        or row.get("provider_game_id")
        or (row.get("lockedCardAudit") or {}).get("providerGameId")
        or ""
    )


def main() -> int:
    cutoff = parse_dt(CUTOFF)
    if cutoff is None:
        raise SystemExit(f"Invalid deployment cutoff: {CUTOFF}")
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    rows = list(audit.get("rows") or [])
    eligible = []
    completed = []
    failures: List[Dict[str, Any]] = []

    for row in rows:
        locked = lock_at(row)
        if not locked or locked < cutoff:
            continue
        eligible.append(row)
        if row.get("completed") is not True:
            continue
        completed.append(row)
        reasons: List[str] = []
        if row.get("status") != "GRADED":
            reasons.append("completed_game_not_graded")
        if not row.get("winner"):
            reasons.append("missing_final_winner_label")
        if not isinstance(row.get("correct"), bool):
            reasons.append("missing_boolean_pick_correct_label")
        vector = row.get("frozenFeatureVector") or {}
        labels = vector.get("labels") or {}
        if labels.get("homeWon") is not None or labels.get("pickCorrect") is not None:
            reasons.append("immutable_pregame_vector_labels_mutated_after_final")
        if reasons:
            failures.append({
                "gameId": game_id(row),
                "slateDateEt": row.get("slateDateEt"),
                "matchup": row.get("matchup"),
                "lockAtUtc": locked.isoformat(),
                "status": row.get("status"),
                "reasons": reasons,
            })

    report = {
        "ok": not failures,
        "proofType": "MLB_POSTDEPLOY_SETTLEMENT_INTEGRITY",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "greenDeploymentCutoffUtc": cutoff.isoformat(),
        "postDeployLockedRowsInWindow": len(eligible),
        "postDeployCompletedRowsInWindow": len(completed),
        "postDeployGradedRowsInWindow": len([row for row in completed if row.get("status") == "GRADED"]),
        "failureCount": len(failures),
        "failures": failures,
        "status": "VERIFIED" if completed and not failures else "WAITING_FOR_FIRST_POSTDEPLOY_FINAL" if not completed else "FAILED",
        "policy": "Final winner and correctness labels are joined only after completion; the immutable pregame feature vector must retain blank outcome labels.",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
