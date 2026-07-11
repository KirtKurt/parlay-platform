from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

VERSION = "MLB-LOCKED-CARD-AUDIT-v1.2-FINAL-GUARDED-STATE"


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _gate(row: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("slatePredictionLock", "lastPossiblePredictionGate"):
        value = row.get(key)
        if isinstance(value, dict) and (value.get("lockAtUtc") or value.get("latestScoringPullAt") or value.get("locked") is not None):
            return value
    return {}


def _lock_at(row: Dict[str, Any]) -> Optional[datetime]:
    return _parse_dt(_gate(row).get("lockAtUtc"))


def _explicit_source_at(row: Dict[str, Any]) -> Optional[datetime]:
    """Return only a real scoring/source-pull timestamp, never row creation time."""
    gate = _gate(row)
    for key in ("latestScoringPullAt", "latestAvailablePullAt", "predictionSourcePullAt", "sourcePullAt", "scoringPullAt"):
        dt = _parse_dt(gate.get(key) if key in gate else row.get(key))
        if dt:
            return dt
    return None


def _created_at(row: Dict[str, Any]) -> Optional[datetime]:
    return _parse_dt(row.get("createdAt") or row.get("created_at") or row.get("storedAt"))


def _locked_flag(row: Dict[str, Any]) -> bool:
    gate = _gate(row)
    tags = set(str(x) for x in (row.get("tags") or []))
    return bool(
        gate.get("locked") is True
        or gate.get("finalLocked") is True
        or gate.get("phase") == "SLATE_LOCKED"
        or gate.get("lockStatus") == "LOCKED"
        or row.get("lockedPrediction") is True
        or "SLATE_LOCKED" in tags
        or "FINAL_LOCKED" in tags
    )


def _pipeline_state(row: Dict[str, Any]) -> Dict[str, Any]:
    integrity = row.get("winnerOptimizerProtection") or {}
    directional = row.get("directionalScoreV1") or {}
    overlay = row.get("mlOverlay") or {}
    signal_policy = row.get("signalPolicyV13") or {}
    final_store = bool(row.get("finalGuardedStored") or row.get("finalGuardedStoreRequested"))
    playable = bool(row.get("actionablePick") is True or row.get("officialPick") is True or row.get("accuracyTargetEligible") is True)
    explicit_non_playable = bool(
        row.get("officialPick") is False
        and row.get("actionablePick") is False
        and row.get("accuracyTargetEligible") is False
        and (
            final_store
            or integrity.get("applied")
            or directional.get("applied")
            or overlay.get("applied")
            or row.get("recommendationStatus") == "LOW_CONFIDENCE_PREDICTION_NOT_PLAYABLE"
        )
    )
    depth = sum(
        int(bool(x))
        for x in (
            integrity.get("applied"),
            directional.get("applied"),
            overlay.get("applied"),
            signal_policy.get("applied"),
            final_store,
        )
    )
    return {
        "integrityApplied": bool(integrity.get("applied")),
        "directionalApplied": bool(directional.get("applied")),
        "mlOverlayApplied": bool(overlay.get("applied")),
        "signalPolicyApplied": bool(signal_policy.get("applied")),
        "finalGuardedStored": final_store,
        "playable": playable,
        "explicitNonPlayable": explicit_non_playable,
        "pipelineDepth": depth,
        "predictionVisibility": row.get("predictionVisibility"),
        "recommendationStatus": row.get("recommendationStatus"),
    }


def _candidate_rank(row: Dict[str, Any]) -> Optional[Tuple[int, str]]:
    lock_at = _lock_at(row)
    source_at = _explicit_source_at(row)
    locked = _locked_flag(row)

    if lock_at and source_at and source_at > lock_at:
        return None
    if not locked and not (lock_at and source_at and source_at <= lock_at):
        return None

    state = _pipeline_state(row)
    quality = 0
    if locked:
        quality += 1_000_000
    if row.get("finalGateStored") or row.get("fullDataFinalPick"):
        quality += 100_000
    if state["finalGuardedStored"]:
        quality += 500_000
    if state["integrityApplied"]:
        quality += 250_000
    if state["mlOverlayApplied"]:
        quality += 180_000
    if state["directionalApplied"]:
        quality += 140_000
    if state["signalPolicyApplied"]:
        quality += 80_000
    quality += state["pipelineDepth"] * 10_000

    if state["explicitNonPlayable"]:
        quality += 60_000
    if state["playable"]:
        quality += 40_000
    if row.get("winnerStackV2"):
        quality += 5_000

    if source_at and lock_at:
        seconds_before = max(0, int((lock_at - source_at).total_seconds()))
        quality += max(0, 100_000 - min(seconds_before, 100_000))
    created = _created_at(row)
    return quality, created.isoformat() if created else str(row.get("createdAt") or row.get("created_at") or "")


def _copy_audit_fields(pred: Dict[str, Any]) -> Dict[str, Any]:
    lock_at = _lock_at(pred)
    source_at = _explicit_source_at(pred)
    created = _created_at(pred)
    state = _pipeline_state(pred)
    return {
        "predictedWinner": pred.get("predictedWinner"),
        "predictedSide": pred.get("predictedSide"),
        "score": pred.get("score"),
        "winProbabilityPct": pred.get("winProbabilityPct"),
        "cappedWinProbabilityPct": pred.get("cappedWinProbabilityPct"),
        "confidenceTier": pred.get("confidenceTier"),
        "choiceQuality": pred.get("choiceQuality"),
        "tags": pred.get("tags") or [],
        "winnerOptimizer": pred.get("winnerOptimizer"),
        "winnerStackV2": pred.get("winnerStackV2"),
        "winnerOptimizerProtection": pred.get("winnerOptimizerProtection"),
        "directionalScoreV1": pred.get("directionalScoreV1"),
        "mlOverlay": pred.get("mlOverlay"),
        "signalPolicyV13": pred.get("signalPolicyV13"),
        "officialPick": pred.get("officialPick"),
        "officialPrediction": pred.get("officialPrediction"),
        "actionablePick": pred.get("actionablePick"),
        "accuracyTargetEligible": pred.get("accuracyTargetEligible"),
        "platformPick": pred.get("platformPick"),
        "customerVisibleWinnerPick": pred.get("customerVisibleWinnerPick"),
        "recommendationStatus": pred.get("recommendationStatus"),
        "predictionVisibility": pred.get("predictionVisibility"),
        "isOfficialDisplayPick": pred.get("isOfficialDisplayPick"),
        "doNotUseAsWinnerPick": pred.get("doNotUseAsWinnerPick"),
        "publicPick": pred.get("publicPick"),
        "displayWinner": pred.get("displayWinner"),
        "optimizerFlippedPick": pred.get("optimizerFlippedPick"),
        "optimizerFlipRequested": pred.get("optimizerFlipRequested"),
        "optimizerFlipAllowed": pred.get("optimizerFlipAllowed"),
        "optimizerFlipBlockedReasons": pred.get("optimizerFlipBlockedReasons") or [],
        "actionability": pred.get("actionability"),
        "actionabilityReason": pred.get("actionabilityReason"),
        "actionabilityRiskReasons": pred.get("actionabilityRiskReasons") or [],
        "homeSignal": pred.get("homeSignal"),
        "awaySignal": pred.get("awaySignal"),
        "finalGuardedStored": pred.get("finalGuardedStored"),
        "finalPipelineVersion": pred.get("finalPipelineVersion"),
        "lockedCardAudit": {
            "applied": True,
            "version": VERSION,
            "selectionPolicy": "prefer_latest_final_guarded_locked_state_and_reject_only_explicit_post_lock_scoring_sources",
            "lockedFlag": _locked_flag(pred),
            "lockAtUtc": lock_at.isoformat() if lock_at else None,
            "explicitSourceAtUtc": source_at.isoformat() if source_at else None,
            "rowCreatedAtUtc": created.isoformat() if created else None,
            "createdAtNotUsedAsScoringSource": True,
            "preventsLateRows": True,
            "finalPipelineState": state,
        },
    }


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_LOCKED_CARD_AUDIT_V1_APPLIED", False):
        return module

    def predictions_index(finals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        dates = sorted(set([f.get("slateDateEt") for f in finals if f.get("slateDateEt")]))
        index: Dict[str, Dict[str, Any]] = {}
        for slate in dates:
            for pred in module._query_predictions_for_slate(slate):
                key = f"{module.normalize_team(pred.get('awayTeam'))}|{module.normalize_team(pred.get('homeTeam'))}"
                if not key.strip("|"):
                    continue
                rank = _candidate_rank(pred)
                if rank is None:
                    continue
                current = index.get(key)
                if current is None or rank > (_candidate_rank(current) or (-1, "")):
                    index[key] = pred
        return index

    def audit_rows(finals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        index = predictions_index(finals)
        rows = []
        for final in finals:
            pred = index.get(final.get("gameKeyBase")) or {}
            if not pred:
                rows.append({
                    **final,
                    "status": "MISSING_LOCKED_PREDICTION",
                    "lockedCardAudit": {
                        "applied": True,
                        "version": VERSION,
                        "selectionPolicy": "strict_45_minute_locked_card_only",
                        "missingReason": "no_locked_prediction_row_or_no_pre_lock_source_row",
                    },
                })
                continue
            correct = module.normalize_team(pred.get("predictedWinner")) == module.normalize_team(final.get("winner"))
            rows.append({**final, "status": "GRADED", **_copy_audit_fields(pred), "correct": correct})
        return rows

    module.predictions_index = predictions_index
    module.audit_rows = audit_rows
    module._INQSI_MLB_LOCKED_CARD_AUDIT_V1_APPLIED = True
    return module
