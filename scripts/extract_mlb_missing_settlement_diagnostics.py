#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "runtime_reports" / "mlb_rolling_24h_audit_latest.json"
OUTPUT = ROOT / "runtime_reports" / "mlb_missing_settlement_diagnostics_latest.json"


def main() -> int:
    report = json.loads(AUDIT.read_text(encoding="utf-8"))
    rows = list(report.get("rows") or [])
    missing = []
    for row in rows:
        if row.get("status") not in {"MISSING_LOCKED_PREDICTION", "MISSING_PREDICTION"}:
            continue
        audit = row.get("lockedCardAudit") or {}
        missing.append({
            "status": row.get("status"),
            "id": row.get("id"),
            "gameId": row.get("gameId"),
            "provider_game_id": row.get("provider_game_id"),
            "slateDateEt": row.get("slateDateEt"),
            "matchup": row.get("matchup"),
            "awayTeam": row.get("awayTeam"),
            "homeTeam": row.get("homeTeam"),
            "commenceTime": row.get("commenceTime"),
            "winner": row.get("winner"),
            "homeScore": row.get("homeScore"),
            "awayScore": row.get("awayScore"),
            "missingReason": audit.get("missingReason"),
            "finalProviderIds": audit.get("finalProviderIds"),
            "finalCommenceTime": audit.get("finalCommenceTime"),
            "matchupCandidateCount": audit.get("matchupCandidateCount"),
            "candidateDiagnostics": audit.get("candidateDiagnostics") or [],
            "selectionPolicy": audit.get("selectionPolicy"),
            "auditVersion": audit.get("version"),
        })
    payload = {
        "ok": len(missing) == 0,
        "proofType": "MLB_MISSING_SETTLEMENT_DIAGNOSTICS",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "auditCreatedAt": report.get("createdAt"),
        "completedFinalGames": (report.get("summary") or {}).get("completedFinalGames"),
        "gradedPredictionCount": (report.get("summary") or {}).get("gradedPredictionCount"),
        "missingPredictionCount": len(missing),
        "missingRows": missing,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
