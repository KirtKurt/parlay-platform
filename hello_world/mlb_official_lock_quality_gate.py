from __future__ import annotations

import os
from typing import Any, Dict, Optional

import mlb_precision_admission_gate_v1 as precision_admission
import mlb_reversal_similarity_v2 as reversal_similarity


VERSION = "MLB-OFFICIAL-LOCK-QUALITY-v2-70pct-precision-admission"
MIN_OFFICIAL_PROBABILITY_PCT = 60.0
MAX_UNCONFIRMED_REVERSALS = 1
MAX_UNCONFIRMED_BOOK_DIVERGENCE = 0.035
ENFORCE_PRECISION_ADMISSION = str(
    os.environ.get("INQSI_MLB_ENFORCE_70_PRECISION_ADMISSION", "true")
).strip().lower() not in {"0", "false", "no", "off"}


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _tags(row: Dict[str, Any], selected: Dict[str, Any]) -> set[str]:
    return {
        str(value).upper()
        for value in [*(row.get("tags") or []), *(selected.get("tags") or [])]
    }


def _selected_signal(row: Dict[str, Any], accuracy_module: Any = None) -> Dict[str, Any]:
    if accuracy_module is not None and hasattr(accuracy_module, "_selected_signal"):
        selected = accuracy_module._selected_signal(row)
        if isinstance(selected, dict):
            return selected
    side = str(row.get("predictedSide") or "").lower()
    selected = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return selected if isinstance(selected, dict) else {}


def _team_probability(row: Dict[str, Any], selected: Dict[str, Any], accuracy_module: Any = None) -> Optional[float]:
    if accuracy_module is not None and hasattr(accuracy_module, "_team_probability"):
        probability = _f(accuracy_module._team_probability(row))
        if probability is not None:
            return probability / 100.0 if probability > 1.0 else probability
    for value in (
        row.get("teamWinProbabilityPct"),
        row.get("winProbabilityPct"),
        selected.get("marketConsensusProbability"),
        selected.get("fairProbability"),
        selected.get("probLatest"),
    ):
        probability = _f(value)
        if probability is None:
            continue
        probability = probability / 100.0 if probability > 1.0 else probability
        if 0.0 < probability < 1.0:
            return probability
    return None


def _reversal_count(selected: Dict[str, Any]) -> int:
    values = [
        _f(selected.get("reversalCount"), 0.0),
        _f(selected.get("reversals"), 0.0),
    ]
    temporal = selected.get("temporalFeatures") or {}
    horizons = temporal.get("horizons") if isinstance(temporal, dict) else {}
    for horizon in ("60m", "180m", "full"):
        payload = (horizons or {}).get(horizon) or {}
        values.append(_f(payload.get("reversalCount"), 0.0))
    return int(max(value or 0.0 for value in values))


def _velocity(selected: Dict[str, Any], horizon: str) -> Optional[float]:
    temporal = selected.get("temporalFeatures") or {}
    horizons = temporal.get("horizons") if isinstance(temporal, dict) else {}
    payload = (horizons or {}).get(horizon) or {}
    return _f(payload.get("velocityPpHr"))


def _clean_confirmation(row: Dict[str, Any], selected: Dict[str, Any], tags: set[str]) -> Dict[str, bool]:
    stack = row.get("winnerStackV2") or {}
    components = stack.get("components") if isinstance(stack, dict) else {}
    movement = (components or {}).get("movement") or {}
    agreement = bool(
        "BOOK_AGREEMENT" in tags
        or selected.get("bookAgreement") is True
        or selected.get("book_agreement") is True
    )
    steam = bool(
        "STEAM" in tags
        or "STEAM_CONFIRMED" in tags
        or selected.get("steam") is True
        or movement.get("cleanSteam") is True
    )
    run_line = bool(
        "RUN_LINE_CONFIRMATION" in tags
        or "RUN_LINE_CONFIRMED" in tags
        or selected.get("runLineConfirmation") is True
        or selected.get("run_line_confirmation") is True
        or movement.get("cleanRunLineConfirmation") is True
    )
    return {
        "bookAgreement": agreement,
        "steam": steam,
        "runLineConfirmation": run_line,
        "independentConfirmation": bool(agreement and (steam or run_line)),
    }


def _signal_family(row: Dict[str, Any], selected: Dict[str, Any], shape: Dict[str, Any]) -> str:
    explicit = row.get("signalFamily") or selected.get("signalFamily")
    if explicit:
        return str(explicit)
    if shape.get("researchCandidate") is True:
        return str(shape.get("signalFamily"))
    return "MLB_GENERAL_OFFICIAL_PICK"


def evaluate(row: Dict[str, Any], accuracy_module: Any = None) -> Dict[str, Any]:
    selected = _selected_signal(row, accuracy_module)
    tags = _tags(row, selected)
    probability = _team_probability(row, selected, accuracy_module)
    probability_pct = round(probability * 100.0, 2) if probability is not None else None
    reversals = _reversal_count(selected)
    divergence = _f(selected.get("bookDivergence"), 0.0) or 0.0
    delta = _f(selected.get("delta"))
    confirmation = _clean_confirmation(row, selected, tags)
    temporal = selected.get("temporalFeatures") if isinstance(selected.get("temporalFeatures"), dict) else {}
    shape = reversal_similarity.analyze(
        temporal,
        independently_confirmed=confirmation["independentConfirmation"],
    )
    family = _signal_family(row, selected, shape)
    precision_evidence = precision_admission.evidence_from_row(row, selected)
    precision = precision_admission.evaluate(
        precision_evidence,
        expected_signal_family=family,
        expected_similarity_signature=str(shape.get("similaritySignature") or ""),
    )
    reasons = []

    if probability_pct is None:
        reasons.append("selected_team_probability_missing")
    elif probability_pct < MIN_OFFICIAL_PROBABILITY_PCT:
        reasons.append("selected_team_probability_below_60pct")

    if "PROBABILITY_DIRECTION_INTEGRITY_CORRECTION" in tags:
        reasons.append("probability_direction_integrity_correction")
    if "SIGNAL_RISK_GATE_BLOCKED" in tags:
        reasons.append("signal_risk_gate_blocked")
    if "RESISTANCE" in tags:
        reasons.append("resistance_against_selection")
    if delta is not None and delta < 0.0:
        reasons.append("movement_against_selected_team")
    if reversals > MAX_UNCONFIRMED_REVERSALS and not confirmation["independentConfirmation"]:
        reasons.append("multiple_reversals_without_independent_confirmation")
    if divergence >= MAX_UNCONFIRMED_BOOK_DIVERGENCE and not confirmation["independentConfirmation"]:
        reasons.append("book_divergence_without_independent_confirmation")
    if "COMPRESSED_MARKET" in tags and not confirmation["independentConfirmation"]:
        reasons.append("compressed_market_without_independent_confirmation")

    velocity_15 = _velocity(selected, "15m")
    velocity_60 = _velocity(selected, "60m")
    velocity_180 = _velocity(selected, "180m")
    late_conflict = bool(
        velocity_15 is not None
        and (
            (velocity_60 is not None and velocity_15 * velocity_60 < 0.0)
            or (velocity_180 is not None and velocity_15 * velocity_180 < 0.0)
        )
    )
    if late_conflict and not confirmation["independentConfirmation"]:
        reasons.append("late_direction_conflict_without_independent_confirmation")

    signal_policy = row.get("signalPolicyV13") or {}
    risk_gate = signal_policy.get("signalRiskGate") if isinstance(signal_policy, dict) else {}
    risk_reasons = {str(value) for value in ((risk_gate or {}).get("reasons") or [])}
    if "late_direction_conflict_without_confirmation" in risk_reasons:
        reasons.append("late_direction_conflict_without_independent_confirmation")

    for risk in shape.get("riskReasons") or []:
        if risk in {
            "reversal_history_unreliable",
            "latest_leg_against_selected_side",
            "dominant_market_flip_against_selected_side",
            "late_direction_conflict",
            "large_reversal_swing_without_independent_confirmation",
        }:
            reasons.append(f"reversal_shape:{risk}")

    if ENFORCE_PRECISION_ADMISSION and precision.get("admitted") is not True:
        reasons.extend(str(reason) for reason in precision.get("reasons") or [])

    reasons = sorted(set(reasons))
    return {
        "applied": True,
        "version": VERSION,
        "officialEligible": not reasons,
        "minimumOfficialProbabilityPct": MIN_OFFICIAL_PROBABILITY_PCT,
        "selectedTeamProbabilityPct": probability_pct,
        "reversalCount": reversals,
        "selectedMovement": delta,
        "bookDivergence": round(divergence, 6),
        "lateDirectionConflict": late_conflict,
        "signalFamily": family,
        "reversalSimilarity": shape,
        "reversalSimilaritySignature": shape.get("similaritySignature"),
        "precisionAdmissionEnforced": ENFORCE_PRECISION_ADMISSION,
        "precisionAdmission": precision,
        **confirmation,
        "reasons": reasons,
        "policy": (
            "Every canonical locked winner remains visible and auditable. Official recommendation eligibility "
            "requires the existing direction, probability and confirmation checks plus a frozen, prospective, "
            "chronological signal-family validation whose 95% Wilson lower precision bound is at least 70%. "
            "Missing or weak evidence causes abstention rather than an unsupported accuracy claim."
        ),
    }


def apply(accuracy_module: Any) -> Any:
    if getattr(accuracy_module, "_INQSI_MLB_OFFICIAL_LOCK_QUALITY_GATE_APPLIED", False):
        return accuracy_module
    original_is_official = accuracy_module._is_official

    def quality_gated_is_official(row: Dict[str, Any]) -> bool:
        if not original_is_official(row):
            return False
        decision = evaluate(row, accuracy_module)
        if isinstance(row, dict):
            row["officialLockQualityGate"] = decision
        return bool(decision.get("officialEligible"))

    accuracy_module._is_official = quality_gated_is_official
    accuracy_module.OFFICIAL_LOCK_QUALITY_GATE_VERSION = VERSION
    accuracy_module.INDIVIDUAL_GAME_OFFICIAL_PICK_PROBABILITY_FLOOR_PCT = MIN_OFFICIAL_PROBABILITY_PCT
    accuracy_module.MIN_OFFICIAL_SIGNAL_FAMILY_WILSON_LOWER_BOUND_PCT = precision_admission.TARGET_PRECISION_PCT
    accuracy_module._INQSI_MLB_OFFICIAL_LOCK_QUALITY_GATE_APPLIED = True
    return accuracy_module
