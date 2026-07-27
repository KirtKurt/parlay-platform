"""Odds-only V7 selective policy search.

Searches policy, calibration, PICK/PASS threshold, and reliability profile jointly on
chronological development evidence.  The untouched holdout is evaluated only after
all choices are frozen.  This module never writes production authority.
"""
from __future__ import annotations

import copy
import math
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

VERSION = "MLB-HISTORICAL-V7-SELECTIVE-SEARCH-v2"
COARSE_THRESHOLDS = (0.60, 0.625, 0.65, 0.675, 0.70, 0.725, 0.75, 0.775, 0.80)
TEMPERATURES = (0.75, 0.90, 1.0, 1.10, 1.25)
MIN_WALK_FORWARD_PICKS = 200
MIN_UNTOUCHED_PICKS = 200
MIN_SELECTION_DAYS = 50
MIN_COVERAGE = 0.05
PRODUCTION_ACCURACY = 0.75
ELITE_ACCURACY = 0.80
LIGHTWEIGHT_EVALUATION_INCREMENT_GAMES = 25
FULL_SEARCH_INCREMENT_GAMES = 50

RELIABILITY_PROFILES = {
    "balanced": {
        "minimumPulls": 4,
        "minimumCoverage": 0.70,
        "maximumDivergence": 0.075,
        "maximumReversals": 2,
        "maximumVolatility": 0.050,
    },
    "stable": {
        "minimumPulls": 6,
        "minimumCoverage": 0.82,
        "maximumDivergence": 0.055,
        "maximumReversals": 1,
        "maximumVolatility": 0.035,
    },
    "strict": {
        "minimumPulls": 8,
        "minimumCoverage": 0.90,
        "maximumDivergence": 0.040,
        "maximumReversals": 0,
        "maximumVolatility": 0.025,
    },
}
BAD_TAGS = {
    "LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE",
    "LATE_INSTABILITY", "COMPRESSED_MARKET", "RESISTANCE",
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _nested(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _temperature_scale(probability: float, temperature: float) -> float:
    probability = min(1.0 - 1e-9, max(1e-9, probability))
    logit = math.log(probability / (1.0 - probability)) / max(1e-6, temperature)
    return 1.0 / (1.0 + math.exp(-logit))


def _regime(record: Mapping[str, Any], prediction: Mapping[str, Any]) -> str:
    side = str(prediction.get("predictedSide") or "")
    signal = record.get("homeSignal") if side == "home" else record.get("awaySignal")
    signal = signal if isinstance(signal, Mapping) else {}
    tags = {str(x) for x in signal.get("tags") or []}
    odds = _f(signal.get("americanOdds"), 0.0)
    reversals = int(_f(signal.get("reversalCount"), 0.0))
    if reversals >= 2:
        return "repeated_reversal"
    if "BOOK_AGREEMENT" in tags and "STEAM" in tags:
        return "consensus_steam"
    if odds < -175:
        return "heavy_favorite"
    if odds < 0:
        return "favorite"
    if odds > 0:
        return "underdog"
    return "pickem"


def _reliable(record: Mapping[str, Any], prediction: Mapping[str, Any], profile: Mapping[str, Any]) -> Tuple[bool, Tuple[str, ...]]:
    side = str(prediction.get("predictedSide") or "")
    signal = record.get("homeSignal") if side == "home" else record.get("awaySignal")
    signal = signal if isinstance(signal, Mapping) else {}
    pulls = int(_f(signal.get("pullCountForGame", _nested(signal, "temporalFeatures", "sourcePointCount")), 0.0))
    coverage = _f(_nested(signal, "temporalFeatures", "horizons", "full", "coverageRatio"), 0.0)
    divergence = max(0.0, _f(signal.get("bookDivergence"), 0.0))
    reversals = max(0, int(_f(signal.get("reversalCount"), 0.0)))
    volatility = max(0.0, _f(_nested(signal, "temporalFeatures", "horizons", "180m", "volatilityPpPerPull"), 0.0))
    tags = {str(x) for x in signal.get("tags") or []}
    reasons = []
    if pulls < int(profile["minimumPulls"]): reasons.append("pull_depth")
    if coverage + 1e-12 < float(profile["minimumCoverage"]): reasons.append("coverage")
    if divergence > float(profile["maximumDivergence"]) + 1e-12: reasons.append("divergence")
    if reversals > int(profile["maximumReversals"]): reasons.append("reversals")
    if volatility > float(profile["maximumVolatility"]) + 1e-12: reasons.append("volatility")
    if tags & BAD_TAGS: reasons.append("unstable_tag")
    return not reasons, tuple(sorted(set(reasons)))


def _prepared(optimizer: Any, records: Sequence[Mapping[str, Any]], policy: Mapping[str, Any], dates: Iterable[str], temperature: float) -> list:
    date_set = {str(x) for x in dates}
    output = []
    for record in records:
        if str(record.get("slateDateEt") or "") not in date_set:
            continue
        prediction = optimizer.predict_record(record, policy)
        home_p = _temperature_scale(_f(prediction.get("homeWinProbability"), 0.5), temperature)
        side = str(prediction.get("predictedSide") or "")
        confidence = home_p if side == "home" else 1.0 - home_p
        output.append({"record": record, "prediction": prediction, "confidence": confidence, "regime": _regime(record, prediction)})
    return output


def _metrics(rows: Sequence[Mapping[str, Any]], threshold: float, profile_name: str, regime: str | None = None) -> Dict[str, Any]:
    profile = RELIABILITY_PROFILES[profile_name]
    eligible = [x for x in rows if regime is None or x["regime"] == regime]
    selected, rejection_reasons = [], Counter()
    for item in eligible:
        if float(item["confidence"]) + 1e-12 < threshold:
            rejection_reasons["confidence"] += 1
            continue
        ok, reasons = _reliable(item["record"], item["prediction"], profile)
        if not ok:
            rejection_reasons.update(reasons)
            continue
        selected.append(item)
    correct = sum(x["prediction"].get("correct") is True for x in selected)
    picks = len(selected)
    by_day = defaultdict(list)
    for item in selected:
        by_day[str(item["record"].get("slateDateEt") or "")].append(item)
    daily = []
    for day in sorted(by_day):
        values = by_day[day]
        day_correct = sum(x["prediction"].get("correct") is True for x in values)
        daily.append({"slateDateEt": day, "pickCount": len(values), "correct": day_correct, "accuracy": day_correct / len(values)})
    return {
        "threshold": threshold,
        "reliabilityProfile": profile_name,
        "regime": regime or "all",
        "eligibleGameCount": len(eligible),
        "pickCount": picks,
        "passCount": max(0, len(eligible) - picks),
        "selectionDayCount": len(by_day),
        "correct": correct,
        "accuracy": correct / picks if picks else 0.0,
        "coverage": picks / len(eligible) if eligible else 0.0,
        "meanDailyAccuracy": sum(x["accuracy"] for x in daily) / len(daily) if daily else 0.0,
        "minimumDailyAccuracy": min((x["accuracy"] for x in daily), default=0.0),
        "rejectionReasons": dict(rejection_reasons),
        "daily": daily,
    }


def _sample_ok(metrics: Mapping[str, Any], minimum_picks: int = MIN_WALK_FORWARD_PICKS) -> bool:
    return bool(
        int(metrics.get("pickCount") or 0) >= minimum_picks
        and int(metrics.get("selectionDayCount") or 0) >= MIN_SELECTION_DAYS
        and float(metrics.get("coverage") or 0.0) + 1e-12 >= MIN_COVERAGE
    )


def _rank(metrics: Mapping[str, Any]) -> tuple:
    return (
        int(_sample_ok(metrics)),
        float(metrics.get("accuracy") or 0.0),
        float(metrics.get("meanDailyAccuracy") or 0.0),
        int(metrics.get("pickCount") or 0),
        float(metrics.get("coverage") or 0.0),
    )


def _refined_thresholds(best: float) -> Tuple[float, ...]:
    values = {best}
    for delta in (-0.010, -0.005, 0.005, 0.010):
        values.add(round(min(0.85, max(0.55, best + delta)), 3))
    return tuple(sorted(values))


def _stability(candidates: Sequence[Mapping[str, Any]], chosen: Mapping[str, Any]) -> Dict[str, Any]:
    threshold = float(chosen.get("threshold") or 0.0)
    peers = [x for x in candidates if x.get("reliabilityProfile") == chosen.get("reliabilityProfile") and abs(float(x.get("threshold") or 0.0) - threshold) <= 0.011]
    accuracies = [float(x.get("accuracy") or 0.0) for x in peers if int(x.get("pickCount") or 0) > 0]
    spread = max(accuracies) - min(accuracies) if accuracies else 1.0
    return {"neighborCount": len(peers), "accuracySpread": spread, "passed": len(peers) >= 3 and spread <= 0.05}


def _loss_analysis(rows: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any]) -> Dict[str, Any]:
    selected_days = {x["slateDateEt"] for x in metrics.get("daily") or []}
    losses = Counter()
    for item in rows:
        prediction = item["prediction"]
        if str(item["record"].get("slateDateEt") or "") not in selected_days or prediction.get("correct") is True:
            continue
        losses[item["regime"]] += 1
    return {"lossCountByRegime": dict(losses.most_common()), "totalObservedLosses": sum(losses.values())}


def search(optimizer: Any, records: Sequence[Mapping[str, Any]], config: Any, *, untouched_holdout_dates=None) -> Dict[str, Any]:
    partitions = optimizer.chronological_partitions(records, config, untouched_holdout_dates=untouched_holdout_dates)
    policies = []
    for index, policy in enumerate(optimizer.candidate_policies(config)):
        policies.append(copy.deepcopy(policy))
        if index + 1 >= int(getattr(config, "maximum_candidates", 100)):
            break
    regimes = (None, "favorite", "underdog", "heavy_favorite", "consensus_steam", "repeated_reversal", "pickem")
    contenders = []
    for policy in policies:
        for temperature in TEMPERATURES:
            prepared = _prepared(optimizer, records, policy, partitions["walkForward"], temperature)
            for profile_name in RELIABILITY_PROFILES:
                coarse = [_metrics(prepared, threshold, profile_name) for threshold in COARSE_THRESHOLDS]
                best_coarse = max(coarse, key=_rank)
                refined = [_metrics(prepared, threshold, profile_name) for threshold in _refined_thresholds(float(best_coarse["threshold"]))]
                best = max(coarse + refined, key=_rank)
                best.update({"policy": copy.deepcopy(policy), "temperature": temperature})
                contenders.append(best)
    frozen = max(contenders, key=_rank)
    frozen_policy = frozen["policy"]
    frozen_temperature = float(frozen["temperature"])
    frozen_threshold = float(frozen["threshold"])
    frozen_profile = str(frozen["reliabilityProfile"])
    wf_rows = _prepared(optimizer, records, frozen_policy, partitions["walkForward"], frozen_temperature)
    holdout_rows = _prepared(optimizer, records, frozen_policy, partitions["untouchedHoldout"], frozen_temperature)
    walk_forward = _metrics(wf_rows, frozen_threshold, frozen_profile)
    untouched = _metrics(holdout_rows, frozen_threshold, frozen_profile)
    neighbor_metrics = [_metrics(wf_rows, threshold, frozen_profile) for threshold in _refined_thresholds(frozen_threshold)]
    stability = _stability(neighbor_metrics, walk_forward)
    specialist_routes = {}
    for regime in regimes[1:]:
        eligible = [x for x in contenders if x.get("regime", "all") in {"all", regime}]
        specialist_routes[regime] = {"available": bool(eligible), "routingMode": "diagnostic_until_200_untouched_picks"}
    errors = []
    if not _sample_ok(walk_forward): errors.append("walk_forward_sample_gate_failed")
    if not _sample_ok(untouched, MIN_UNTOUCHED_PICKS): errors.append("untouched_sample_gate_failed")
    if float(walk_forward["accuracy"]) + 1e-12 < PRODUCTION_ACCURACY: errors.append("walk_forward_accuracy_failed")
    if float(untouched["accuracy"]) + 1e-12 < PRODUCTION_ACCURACY: errors.append("untouched_accuracy_failed")
    if not stability["passed"]: errors.append("threshold_stability_failed")
    return {
        "ok": True,
        "version": VERSION,
        "objective": "selective_individual_game_accuracy",
        "status": "SELECTIVE_PROMOTION_GATE_PASSED" if not errors else "SELECTIVE_CANDIDATE_REJECTED",
        "promotionAuthority": False,
        "partitions": partitions,
        "candidateCountEvaluated": len(contenders),
        "frozenPolicy": frozen_policy,
        "frozenTemperature": frozen_temperature,
        "frozenThreshold": frozen_threshold,
        "frozenReliabilityProfile": frozen_profile,
        "thresholdFrozenBeforeUntouchedHoldout": True,
        "thresholdSelectionUsedHoldoutLabels": False,
        "walkForward": walk_forward,
        "untouchedHoldout": untouched,
        "thresholdStability": stability,
        "specialistRoutes": specialist_routes,
        "lossAnalysis": _loss_analysis(holdout_rows, untouched),
        "cadence": {"lightweightEvaluationIncrementGames": LIGHTWEIGHT_EVALUATION_INCREMENT_GAMES, "fullSearchIncrementGames": FULL_SEARCH_INCREMENT_GAMES, "freshAuditMinimumGames": MIN_UNTOUCHED_PICKS},
        "incrementalSearch": {"retainTopCandidates": 50, "mutateAroundIncumbents": True, "freshCandidateFraction": 0.25},
        "recencyWeighting": {"enabledForDevelopmentOnly": True, "untouchedAuditRemainsUnweighted": True},
        "productionGatePassed": not errors,
        "eliteGatePassed": bool(not errors and walk_forward["accuracy"] >= ELITE_ACCURACY and untouched["accuracy"] >= ELITE_ACCURACY),
        "errors": sorted(set(errors)),
    }


def install(optimizer: Any) -> None:
    if getattr(optimizer, "_INQSI_V7_SELECTIVE_SEARCH_V2_INSTALLED", False):
        return
    optimizer.v7_selective_search = lambda records, config=None, untouched_holdout_dates=None: search(
        optimizer,
        records,
        (config or optimizer.SearchConfig()).validate(),
        untouched_holdout_dates=untouched_holdout_dates,
    )
    optimizer.V7_SELECTIVE_SEARCH_VERSION = VERSION
    optimizer._INQSI_V7_SELECTIVE_SEARCH_V2_INSTALLED = True
