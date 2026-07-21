from __future__ import annotations

import copy
import math
from typing import Any, Dict, Optional, Tuple

import inqsi_pull_history as history


VERSION = "MLB-PREDICTION-PROBABILITY-CONTRACT-v1-canonical-model-direction"
MODEL_PROBABILITY_VERSION = "MLB-MODEL-WIN-PROBABILITY-v1-complementary-home-away"
MARKET_PROBABILITY_VERSION = "MLB-MARKET-DEVIG-BASELINE-v1-canonical-pull-slot"
CORRECTION_REASON = "probability_direction_integrity_correction"
PRICE_REASON = "selected_side_price_binding_unavailable"
LEGACY_SUPPRESSION_REASON = "legacy_probability_contract_missing"


def _number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _probability(value: Any) -> Optional[float]:
    parsed = _number(value)
    if parsed is None:
        return None
    parsed = parsed / 100.0 if parsed > 1.0 else parsed
    return parsed if 0.0 <= parsed <= 1.0 else None


def _pair(home: Optional[float], away: Optional[float]) -> Tuple[float, float]:
    if home is None and away is None:
        return 0.5, 0.5
    if home is None:
        home = 1.0 - float(away)
    if away is None:
        away = 1.0 - float(home)
    home = min(max(float(home), 0.000001), 0.999999)
    away = min(max(float(away), 0.000001), 0.999999)
    total = home + away
    normalized_home = round(home / total, 12)
    return normalized_home, round(1.0 - normalized_home, 12)


def _signal(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    value = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return value if isinstance(value, dict) else {}


def _signal_probability(signal: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = _probability(signal.get(key))
        if value is not None:
            return value
    return None


def _model_pair(row: Dict[str, Any]) -> Tuple[float, float, str]:
    runtime = row.get("mlOptimizationRuntime") or {}
    direction_authority = runtime.get("directionAuthorityEnabled") is True
    if direction_authority:
        home = _probability(row.get("outcomeModelHomeWinProbabilityPct"))
        away = _probability(row.get("outcomeModelAwayWinProbabilityPct"))
        if home is not None or away is not None:
            return (*_pair(home, away), "promoted_outcome_model")

    explicit_home = _probability(row.get("homeModelWinProbability"))
    explicit_away = _probability(row.get("awayModelWinProbability"))
    if explicit_home is not None or explicit_away is not None:
        return (*_pair(explicit_home, explicit_away), "persisted_model_probability_pair")

    home = _signal_probability(
        _signal(row, "home"),
        "modelWinProbability",
        "winProbability",
    )
    away = _signal_probability(
        _signal(row, "away"),
        "modelWinProbability",
        "winProbability",
    )
    return (*_pair(home, away), "normalized_side_model_probabilities")


def _market_pair(row: Dict[str, Any]) -> Tuple[float, float]:
    home = _signal_probability(
        _signal(row, "home"),
        "marketProbability",
        "marketConsensusProbability",
        "fairProbability",
        "probLatest",
    )
    away = _signal_probability(
        _signal(row, "away"),
        "marketProbability",
        "marketConsensusProbability",
        "fairProbability",
        "probLatest",
    )
    return _pair(home, away)


def _selected_price(signal: Dict[str, Any]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    price = _number(signal.get("americanOdds"))
    if price is None:
        price = _number(signal.get("averageAmericanOdds"))
    if price == 0:
        price = None
    book = str(signal.get("priceBook") or "").strip() or None
    source = str(signal.get("priceSource") or "").strip() or None
    return price, book, source


def _reliability(row: Dict[str, Any]) -> Optional[float]:
    for field in (
        "optimizedPickReliabilityPct",
        "mlPickReliabilityPct",
        "pickReliabilityPct",
    ):
        value = _probability(row.get(field))
        if value is not None:
            return value
    return _probability(row.get("pickReliability"))


def _mark_ineligible(row: Dict[str, Any], reason: str) -> None:
    release_reasons = {
        str(value)
        for field in ("blockedReasons", "releaseBlockReasons", "playabilityBlockReasons")
        for value in (row.get(field) or [])
        if value
    }
    release_reasons.add(reason)
    training_reasons = {
        str(value)
        for value in (row.get("trainingExclusionReasons") or [])
        if value
    }
    freeze = dict(row.get("mlFeatureFreeze") or {})
    training_reasons.update(
        str(value) for value in (freeze.get("trainingExclusionReasons") or []) if value
    )
    training_reasons.add(reason)
    row.update({
        "playable": False,
        "playablePick": False,
        "actionablePick": False,
        "blocked": True,
        "releaseBlocked": True,
        "wagerReleaseBlocked": True,
        "playabilityStatus": "BLOCKED",
        "blockedReasons": sorted(release_reasons),
        "releaseBlockReasons": sorted(release_reasons),
        "playabilityBlockReasons": sorted(release_reasons),
        "trainingEligible": False,
        "trainingEligibilityStatus": "INELIGIBLE",
        "trainingExclusionReasons": sorted(training_reasons),
    })
    freeze["trainingEligible"] = False
    freeze["trainingExclusionReasons"] = sorted(training_reasons)
    row["mlFeatureFreeze"] = freeze
    tags = {str(value) for value in (row.get("tags") or [])}
    tags.update({"NOT_PLAYABLE", "RELEASE_BLOCKED"})
    tags.discard("ACTIONABLE_PICK")
    tags.discard("PLAYABLE_PREDICTION")
    row["tags"] = sorted(tags)


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(row or {})
    if not out.get("predictedWinner") and not (_signal(out, "home") or _signal(out, "away")):
        return out

    prior_side = str(out.get("predictedSide") or "").lower()
    prior_winner = out.get("predictedWinner")
    home_model, away_model, model_source = _model_pair(out)
    home_market, away_market = _market_pair(out)
    side = "home" if home_model >= away_model else "away"
    opponent_side = "away" if side == "home" else "home"
    winner = out.get("homeTeam") if side == "home" else out.get("awayTeam")
    opponent = out.get("awayTeam") if side == "home" else out.get("homeTeam")
    selected = _signal(out, side)
    selected_model = home_model if side == "home" else away_model
    selected_market = home_market if side == "home" else away_market
    selected_price, price_book, price_source = _selected_price(selected)
    reliability = _reliability(out)

    corrected_now = bool(
        prior_side not in {"home", "away"}
        or prior_side != side
        or (winner and str(prior_winner or "").strip().lower() != str(winner).strip().lower())
    )
    corrected = bool(
        corrected_now
        or out.get("probabilityCorrectionApplied") is True
        or CORRECTION_REASON
        in {
            str(reason)
            for reason in (out.get("trainingExclusionReasons") or [])
        }
    )

    for signal_side, model_probability, market_probability in (
        ("home", home_model, home_market),
        ("away", away_model, away_market),
    ):
        signal = _signal(out, signal_side)
        if signal:
            signal["modelWinProbability"] = model_probability
            signal["modelWinProbabilityPct"] = round(model_probability * 100.0, 6)
            signal["marketProbability"] = market_probability
            signal["marketProbabilityPct"] = round(market_probability * 100.0, 6)
            signal["signalScore"] = signal.get("score")

    out.update({
        "predictedWinner": winner,
        "predictedSide": side,
        "opponent": opponent,
        "homeModelWinProbability": home_model,
        "awayModelWinProbability": away_model,
        "homeModelWinProbabilityPct": round(home_model * 100.0, 6),
        "awayModelWinProbabilityPct": round(away_model * 100.0, 6),
        "modelWinProbability": selected_model,
        "modelWinProbabilityPct": round(selected_model * 100.0, 6),
        "winProbability": selected_model,
        "winProbabilityPct": round(selected_model * 100.0, 6),
        "teamWinProbabilityPct": round(selected_model * 100.0, 6),
        "homeMarketDeVigProbability": home_market,
        "awayMarketDeVigProbability": away_market,
        "marketProbability": selected_market,
        "marketProbabilityPct": round(selected_market * 100.0, 6),
        "fairProbabilityPct": round(selected_market * 100.0, 6),
        "signalScore": selected.get("score"),
        "pickReliability": reliability,
        "pickReliabilityPct": round(reliability * 100.0, 6) if reliability is not None else None,
        "americanOdds": selected_price,
        "priceBook": price_book,
        "priceSource": price_source,
        "marketSide": selected.get("marketSide"),
        "edgeVsBook": selected.get("edgeVsBook"),
        "edgeVsBookPct": selected.get("edgeVsBookPct"),
        "expectedValue": selected.get("expectedValue"),
        "expectedValuePct": selected.get("expectedValuePct"),
        "score": selected.get("score"),
        "modelProbabilityVersion": MODEL_PROBABILITY_VERSION,
        "modelProbabilitySource": model_source,
        "marketProbabilityVersion": MARKET_PROBABILITY_VERSION,
        "marketProbabilitySourceAtUtc": out.get("predictionSourcePullAt"),
        "winProbabilityMeaning": "estimated_probability_selected_team_wins_game",
        "marketProbabilityMeaning": "same_time_devigged_market_probability_selected_team",
        "signalScoreMeaning": "rule_engine_directional_signal_score_not_probability",
        "pickReliabilityMeaning": "estimated_probability_selected_pick_is_correct_not_team_win_probability",
        "probabilitySemanticsFixed": True,
        "probabilityContractVersion": VERSION,
    })

    # Side-dependent tags and promotion facts must follow the selected team.
    if selected:
        out["promoted"] = selected.get("promoted")
        out["promotionStatus"] = selected.get("promotionStatus")
        side_tags = {
            "FAVORITE", "UNDERDOG", "PICKEM", "POSITIVE_MOVE", "NEGATIVE_MOVE",
            "REVERSAL", "BOOK_AGREEMENT", "BOOK_DIVERGENCE", "STEAM", "RESISTANCE",
        }
        tags = {str(value) for value in (out.get("tags") or [])} - side_tags
        tags.update(str(value) for value in (selected.get("tags") or []))
        out["tags"] = sorted(tags)

    source_slot = out.get("predictionSourceCanonicalSlot") or {}
    market_material = {
        "version": MARKET_PROBABILITY_VERSION,
        "gameIdentity": out.get("gameIdentity") or out.get("gameId"),
        "sourcePullAtUtc": out.get("marketProbabilitySourceAtUtc"),
        "sourcePullId": out.get("predictionSourcePullId"),
        "canonicalSlotFingerprint": source_slot.get("canonicalPullFingerprint"),
        "homeMarketDeVigProbability": home_market,
        "awayMarketDeVigProbability": away_market,
    }
    out["marketProbabilityFingerprint"] = history.canonical_payload_fingerprint(
        market_material
    )

    contract_errors = []
    if selected_model < 0.5:
        contract_errors.append("selected_model_probability_below_50")
    if not winner:
        contract_errors.append("selected_winner_team_missing")
    if selected_price is None or not price_book or str(price_source or "").lower() not in {
        "real_book", "locked_real_book"
    }:
        contract_errors.append(PRICE_REASON)
        _mark_ineligible(out, PRICE_REASON)
    if corrected:
        if "preProbabilityContractPredictedSide" not in out:
            out["preProbabilityContractPredictedSide"] = prior_side or None
        if "preProbabilityContractPredictedWinner" not in out:
            out["preProbabilityContractPredictedWinner"] = prior_winner
        out["probabilityCorrectionApplied"] = True
        out["probabilityCorrectionReason"] = CORRECTION_REASON
        _mark_ineligible(out, CORRECTION_REASON)
        tags = {str(value) for value in (out.get("tags") or [])}
        tags.add("PROBABILITY_DIRECTION_INTEGRITY_CORRECTION")
        out["tags"] = sorted(tags)
    else:
        out["probabilityCorrectionApplied"] = False

    out["probabilityContract"] = {
        "applied": True,
        "version": VERSION,
        "modelProbabilityVersion": MODEL_PROBABILITY_VERSION,
        "marketProbabilityVersion": MARKET_PROBABILITY_VERSION,
        "homeAwayModelComplementVerified": abs((home_model + away_model) - 1.0) <= 1e-12,
        "homeAwayMarketComplementVerified": abs((home_market + away_market) - 1.0) <= 1e-12,
        "selectedSideAtLeastFiftyPct": selected_model >= 0.5,
        "winnerSideTeamBound": bool(winner),
        "selectedPriceBound": PRICE_REASON not in contract_errors,
        "correctionApplied": corrected,
        "errors": sorted(set(contract_errors)),
    }
    return out


def validation_errors(row: Dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if row.get("probabilityContractVersion") != VERSION:
        errors.append("probability_contract_version_missing_or_wrong")
    home_model = _probability(row.get("homeModelWinProbability"))
    away_model = _probability(row.get("awayModelWinProbability"))
    home_market = _probability(row.get("homeMarketDeVigProbability"))
    away_market = _probability(row.get("awayMarketDeVigProbability"))
    if home_model is None or away_model is None or abs(home_model + away_model - 1.0) > 1e-9:
        errors.append("model_probabilities_not_complementary")
    if home_market is None or away_market is None or abs(home_market + away_market - 1.0) > 1e-9:
        errors.append("market_probabilities_not_complementary")
    side = str(row.get("predictedSide") or "").lower()
    expected_winner = row.get("homeTeam") if side == "home" else row.get("awayTeam") if side == "away" else None
    selected_model = home_model if side == "home" else away_model if side == "away" else None
    selected_market = home_market if side == "home" else away_market if side == "away" else None
    if not expected_winner or str(row.get("predictedWinner") or "").strip().lower() != str(expected_winner).strip().lower():
        errors.append("winner_side_team_mismatch")
    if selected_model is None or selected_model < 0.5:
        errors.append("selected_model_probability_below_50")
    if _probability(row.get("modelWinProbability")) != selected_model:
        errors.append("selected_model_probability_mismatch")
    if _probability(row.get("marketProbability")) != selected_market:
        errors.append("selected_market_probability_mismatch")
    selected = _signal(row, side) if side in {"home", "away"} else {}
    price, book, source = _selected_price(selected)
    if (
        _number(row.get("americanOdds")) != price
        or str(row.get("priceBook") or "") != str(book or "")
        or str(row.get("priceSource") or "") != str(source or "")
    ):
        errors.append("selected_side_price_binding_mismatch")
    if price is None or not book or str(source or "").lower() not in {
        "real_book", "locked_real_book"
    }:
        errors.append(PRICE_REASON)
    if not row.get("marketProbabilitySourceAtUtc"):
        errors.append("market_probability_source_timestamp_missing")
    if row.get("marketProbabilityVersion") != MARKET_PROBABILITY_VERSION:
        errors.append("market_probability_version_missing_or_wrong")
    source_slot = row.get("predictionSourceCanonicalSlot") or {}
    if (
        source_slot.get("version") != history.PULL_SLOT_VERSION
        or not source_slot.get("canonicalPullFingerprint")
    ):
        errors.append("market_probability_canonical_slot_proof_missing")
    material = {
        "version": MARKET_PROBABILITY_VERSION,
        "gameIdentity": row.get("gameIdentity") or row.get("gameId"),
        "sourcePullAtUtc": row.get("marketProbabilitySourceAtUtc"),
        "sourcePullId": row.get("predictionSourcePullId"),
        "canonicalSlotFingerprint": source_slot.get("canonicalPullFingerprint"),
        "homeMarketDeVigProbability": home_market,
        "awayMarketDeVigProbability": away_market,
    }
    if row.get("marketProbabilityFingerprint") != history.canonical_payload_fingerprint(material):
        errors.append("market_probability_fingerprint_mismatch")
    if row.get("probabilityCorrectionApplied") is True:
        if row.get("playable") is not False or row.get("trainingEligible") is not False:
            errors.append("probability_correction_not_fail_closed")
    return sorted(set(errors))


def suppress_legacy_probability_authority(row: Dict[str, Any]) -> Dict[str, Any]:
    """Preserve an immutable legacy selection without publishing probability authority."""
    out = copy.deepcopy(row or {})
    probability_fields = {
        "winProbability", "winProbabilityPct", "teamWinProbabilityPct",
        "modelWinProbability", "modelWinProbabilityPct",
        "homeModelWinProbability", "awayModelWinProbability",
        "homeModelWinProbabilityPct", "awayModelWinProbabilityPct",
        "marketProbability", "marketProbabilityPct", "fairProbabilityPct",
        "homeMarketDeVigProbability", "awayMarketDeVigProbability",
        "pickReliability", "pickReliabilityPct", "optimizedPickReliabilityPct",
        "outcomeModelHomeWinProbabilityPct", "outcomeModelAwayWinProbabilityPct",
    }
    for field in probability_fields:
        out.pop(field, None)
    for signal_name in ("homeSignal", "awaySignal"):
        signal = out.get(signal_name)
        if isinstance(signal, dict):
            for field in list(signal):
                lowered = field.lower()
                if "probability" in lowered or field in {"probStart", "probLatest"}:
                    signal.pop(field, None)
    _mark_ineligible(out, LEGACY_SUPPRESSION_REASON)
    exclusions = set(out.get("trainingExclusionReasons") or [])
    exclusions.add(LEGACY_SUPPRESSION_REASON)
    out.update({
        "probabilityAuthoritySuppressed": True,
        "probabilityAuthoritySuppressionReason": LEGACY_SUPPRESSION_REASON,
        "probabilitySemanticsFixed": False,
        "trainingEligible": False,
        "trainingExclusionReasons": sorted(exclusions),
    })
    authority = dict(out.get("canonicalLockAuthority") or {})
    authority["learningEligible"] = False
    authority_reasons = set(authority.get("trainingExclusionReasons") or [])
    authority_reasons.add(LEGACY_SUPPRESSION_REASON)
    authority["trainingExclusionReasons"] = sorted(authority_reasons)
    out["canonicalLockAuthority"] = authority
    return out


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    out = dict(result)
    rows = [normalize_row(row) for row in (result.get("predictions") or []) if isinstance(row, dict)]
    out["predictions"] = rows
    out["probabilityContract"] = {
        "applied": True,
        "version": VERSION,
        "rowCount": len(rows),
        "correctionCount": sum(row.get("probabilityCorrectionApplied") is True for row in rows),
        "invalidCount": sum(bool((row.get("probabilityContract") or {}).get("errors")) for row in rows),
        "modelAndMarketProbabilitySeparated": True,
    }
    return out


def apply(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))

    module.predict_all = patched_predict_all
    module.MLB_PREDICTION_PROBABILITY_CONTRACT_VERSION = VERSION
    module._INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED = True
    return module
