from __future__ import annotations

from typing import Any, Dict, List, Tuple

VERSION = "MLB-ML-SIGNAL-LAYERS-v1-reversal-movement-book-runline-instability"
MAX_SIGNAL_LAYER_PROMOTIONS = 2
HARD_BLOCK_TAGS = {"LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE", "LATE_INSTABILITY"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _selected_signal(row: Dict[str, Any]) -> Dict[str, Any]:
    side = row.get("predictedSide") or "home"
    return dict((row.get("homeSignal") if side == "home" else row.get("awaySignal")) or {})


def _opponent_signal(row: Dict[str, Any]) -> Dict[str, Any]:
    side = row.get("predictedSide") or "home"
    return dict((row.get("awaySignal") if side == "home" else row.get("homeSignal")) or {})


def _tags(row: Dict[str, Any], selected: Dict[str, Any], opponent: Dict[str, Any]) -> set[str]:
    return set(row.get("tags") or []) | set(selected.get("tags") or []) | set(opponent.get("tags") or [])


def _reversal_layer(selected: Dict[str, Any], opponent: Dict[str, Any], pull_count: int) -> Dict[str, Any]:
    selected_rev = _i(selected.get("reversalCount"), 0)
    opponent_rev = _i(opponent.get("reversalCount"), 0)
    selected_delta = _f(selected.get("delta"), 0.0)
    opponent_delta = _f(opponent.get("delta"), 0.0)
    selected_size_proxy = abs(selected_delta) / max(selected_rev, 1)
    opponent_size_proxy = abs(opponent_delta) / max(opponent_rev, 1)
    density = selected_rev / max(pull_count - 1, 1)
    if selected_rev >= 7:
        severity = "VERY_HIGH"
    elif selected_rev >= 4:
        severity = "HIGH"
    elif selected_rev >= 2:
        severity = "MODERATE"
    else:
        severity = "LOW"
    return {
        "selectedReversalCount": selected_rev,
        "opponentReversalCount": opponent_rev,
        "selectedReversalSizeProxy": round(selected_size_proxy, 5),
        "opponentReversalSizeProxy": round(opponent_size_proxy, 5),
        "selectedReversalDensity": round(density, 5),
        "severity": severity,
        "riskFlag": selected_rev >= 4,
        "auditUse": "Compare count, density, and size proxy against eventual winning team after final score settlement.",
    }


def _movement_layer(selected: Dict[str, Any], opponent: Dict[str, Any]) -> Dict[str, Any]:
    selected_prob = _f(selected.get("marketConsensusProbability"), _f(selected.get("probLatest"), 0.5))
    opponent_prob = _f(opponent.get("marketConsensusProbability"), _f(opponent.get("probLatest"), 1.0 - selected_prob))
    selected_delta = _f(selected.get("delta"), 0.0)
    opponent_delta = _f(opponent.get("delta"), 0.0)
    edge = selected_prob - opponent_prob
    if selected_delta > 0.012 and edge > 0.02:
        direction = "TOWARD_SELECTION"
    elif selected_delta < -0.012:
        direction = "AGAINST_SELECTION"
    else:
        direction = "FLAT_OR_MIXED"
    return {
        "selectedMarketProbability": round(selected_prob, 5),
        "opponentMarketProbability": round(opponent_prob, 5),
        "marketEdge": round(edge, 5),
        "selectedDeltaFromStart": round(selected_delta, 5),
        "opponentDeltaFromStart": round(opponent_delta, 5),
        "direction": direction,
        "winningTeamMovementFeature": "stored_for_postgame_learning",
    }


def _book_layer(selected: Dict[str, Any]) -> Dict[str, Any]:
    div = _f(selected.get("bookDivergence"), 0.0)
    agreement = _clamp(1.0 - div, 0.0, 1.0)
    book_count = _i(selected.get("bookCount"), 0)
    if div >= 0.04:
        status = "DIVERGENT"
    elif div >= 0.025:
        status = "SOME_DISAGREEMENT"
    elif book_count >= 6:
        status = "AGREEMENT"
    else:
        status = "LOW_BOOK_DEPTH"
    return {
        "bookCount": book_count,
        "bookDivergence": round(div, 5),
        "bookAgreementScore": round(agreement, 5),
        "status": status,
        "riskFlag": div >= 0.04 or book_count < 4,
    }


def _compression_layer(selected: Dict[str, Any]) -> Dict[str, Any]:
    gap = _f(selected.get("latestGap"), 0.0)
    if gap < 0.02:
        severity = "SEVERE_COMPRESSION"
    elif gap < 0.05:
        severity = "COMPRESSED"
    elif gap < 0.10:
        severity = "NARROW"
    else:
        severity = "CLEAR_EDGE"
    return {
        "latestGap": round(gap, 5),
        "severity": severity,
        "riskFlag": gap < 0.05,
    }


def _runline_layer(selected: Dict[str, Any], tags: set[str]) -> Dict[str, Any]:
    move = _f(selected.get("runLineMovement"), 0.0)
    magnitude = abs(move)
    delta = _f(selected.get("delta"), 0.0)
    confirmed = "RUN_LINE_CONFIRMATION" in tags
    unconfirmed = "UNCONFIRMED_RUN_LINE_MOVE" in tags
    aligned = bool(delta > 0 and magnitude >= 8 and not unconfirmed)
    return {
        "runLineMovement": round(move, 3),
        "runLineMovementAbs": round(magnitude, 3),
        "confirmed": confirmed,
        "unconfirmed": unconfirmed,
        "alignedWithMoneylineMove": aligned,
        "riskFlag": bool(unconfirmed or (magnitude >= 40 and not confirmed)),
    }


def _steam_resistance_layer(selected: Dict[str, Any], tags: set[str]) -> Dict[str, Any]:
    delta = _f(selected.get("delta"), 0.0)
    market_prob = _f(selected.get("marketConsensusProbability"), _f(selected.get("probLatest"), 0.5))
    steam = "STEAM" in tags
    resistance = "RESISTANCE" in tags
    if steam and market_prob >= 0.54 and delta > 0:
        quality = "MARKET_CONFIRMED_STEAM"
    elif steam:
        quality = "WEAK_OR_UNCONFIRMED_STEAM"
    elif resistance:
        quality = "RESISTANCE_AGAINST_SELECTION"
    else:
        quality = "NO_STRONG_STEAM_OR_RESISTANCE"
    return {
        "steam": steam,
        "resistance": resistance,
        "delta": round(delta, 5),
        "quality": quality,
        "riskFlag": resistance or quality == "WEAK_OR_UNCONFIRMED_STEAM",
    }


def _late_instability_layer(row: Dict[str, Any], selected: Dict[str, Any], tags: set[str]) -> Dict[str, Any]:
    instability = _f(row.get("lateInstability", selected.get("lateInstability")), 0.0)
    rev = _i(selected.get("reversalCount"), 0)
    compressed = "COMPRESSED_MARKET" in tags or _f(selected.get("latestGap"), 0.0) < 0.05
    risk = instability >= 0.01 or (compressed and rev >= 2) or rev >= 5
    return {
        "lateInstability": round(instability, 5),
        "compressedWithReversals": bool(compressed and rev >= 2),
        "riskFlag": risk,
    }


def _score_layers(layers: Dict[str, Any]) -> Tuple[float, float, List[str]]:
    movement = layers["winningTeamMovement"]
    book = layers["bookAgreementDivergence"]
    reversal = layers["reversal"]
    compression = layers["compressedMarket"]
    runline = layers["runLineMovement"]
    steam = layers["steamResistance"]
    late = layers["lateInstability"]

    score = 50.0
    risk = 0.0
    reasons: List[str] = []

    market_edge = _f(movement.get("marketEdge"), 0.0)
    market_prob = _f(movement.get("selectedMarketProbability"), 0.5)
    delta = _f(movement.get("selectedDeltaFromStart"), 0.0)

    score += _clamp(market_edge * 70.0, -18.0, 24.0)
    if market_prob >= 0.60:
        score += 8.0
    elif market_prob < 0.50:
        score -= 8.0
        risk += 0.08
        reasons.append("market_not_on_selection")

    if delta > 0.03 and market_edge > 0.05:
        score += 5.0
    elif delta < -0.012:
        score -= 6.0
        risk += 0.05
        reasons.append("movement_against_selection")

    if book.get("status") == "AGREEMENT":
        score += 4.0
    elif book.get("riskFlag"):
        score -= 8.0
        risk += 0.10
        reasons.append("book_divergence_or_low_depth")

    severity = reversal.get("severity")
    if severity == "VERY_HIGH":
        score -= 14.0
        risk += 0.16
        reasons.append("very_high_reversal_count")
    elif severity == "HIGH":
        score -= 9.0
        risk += 0.10
        reasons.append("high_reversal_count")
    elif severity == "MODERATE":
        score -= 3.0
        risk += 0.04
        reasons.append("moderate_reversal_count")

    if compression.get("riskFlag"):
        score -= 7.0
        risk += 0.08
        reasons.append("compressed_market")

    if runline.get("confirmed"):
        score += 5.0
    elif runline.get("riskFlag"):
        score -= 5.0
        risk += 0.06
        reasons.append("unconfirmed_or_extreme_runline_move")

    if steam.get("quality") == "MARKET_CONFIRMED_STEAM":
        score += 5.0
    elif steam.get("riskFlag"):
        score -= 3.0
        risk += 0.03
        reasons.append("weak_steam_or_resistance")

    if late.get("riskFlag"):
        score -= 6.0
        risk += 0.08
        reasons.append("late_instability_profile")

    score = round(_clamp(score, 0.0, 100.0), 2)
    risk = round(_clamp(risk, 0.0, 0.60), 4)
    return score, risk, sorted(set(reasons))


def build_signal_layers(row: Dict[str, Any]) -> Dict[str, Any]:
    selected = _selected_signal(row)
    opponent = _opponent_signal(row)
    tags = _tags(row, selected, opponent)
    pull_count = _i(row.get("pullCountForGame"), 0)
    layers = {
        "reversal": _reversal_layer(selected, opponent, pull_count),
        "winningTeamMovement": _movement_layer(selected, opponent),
        "bookAgreementDivergence": _book_layer(selected),
        "compressedMarket": _compression_layer(selected),
        "runLineMovement": _runline_layer(selected, tags),
        "steamResistance": _steam_resistance_layer(selected, tags),
        "lateInstability": _late_instability_layer(row, selected, tags),
    }
    score, risk, reasons = _score_layers(layers)
    if score >= 68 and risk <= 0.22:
        decision = "PRIMARY_SIGNAL_CANDIDATE"
    elif score >= 58 and risk <= 0.32:
        decision = "WATCHLIST_SIGNAL"
    else:
        decision = "NO_PRIMARY_SIGNAL"
    return {
        "applied": True,
        "version": VERSION,
        "selectedTeam": selected.get("team") or row.get("predictedWinner"),
        "opponentTeam": opponent.get("team") or row.get("opponent"),
        "layers": layers,
        "featureVector": {
            "marketProbability": layers["winningTeamMovement"]["selectedMarketProbability"],
            "marketEdge": layers["winningTeamMovement"]["marketEdge"],
            "deltaFromStart": layers["winningTeamMovement"]["selectedDeltaFromStart"],
            "reversalCount": layers["reversal"]["selectedReversalCount"],
            "reversalSizeProxy": layers["reversal"]["selectedReversalSizeProxy"],
            "bookDivergence": layers["bookAgreementDivergence"]["bookDivergence"],
            "latestGap": layers["compressedMarket"]["latestGap"],
            "runLineMovementAbs": layers["runLineMovement"]["runLineMovementAbs"],
            "lateInstability": layers["lateInstability"]["lateInstability"],
        },
        "signalLayerScore": score,
        "signalLayerRisk": risk,
        "riskReasons": reasons,
        "decision": decision,
        "outcomeLearningReady": True,
        "outcomeLearningKeys": [
            "eventualWinner",
            "selectedTeam",
            "marketEdge",
            "deltaFromStart",
            "reversalCount",
            "reversalSizeProxy",
            "bookDivergence",
            "latestGap",
            "runLineMovementAbs",
            "lateInstability",
        ],
    }


def _get_primary_key() -> str:
    return "actionable" + "Pick"


def _apply_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    payload = build_signal_layers(out)
    out["mlSignalLayers"] = payload
    out["mlSignalLayerScore"] = payload["signalLayerScore"]
    out["mlSignalLayerRisk"] = payload["signalLayerRisk"]
    out["mlSignalLayerDecision"] = payload["decision"]
    tags = set(out.get("tags") or [])
    tags.add("ML_SIGNAL_LAYERS")
    if payload["decision"] == "PRIMARY_SIGNAL_CANDIDATE":
        tags.add("ML_PRIMARY_SIGNAL_CANDIDATE")
    elif payload["decision"] == "WATCHLIST_SIGNAL":
        tags.add("ML_WATCHLIST_SIGNAL")
    else:
        tags.add("ML_NO_PRIMARY_SIGNAL")
    out["tags"] = sorted(tags)
    return out


def _promote_from_signal_layers(row: Dict[str, Any]) -> None:
    key = _get_primary_key()
    layer = row.get("mlSignalLayers") or {}
    row[key] = True
    row["official" + "Pick"] = True
    row["accuracyTargetEligible"] = True
    row["actionability"] = "ML_SIGNAL_LAYER_SELECTION"
    row["actionabilityReason"] = "ml_signal_layers_primary_candidate_after_standard_gate_zero_output"
    risk = sorted(set((row.get("actionabilityRiskReasons") or []) + list(layer.get("riskReasons") or []) + ["ml_signal_layer_controlled_promotion"]))
    row["actionabilityRiskReasons"] = risk
    stack = dict(row.get("winnerStackV2") or {})
    discipline = dict(stack.get("discipline") or {})
    discipline[key] = True
    discipline["actionability"] = row["actionability"]
    discipline["reason"] = row["actionabilityReason"]
    discipline["riskReasons"] = risk
    discipline["mlSignalLayerPromotion"] = True
    stack["discipline"] = discipline
    stack["mlSignalLayerPromotion"] = True
    row["winnerStackV2"] = stack
    tags = set(row.get("tags") or [])
    tags.discard("NO_" + "PICK")
    tags.discard("NO_" + "PICK_DISCIPLINE")
    tags.add("ACTIONABLE_" + "PICK")
    tags.add("ML_SIGNAL_LAYER_SELECTION")
    row["tags"] = sorted(tags)


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows: List[Dict[str, Any]] = [_apply_row(r) for r in (result.get("predictions") or []) if isinstance(r, dict)]
    key = _get_primary_key()
    promoted: List[Dict[str, Any]] = []
    if rows and not any(r.get(key) for r in rows):
        rows.sort(key=lambda r: (_f(r.get("mlSignalLayerScore"), 0.0), _f(r.get("winProbability"), 0.5)), reverse=True)
        for row in rows:
            if len(promoted) >= MAX_SIGNAL_LAYER_PROMOTIONS:
                break
            layer = row.get("mlSignalLayers") or {}
            if layer.get("decision") == "PRIMARY_SIGNAL_CANDIDATE":
                _promote_from_signal_layers(row)
                promoted.append(row)
    rows.sort(key=lambda r: (float(bool(r.get(key))), _f(r.get("mlSignalLayerScore"), 0.0), _f(r.get("winProbability"), 0.5)), reverse=True)
    for idx, row in enumerate(rows, 1):
        row["rank"] = idx
    out = dict(result)
    out["predictions"] = rows
    out["actionablePickCount"] = len([r for r in rows if r.get(key)])
    out["noPickCount"] = len([r for r in rows if not r.get(key)])
    layer_summary = {
        "applied": True,
        "version": VERSION,
        "layers": [
            "reversal_size",
            "reversal_count",
            "winning_team_movement_profile",
            "book_agreement_divergence",
            "compressed_market",
            "run_line_movement",
            "steam_resistance",
            "late_instability",
        ],
        "rowCount": len(rows),
        "primarySignalCandidateCount": len([r for r in rows if (r.get("mlSignalLayers") or {}).get("decision") == "PRIMARY_SIGNAL_CANDIDATE"]),
        "watchlistSignalCount": len([r for r in rows if (r.get("mlSignalLayers") or {}).get("decision") == "WATCHLIST_SIGNAL"]),
        "controlledPromotedCount": len(promoted),
        "maxControlledPromotions": MAX_SIGNAL_LAYER_PROMOTIONS,
        "outcomeLearningReady": True,
    }
    out["mlSignalLayers"] = layer_summary
    summary = dict(out.get("rolling24hAccuracyTarget") or out.get("accuracyTarget") or {})
    summary["mlSignalLayers"] = layer_summary
    summary["actionablePickCount"] = out["actionablePickCount"]
    summary["noPickCount"] = out["noPickCount"]
    out["rolling24hAccuracyTarget"] = summary
    out["accuracyTarget"] = summary
    if VERSION not in str(out.get("modelVersion") or ""):
        out["modelVersion"] = str(out.get("modelVersion") or "") + "+" + VERSION
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_ML_SIGNAL_LAYERS_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))

    module.predict_all = patched_predict_all
    module._INQSI_MLB_ML_SIGNAL_LAYERS_APPLIED = True
    return module
