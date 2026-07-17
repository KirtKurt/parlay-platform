"""Prevent weak market-anchor fallbacks from bypassing winner-flip safety.

The visible winner optimizer may prefer the de-vigged market leader when a
movement-based directional override is too weak.  That fallback must not
silently replace the last selected side when the market leader itself has an
unstable profile.  This patch applies the existing conservative flip gate to
all side changes, including market-anchor changes.
"""

VERSION = "MLB-MARKET-ANCHOR-FLIP-SAFETY-v1"
RISK_POLICY_VERSION = "MLB-INDIVIDUAL-WINNER-RISK-CALIBRATION-v6-market-anchor-flip-safety"


def apply(target):
    if getattr(target, "_INQSI_MLB_MARKET_ANCHOR_FLIP_SAFETY_APPLIED", False):
        return target

    original_summary = target._summary

    def guarded_optimize_prediction(row):
        home = target._optimized_signal(row.get("homeSignal") or {})
        away = target._optimized_signal(row.get("awaySignal") or {})
        if not home and not away:
            return row

        old_winner = row.get("predictedWinner")
        old_side = row.get("predictedSide")
        previous = target._previous_signal(home, away, old_side, old_winner)
        market_anchor = target._market_anchor(home, away)
        score_candidate = (
            home
            if target._as_float(home.get("optimizedWinnerScore"), -1.0)
            >= target._as_float(away.get("optimizedWinnerScore"), -1.0)
            else away
        )

        override_requested = bool(
            market_anchor and score_candidate.get("team") != market_anchor.get("team")
        )
        override_allowed, override_block_reasons = target._directional_override(
            score_candidate, market_anchor
        )
        candidate = score_candidate if override_allowed else market_anchor

        previous_override_allowed, previous_override_reasons = target._directional_override(
            previous, market_anchor
        )
        previous_needs_anchor_correction = bool(
            previous
            and market_anchor
            and previous.get("team") != market_anchor.get("team")
            and not previous_override_allowed
        )
        if previous_needs_anchor_correction:
            candidate = market_anchor

        market_anchor_applied = bool(
            market_anchor
            and candidate
            and candidate.get("team") == market_anchor.get("team")
            and (
                (score_candidate and score_candidate.get("team") != market_anchor.get("team"))
                or previous_needs_anchor_correction
            )
        )

        flip_requested = bool(previous and candidate.get("team") != previous.get("team"))

        # Critical safety correction: a market-anchor fallback is still a side
        # change.  It must satisfy the same confirmation, reversal, movement,
        # and score-margin requirements as every other optimizer flip.
        flip_allowed, flip_block_reasons = target._safe_optimizer_flip(candidate, previous)

        pick = candidate
        if flip_requested and not flip_allowed:
            pick = previous

        opponent = away if pick.get("side") == "home" else home
        score = target._as_float(pick.get("optimizedWinnerScore"), 0.0)
        prob = target._as_float(pick.get("winProbability"), target._prob_from_score(score))
        tags = sorted(set(pick.get("tags") or []))
        risks = target._preliminary_risks(pick)
        if market_anchor_applied:
            risks = sorted(set(risks + ["market_anchor_low_confidence_fallback_applied"]))
        if override_requested and not override_allowed:
            risks = sorted(set(risks + override_block_reasons))
        if previous_needs_anchor_correction:
            risks = sorted(set(risks + previous_override_reasons))
        if flip_requested and not flip_allowed:
            risks = sorted(
                set(
                    risks
                    + ["unsafe_optimizer_flip_blocked", "market_anchor_flip_safety_enforced"]
                    + flip_block_reasons
                )
            )

        out = dict(row)
        out["selectionBeforeWinnerOptimizer"] = {
            "predictedWinner": old_winner,
            "predictedSide": old_side,
            "score": row.get("score"),
            "tags": row.get("tags") or [],
        }
        out["predictedSide"] = pick.get("side")
        out["predictedWinner"] = pick.get("team")
        out["opponent"] = opponent.get("team")
        out["score"] = round(score, 2)
        out["winProbability"] = round(prob, 4)
        out["winProbabilityPct"] = round(prob * 100.0, 2)
        out["confidenceTier"] = target._confidence_tier(prob, score, tags)
        out["tags"] = tags
        out["homeSignal"] = home
        out["awaySignal"] = away
        out["individualWinnerOptimized"] = True
        out["optimizerFlippedPick"] = bool(old_winner and old_winner != pick.get("team"))
        out["optimizerFlipRequested"] = flip_requested
        out["optimizerFlipAllowed"] = bool(not flip_requested or flip_allowed)
        out["optimizerFlipBlockedReasons"] = (
            flip_block_reasons if flip_requested and not flip_allowed else []
        )
        out["marketAnchorApplied"] = market_anchor_applied
        out["marketAnchorTeam"] = market_anchor.get("team") if market_anchor else None
        out["marketAnchorProbability"] = (
            round(target._market_probability(market_anchor), 6) if market_anchor else None
        )
        out["directionalOverrideRequested"] = override_requested
        out["directionalOverrideAllowed"] = bool(not override_requested or override_allowed)
        out["directionalOverrideBlockedReasons"] = (
            override_block_reasons if override_requested and not override_allowed else []
        )
        out["marketAnchorFlipSafetyApplied"] = True
        out["marketAnchorFlipSafetyVersion"] = VERSION

        # Preserve the separation between a visible prediction and a validated,
        # actionable recommendation.  This patch never promotes playability.
        out["officialPrediction"] = True
        out["platformPick"] = bool(pick.get("team"))
        out["customerVisibleWinnerPick"] = bool(pick.get("team"))
        out["officialPick"] = False
        out["accuracyTargetEligible"] = False
        out["actionablePick"] = False
        out["actionability"] = "OPTIMIZED_WINNER_PREDICTION_PENDING_FINAL_SIGNAL_GATE"
        out["actionabilityReason"] = (
            "visible_winner_prediction_requires_downstream_signal_or_validated_ml_confirmation"
        )
        out["actionabilityRiskReasons"] = risks
        out["rolling24hAccuracyTarget"] = {
            "targetAccuracyPct": target.ROLLING_TARGET_ACCURACY_PCT,
            "windowHours": target.ROLLING_WINDOW_HOURS,
            "measuredBy": "mlb_rolling_24h_audit",
            "note": (
                "Only downstream-gated official/actionable selections count toward the 90% "
                "target; every game still keeps a visible winner prediction."
            ),
        }
        out["winnerOptimizer"] = {
            "applied": True,
            "basis": (
                "market_signal_plus_multi_window_learning_plus_market_anchor_and_"
                "universal_flip_safety"
            ),
            "latestLearningApplied": bool(target._latest_learning()),
            "homeOptimizedScore": home.get("optimizedWinnerScore"),
            "awayOptimizedScore": away.get("optimizedWinnerScore"),
            "marketAnchorTeam": out["marketAnchorTeam"],
            "marketAnchorProbability": out["marketAnchorProbability"],
            "marketAnchorApplied": market_anchor_applied,
            "marketAnchorFlipSafetyEnforced": True,
            "marketAnchorFlipSafetyVersion": VERSION,
            "directionalOverrideRequested": override_requested,
            "directionalOverrideAllowed": out["directionalOverrideAllowed"],
            "directionalOverrideBlockedReasons": out["directionalOverrideBlockedReasons"],
            "flippedPick": out["optimizerFlippedPick"],
            "flipRequested": flip_requested,
            "flipAllowed": bool(not flip_requested or flip_allowed),
            "flipBlockedReasons": out["optimizerFlipBlockedReasons"],
            "riskPolicyVersion": RISK_POLICY_VERSION,
        }
        return out

    def guarded_summary(predictions):
        summary = original_summary(predictions)
        summary["policy"] = (
            "Every game receives a visible optimized winner prediction. A market-anchor "
            "fallback may change sides only when the candidate passes the same reversal, "
            "movement, market-confirmation, and score-margin flip gate as any other change. "
            "Official/actionable status remains downstream-gated."
        )
        summary["riskPolicyVersion"] = RISK_POLICY_VERSION
        summary["marketAnchorFlipSafetyEnforced"] = True
        summary["marketAnchorFlipSafetyVersion"] = VERSION
        return summary

    target.optimize_prediction = guarded_optimize_prediction
    target._summary = guarded_summary
    target.RISK_POLICY_VERSION = RISK_POLICY_VERSION
    target._INQSI_MLB_MARKET_ANCHOR_FLIP_SAFETY_APPLIED = True
    return target
