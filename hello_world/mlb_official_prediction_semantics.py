from __future__ import annotations

from typing import Any, Dict, List

VERSION = "MLB-OFFICIAL-PREDICTION-SEMANTICS-v2-lock-release-training-separated"


def _tags(row: Dict[str, Any]) -> set[str]:
    return {str(value) for value in (row.get("tags") or [])}


def _result_locked(result: Dict[str, Any]) -> bool:
    lock = result.get("slatePredictionLock") or {}
    if isinstance(lock, dict) and lock.get("locked") is True:
        return True
    gate = result.get("lastPossiblePredictionGate") or {}
    if isinstance(gate, dict) and int(gate.get("finalLockedCount") or 0) > 0:
        return True
    target = result.get("rolling24hAccuracyTarget") or result.get("accuracyTarget") or {}
    if isinstance(target, dict):
        gate = target.get("lastPossiblePredictionGate") or {}
        if isinstance(gate, dict) and int(gate.get("finalLockedCount") or 0) > 0:
            return True
    return False


def _row_locked(row: Dict[str, Any], result_locked: bool) -> bool:
    tags = _tags(row)
    lock = row.get("slatePredictionLock") or {}
    return bool(
        result_locked
        or row.get("lockedPrediction") is True
        or "FINAL_LOCKED" in tags
        or "SLATE_LOCKED" in tags
        or (isinstance(lock, dict) and lock.get("locked") is True)
        or str(row.get("predictionPhase") or row.get("phase") or "").upper() == "SLATE_LOCKED"
    )


def _list_values(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item).strip() for item in values if str(item).strip()]


def _release_block_reasons(row: Dict[str, Any]) -> List[str]:
    """Return wagering-release blockers without changing prediction authority."""
    reasons: set[str] = set()
    for field in (
        "playabilityBlockReasons",
        "releaseBlockReasons",
        "wagerReleaseBlockReasons",
        "hardConfidenceBlockers",
        "contextActionabilityBlockers",
    ):
        reasons.update(_list_values(row.get(field)))

    gate = row.get("impactPlayerAbsenceGate") or {}
    if isinstance(gate, dict) and gate.get("blocked") is True:
        reasons.add(str(gate.get("blocker") or "CONFIRMED_IMPACT_PLAYER_ABSENCE"))

    explicitly_blocked = bool(
        row.get("blocked") is True
        or row.get("releaseBlocked") is True
        or row.get("wagerReleaseBlocked") is True
        or row.get("predictionReleaseBlocked") is True
        or str(row.get("playabilityStatus") or "").upper() == "BLOCKED"
    )
    if row.get("predictionIntentionallyBlocked") is True:
        explicitly_blocked = True
        reasons.add(str(row.get("predictionBlockReason") or "PREDICTION_INTENTIONALLY_BLOCKED"))
    if str(row.get("predictionBlockStatus") or "").upper() == "INTENTIONAL_POLICY_BLOCK":
        explicitly_blocked = True
        reasons.add(str(row.get("predictionBlockReason") or "INTENTIONAL_POLICY_BLOCK"))

    tags = _tags(row)
    if "RELEASE_BLOCKED" in tags or "WAGER_RELEASE_BLOCKED" in tags:
        explicitly_blocked = True
    if explicitly_blocked and not reasons:
        reasons.add("WAGER_RELEASE_BLOCKED")
    return sorted(reasons)


def _release_blocked_before_semantics(row: Dict[str, Any]) -> bool:
    return bool(
        _release_block_reasons(row)
        or row.get("blocked") is True
        or row.get("releaseBlocked") is True
        or row.get("wagerReleaseBlocked") is True
        or row.get("predictionReleaseBlocked") is True
        or str(row.get("playabilityStatus") or "").upper() == "BLOCKED"
    )


def _training_eligible_before_semantics(row: Dict[str, Any]) -> Any:
    """Read, but never infer, training eligibility from the frozen row."""
    if isinstance(row.get("trainingEligible"), bool):
        return row.get("trainingEligible")
    freeze = row.get("mlFeatureFreeze") or {}
    if isinstance(freeze, dict) and isinstance(freeze.get("trainingEligible"), bool):
        return freeze.get("trainingEligible")
    return None


def _playable_before_semantics(row: Dict[str, Any], release_blocked: bool = False) -> bool:
    tags = _tags(row)
    explicit = bool(
        row.get("playable") is True
        or row.get("playablePick") is True
        or row.get("actionablePick") is True
        or row.get("recommendationStatus") == "PLAYABLE_PREDICTION"
    )
    tag_approved = "ACTIONABLE_PICK" in tags or "PLAYABLE_PREDICTION" in tags or "ML_CONFIRMED" in tags
    legacy_not_playable = "NOT_PLAYABLE" in tags or "ML_REJECTED" in tags
    if release_blocked:
        return False
    # Preserve the legacy contract in which an explicit actionable/playable
    # boolean wins over stale display tags. New release blocks are authoritative.
    return bool(explicit or (tag_approved and not legacy_not_playable))


def _normalize_row(row: Dict[str, Any], result_locked: bool) -> Dict[str, Any]:
    out = dict(row or {})
    tags = _tags(out)
    has_winner = bool(out.get("predictedWinner"))
    locked = _row_locked(out, result_locked)
    official = bool(has_winner and locked)
    release_block_reasons = _release_block_reasons(out)
    blocked = bool(has_winner and _release_blocked_before_semantics(out))
    playable = bool(has_winner and _playable_before_semantics(out, blocked))
    training_eligible = _training_eligible_before_semantics(out)
    official_accuracy_eligible = official
    settlement_eligible = official
    playable_accuracy_eligible = bool(official and playable)

    out.update(
        {
            "predictionRequired": True,
            "requiredGameWinnerPrediction": has_winner,
            "winnerPredictionAvailable": has_winner,
            "displayPrediction": has_winner,
            "platformPick": has_winner,
            "customerVisibleWinnerPick": has_winner,
            "officialPrediction": official,
            "officialPick": official,
            "isOfficialDisplayPick": official,
            "officialPredictionStatus": (
                "OFFICIAL_LOCKED_PREDICTION"
                if official
                else "PRE_LOCK_PLATFORM_PREDICTION"
                if has_winner
                else "MISSING_REQUIRED_PREDICTION"
            ),
            "officialPredictionReason": (
                "immutable_slate_locked_winner_prediction"
                if official
                else "prediction_not_yet_slate_locked"
                if has_winner
                else "missing_predicted_winner"
            ),
            "playable": playable,
            "playablePick": playable,
            "actionablePick": playable,
            "blocked": blocked,
            "releaseBlocked": blocked,
            "wagerReleaseBlocked": blocked,
            "releaseBlockReasons": release_block_reasons,
            "playabilityBlockReasons": release_block_reasons,
            "officialAccuracyEligible": official_accuracy_eligible,
            "officialOutcomeAuditEligible": official_accuracy_eligible,
            "settlementEligible": settlement_eligible,
            "officialSettlementEligible": settlement_eligible,
            "playableAccuracyEligible": playable_accuracy_eligible,
            # Compatibility alias: this field has historically represented the
            # playable/actionable subset, not all immutable official predictions.
            "accuracyTargetEligible": playable,
            "playabilityStatus": "BLOCKED" if blocked else "PLAYABLE" if playable else "NOT_PLAYABLE",
            "predictionSemanticsVersion": VERSION,
        }
    )
    if training_eligible is not None:
        out["trainingEligible"] = training_eligible
        out["trainingEligibilityStatus"] = "ELIGIBLE" if training_eligible else "INELIGIBLE"
    else:
        out["trainingEligibilityStatus"] = "NOT_EVALUATED"

    if official and playable:
        out["displayGroup"] = "official_playable_prediction"
        out["recommendationStatus"] = "PLAYABLE_PREDICTION"
    elif official:
        out["displayGroup"] = "official_non_playable_prediction"
        out["recommendationStatus"] = "OFFICIAL_PREDICTION_NOT_PLAYABLE"
    elif has_winner:
        out["displayGroup"] = "pre_lock_prediction"
        out["recommendationStatus"] = "PRE_LOCK_PREDICTION"
    else:
        out["displayGroup"] = "missing_required_prediction"
        out["recommendationStatus"] = "MISSING_REQUIRED_PREDICTION"

    if official:
        tags.update({"OFFICIAL_PREDICTION", "OFFICIAL_LOCKED_PREDICTION"})
        tags.discard("NON_OFFICIAL_PREDICTION_DISPLAY")
    else:
        tags.discard("OFFICIAL_LOCKED_PREDICTION")

    if playable:
        tags.update({"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})
        tags.difference_update({
            "NOT_PLAYABLE",
            "LOW_CONFIDENCE_PREDICTION",
            "OFFICIAL_PREDICTION_NOT_PLAYABLE",
            "RELEASE_BLOCKED",
            "WAGER_RELEASE_BLOCKED",
        })
    else:
        tags.update({"NOT_PLAYABLE"})
        tags.difference_update({"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})
        if official:
            tags.add("OFFICIAL_PREDICTION_NOT_PLAYABLE")
        if blocked:
            tags.update({"RELEASE_BLOCKED", "WAGER_RELEASE_BLOCKED"})

    out["tags"] = sorted(tags)
    return out


def _card(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "gameId": row.get("gameId"),
        "gameKey": row.get("gameKey"),
        "homeTeam": row.get("homeTeam"),
        "awayTeam": row.get("awayTeam"),
        "commenceTime": row.get("commenceTime"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "confidenceTier": row.get("confidenceTier"),
        "teamWinProbabilityPct": row.get("teamWinProbabilityPct", row.get("winProbabilityPct")),
        "mlPickReliabilityPct": row.get("mlPickReliabilityPct"),
        "score": row.get("score"),
        "rank": row.get("rank"),
        "officialPrediction": bool(row.get("officialPrediction")),
        "officialPick": bool(row.get("officialPick")),
        "playable": bool(row.get("playable")),
        "playablePick": bool(row.get("playablePick")),
        "blocked": bool(row.get("blocked")),
        "releaseBlocked": bool(row.get("releaseBlocked")),
        "releaseBlockReasons": row.get("releaseBlockReasons") or [],
        "trainingEligible": row.get("trainingEligible"),
        "trainingEligibilityStatus": row.get("trainingEligibilityStatus"),
        "officialAccuracyEligible": bool(row.get("officialAccuracyEligible")),
        "settlementEligible": bool(row.get("settlementEligible")),
        "playableAccuracyEligible": bool(row.get("playableAccuracyEligible")),
        "officialPredictionStatus": row.get("officialPredictionStatus"),
        "playabilityStatus": row.get("playabilityStatus"),
        "recommendationStatus": row.get("recommendationStatus"),
        "actionability": row.get("actionability"),
        "actionabilityReason": row.get("actionabilityReason"),
        "riskReasons": row.get("actionabilityRiskReasons") or [],
        "tags": row.get("tags") or [],
    }


def _update_summary(summary: Any, counts: Dict[str, int]) -> Dict[str, Any]:
    out = dict(summary or {}) if isinstance(summary, dict) else {}
    out.update(counts)
    out["predictionSemanticsVersion"] = VERSION
    out["officialPredictionMeaning"] = "immutable winner selected for a slate-locked game"
    out["playableMeaning"] = "higher-confidence wagering recommendation; separate from official prediction"
    out["blockedMeaning"] = "wagering release is blocked; the immutable winner remains official"
    out["trainingEligibleMeaning"] = "lock-time feature provenance qualifies the row for training; independent of wagering release"
    out["accuracyTargetEligibleMeaning"] = "legacy playable/actionable accuracy subset"
    out["settlementEligibleMeaning"] = "immutable official prediction eligible to receive a final outcome"
    return out


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = [row for row in (result.get("predictions") or []) if isinstance(row, dict)]
    result_locked = _result_locked(result)
    normalized = [_normalize_row(row, result_locked) for row in rows]

    official_rows = [row for row in normalized if row.get("officialPrediction") is True]
    playable_rows = [row for row in normalized if row.get("playable") is True]
    blocked_rows = [row for row in normalized if row.get("blocked") is True]
    blocked_official_rows = [
        row for row in normalized
        if row.get("officialPrediction") is True and row.get("blocked") is True
    ]
    official_accuracy_rows = [
        row for row in normalized if row.get("officialAccuracyEligible") is True
    ]
    settlement_rows = [
        row for row in normalized if row.get("settlementEligible") is True
    ]
    playable_accuracy_rows = [
        row for row in normalized if row.get("playableAccuracyEligible") is True
    ]
    training_rows = [
        row for row in normalized if row.get("trainingEligible") is True
    ]
    training_official_rows = [
        row for row in normalized
        if row.get("officialPrediction") is True and row.get("trainingEligible") is True
    ]
    non_playable_official_rows = [
        row for row in normalized if row.get("officialPrediction") is True and row.get("playable") is not True
    ]
    pre_lock_rows = [
        row for row in normalized if row.get("predictedWinner") and row.get("officialPrediction") is not True
    ]
    missing_rows = [row for row in normalized if not row.get("predictedWinner")]

    counts = {
        "officialPredictionCount": len(official_rows),
        "officialPickCount": len(official_rows),
        "playablePredictionCount": len(playable_rows),
        "actionablePickCount": len(playable_rows),
        "blockedPredictionCount": len(blocked_rows),
        "blockedOfficialPredictionCount": len(blocked_official_rows),
        "officialAccuracyEligibleCount": len(official_accuracy_rows),
        "settlementEligibleCount": len(settlement_rows),
        "playableAccuracyEligibleCount": len(playable_accuracy_rows),
        "trainingEligiblePredictionCount": len(training_rows),
        "trainingEligibleOfficialPredictionCount": len(training_official_rows),
        "nonPlayableOfficialPredictionCount": len(non_playable_official_rows),
        "preLockPredictionCount": len(pre_lock_rows),
        "missingRequiredPredictionCount": len(missing_rows),
        "lowConfidencePredictionCount": len(non_playable_official_rows) + len(pre_lock_rows),
    }

    out = dict(result)
    out["predictions"] = normalized
    out.update(counts)
    out["noPickCount"] = len(missing_rows)
    out["requiredGameWinnerPredictionCount"] = len([row for row in normalized if row.get("predictedWinner")])
    out["displayPredictionCount"] = out["requiredGameWinnerPredictionCount"]
    out["allGamesHaveDisplayedWinnerPrediction"] = bool(normalized and not missing_rows)
    out["predictionSemantics"] = {
        "applied": True,
        "version": VERSION,
        "officialPredictionMeaning": "immutable winner selected for a slate-locked game",
        "playableMeaning": "higher-confidence wagering recommendation; separate from official prediction",
        "blockedMeaning": "wagering release is blocked; the immutable winner remains official",
        "trainingEligibleMeaning": "lock-time feature provenance qualifies the row for training; independent of wagering release",
        "accuracyTargetEligibleMeaning": "legacy playable/actionable accuracy subset",
        "settlementEligibleMeaning": "immutable official prediction eligible to receive a final outcome",
        "laterRiskGatesMayChangePlayability": True,
        "laterRiskGatesMayChangeBlocked": True,
        "laterRiskGatesMayEraseOfficialLockedPrediction": False,
        **counts,
    }
    out["requiredWinnerPredictionDisplay"] = [_card(row) for row in normalized if row.get("predictedWinner")]
    out["officialPredictionDisplay"] = [_card(row) for row in official_rows]
    out["playablePredictionDisplay"] = [_card(row) for row in playable_rows]
    out["blockedPredictionDisplay"] = [_card(row) for row in blocked_rows]
    out["nonPlayableOfficialPredictionDisplay"] = [_card(row) for row in non_playable_official_rows]
    out["nonOfficialPredictionDisplay"] = [_card(row) for row in pre_lock_rows]

    for key in ("winnerStackV2", "rolling24hAccuracyTarget", "accuracyTarget", "signalPolicyV13", "directionalScoreV1"):
        if key in out or key in {"rolling24hAccuracyTarget", "accuracyTarget"}:
            out[key] = _update_summary(out.get(key), counts)
    ml_summary = out.get("mlOverlay")
    if isinstance(ml_summary, dict):
        out["mlOverlay"] = _update_summary(ml_summary, counts)
    stack = out.get("winnerStackV2")
    if isinstance(stack, dict) and isinstance(stack.get("mlOverlay"), dict):
        stack["mlOverlay"] = _update_summary(stack.get("mlOverlay"), counts)
        out["winnerStackV2"] = stack
    for key in ("rolling24hAccuracyTarget", "accuracyTarget"):
        summary = out.get(key)
        if isinstance(summary, dict) and isinstance(summary.get("mlOverlay"), dict):
            summary["mlOverlay"] = _update_summary(summary.get("mlOverlay"), counts)
            out[key] = summary

    if VERSION not in str(out.get("modelVersion") or ""):
        out["modelVersion"] = str(out.get("modelVersion") or "") + "+" + VERSION
    return out


def _store_final(module: Any, result: Dict[str, Any], requested: bool) -> Dict[str, Any]:
    if not requested or not isinstance(result, dict) or not hasattr(module, "_store_prediction"):
        return result
    stored_count = 0
    errors: List[str] = []
    for row in result.get("predictions") or []:
        if not isinstance(row, dict):
            continue
        try:
            stored = module._store_prediction(row)
            row["officialSemanticsStore"] = stored
            if isinstance(stored, dict) and stored.get("ok"):
                stored_count += 1
            else:
                errors.append(str(stored))
        except Exception as exc:
            row["officialSemanticsStoreError"] = str(exc)
            errors.append(str(exc))
    result["officialSemanticsStoredCount"] = stored_count
    result["officialSemanticsStoreErrors"] = errors
    return result


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_OFFICIAL_PREDICTION_SEMANTICS_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        result = enhance_result(original(*args, **kwargs))
        return _store_final(module, result, bool(kwargs.get("store")))

    module.predict_all = patched_predict_all
    module._INQSI_MLB_OFFICIAL_PREDICTION_SEMANTICS_APPLIED = True
    return module
