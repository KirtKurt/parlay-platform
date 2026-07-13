from __future__ import annotations

from typing import Any, Dict

VERSION = "MLB-INDIVIDUAL-GAME-LOCK-PROBABILITY-POLICY-v1-60pct-floor"
MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT = 60.0


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
        if 0.0 < parsed <= 1.0:
            parsed *= 100.0
        return parsed
    except Exception:
        return None


def selected_team_probability_pct(row: Dict[str, Any]) -> float | None:
    """Return the selected team's lock-time probability using modern semantics.

    A directional risk cap is authoritative when present. Otherwise use the
    selected-team probability, then the generic winner probability, and finally
    the selected signal's de-vigged market probability.
    """
    capped = _number(row.get("cappedWinProbabilityPct"))
    direct = _number(row.get("teamWinProbabilityPct"))
    if direct is None:
        direct = _number(row.get("winProbabilityPct"))
    if direct is None:
        direct = _number(row.get("winProbability"))

    side = str(row.get("predictedSide") or "").lower()
    signal = row.get("homeSignal") if side == "home" else row.get("awaySignal") if side == "away" else None
    signal = signal if isinstance(signal, dict) else {}
    market = _number(signal.get("marketConsensusProbability"))
    if market is None:
        market = _number(signal.get("probLatest"))

    probability = direct if direct is not None else market
    if probability is None:
        return None
    if capped is not None:
        probability = min(probability, capped)
    return round(max(0.0, min(100.0, probability)), 2)


def lock_eligible(row: Dict[str, Any]) -> bool:
    probability = selected_team_probability_pct(row)
    return bool(probability is not None and probability >= MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT)


def _looks_locked(row: Dict[str, Any]) -> bool:
    tags = {str(tag) for tag in (row.get("tags") or [])}
    for key in ("slatePredictionLock", "lastPossiblePredictionGate"):
        gate = row.get(key) or {}
        if isinstance(gate, dict) and (gate.get("locked") is True or gate.get("finalLocked") is True):
            return True
    return bool(
        row.get("lockedPrediction") is True
        or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"
        or "FINAL_LOCKED" in tags
        or "PER_GAME_TMINUS45_LOCKED" in tags
        or "SLATE_LOCKED" in tags
    )


def apply_lock_floor(row: Dict[str, Any], locked: bool | None = None) -> Dict[str, Any]:
    out = dict(row or {})
    locked_now = _looks_locked(out) if locked is None else bool(locked)
    has_winner = bool(out.get("predictedWinner"))
    probability = selected_team_probability_pct(out)
    eligible = bool(
        locked_now
        and has_winner
        and probability is not None
        and probability >= MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT
    )

    out["individualGameLockPolicyVersion"] = VERSION
    out["individualGameLockMinimumProbabilityPct"] = MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT
    out["individualGameLockProbabilityPct"] = probability
    out["individualGameLockEligible"] = eligible

    tags = {str(tag) for tag in (out.get("tags") or [])}
    if not locked_now or not has_winner:
        out["tags"] = sorted(tags)
        return out

    if eligible:
        out["officialPrediction"] = True
        out["officialPick"] = True
        out["isOfficialDisplayPick"] = True
        out["officialPredictionStatus"] = "OFFICIAL_LOCKED_PREDICTION"
        out["officialPredictionReason"] = "selected_team_probability_met_60pct_individual_game_lock_floor"
        tags.update({
            "OFFICIAL_PREDICTION",
            "OFFICIAL_LOCKED_PREDICTION",
            "INDIVIDUAL_GAME_LOCK_60PCT_ELIGIBLE",
        })
        tags.discard("BELOW_60PCT_GAME_LOCK_FLOOR")
        tags.discard("LOCKED_DIAGNOSTIC_BELOW_60PCT")
        if out.get("playable") is not True and out.get("actionablePick") is not True:
            out["displayGroup"] = "official_non_playable_prediction"
            out["recommendationStatus"] = "OFFICIAL_PREDICTION_NOT_PLAYABLE"
    else:
        out["officialPrediction"] = False
        out["officialPick"] = False
        out["isOfficialDisplayPick"] = False
        out["playable"] = False
        out["playablePick"] = False
        out["actionablePick"] = False
        out["accuracyTargetEligible"] = False
        out["officialPredictionStatus"] = "LOCKED_DIAGNOSTIC_BELOW_60PCT"
        out["officialPredictionReason"] = "selected_team_probability_below_60pct_individual_game_lock_floor"
        out["playabilityStatus"] = "NOT_PLAYABLE"
        out["displayGroup"] = "locked_diagnostic_below_60pct"
        out["recommendationStatus"] = "LOCKED_PREDICTION_BELOW_60PCT_NOT_OFFICIAL"
        out["actionability"] = "BELOW_60PCT_GAME_LOCK_FLOOR"
        out["actionabilityReason"] = "individual_game_official_lock_requires_selected_team_probability_at_or_above_60pct"
        risks = list(out.get("actionabilityRiskReasons") or [])
        risks.append("selected_team_probability_below_60pct_lock_floor")
        out["actionabilityRiskReasons"] = sorted(set(str(reason) for reason in risks))
        tags.update({"NOT_PLAYABLE", "BELOW_60PCT_GAME_LOCK_FLOOR", "LOCKED_DIAGNOSTIC_BELOW_60PCT"})
        tags.difference_update({
            "OFFICIAL_PREDICTION",
            "OFFICIAL_LOCKED_PREDICTION",
            "ACTIONABLE_PICK",
            "PLAYABLE_PREDICTION",
            "INDIVIDUAL_GAME_LOCK_60PCT_ELIGIBLE",
        })

    out["tags"] = sorted(tags)
    return out


def install() -> Dict[str, Any]:
    patched = []
    warnings = []

    try:
        import mlb_official_prediction_semantics as semantics

        if not getattr(semantics, "_INQSI_MLB_60PCT_GAME_LOCK_FLOOR_APPLIED", False):
            original_normalize = semantics._normalize_row

            def normalize_with_probability_floor(row, result_locked):
                return apply_lock_floor(original_normalize(row, result_locked), locked=bool(result_locked or _looks_locked(row)))

            semantics._normalize_row = normalize_with_probability_floor
            semantics.MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT = MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT
            semantics.INDIVIDUAL_GAME_LOCK_PROBABILITY_POLICY_VERSION = VERSION
            semantics._INQSI_MLB_60PCT_GAME_LOCK_FLOOR_APPLIED = True
        patched.append("official_prediction_semantics_60pct_floor")
    except Exception as exc:
        warnings.append(f"official_prediction_semantics:{exc}")

    try:
        import mlb_directional_score_v1 as directional

        if not getattr(directional, "_INQSI_MLB_DIRECTIONAL_60PCT_FLOOR_APPLIED", False):
            original_official = directional._official

            def official_with_probability_floor(row, score, cap):
                allowed, reasons = original_official(row, score, cap)
                probability = selected_team_probability_pct(row)
                if probability is None or probability < MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT:
                    reasons = sorted(set(list(reasons or []) + ["selected_team_probability_below_60pct_lock_floor"]))
                    return False, reasons
                return allowed, reasons

            directional._official = official_with_probability_floor
            directional.MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT = MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT
            directional._INQSI_MLB_DIRECTIONAL_60PCT_FLOOR_APPLIED = True
        patched.append("directional_gate_60pct_floor")
    except Exception as exc:
        warnings.append(f"directional_score:{exc}")

    try:
        import mlb_daily_per_game_lock_patch as per_game_lock

        if not getattr(per_game_lock, "_INQSI_MLB_PER_GAME_60PCT_LOCK_FLOOR_APPLIED", False):
            original_prepare = per_game_lock._prepare_row

            def prepare_with_probability_floor(*args, **kwargs):
                prepared = apply_lock_floor(original_prepare(*args, **kwargs), locked=True)
                lock = dict(prepared.get("slatePredictionLock") or {})
                lock.update({
                    "individualGameLockMinimumProbabilityPct": MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT,
                    "individualGameLockProbabilityPct": prepared.get("individualGameLockProbabilityPct"),
                    "confidenceQualifiedOfficialLock": prepared.get("individualGameLockEligible") is True,
                    "probabilityPolicyVersion": VERSION,
                })
                prepared["slatePredictionLock"] = lock
                audit = dict(prepared.get("lockedCardAudit") or {})
                audit.update({
                    "individualGameLockMinimumProbabilityPct": MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT,
                    "individualGameLockProbabilityPct": prepared.get("individualGameLockProbabilityPct"),
                    "confidenceQualifiedOfficialLock": prepared.get("individualGameLockEligible") is True,
                    "probabilityPolicyVersion": VERSION,
                })
                prepared["lockedCardAudit"] = audit
                return prepared

            per_game_lock._prepare_row = prepare_with_probability_floor
            per_game_lock.MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT = MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT
            per_game_lock._INQSI_MLB_PER_GAME_60PCT_LOCK_FLOOR_APPLIED = True
        patched.append("per_game_lock_60pct_floor")
    except Exception as exc:
        warnings.append(f"per_game_lock:{exc}")

    try:
        import mlb_locked_prediction_storage_finalizer_v1 as finalizer

        if not getattr(finalizer, "_INQSI_MLB_STORAGE_60PCT_LOCK_FLOOR_APPLIED", False):
            original_prepare_result = finalizer._prepare_locked_result

            def prepare_locked_result_with_probability_floor(result):
                out = original_prepare_result(result)
                out["predictions"] = [
                    apply_lock_floor(row, locked=True) if isinstance(row, dict) else row
                    for row in (out.get("predictions") or [])
                ]
                try:
                    import mlb_official_prediction_semantics as semantics
                    out = semantics.enhance_result(out)
                except Exception:
                    pass
                return out

            finalizer._prepare_locked_result = prepare_locked_result_with_probability_floor
            finalizer.MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT = MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT
            finalizer._INQSI_MLB_STORAGE_60PCT_LOCK_FLOOR_APPLIED = True
        patched.append("locked_storage_finalizer_60pct_floor")
    except Exception as exc:
        warnings.append(f"locked_storage_finalizer:{exc}")

    return {
        "ok": not warnings,
        "version": VERSION,
        "minimumIndividualGameLockProbabilityPct": MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT,
        "belowFloorRowsRemainVisible": True,
        "belowFloorRowsRemainImmutableDiagnostics": True,
        "belowFloorRowsAreOfficial": False,
        "patched": patched,
        "warnings": warnings,
        "policy": (
            "An MLB game prediction becomes an official individual-game locked pick only when the selected team's "
            "lock-time probability is at least 60%. Lower-probability rows remain visible and immutably stored for "
            "audit and learning, but are not official, actionable, or accuracy-target eligible."
        ),
    }
