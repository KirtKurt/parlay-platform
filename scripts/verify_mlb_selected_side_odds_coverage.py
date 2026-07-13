#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "runtime_reports" / "mlb_rolling_24h_audit_latest.json"
REPORT_PATH = ROOT / "runtime_reports" / "mlb_selected_side_odds_coverage_latest.json"
DEFAULT_CUTOFF = "2026-07-13T00:20:03+00:00"
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


def number(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
        return parsed if parsed != 0 else None
    except Exception:
        return None


def selected_price(row: Dict[str, Any]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    side = str(row.get("predictedSide") or "").lower()
    signal = row.get("homeSignal") if side == "home" else row.get("awaySignal") if side == "away" else {}
    signal = signal if isinstance(signal, dict) else {}
    price = number(row.get("lockedAmericanOdds"))
    if price is None:
        price = number(row.get("americanOdds"))
    if price is None:
        price = number(signal.get("americanOdds"))
    book = row.get("priceBook") or signal.get("priceBook")
    source = row.get("priceSource") or signal.get("priceSource")
    return price, str(book) if book else None, str(source) if source else None


def main() -> int:
    cutoff = parse_dt(CUTOFF)
    if cutoff is None:
        raise SystemExit(f"Invalid deployment cutoff: {CUTOFF}")
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    rows = list(audit.get("rows") or [])
    postdeploy: List[Dict[str, Any]] = []
    priced: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for row in rows:
        locked = lock_at(row)
        if not locked or locked < cutoff:
            continue
        postdeploy.append(row)
        price, book, source = selected_price(row)
        source_proven = bool(book or str(source or "").lower() in {"real_book", "locked_real_book"})
        if price is not None and source_proven:
            priced.append(row)
        if row.get("completed") is True and row.get("status") == "GRADED" and (price is None or not source_proven):
            failures.append({
                "gameId": game_id(row),
                "slateDateEt": row.get("slateDateEt"),
                "matchup": row.get("matchup"),
                "lockedAmericanOdds": row.get("lockedAmericanOdds"),
                "americanOdds": row.get("americanOdds"),
                "priceBook": book,
                "priceSource": source,
                "reason": "completed_graded_row_missing_real_selected_side_lock_price",
            })

    coverage = round((len(priced) / len(postdeploy) * 100.0), 2) if postdeploy else None
    report = {
        "ok": not failures,
        "proofType": "MLB_SELECTED_SIDE_ODDS_COVERAGE",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "greenDeploymentCutoffUtc": cutoff.isoformat(),
        "postDeployLockedRowCount": len(postdeploy),
        "postDeployPricedRowCount": len(priced),
        "postDeployPriceCoveragePct": coverage,
        "completedGradedMissingPriceCount": len(failures),
        "failures": failures,
        "status": "VERIFIED" if postdeploy and not failures else "WAITING_FOR_FIRST_POSTDEPLOY_LOCK" if not postdeploy else "FAILED",
        "optimizationCountPolicy": "A completed row cannot enter the clean optimization cohort without selected-side American odds and a real book/source attribution.",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
