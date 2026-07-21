from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import mlb_fundamentals_snapshot_v1 as fundamentals
import mlb_temporal_features_v1 as temporal


def _parse(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def source_honest_missing_context() -> Dict[str, Any]:
    groups = [
        "confirmed_probable_pitchers", "fip_xfip", "wrc_plus",
        "starter_handedness_splits", "bullpen_fatigue", "confirmed_lineups",
        "weather_wind_roof", "ballpark_factors", "travel_rest",
        "injuries_late_scratches_news", "public_betting_handle", "closing_line_value",
    ]
    return {group: {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"} for group in groups}


def attach_canonical_pull_proof(row: Dict[str, Any], unique_slot_count: int = 13) -> Dict[str, Any]:
    source_value = (
        row.get("predictionSourcePullAt")
        or (row.get("slatePredictionLock") or {}).get("latestScoringPullAt")
        or (row.get("lockedCardAudit") or {}).get("explicitSourceAtUtc")
    )
    source = _parse(source_value)
    slot = source.replace(minute=(source.minute // 15) * 15, second=0, microsecond=0)
    slot_starts = [
        (slot - timedelta(minutes=15 * index)).isoformat()
        for index in reversed(range(unique_slot_count))
    ]
    proof_seed = "|".join([source.isoformat(), *slot_starts])
    history_fingerprint = hashlib.sha256(proof_seed.encode("utf-8")).hexdigest()
    pull_fingerprint = hashlib.sha256((proof_seed + "|terminal").encode("utf-8")).hexdigest()
    row["pullHistoryIntegrity"] = {
        "version": "INQSI-PULL-HISTORY-INTEGRITY-v1-canonical-quarter-hour",
        "canonicalizationVersion": "INQSI-CANONICAL-PULL-SLOT-v1-earliest-integrity-valid",
        "slotMinutes": 15,
        "rawPullCount": unique_slot_count,
        "uniqueSlotCount": unique_slot_count,
        "duplicatePullCount": 0,
        "invalidPullCount": 0,
        "contaminatedSlotCount": 0,
        "duplicateContaminated": False,
        "slotStartsUtc": slot_starts,
        "canonicalSlotFingerprint": history_fingerprint,
    }
    row["predictionSourceCanonicalSlot"] = {
        "version": "INQSI-CANONICAL-PULL-SLOT-v1-earliest-integrity-valid",
        "slotMinutes": 15,
        "slotStartUtc": slot.isoformat(),
        "canonical": True,
        "selectionPolicy": "earliest_integrity_valid_pull_in_utc_quarter_hour",
        "canonicalPullId": f"test-pull-{slot.isoformat()}",
        "canonicalPulledAtUtc": source.isoformat(),
        "canonicalPullFingerprint": pull_fingerprint,
        "rawPullCount": 1,
        "validPullCount": 1,
        "invalidPullCount": 0,
        "duplicatePullCount": 0,
        "contaminated": False,
    }
    return row


def attach_lock_safe_features(row: Dict[str, Any]) -> Dict[str, Any]:
    source_value = (
        row.get("predictionSourcePullAt")
        or (row.get("slatePredictionLock") or {}).get("latestScoringPullAt")
        or (row.get("lockedCardAudit") or {}).get("explicitSourceAtUtc")
    )
    source = _parse(source_value)
    home_signal = row.setdefault("homeSignal", {})
    away_signal = row.setdefault("awaySignal", {})
    home_latest = float(
        home_signal.get("marketConsensusProbability")
        or home_signal.get("probLatest")
        or 0.55
    )
    points = []
    for index in range(13):
        at = source - timedelta(minutes=15 * (12 - index))
        home = home_latest - (12 - index) * 0.001
        points.append({"pulled_at": at.isoformat(), "fair": {"home": home, "away": 1.0 - home}})
    home_signal["temporalFeatures"] = temporal.summarize_side(points, "home", cutoff_at=source)
    away_signal["temporalFeatures"] = temporal.summarize_side(points, "away", cutoff_at=source)
    row.setdefault("advanced_context", source_honest_missing_context())
    row["fundamentalsSnapshot"] = fundamentals.build(row)
    attach_canonical_pull_proof(row)
    return row
