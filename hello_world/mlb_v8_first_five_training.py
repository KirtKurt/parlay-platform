"""Leakage-safe eligibility contract for MLB V8 historical first-five rows."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

REQUIRED_MARKETS = (
    "h2h_1st_5_innings",
    "spreads_1st_5_innings",
    "totals_1st_5_innings",
)


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def evaluate_training_eligibility(row: Mapping[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    if row.get("gameStatus") != "FINAL":
        blockers.append("game_not_final")
    if row.get("winnerSide") not in {"home", "away"}:
        blockers.append("winner_label_missing")
    if row.get("postLockDataExcluded") is not True:
        blockers.append("post_lock_exclusion_unproven")
    if row.get("canonicalSlotFingerprint") in {None, ""}:
        blockers.append("canonical_slot_fingerprint_missing")
    if row.get("duplicateContaminated") is True:
        blockers.append("duplicate_contaminated")
    markets = row.get("markets") or {}
    for market in REQUIRED_MARKETS:
        if not markets.get(market):
            blockers.append(f"missing_market:{market}")
    try:
        captured = _dt(row.get("capturedAtUtc"))
        locked = _dt(row.get("lockAtUtc"))
        commenced = _dt(row.get("commenceTimeUtc"))
        if captured > locked:
            blockers.append("captured_after_lock")
        if locked >= commenced:
            blockers.append("invalid_lock_time")
    except Exception:
        blockers.append("timestamp_contract_invalid")
    return {
        "trainingEligible": not blockers,
        "blockers": sorted(set(blockers)),
        "featureGroup": "v8_first_five",
        "postLockDataExcluded": row.get("postLockDataExcluded") is True,
    }


def require_training_eligible(row: Mapping[str, Any]) -> None:
    result = evaluate_training_eligibility(row)
    if not result["trainingEligible"]:
        raise ValueError("V8 first-five row is not training eligible:" + ",".join(result["blockers"]))
