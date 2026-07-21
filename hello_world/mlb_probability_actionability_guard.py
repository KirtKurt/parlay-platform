"""Provider-neutral MLB probability calibration and no-pick discipline.

This layer preserves the market-risk calibration and actionability behavior
that predates the V2 prospective experiment.  It deliberately has no provider
client, credential, matchup join, winner-flip, or feature-completeness
authority.  Fundamentals V2 and all provider shadow payloads are evaluated by
their own versioned contracts and cannot influence live picks through here.
"""

from __future__ import annotations

import copy
import math
import os
from typing import Any, Dict, Iterable, List, Tuple


CALIBRATION_ENABLED = os.environ.get(
    "INQSI_MLB_CALIBRATION_ENABLED", "true"
).lower() in {"1", "true", "yes"}
NO_PICK_ENABLED = os.environ.get(
    "INQSI_MLB_NO_PICK_DISCIPLINE_ENABLED", "true"
).lower() in {"1", "true", "yes"}
MIN_ACTIONABLE_PULLS = int(os.environ.get("INQSI_MLB_MIN_ACTIONABLE_PULLS", "12"))
MIN_ACTIONABLE_PROB = float(
    os.environ.get("INQSI_MLB_MIN_ACTIONABLE_CALIBRATED_PROB", "0.59")
)
MIN_ACTIONABLE_SCORE = float(
    os.environ.get("INQSI_MLB_MIN_ACTIONABLE_SCORE", "56")
)
MAX_ACTIONABLE_RISK = float(
    os.environ.get("INQSI_MLB_MAX_ACTIONABLE_RISK", "0.18")
)
PATCH_VERSION = "MLB-PROVIDER-NEUTRAL-CALIBRATION-NO-PICK-v2"
FUNDAMENTALS_MODE = "FUNDAMENTALS_V2_NOT_ACTIVE_IN_LIVE_SCORING"
UPSTREAM_RELEASE_REASON_FIELDS = (
    "blockedReasons",
    "releaseBlockReasons",
    "playabilityBlockReasons",
    "wagerReleaseBlockReasons",
    "hardConfidenceBlockers",
    "contextActionabilityBlockers",
)
UPSTREAM_RELEASE_FLAG_FIELDS = (
    "blocked",
    "releaseBlocked",
    "wagerReleaseBlocked",
    "predictionReleaseBlocked",
    "predictionIntentionallyBlocked",
)
UPSTREAM_RELEASE_BLOCK_TAGS = {"RELEASE_BLOCKED", "WAGER_RELEASE_BLOCKED"}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _prob_from_score(score: float) -> float:
    value = 1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0))
    return _clamp(value, 0.05, 0.95)


def _tier(prob: float, score: float, tags: Iterable[str]) -> str:
    edge = abs(float(prob or 0.5) - 0.5)
    tag_set = set(tags or [])
    if "LOW_PULL_DEPTH" in tag_set or "INSUFFICIENT_HISTORY" in tag_set:
        return "Baseline"
    if score >= 72 and edge >= 0.12:
        return "Premium"
    if score >= 64 and edge >= 0.08:
        return "Solid"
    if score >= 56 and edge >= 0.04:
        return "Lean"
    if score >= 50:
        return "Coin Flip"
    return "Pass"


def _signal(row: Dict[str, Any]) -> Dict[str, Any]:
    side = str(row.get("predictedSide") or "home").lower()
    value = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return dict(value or {})


def _annotate_source_honesty(row: Dict[str, Any]) -> Dict[str, Any]:
    """Mark fundamentals unavailable without reading or inventing provider data."""
    out = copy.deepcopy(row or {})
    optimizer = dict(out.get("winnerOptimizer") or {})
    optimizer["fundamentalsApplied"] = False
    optimizer["fundamentalsMode"] = FUNDAMENTALS_MODE
    optimizer["basis"] = optimizer.get("basis") or (
        "market_signal_plus_multi_window_learning"
    )
    out["winnerOptimizer"] = optimizer
    out["tags"] = sorted(set((out.get("tags") or []) + ["MISSING_FUNDAMENTALS"]))
    out["fundamentalsLayer"] = {
        "available": False,
        "applied": False,
        "mode": FUNDAMENTALS_MODE,
        "message": (
            "No validated Fundamentals V2 package is active in live scoring. "
            "Market signal remains primary."
        ),
    }
    return out


def _risk_penalty(row: Dict[str, Any]) -> Tuple[float, List[str]]:
    pick = _signal(row)
    tags = set(row.get("tags") or []) | set(pick.get("tags") or [])
    reasons: List[str] = []
    penalty = 0.0
    pull_count = _int(row.get("pullCountForGame"), 0)
    if pull_count < MIN_ACTIONABLE_PULLS:
        penalty += 0.07
        reasons.append("LOW_PULL_DEPTH")
    elif pull_count < MIN_ACTIONABLE_PULLS * 2:
        penalty += 0.025
        reasons.append("MODERATE_PULL_DEPTH")
    divergence = _num(pick.get("bookDivergence"), 0.0)
    reversals = _int(pick.get("reversalCount"), 0)
    raw_gap = pick.get("latestGap")
    if raw_gap not in (None, ""):
        gap = abs(_num(raw_gap, 0.0))
    else:
        home_market = row.get("homeMarketDeVigProbability")
        away_market = row.get("awayMarketDeVigProbability")
        if home_market not in (None, "") and away_market not in (None, ""):
            gap = abs(_num(home_market, 0.5) - _num(away_market, 0.5))
        else:
            latest = pick.get("marketProbability", pick.get("probLatest"))
            gap = (
                abs(2.0 * _num(latest, 0.5) - 1.0)
                if latest not in (None, "")
                else 0.0
            )
    if divergence >= 0.04 or "BOOK_DIVERGENCE" in tags:
        penalty += 0.045
        reasons.append("BOOK_DIVERGENCE")
    elif divergence >= 0.025:
        penalty += 0.02
        reasons.append("BOOK_DISAGREEMENT")
    if reversals >= 4:
        penalty += 0.06
        reasons.append("HIGH_REVERSAL_COUNT")
    elif reversals >= 2 or "REVERSAL" in tags:
        penalty += 0.025
        reasons.append("REVERSAL_RISK")
    if gap < 0.05 or "COMPRESSED_MARKET" in tags:
        penalty += 0.04
        reasons.append("COMPRESSED_MARKET")
    if "MISSING_FUNDAMENTALS" in tags:
        penalty += 0.02
        reasons.append("MISSING_FUNDAMENTALS")
    if "LOW_PULL_DEPTH" in tags or "INSUFFICIENT_HISTORY" in tags:
        penalty += 0.04
        reasons.append("INSUFFICIENT_HISTORY")
    return round(_clamp(penalty, 0.0, 0.30), 4), sorted(set(reasons))


def _calibrate(row: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(row or {})
    if not CALIBRATION_ENABLED:
        out["calibration"] = {"enabled": False, "version": PATCH_VERSION}
        return out
    pick = _signal(out)
    raw_prob = _clamp(_num(out.get("winProbability"), 0.5), 0.05, 0.95)
    market_prob = _clamp(
        _num(
            out.get(
                "marketProbability",
                pick.get(
                    "marketProbability",
                    pick.get("probLatest", pick.get("marketConsensusProbability")),
                ),
            ),
            raw_prob,
        ),
        0.05,
        0.95,
    )
    score_prob = _prob_from_score(_num(out.get("score"), 50.0))
    penalty, reasons = _risk_penalty(out)
    raw_edge = raw_prob - 0.5
    market_edge = market_prob - 0.5
    score_edge = score_prob - 0.5
    blended_edge = raw_edge * 0.35 + market_edge * 0.45 + score_edge * 0.20
    shrinkage = _clamp(0.18 + penalty, 0.18, 0.48)
    calibrated = round(
        _clamp(0.5 + blended_edge * (1.0 - shrinkage), 0.05, 0.92), 4
    )
    # The canonical probability contract owns ``winProbability`` and binds it
    # to the selected side's complementary model pair.  Calibration is a
    # separate playability estimate and must not rewrite that authority.
    out["rawWinProbabilityBeforeCalibration"] = raw_prob
    out["calibratedWinProbability"] = calibrated
    out["calibratedWinProbabilityPct"] = round(calibrated * 100.0, 2)
    out["confidenceTier"] = _tier(
        calibrated, _num(out.get("score"), 0.0), out.get("tags") or []
    )
    out["calibration"] = {
        "enabled": True,
        "version": PATCH_VERSION,
        "method": "market_consensus_score_blend_with_risk_shrinkage",
        "rawProbability": raw_prob,
        "marketConsensusProbability": market_prob,
        "scoreImpliedProbability": round(score_prob, 4),
        "calibratedProbability": calibrated,
        "shrinkage": round(shrinkage, 4),
        "riskPenalty": penalty,
        "riskReasons": reasons,
        "fundamentalsBoost": 0.0,
        "note": (
            "Calibration compresses aggressive raw scores toward market "
            "consensus and penalizes instability."
        ),
    }
    return out


def _list_values(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item).strip() for item in values if str(item).strip()]


def _blocked_status(value: Any) -> bool:
    status = str(value or "").strip().upper()
    return bool(
        status == "BLOCKED"
        or (
            "BLOCK" in status
            and status not in {"NOT_BLOCKED", "UNBLOCKED"}
        )
    )


def _upstream_release_block(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Normalize every upstream release channel without losing its reasons."""
    reasons = {
        reason
        for field in UPSTREAM_RELEASE_REASON_FIELDS
        for reason in _list_values(row.get(field))
    }
    explicit_block = any(
        row.get(field) is True for field in UPSTREAM_RELEASE_FLAG_FIELDS
    )
    playability_status_blocked = _blocked_status(row.get("playabilityStatus"))
    prediction_status_blocked = _blocked_status(row.get("predictionBlockStatus"))
    explicit_block = bool(
        explicit_block
        or playability_status_blocked
        or prediction_status_blocked
    )

    tags = {
        str(value)
        for value in (row.get("tags") or [])
        if value
    }
    tagged_blocks = tags & UPSTREAM_RELEASE_BLOCK_TAGS
    reasons.update(tagged_blocks)

    prediction_block_reason = str(row.get("predictionBlockReason") or "").strip()
    if prediction_block_reason and (
        row.get("predictionReleaseBlocked") is True
        or row.get("predictionIntentionallyBlocked") is True
        or prediction_status_blocked
    ):
        reasons.add(prediction_block_reason)

    blocked = bool(explicit_block or tagged_blocks or reasons)
    if blocked and not reasons:
        # Some legacy writers expose only a boolean/status. Retain a stable,
        # truthful reason instead of dropping the block while normalizing it.
        reasons.add("WAGER_RELEASE_BLOCKED")
    return blocked, sorted(reasons)


def _apply_playability_aliases(
    out: Dict[str, Any],
    *,
    actionable: bool,
    level: str,
    mandatory_block_reasons: Iterable[str],
    contract_validation_errors: Iterable[str],
    upstream_block_reasons: Iterable[str],
) -> Dict[str, Any]:
    mandatory = sorted(set(str(value) for value in mandatory_block_reasons if value))
    contract_errors = sorted(
        set(str(value) for value in contract_validation_errors if value)
    )
    upstream_reasons = sorted(
        set(str(value) for value in upstream_block_reasons if value)
    )
    hard_blocked = bool(mandatory)
    # Immutable lock semantics own officialPrediction/officialPick. This gate
    # may change only playability/actionability and must never make a pre-lock
    # row official or strip official status from a locked non-playable row.
    out["officialPick"] = out.get("officialPrediction") is True
    out["playable"] = bool(actionable)
    out["playablePick"] = bool(actionable)
    out["actionablePick"] = bool(actionable)
    out["accuracyTargetEligible"] = bool(actionable)
    # A prediction can be actionable before T-45, but it is not eligible for
    # official playable-accuracy accounting until the immutable lock authority
    # has promoted it to an official prediction.
    out["playableAccuracyEligible"] = bool(
        actionable and out.get("officialPrediction") is True
    )
    out["actionability"] = level
    out["playabilityStatus"] = (
        "BLOCKED" if hard_blocked else "PLAYABLE" if actionable else "NOT_PLAYABLE"
    )

    tags = set(str(value) for value in (out.get("tags") or []) if value)
    if actionable:
        tags.update({"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})
        tags.difference_update(
            {
                "NO_PICK",
                "NOT_PLAYABLE",
                "OFFICIAL_PREDICTION_NOT_PLAYABLE",
                "RELEASE_BLOCKED",
                "WAGER_RELEASE_BLOCKED",
            }
        )
        out["recommendationStatus"] = "PLAYABLE_PREDICTION"
    else:
        tags.update({"NO_PICK", "NOT_PLAYABLE"})
        tags.difference_update({"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})
        out["recommendationStatus"] = (
            "OFFICIAL_PREDICTION_NOT_PLAYABLE"
            if out.get("officialPrediction") is True
            else "PRE_LOCK_PREDICTION"
        )
        if hard_blocked:
            tags.update({"RELEASE_BLOCKED", "WAGER_RELEASE_BLOCKED"})

    if hard_blocked:
        out["blocked"] = True
        out["releaseBlocked"] = True
        out["wagerReleaseBlocked"] = True
        exact_reasons = {
            *mandatory,
            *upstream_reasons,
            *(f"probability_contract:{value}" for value in contract_errors),
        }
        block_reason_fields = (
            "blockedReasons",
            "releaseBlockReasons",
            "playabilityBlockReasons",
            "wagerReleaseBlockReasons",
        )
        for field in block_reason_fields:
            exact_reasons.update(_list_values(out.get(field)))
        exact_reasons = sorted(exact_reasons)
        for field in block_reason_fields:
            out[field] = sorted(exact_reasons)

    tags.add("CALIBRATED_PROBABILITY")
    out["tags"] = sorted(tags)
    return out


def _no_pick(row: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(row or {})
    calibrated = _num(
        out.get("calibratedWinProbability", out.get("winProbability")), 0.5
    )
    score = _num(out.get("score"), 0.0)
    pull_count = _int(out.get("pullCountForGame"), 0)
    confidence = str(out.get("confidenceTier") or "")
    calibration = out.get("calibration") or {}
    risk = _num(calibration.get("riskPenalty"), 0.0)
    reasons = list(calibration.get("riskReasons") or [])
    mandatory_block_reasons: List[str] = []
    probability_contract = out.get("probabilityContract") or {}
    if out.get("probabilityCorrectionApplied") is True:
        mandatory_block_reasons.append("probability_direction_integrity_correction")
    if (probability_contract.get("errors") or []):
        mandatory_block_reasons.append("probability_contract_invalid")
    contract_validation_errors: List[str] = []
    try:
        import mlb_prediction_probability_contract_v1 as probability_contract_v1

        contract_validation_errors = list(
            probability_contract_v1.validation_errors(out)
        )
    except Exception as exc:
        contract_validation_errors = [
            f"probability_contract_validator_unavailable:{type(exc).__name__}"
        ]
    if contract_validation_errors:
        mandatory_block_reasons.append("probability_contract_invalid")
    upstream_release_blocked, upstream_block_reasons = _upstream_release_block(out)
    if upstream_release_blocked:
        mandatory_block_reasons.append("upstream_release_blocked")

    if not NO_PICK_ENABLED:
        actionable = not mandatory_block_reasons
        level = (
            "PREDICTION_ONLY_NO_DISCIPLINE_GATE"
            if actionable
            else "NO_PICK"
        )
        _apply_playability_aliases(
            out,
            actionable=actionable,
            level=level,
            mandatory_block_reasons=mandatory_block_reasons,
            contract_validation_errors=contract_validation_errors,
            upstream_block_reasons=upstream_block_reasons,
        )
        out["actionabilityReason"] = (
            "optional_threshold_discipline_disabled"
            if actionable
            else ";".join(sorted(set(mandatory_block_reasons)))
        )
        out["pickDiscipline"] = {
            "enabled": False,
            "version": PATCH_VERSION,
            "actionable": bool(actionable),
            "level": level,
            "mandatoryBlockReasons": sorted(set(mandatory_block_reasons)),
            "upstreamReleaseBlockReasons": upstream_block_reasons,
            "probabilityContractValidationErrors": sorted(
                set(contract_validation_errors)
            ),
            "rule": (
                "Optional thresholds may be disabled, but canonical probability "
                "and upstream release blocks always fail closed."
            ),
        }
        return out

    no_pick_reasons: List[str] = list(mandatory_block_reasons)
    if pull_count < MIN_ACTIONABLE_PULLS:
        no_pick_reasons.append("needs_more_pull_depth")
    if calibrated < MIN_ACTIONABLE_PROB:
        no_pick_reasons.append("calibrated_probability_below_actionable_threshold")
    if score < MIN_ACTIONABLE_SCORE:
        no_pick_reasons.append("score_below_actionable_threshold")
    if confidence in {"Pass", "Coin Flip", "Baseline"}:
        no_pick_reasons.append(
            f"confidence_tier_{confidence.lower().replace(' ', '_')}"
        )
    if risk > MAX_ACTIONABLE_RISK:
        no_pick_reasons.append("market_instability_risk_too_high")
    if "MISSING_FUNDAMENTALS" in set(out.get("tags") or []) and calibrated < 0.66:
        no_pick_reasons.append("missing_fundamentals_requires_stronger_market_edge")

    actionable = not no_pick_reasons
    if actionable and calibrated >= 0.68 and score >= 66 and risk <= 0.12:
        level = "STRONG_ACTIONABLE_PICK"
    elif actionable:
        level = "ACTIONABLE_LEAN_PICK"
    else:
        level = "NO_PICK"
    _apply_playability_aliases(
        out,
        actionable=actionable,
        level=level,
        mandatory_block_reasons=mandatory_block_reasons,
        contract_validation_errors=contract_validation_errors,
        upstream_block_reasons=upstream_block_reasons,
    )
    out["actionabilityReason"] = (
        "passes_calibration_and_no_pick_gate"
        if actionable
        else ";".join(sorted(set(no_pick_reasons)))
    )
    out["pickDiscipline"] = {
        "enabled": True,
        "version": PATCH_VERSION,
        "actionable": bool(actionable),
        "level": level,
        "thresholds": {
            "minPulls": MIN_ACTIONABLE_PULLS,
            "minCalibratedProbability": MIN_ACTIONABLE_PROB,
            "minScore": MIN_ACTIONABLE_SCORE,
            "maxRiskPenalty": MAX_ACTIONABLE_RISK,
        },
        "calibratedProbability": calibrated,
        "score": score,
        "pullCountForGame": pull_count,
        "riskPenalty": risk,
        "riskReasons": reasons,
        "noPickReasons": sorted(set(no_pick_reasons)),
        "mandatoryBlockReasons": sorted(set(mandatory_block_reasons)),
        "upstreamReleaseBlockReasons": upstream_block_reasons,
        "probabilityContractValidationErrors": sorted(
            set(contract_validation_errors)
        ),
        "rule": (
            "Every game can receive a prediction, but only rows passing this "
            "gate are actionable picks."
        ),
    }
    return out


def guard_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
    """Apply fallback calibration without changing team or side direction."""
    out = _annotate_source_honesty(row)
    out = _calibrate(out)
    return _no_pick(out)


def apply(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_PROVIDER_NEUTRAL_CALIBRATION_APPLIED", False):
        return module
    original_predict_all = module.predict_all

    def patched_predict_all(*args: Any, **kwargs: Any) -> Any:
        result = original_predict_all(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        predictions = [
            guard_prediction(row) for row in (result.get("predictions") or [])
        ]
        predictions.sort(
            key=lambda row: (
                float(row.get("actionablePick") is True),
                float(row.get("score") or 0),
                float(row.get("winProbability") or 0),
            ),
            reverse=True,
        )
        for rank, row in enumerate(predictions, 1):
            row["rank"] = rank
        result["predictions"] = predictions
        result["count"] = len(predictions)
        summary = dict(
            result.get("rolling24hAccuracyTarget")
            or result.get("accuracyTarget")
            or {}
        )
        summary.update(
            {
                "fundamentalsEnabled": False,
                "fundamentalsMode": FUNDAMENTALS_MODE,
                "fundamentalsAppliedCount": 0,
                "fundamentalsMissingCount": len(
                    [
                        row
                        for row in predictions
                        if "MISSING_FUNDAMENTALS" in set(row.get("tags") or [])
                    ]
                ),
                "calibrationEnabled": CALIBRATION_ENABLED,
                "calibratedPredictionCount": len(
                    [
                        row
                        for row in predictions
                        if (row.get("calibration") or {}).get("enabled")
                    ]
                ),
                "noPickDisciplineEnabled": NO_PICK_ENABLED,
                "actionablePickCount": len(
                    [row for row in predictions if row.get("actionablePick")]
                ),
                "noPickCount": len(
                    [row for row in predictions if not row.get("actionablePick")]
                ),
                "patchVersion": PATCH_VERSION,
                "providerShadowCanInfluenceLivePick": False,
            }
        )
        result["rolling24hAccuracyTarget"] = summary
        result["accuracyTarget"] = summary
        result["actionablePickCount"] = summary["actionablePickCount"]
        result["noPickCount"] = summary["noPickCount"]
        result["calibrationPolicy"] = {
            "enabled": CALIBRATION_ENABLED,
            "method": "market_consensus_score_blend_with_risk_shrinkage",
            "brierLogLossReady": True,
            "providerNeutral": True,
        }
        result["noPickPolicy"] = {
            "enabled": NO_PICK_ENABLED,
            "minPulls": MIN_ACTIONABLE_PULLS,
            "minCalibratedProbability": MIN_ACTIONABLE_PROB,
            "minScore": MIN_ACTIONABLE_SCORE,
            "maxRiskPenalty": MAX_ACTIONABLE_RISK,
        }
        suffix = "+provider-neutral-calibration-no-pick-v2"
        model = str(result.get("modelVersion") or "")
        result["modelVersion"] = model if suffix in model else model + suffix
        return result

    module.predict_all = patched_predict_all
    module._INQSI_MLB_PROVIDER_NEUTRAL_CALIBRATION_APPLIED = True
    module.MLB_PROBABILITY_ACTIONABILITY_GUARD_VERSION = PATCH_VERSION
    return module
