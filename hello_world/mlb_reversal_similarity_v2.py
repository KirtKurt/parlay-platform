from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


VERSION = "MLB-REVERSAL-SIMILARITY-v2-market-flip-time-size"
RESEARCH_SIGNAL_FAMILY = "MLB_REVERSAL_LARGEST_TOWARD_MARKET_FLIP_2_TO_3PP"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _bucket(value: float, breaks: Iterable[tuple[float, str]], fallback: str) -> str:
    for upper, label in breaks:
        if value < upper:
            return label
    return fallback


def _horizon(summary: Any, label: str) -> Dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    horizons = summary.get("horizons") or {}
    payload = horizons.get(label) or {}
    return payload if isinstance(payload, dict) else {}


def analyze(summary: Any, *, independently_confirmed: bool = False) -> Dict[str, Any]:
    """Create a deterministic similarity signature from lock-safe temporal features.

    This module only labels and blocks risk. It never adds positive production score.
    The 2–3 percentage-point market-flip pocket is explicitly research-only until a
    prospective precision admission record approves it.
    """
    full = _horizon(summary, "full")
    h180 = _horizon(summary, "180m")
    h60 = _horizon(summary, "60m")
    h15 = _horizon(summary, "15m")

    available = bool(isinstance(summary, dict) and summary.get("available") is True)
    pull_count = int(_f(full.get("pullCount")))
    duration_minutes = _f(full.get("durationMinutes"))
    coverage = _f(full.get("coverageRatio"))
    reversal_count = int(max(
        _f(full.get("reversalCount")),
        _f(h180.get("reversalCount")),
        _f(h60.get("reversalCount")),
    ))
    gross_move = _f(full.get("grossMovePp"))
    net_move = _f(full.get("netMovePp"))
    efficiency = _f(full.get("pathEfficiency"))
    latest_signed = _f(full.get("latestLegSignedMovePp"))
    latest_move = _f(full.get("latestLegMovePp"))
    latest_duration = _f(full.get("latestLegDurationMinutes"))
    prior_move = _f(full.get("priorLegMovePp"))
    recovery_ratio = _f(full.get("reversalRecoveryRatio"))
    minutes_since_reversal = _f(full.get("minutesSinceLastReversal"))
    market_flip_count = int(_f(full.get("marketFlipCount")))
    largest_toward_flip = _f(full.get("largestMarketFlipTowardSidePp"))
    largest_against_flip = _f(full.get("largestMarketFlipAgainstSidePp"))
    largest_flip_age = _f(full.get("largestMarketFlipAgeMinutes"))
    max_reversal_swing = _f(full.get("maxReversalSwingPp"))
    reversal_density_per_hour = (
        reversal_count / (duration_minutes / 60.0) if duration_minutes > 0 else 0.0
    )

    late_velocity = _f(h15.get("velocityPpHr"))
    velocity_60 = _f(h60.get("velocityPpHr"))
    velocity_180 = _f(h180.get("velocityPpHr"))
    late_direction_conflict = bool(
        late_velocity
        and ((velocity_60 and late_velocity * velocity_60 < 0.0)
             or (velocity_180 and late_velocity * velocity_180 < 0.0))
    )

    candidate = bool(
        available
        and full.get("marketFlip2To3PpCandidate") is True
        and 2.0 <= largest_toward_flip < 3.0
    )
    size_bucket = _bucket(
        largest_toward_flip,
        ((0.01, "NONE"), (1.0, "LT1"), (2.0, "1TO2"), (3.0, "2TO3"), (5.0, "3TO5")),
        "5PLUS",
    )
    age_bucket = _bucket(
        largest_flip_age,
        ((30.0, "LE30M"), (90.0, "31TO90M"), (180.0, "91TO180M"), (360.0, "181TO360M")),
        "GT360M",
    )
    recovery_bucket = _bucket(
        recovery_ratio,
        ((1.0, "LT1"), (3.0, "1TO3"), (6.0, "3TO6"), (10.0, "6TO10")),
        "10PLUS",
    )
    persistence_bucket = _bucket(
        latest_duration,
        ((30.0, "LT30M"), (90.0, "30TO90M"), (180.0, "90TO180M")),
        "180MPLUS",
    )
    reversal_bucket = _bucket(
        float(reversal_count),
        ((1.0, "0"), (2.0, "1"), (4.0, "2TO3"), (7.0, "4TO6")),
        "7PLUS",
    )
    noise_bucket = (
        "CLEAN" if efficiency >= 0.65
        else "MIXED" if efficiency >= 0.35
        else "CHURN"
    )

    risks = []
    if not available or pull_count < 4 or coverage < 0.60:
        risks.append("reversal_history_unreliable")
    if reversal_density_per_hour >= 1.0 and reversal_count >= 3:
        risks.append("high_reversal_density")
    if efficiency < 0.25 and reversal_count >= 2 and gross_move >= 1.0:
        risks.append("low_efficiency_churn")
    if latest_signed < 0.0 and latest_move >= 0.30:
        risks.append("latest_leg_against_selected_side")
    if largest_against_flip >= 2.0 and largest_against_flip > largest_toward_flip:
        risks.append("dominant_market_flip_against_selected_side")
    if late_direction_conflict:
        risks.append("late_direction_conflict")
    if max_reversal_swing >= 3.0 and not independently_confirmed:
        risks.append("large_reversal_swing_without_independent_confirmation")

    hard_risks = {
        "reversal_history_unreliable",
        "latest_leg_against_selected_side",
        "dominant_market_flip_against_selected_side",
        "late_direction_conflict",
        "large_reversal_swing_without_independent_confirmation",
    }
    blocked = any(reason in hard_risks for reason in risks)
    tags = []
    if reversal_count:
        tags.append("REVERSAL_PROFILED")
    if candidate:
        tags.extend(["MARKET_FLIP_2_TO_3PP", "REVERSAL_RESEARCH_CANDIDATE"])
    if independently_confirmed:
        tags.append("REVERSAL_INDEPENDENTLY_CONFIRMED")
    if blocked:
        tags.append("REVERSAL_SHAPE_RISK_BLOCKED")

    signature = (
        f"FAMILY_{RESEARCH_SIGNAL_FAMILY if candidate else 'GENERAL'}"
        f"|SIZE_{size_bucket}"
        f"|AGE_{age_bucket}"
        f"|REV_{reversal_bucket}"
        f"|REC_{recovery_bucket}"
        f"|PERSIST_{persistence_bucket}"
        f"|NOISE_{noise_bucket}"
        f"|LATE_{'CONFLICT' if late_direction_conflict else 'ALIGNED'}"
        f"|CONF_{'YES' if independently_confirmed else 'NO'}"
    )

    return {
        "applied": True,
        "version": VERSION,
        "available": available,
        "signalFamily": RESEARCH_SIGNAL_FAMILY if candidate else "MLB_GENERAL_REVERSAL_PROFILE",
        "researchCandidate": candidate,
        "productionApproved": False if candidate else None,
        "similaritySignature": signature,
        "largestTowardMarketFlipPp": round(largest_toward_flip, 6),
        "largestAgainstMarketFlipPp": round(largest_against_flip, 6),
        "largestMarketFlipAgeMinutes": round(largest_flip_age, 3),
        "marketFlipCount": market_flip_count,
        "reversalCount": reversal_count,
        "reversalDensityPerHour": round(reversal_density_per_hour, 6),
        "grossMovePp": round(gross_move, 6),
        "netMovePp": round(net_move, 6),
        "pathEfficiency": round(efficiency, 6),
        "latestLegSignedMovePp": round(latest_signed, 6),
        "latestLegMovePp": round(latest_move, 6),
        "latestLegDurationMinutes": round(latest_duration, 3),
        "priorLegMovePp": round(prior_move, 6),
        "reversalRecoveryRatio": round(recovery_ratio, 6),
        "minutesSinceLastReversal": round(minutes_since_reversal, 3),
        "lateDirectionConflict": late_direction_conflict,
        "independentlyConfirmed": independently_confirmed,
        "blocked": blocked,
        "riskReasons": sorted(set(risks)),
        "tags": sorted(set(tags)),
        "policy": (
            "Reversal count alone never earns positive authority. Time, leg size, market-flip amplitude, "
            "recovery ratio, path efficiency, persistence, and late direction are fingerprinted. The 2–3pp "
            "largest toward-side market-flip pattern remains shadow-only until prospective precision admission."
        ),
    }
