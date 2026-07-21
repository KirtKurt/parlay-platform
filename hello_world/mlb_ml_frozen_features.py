from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VERSION = "MLB-ML-FROZEN-FEATURES-v1-clean-postfix-lock-vector"
OUTCOME_FEATURE_VERSION = "MLB-OUTCOME-FEATURES-v1-home-away-lock-state"
RELIABILITY_FEATURE_VERSION = "MLB-RELIABILITY-FEATURES-v1-selected-side-lock-state"

ADVANCED_FIELDS = [
    "starting_pitcher_fip_xfip",
    "offense_wrc_plus",
    "confirmed_lineups",
    "injuries_news",
    "bullpen_availability",
    "weather_roof",
    "travel_rest",
    "defense",
]

OUTCOME_FEATURES: List[str] = [
    "homeMarketProb", "awayMarketProb", "marketProbDiff",
    "homeDelta", "awayDelta", "deltaDiff",
    "homeScore", "awayScore", "scoreDiff",
    "homeBookDivergence", "awayBookDivergence", "divergenceDiff",
    "homeReversalCount", "awayReversalCount", "reversalDiff",
    "homeRunLineMovement", "awayRunLineMovement", "runLineDiff",
    "homeBookAgreement", "awayBookAgreement",
    "homeSteam", "awaySteam", "homeResistance", "awayResistance",
    "homeLowPullDepth", "awayLowPullDepth",
    "homeAmericanImpliedProb", "awayAmericanImpliedProb", "americanImpliedDiff",
    "advancedInputCoveragePct", "fundamentalsApplied",
]

RELIABILITY_FEATURES: List[str] = [
    "selectedTeamWinProbability", "selectedMarketProb", "opponentMarketProb", "selectedMarketEdge",
    "selectedDelta", "opponentDelta", "movementGap",
    "selectedScore", "opponentScore", "scoreMargin",
    "selectedBookDivergence", "selectedReversalCount", "selectedRunLineMovement",
    "bookAgreement", "steam", "resistance", "compressedMarket", "lowPullDepth",
    "selectedFavorite", "selectedUnderdog", "selectedAmericanImpliedProb",
    "advancedInputCoveragePct", "fundamentalsApplied",
]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _signal(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    value = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return value if isinstance(value, dict) else {}


def _tags(row: Dict[str, Any], signal: Dict[str, Any]) -> set[str]:
    return {str(v) for v in (row.get("tags") or [])} | {str(v) for v in (signal.get("tags") or [])}


def _market_prob(signal: Dict[str, Any]) -> float:
    return min(0.999, max(0.001, _f(signal.get("marketConsensusProbability"), _f(signal.get("probLatest"), 0.5))))


def _american_prob(value: Any) -> float:
    price = _f(value, 0.0)
    if price == 0:
        return 0.5
    return abs(price) / (abs(price) + 100.0) if price < 0 else 100.0 / (price + 100.0)


def _american_odds(row: Dict[str, Any], side: str) -> Optional[float]:
    signal = _signal(row, side)
    for key in ("americanOdds", "averageAmericanOdds"):
        value = signal.get(key)
        if value not in (None, ""):
            return _f(value)
    if str(row.get("predictedSide") or "").lower() == side and row.get("americanOdds") not in (None, ""):
        return _f(row.get("americanOdds"))
    return None


def _advanced_context(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    candidates = [
        row.get("advanced_context"), row.get("advancedContext"),
        (row.get("winnerOptimizer") or {}).get("fundamentals"),
        (row.get("winnerStackV2") or {}).get("components", {}).get("fundamentals", {}).get("details"),
    ]
    aliases = {
        "starting_pitcher_fip_xfip": ("starting_pitcher_fip_xfip", "fip_xfip", "FIP/xFIP"),
        "offense_wrc_plus": ("offense_wrc_plus", "wrc_plus", "wRC+"),
        "confirmed_lineups": ("confirmed_lineups", "lineups_confirmed"),
        "injuries_news": ("injuries_news", "injuries"),
        "bullpen_availability": ("bullpen_availability", "bullpen"),
        "weather_roof": ("weather_roof", "weather", "roof"),
        "travel_rest": ("travel_rest", "rest"),
        "defense": ("defense", "defense_rating"),
    }
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for canonical, keys in aliases.items():
            if canonical in out:
                continue
            for key in keys:
                if candidate.get(key) not in (None, "", [], {}):
                    out[canonical] = candidate.get(key)
                    break
    return out


def advanced_input_status(row: Dict[str, Any]) -> Dict[str, Any]:
    context = _advanced_context(row)
    present = [field for field in ADVANCED_FIELDS if field in context]
    missing = [field for field in ADVANCED_FIELDS if field not in context]
    coverage = round(len(present) / len(ADVANCED_FIELDS) * 100.0, 2) if ADVANCED_FIELDS else 100.0
    fundamentals = (row.get("winnerOptimizer") or {}).get("fundamentalsApplied") is True
    return {
        "present": present,
        "missing": missing,
        "coveragePct": coverage,
        "fundamentalsApplied": bool(fundamentals),
        "mode": "FULL_BASEBALL_CONTEXT" if coverage >= 75.0 else "MARKET_ONLY_WITH_MISSINGNESS",
    }


def build_outcome_features(row: Dict[str, Any]) -> Dict[str, float]:
    home = _signal(row, "home")
    away = _signal(row, "away")
    home_tags = _tags(row, home)
    away_tags = _tags(row, away)
    hp = _market_prob(home)
    ap = _market_prob(away)
    hd = _f(home.get("delta"), hp - _f(home.get("probStart"), hp))
    ad = _f(away.get("delta"), ap - _f(away.get("probStart"), ap))
    hs = _f(home.get("score"))
    ass = _f(away.get("score"))
    hb = _f(home.get("bookDivergence"))
    ab = _f(away.get("bookDivergence"))
    hr = _f(home.get("reversalCount"))
    ar = _f(away.get("reversalCount"))
    hrl = _f(home.get("runLineMovement"))
    arl = _f(away.get("runLineMovement"))
    hao = _american_prob(_american_odds(row, "home"))
    aao = _american_prob(_american_odds(row, "away"))
    advanced = advanced_input_status(row)
    return {
        "homeMarketProb": hp, "awayMarketProb": ap, "marketProbDiff": hp - ap,
        "homeDelta": hd, "awayDelta": ad, "deltaDiff": hd - ad,
        "homeScore": hs, "awayScore": ass, "scoreDiff": hs - ass,
        "homeBookDivergence": hb, "awayBookDivergence": ab, "divergenceDiff": hb - ab,
        "homeReversalCount": hr, "awayReversalCount": ar, "reversalDiff": hr - ar,
        "homeRunLineMovement": hrl, "awayRunLineMovement": arl, "runLineDiff": hrl - arl,
        "homeBookAgreement": 1.0 if "BOOK_AGREEMENT" in home_tags else 0.0,
        "awayBookAgreement": 1.0 if "BOOK_AGREEMENT" in away_tags else 0.0,
        "homeSteam": 1.0 if "STEAM" in home_tags else 0.0,
        "awaySteam": 1.0 if "STEAM" in away_tags else 0.0,
        "homeResistance": 1.0 if "RESISTANCE" in home_tags else 0.0,
        "awayResistance": 1.0 if "RESISTANCE" in away_tags else 0.0,
        "homeLowPullDepth": 1.0 if "LOW_PULL_DEPTH" in home_tags else 0.0,
        "awayLowPullDepth": 1.0 if "LOW_PULL_DEPTH" in away_tags else 0.0,
        "homeAmericanImpliedProb": hao, "awayAmericanImpliedProb": aao, "americanImpliedDiff": hao - aao,
        "advancedInputCoveragePct": advanced["coveragePct"] / 100.0,
        "fundamentalsApplied": 1.0 if advanced["fundamentalsApplied"] else 0.0,
    }


def build_reliability_features(row: Dict[str, Any]) -> Dict[str, float]:
    side = str(row.get("predictedSide") or "home").lower()
    if side not in {"home", "away"}:
        side = "home"
    other = "away" if side == "home" else "home"
    selected = _signal(row, side)
    opponent = _signal(row, other)
    tags = _tags(row, selected)
    sp = _market_prob(selected)
    op = _market_prob(opponent)
    sd = _f(selected.get("delta"), sp - _f(selected.get("probStart"), sp))
    od = _f(opponent.get("delta"), op - _f(opponent.get("probStart"), op))
    ss = _f(selected.get("score"), _f(row.get("score")))
    oscore = _f(opponent.get("score"))
    odds = _american_odds(row, side)
    team_probability = _f(row.get("teamWinProbabilityPct"), sp * 100.0)
    team_probability = team_probability / 100.0 if team_probability > 1.0 else team_probability
    advanced = advanced_input_status(row)
    return {
        "selectedTeamWinProbability": min(0.999, max(0.001, team_probability)),
        "selectedMarketProb": sp, "opponentMarketProb": op, "selectedMarketEdge": sp - op,
        "selectedDelta": sd, "opponentDelta": od, "movementGap": sd - od,
        "selectedScore": ss, "opponentScore": oscore, "scoreMargin": ss - oscore,
        "selectedBookDivergence": _f(selected.get("bookDivergence")),
        "selectedReversalCount": _f(selected.get("reversalCount")),
        "selectedRunLineMovement": _f(selected.get("runLineMovement")),
        "bookAgreement": 1.0 if "BOOK_AGREEMENT" in tags else 0.0,
        "steam": 1.0 if "STEAM" in tags else 0.0,
        "resistance": 1.0 if "RESISTANCE" in tags else 0.0,
        "compressedMarket": 1.0 if "COMPRESSED_MARKET" in tags else 0.0,
        "lowPullDepth": 1.0 if "LOW_PULL_DEPTH" in tags else 0.0,
        "selectedFavorite": 1.0 if sp >= 0.5 else 0.0,
        "selectedUnderdog": 1.0 if sp < 0.5 else 0.0,
        "selectedAmericanImpliedProb": _american_prob(odds),
        "advancedInputCoveragePct": advanced["coveragePct"] / 100.0,
        "fundamentalsApplied": 1.0 if advanced["fundamentalsApplied"] else 0.0,
    }


def _lock_info(row: Dict[str, Any]) -> Dict[str, Any]:
    gate = row.get("slatePredictionLock") or row.get("lastPossiblePredictionGate") or {}
    lock_at = gate.get("lockAtUtc") or row.get("lockedAtUtc")
    source_at = row.get("predictionSourcePullAt") or gate.get("latestScoringPullAt")
    locked = bool(
        gate.get("locked") is True or gate.get("finalLocked") is True or gate.get("phase") == "SLATE_LOCKED"
        or row.get("lockedPrediction") is True or "SLATE_LOCKED" in set(row.get("tags") or [])
    )
    lock_dt = _parse_dt(lock_at)
    source_dt = _parse_dt(source_at)
    source_before_lock = bool(lock_dt and source_dt and source_dt <= lock_dt)
    return {
        "locked": locked,
        "lockAtUtc": lock_dt.isoformat() if lock_dt else None,
        "sourcePullAtUtc": source_dt.isoformat() if source_dt else None,
        "sourceAtOrBeforeLock": source_before_lock,
    }


def freeze_row(row: Dict[str, Any], coverage_complete: Optional[bool] = None) -> Dict[str, Any]:
    out = copy.deepcopy(row or {})
    lock = _lock_info(out)
    semantics = str(out.get("predictionSemanticsVersion") or "")
    semantics_ok = semantics.startswith((
        "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1",
        "MLB-OFFICIAL-PREDICTION-SEMANTICS-v2",
    ))
    provider_id = str(out.get("gameId") or out.get("game_id") or out.get("id") or "").strip()
    if coverage_complete is None:
        coverage_complete = bool((out.get("slateCoverage") or {}).get("coverageComplete"))
    advanced = advanced_input_status(out)
    eligible = bool(
        lock["locked"] and lock["sourceAtOrBeforeLock"] and semantics_ok and provider_id
        and coverage_complete is True and out.get("predictedWinner") and out.get("predictedSide") in {"home", "away"}
    )
    out["frozenOutcomeFeatures"] = build_outcome_features(out)
    out["frozenReliabilityFeatures"] = build_reliability_features(out)
    out["mlFeatureFreeze"] = {
        "applied": True,
        "version": VERSION,
        "outcomeFeatureVersion": OUTCOME_FEATURE_VERSION,
        "reliabilityFeatureVersion": RELIABILITY_FEATURE_VERSION,
        "immutable": True,
        "frozenAtUtc": datetime.now(timezone.utc).isoformat(),
        "lockAtUtc": lock["lockAtUtc"],
        "sourcePullAtUtc": lock["sourcePullAtUtc"],
        "sourceAtOrBeforeLock": lock["sourceAtOrBeforeLock"],
        "providerGameId": provider_id or None,
        "completeSlateCoverage": bool(coverage_complete),
        "probabilitySemanticsVersion": semantics or None,
        "trainingEligible": eligible,
        "trainingExclusionReasons": [
            reason for reason, condition in [
                ("not_locked", not lock["locked"]),
                ("source_after_or_missing_lock", not lock["sourceAtOrBeforeLock"]),
                ("legacy_probability_semantics", not semantics_ok),
                ("missing_provider_game_id", not provider_id),
                ("incomplete_slate_coverage", coverage_complete is not True),
                ("missing_prediction", not out.get("predictedWinner")),
            ] if condition
        ],
        "advancedInputs": advanced,
        "policy": "Features are copied at lock and must never be regenerated from settled rows or later learning weights.",
    }
    out["featureVectorFrozenAtLock"] = True
    return out


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    coverage = result.get("slateCoverage") or {}
    complete = coverage.get("coverageComplete") is True
    rows = [freeze_row(row, coverage_complete=complete) for row in (result.get("predictions") or [])]
    result = dict(result)
    result["predictions"] = rows
    result["mlFeatureFreeze"] = {
        "applied": True,
        "version": VERSION,
        "frozenRowCount": len(rows),
        "trainingEligibleCount": sum((row.get("mlFeatureFreeze") or {}).get("trainingEligible") is True for row in rows),
        "coverageComplete": complete,
    }
    return result


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_FROZEN_FEATURES_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        result = enhance_result(original(*args, **kwargs))
        if bool(kwargs.get("store")) and hasattr(module, "_store_prediction"):
            stored = 0
            errors = []
            for row in result.get("predictions") or []:
                try:
                    response = module._store_prediction(row)
                    row["frozenFeatureStore"] = response
                    if isinstance(response, dict) and response.get("ok"):
                        stored += 1
                    else:
                        errors.append(str(response))
                except Exception as exc:
                    errors.append(str(exc))
            result["frozenFeatureStoredCount"] = stored
            result["frozenFeatureStoreErrors"] = errors
        return result

    module.predict_all = patched_predict_all
    module._INQSI_MLB_FROZEN_FEATURES_APPLIED = True
    return module
