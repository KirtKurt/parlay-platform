"""Nested chronological supervised MLB learner with trainable V8 inputs.

The historical optimizer previously searched 25,000 hand-written rule-weight
combinations. This module replaces that search with a deterministic, day-balanced
L2 logistic model. Hyperparameters and probability calibration are chosen only on
expanding inner chronological folds; the outer untouched audit is read once after
the candidate is frozen. Production authority remains behind the existing 80%
every-day gate.
"""
from __future__ import annotations

import copy
import math
import os
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

VERSION = "MLB-HISTORICAL-SUPERVISED-v9.0-nested-chrono-v8-trainable-calibrated"
FEATURE_VERSION = "MLB-SUPERVISED-PAIR-FEATURES-v1"
FEATURES = (
    "marketLogit", "ruleLogit", "ruleScoreDiff", "deltaDiff", "velocityDiff",
    "accelerationDiff", "volatilitySum", "divergenceMean", "coverageMin",
    "favoriteDirection", "derivedMovementDiff", "derivedAgreementDiff",
    "patternFingerprintDiff", "patternRegimeDiff", "v8FirstFiveLogit",
    "v8SpreadDirection", "fundamentalsDiff", "featureAvailability",
)
L2_GRID = (0.01, 0.10, 0.50, 2.00)
BLEND_GRID = (0.50, 0.75, 1.00)
TEMPERATURE_GRID = (0.80, 1.00, 1.25, 1.50)
EPOCHS = max(80, min(400, int(os.environ.get("MLB_SUPERVISED_EPOCHS", "160"))))
LEARNING_RATE = max(0.005, min(0.25, float(os.environ.get("MLB_SUPERVISED_LEARNING_RATE", "0.06"))))
MIN_WF_MEAN_GAIN = max(0.0, min(0.10, float(os.environ.get("MLB_SUPERVISED_MIN_WF_MEAN_GAIN", "0.0025"))))

_ORIGINAL_SIGNAL = None
_ORIGINAL_SELECT = None
_ORIGINAL_COMPLEMENT = None


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _nested(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _clip(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-_clip(value, -35.0, 35.0)))


def _logit(value: float) -> float:
    value = _clip(value, 1e-6, 1.0 - 1e-6)
    return math.log(value / (1.0 - value))


def _market(signal: Mapping[str, Any]) -> float:
    return _clip(_f(signal.get("marketConsensusProbability", signal.get("probLatest")), _f(signal.get("fairProbability"), 0.5)), 0.001, 0.999)


def _temporal(signal: Mapping[str, Any], horizon: str, name: str) -> float:
    return _f(_nested(signal, "temporalFeatures", "horizons", horizon, name), 0.0)


def _derived(signal: Mapping[str, Any], name: str) -> float:
    values = signal.get("derivedFeatures")
    if isinstance(values, Mapping) and name in values:
        return _f(values.get(name))
    try:
        import mlb_historical_derived_features_v1 as module
        return _f(module.derive(signal).get(name))
    except Exception:
        return 0.0


def _pattern(signal: Mapping[str, Any], name: str) -> float:
    values = signal.get("oddsPatternFeatures")
    if isinstance(values, Mapping) and name in values:
        return _f(values.get(name))
    try:
        import mlb_odds_pattern_features_v1 as module
        return _f(module.extract(signal).get(name))
    except Exception:
        return 0.0


def _v8(signal: Mapping[str, Any], name: str):
    values = signal.get("v8TrainableFeatures")
    if not isinstance(values, Mapping) or values.get(name) in (None, ""):
        return None
    return _f(values.get(name))


def _fundamental(signal: Mapping[str, Any], names: Sequence[str]):
    for source in (signal.get("fundamentals"), signal.get("fundamentalsSnapshotV2"), signal):
        if not isinstance(source, Mapping):
            continue
        for name in names:
            if source.get(name) not in (None, ""):
                return _f(source.get(name))
    return None


def _field(kind: str, feature: str) -> str:
    return f"supervised{kind}{feature}"


def _defaults() -> Dict[str, float]:
    out = {"supervisedEnabled": 0.0, "supervisedIntercept": 0.0, "supervisedBlend": 0.0, "supervisedTemperature": 1.0}
    for feature in FEATURES:
        out[_field("Coefficient", feature)] = 0.0
        out[_field("Mean", feature)] = 0.0
        out[_field("Scale", feature)] = 1.0
    return out


def _bounds() -> Dict[str, Tuple[float, float]]:
    out = {"supervisedEnabled": (0.0, 1.0), "supervisedIntercept": (-20.0, 20.0), "supervisedBlend": (0.0, 1.0), "supervisedTemperature": (0.5, 4.0)}
    for feature in FEATURES:
        out[_field("Coefficient", feature)] = (-20.0, 20.0)
        out[_field("Mean", feature)] = (-10000.0, 10000.0)
        out[_field("Scale", feature)] = (1e-8, 10000.0)
    return out


def _expansion_value(expansion: Mapping[str, Any], team: str, market: str, suffix: str):
    value = expansion.get(f"{market}_{team.replace(' ', '_')}{suffix}")
    return _f(value) if value not in (None, "") else None


def _v8_trainable(game: Mapping[str, Any], latest: Mapping[str, Any], side: str) -> Dict[str, Any]:
    expansion = latest.get("oddsMarketExpansionFeatures")
    if not isinstance(expansion, Mapping):
        return {"version": FEATURE_VERSION, "available": False, "observationCount": 0}
    team = str(game.get("homeTeam") if side == "home" else game.get("awayTeam") or "")
    home_div = expansion.get("homeStarterBullpenSpreadDivergence")
    values = {
        "h2hMedianImpliedProbability": _expansion_value(expansion, team, "h2h", "MedianImpliedProbability"),
        "firstFiveH2HMedianImpliedProbability": _expansion_value(expansion, team, "h2h_1st_5_innings", "MedianImpliedProbability"),
        "fullGameSpreadMedian": _expansion_value(expansion, team, "spreads", "MedianPoint"),
        "firstFiveSpreadMedian": _expansion_value(expansion, team, "spreads_1st_5_innings", "MedianPoint"),
        "impliedLateInningRunEnvironment": expansion.get("impliedLateInningRunEnvironment"),
        "starterBullpenSpreadDivergence": home_div if side == "home" else (-_f(home_div) if home_div not in (None, "") else None),
    }
    return {"version": FEATURE_VERSION, "available": any(v not in (None, "") for v in values.values()), "observationCount": sum(v not in (None, "") for v in values.values()), **values}


def _base_outputs(home: Mapping[str, Any], away: Mapping[str, Any], policy: Mapping[str, Any]):
    if _ORIGINAL_SELECT is None or _ORIGINAL_COMPLEMENT is None:
        raise RuntimeError("supervised runtime not installed")
    base = copy.deepcopy(dict(policy)); base["supervisedEnabled"] = 0.0
    selected, scored_home, scored_away = _ORIGINAL_SELECT(home, away, base)
    probability, _ = _ORIGINAL_COMPLEMENT(scored_home, scored_away)
    return selected, scored_home, scored_away, _clip(_f(probability, 0.5), 1e-6, 1.0 - 1e-6)


def pair_features(home: Mapping[str, Any], away: Mapping[str, Any], policy: Mapping[str, Any]) -> Dict[str, float]:
    _, base_home, base_away, base_probability = _base_outputs(home, away, policy)
    home_market, away_market = _market(home), _market(away)
    market_probability = home_market / max(1e-12, home_market + away_market)
    home_f5, away_f5 = _v8(home, "firstFiveH2HMedianImpliedProbability"), _v8(away, "firstFiveH2HMedianImpliedProbability")
    f5_logit = _logit(home_f5 / (home_f5 + away_f5)) if home_f5 is not None and away_f5 is not None and home_f5 + away_f5 > 0 else 0.0
    home_spread, away_spread = _v8(home, "fullGameSpreadMedian"), _v8(away, "fullGameSpreadMedian")
    spread = _clip((away_spread - home_spread) / 3.0, -2.0, 2.0) if home_spread is not None and away_spread is not None else 0.0
    home_starter = _fundamental(home, ("starterQuality", "startingPitcherQuality")); away_starter = _fundamental(away, ("starterQuality", "startingPitcherQuality"))
    home_bullpen = _fundamental(home, ("bullpenQuality", "bullpenStrength")); away_bullpen = _fundamental(away, ("bullpenQuality", "bullpenStrength"))
    home_lineup = _fundamental(home, ("lineupQuality", "lineupStrength")); away_lineup = _fundamental(away, ("lineupQuality", "lineupStrength"))
    observed = sum(v is not None for v in (home_f5, away_f5, home_spread, away_spread, home_starter, away_starter, home_bullpen, away_bullpen, home_lineup, away_lineup))
    home_side, away_side = str(home.get("marketSide") or "").lower(), str(away.get("marketSide") or "").lower()
    return {
        "marketLogit": _logit(market_probability),
        "ruleLogit": _logit(base_probability),
        "ruleScoreDiff": (_f(base_home.get("score"), 50.0) - _f(base_away.get("score"), 50.0)) / 25.0,
        "deltaDiff": _f(home.get("delta")) - _f(away.get("delta")),
        "velocityDiff": _temporal(home, "60m", "velocityPpHr") - _temporal(away, "60m", "velocityPpHr"),
        "accelerationDiff": _temporal(home, "180m", "accelerationPpHr2") - _temporal(away, "180m", "accelerationPpHr2"),
        "volatilitySum": _temporal(home, "180m", "volatilityPpPerPull") + _temporal(away, "180m", "volatilityPpPerPull"),
        "divergenceMean": (_f(home.get("bookDivergence")) + _f(away.get("bookDivergence"))) / 2.0,
        "coverageMin": min(_temporal(home, "full", "coverageRatio"), _temporal(away, "full", "coverageRatio")),
        "favoriteDirection": 1.0 if home_side == "favorite" else -1.0 if away_side == "favorite" else 0.0,
        "derivedMovementDiff": _derived(home, "movementSqrt") - _derived(away, "movementSqrt"),
        "derivedAgreementDiff": _derived(home, "agreementMomentum") - _derived(away, "agreementMomentum"),
        "patternFingerprintDiff": _pattern(home, "fingerprintScore") - _pattern(away, "fingerprintScore"),
        "patternRegimeDiff": _pattern(home, "regimeScore") - _pattern(away, "regimeScore"),
        "v8FirstFiveLogit": f5_logit,
        "v8SpreadDirection": spread,
        "fundamentalsDiff": (_f(home_starter) - _f(away_starter)) + (_f(home_bullpen) - _f(away_bullpen)) + (_f(home_lineup) - _f(away_lineup)),
        "featureAvailability": observed / 10.0,
    }


def _examples(records: Sequence[Mapping[str, Any]], dates: Iterable[str], policy: Mapping[str, Any]):
    allowed = {str(day) for day in dates}; out = []
    for row in records:
        day = str(row.get("slateDateEt") or "")
        if day not in allowed: continue
        values = pair_features(row.get("homeSignal") or {}, row.get("awaySignal") or {}, policy)
        out.append((day, [_f(values.get(name)) for name in FEATURES], int(row.get("homeWon") or 0)))
    return out


def _fit(examples, l2: float):
    if not examples: raise RuntimeError("supervised fit received no examples")
    n = float(len(examples)); means = [sum(row[1][i] for row in examples)/n for i in range(len(FEATURES))]
    scales = [max(1e-6, math.sqrt(sum((row[1][i]-means[i])**2 for row in examples)/n)) for i in range(len(FEATURES))]
    counts = Counter(day for day, _, _ in examples); weights = [1.0/max(1, counts[day]) for day, _, _ in examples]
    normalizer = len(weights)/max(1e-12, sum(weights)); weights = [w*normalizer for w in weights]
    label_mean = sum(w*y for w, (_, _, y) in zip(weights, examples))/max(1e-12, sum(weights))
    intercept, coefficients = _logit(_clip(label_mean, .02, .98)), [0.0]*len(FEATURES)
    previous, stable = float("inf"), 0
    for epoch in range(EPOCHS):
        gi, gradients, loss, total = 0.0, [0.0]*len(FEATURES), 0.0, 0.0
        for weight, (_, raw, label) in zip(weights, examples):
            x = [_clip((raw[i]-means[i])/scales[i], -8.0, 8.0) for i in range(len(FEATURES))]
            probability = _sigmoid(intercept + sum(c*v for c,v in zip(coefficients,x))); error = probability-label
            gi += weight*error
            for i, value in enumerate(x): gradients[i] += weight*error*value
            p = _clip(probability, 1e-9, 1-1e-9); loss += weight*(-(label*math.log(p)+(1-label)*math.log(1-p))); total += weight
        total = max(1e-12,total); step = LEARNING_RATE/math.sqrt(1.0+epoch/25.0); intercept -= step*gi/total
        coefficients = [_clip(c-step*(gradients[i]/total+l2*c), -12.0, 12.0) for i,c in enumerate(coefficients)]
        loss = loss/total + .5*l2*sum(c*c for c in coefficients)
        stable = stable+1 if abs(previous-loss)<1e-7 else 0
        if stable >= 10: break
        previous = loss
    return intercept, coefficients, means, scales, {"epochsCompleted": epoch+1, "trainingLoss": round(previous,10), "l2": l2, "trainingExampleCount": len(examples), "trainingDayCount": len(counts), "dayBalancedWeights": True}


def _policy(base: Mapping[str, Any], model, blend: float, temperature: float) -> Dict[str, Any]:
    intercept, coefficients, means, scales, _ = model; out = copy.deepcopy(dict(base)); out.update(_defaults())
    out.update({"supervisedEnabled":1.0,"supervisedIntercept":_clip(_f(intercept),-20,20),"supervisedBlend":_clip(blend,0,1),"supervisedTemperature":_clip(temperature,.5,4)})
    for i,name in enumerate(FEATURES):
        out[_field("Coefficient",name)] = _clip(_f(coefficients[i]),-20,20); out[_field("Mean",name)] = _clip(_f(means[i]),-10000,10000); out[_field("Scale",name)] = _clip(max(1e-8,_f(scales[i],1)),1e-8,10000)
    return out


def _home_probability(home: Mapping[str, Any], away: Mapping[str, Any], policy: Mapping[str, Any]):
    _,_,_,base = _base_outputs(home,away,policy); values = pair_features(home,away,policy); linear = _f(policy.get("supervisedIntercept"))
    for name in FEATURES:
        mean,scale = _f(policy.get(_field("Mean",name))),max(1e-8,_f(policy.get(_field("Scale",name)),1)); z = _clip((_f(values.get(name))-mean)/scale,-8,8)
        linear += _f(policy.get(_field("Coefficient",name)))*z
    model = _sigmoid(linear/_clip(_f(policy.get("supervisedTemperature"),1),.5,4)); blend = _clip(_f(policy.get("supervisedBlend")),0,1)
    probability = _clip(_sigmoid((1-blend)*_logit(base)+blend*_logit(model)),.02,.98)
    return probability, model, values


def _rank(metrics: Mapping[str, Any]):
    return (_f(metrics.get("dailyPassRate")),_f(metrics.get("minimumDailyAccuracy")),_f(metrics.get("meanDailyAccuracy")),_f(metrics.get("overallAccuracy")),-_f(metrics.get("brierScore"),1),-_f(metrics.get("logLoss"),10))


def _folds(dates: Sequence[str]):
    dates = sorted({str(day) for day in dates}); n = len(dates)
    if n < 30:
        split = max(1,n-max(5,n//5)); return [(dates[:split],dates[split:])] if split<n else []
    width = max(8,min(20,n//8)); out=[]
    for start in sorted({max(20,int(n*f)) for f in (.55,.70,.82)}):
        if dates[:start] and dates[start:min(n,start+width)]: out.append((dates[:start],dates[start:min(n,start+width)]))
    return out


def fit_supervised_policy(optimizer: Any, records, train_dates, base_policy):
    folds = _folds(train_dates)
    if not folds: raise RuntimeError("not enough chronological dates for supervised inner validation")
    candidates=[]
    for l2 in L2_GRID:
        models=[(valid,_fit(_examples(records,fit,base_policy),l2)) for fit,valid in folds]
        for blend in BLEND_GRID:
            for temperature in TEMPERATURE_GRID:
                metrics=[optimizer.evaluate_policy(records,_policy(base_policy,model,blend,temperature),valid) for valid,model in models]
                aggregate={"dailyPassRate":sum(_f(m.get("dailyPassRate")) for m in metrics)/len(metrics),"minimumDailyAccuracy":min(_f(m.get("minimumDailyAccuracy")) for m in metrics),"meanDailyAccuracy":sum(_f(m.get("meanDailyAccuracy")) for m in metrics)/len(metrics),"overallAccuracy":sum(_f(m.get("overallAccuracy")) for m in metrics)/len(metrics),"brierScore":sum(_f(m.get("brierScore"),1) for m in metrics)/len(metrics),"logLoss":sum(_f(m.get("logLoss"),10) for m in metrics)/len(metrics)}
                candidates.append({"l2":l2,"blend":blend,"temperature":temperature,"metrics":aggregate,"rank":_rank(aggregate)})
    best=max(candidates,key=lambda row:row["rank"]); final=_fit(_examples(records,train_dates,base_policy),best["l2"]); policy=_policy(base_policy,final,best["blend"],best["temperature"])
    errors=optimizer.policy_runtime.validate_policy(policy)
    if errors: raise RuntimeError("supervised policy invalid: "+",".join(errors))
    return policy,{"version":VERSION,"featureVersion":FEATURE_VERSION,"features":list(FEATURES),"featureCount":len(FEATURES),"innerFoldCount":len(folds),"hyperparameterCandidateCount":len(candidates),"selectedL2":best["l2"],"selectedBlend":best["blend"],"selectedTemperature":best["temperature"],"selectedInnerMetrics":best["metrics"],"finalFit":final[4],"holdoutLabelsUsedForFitOrSelection":False}


def _gate(optimizer, clean, cfg, baseline_validation, baseline_holdout, train, validation, holdout, overfit):
    errors=[]
    if len(clean)<cfg.minimum_settled_games: errors.append("settled_game_floor_not_met")
    if int(train.get("gameCount") or 0)<cfg.minimum_training_games: errors.append("training_game_floor_not_met")
    for name,metrics,games,days in (("walk_forward",validation,cfg.minimum_walk_forward_games,cfg.minimum_walk_forward_days),("untouched_holdout",holdout,cfg.minimum_untouched_holdout_games,cfg.minimum_holdout_days)):
        if int(metrics.get("gameCount") or 0)<games: errors.append(f"{name}_game_floor_not_met")
        if int(metrics.get("dayCount") or 0)<days: errors.append(f"{name}_day_floor_not_met")
        if _f(metrics.get("minimumSlateCoverage"))<1-1e-12: errors.append(f"{name}_exact_slate_coverage_failed")
        if _f(metrics.get("dailyPassRate"))<1-1e-12: errors.append(f"{name}_contains_day_below_80_percent")
        if _f(metrics.get("minimumDailyAccuracy"))+1e-12<cfg.minimum_daily_accuracy: errors.append(f"{name}_minimum_daily_accuracy_failed")
        if _f(metrics.get("meanDailyAccuracy"))+1e-12<cfg.minimum_daily_accuracy: errors.append(f"{name}_mean_daily_accuracy_failed")
    if not all(row.get("postLockDataExcluded") is True for row in clean): errors.append("post_lock_exclusion_proof_missing")
    if not all(row.get("gameSpecificLockClipping") is True for row in clean): errors.append("game_specific_lock_clipping_proof_missing")
    if overfit.get("passed") is not True: errors.append("overfit_checks_failed")
    improved=_rank(validation)>_rank(baseline_validation) and _f(validation.get("meanDailyAccuracy"))>=_f(baseline_validation.get("meanDailyAccuracy"))+MIN_WF_MEAN_GAIN
    if not improved: errors.append("candidate_did_not_improve_walk_forward_daily_objective")
    if _f(holdout.get("brierScore"),1)>_f(baseline_holdout.get("brierScore"),1)+cfg.maximum_brier_degradation: errors.append("untouched_holdout_brier_degraded")
    if _f(holdout.get("logLoss"),10)>_f(baseline_holdout.get("logLoss"),10)+cfg.maximum_log_loss_degradation: errors.append("untouched_holdout_log_loss_degraded")
    return {"version":optimizer.policy_runtime.PROMOTION_GATE_VERSION,"passed":not errors,"errors":sorted(set(errors)),"settledGameCount":len(clean),"requiredSettledGameCount":cfg.minimum_settled_games,"trainingGameCount":int(train.get("gameCount") or 0),"requiredTrainingGameCount":cfg.minimum_training_games,"walkForwardGameCount":int(validation.get("gameCount") or 0),"requiredWalkForwardGameCount":cfg.minimum_walk_forward_games,"untouchedHoldoutGameCount":int(holdout.get("gameCount") or 0),"requiredUntouchedHoldoutGameCount":cfg.minimum_untouched_holdout_games,"walkForwardDayCount":int(validation.get("dayCount") or 0),"untouchedHoldoutDayCount":int(holdout.get("dayCount") or 0),"walkForwardMinimumDailyAccuracy":validation.get("minimumDailyAccuracy"),"walkForwardMeanDailyAccuracy":validation.get("meanDailyAccuracy"),"untouchedHoldoutMinimumDailyAccuracy":holdout.get("minimumDailyAccuracy"),"untouchedHoldoutMeanDailyAccuracy":holdout.get("meanDailyAccuracy"),"walkForwardSlateCoverage":validation.get("minimumSlateCoverage"),"untouchedHoldoutSlateCoverage":holdout.get("minimumSlateCoverage"),"dailyAccuracyRequirement":cfg.minimum_daily_accuracy,"dailyAccuracyTargetHigh":cfg.target_daily_accuracy_high,"walkForwardReached90PctMean":_f(validation.get("meanDailyAccuracy"))+1e-12>=cfg.target_daily_accuracy_high,"untouchedHoldoutReached90PctMean":_f(holdout.get("meanDailyAccuracy"))+1e-12>=cfg.target_daily_accuracy_high,"holdoutWasUntouchedDuringSearch":True,"chronologicalWholeSlateSplits":True,"postLockDataExcluded":all(row.get("postLockDataExcluded") is True for row in clean),"gameSpecificLockClipping":all(row.get("gameSpecificLockClipping") is True for row in clean),"overfitChecksPassed":overfit.get("passed") is True,"overfitChecks":dict(overfit),"candidateImprovedWalkForwardDailyObjective":improved,"supervisedNestedChronologicalSelection":True,"probabilityCalibrationSeparated":True,"v8TrainableFeatureContractInstalled":True}


def install_policy_runtime(policy_runtime: Any) -> None:
    policy_runtime.BASELINE_POLICY.update({k:policy_runtime.BASELINE_POLICY.get(k,v) for k,v in _defaults().items()}); policy_runtime._NUMERIC_BOUNDS.update(_bounds())
    if getattr(policy_runtime,"_INQSI_MLB_SUPERVISED_V9_POLICY_INSTALLED",False): return
    global _ORIGINAL_SELECT,_ORIGINAL_COMPLEMENT
    _ORIGINAL_SELECT=policy_runtime.select_winner; _ORIGINAL_COMPLEMENT=policy_runtime.complementary_probabilities
    def select(home_signal,away_signal,policy):
        selected,home,away=_ORIGINAL_SELECT(home_signal,away_signal,policy)
        if _f(policy.get("supervisedEnabled"))<.5: return selected,home,away
        probability,raw,features=_home_probability(home_signal,away_signal,policy); home=copy.deepcopy(dict(home)); away=copy.deepcopy(dict(away))
        for signal,p in ((home,probability),(away,1-probability)):
            score=_clip(50+12*_logit(p),0,100); signal.update({"winProbability":round(p,8),"winProbabilityPct":round(p*100,4),"optimizedWinnerScore":round(score,4),"score":round(score,4),"supervisedModelApplied":True,"supervisedModelVersion":VERSION,"supervisedFeatureVersion":FEATURE_VERSION,"supervisedRawHomeProbability":round(raw,8),"supervisedCalibrationTemperature":_f(policy.get("supervisedTemperature"),1),"supervisedBlend":_f(policy.get("supervisedBlend")),"supervisedPairFeatures":copy.deepcopy(features)})
        return (home if probability>=.5 else away),home,away
    policy_runtime.select_winner=select; policy_runtime.SUPERVISED_MODEL_VERSION=VERSION; policy_runtime._INQSI_MLB_SUPERVISED_V9_POLICY_INSTALLED=True


def install(optimizer: Any, policy_runtime: Any) -> None:
    if getattr(optimizer,"_INQSI_MLB_SUPERVISED_V9_INSTALLED",False): return
    install_policy_runtime(policy_runtime)
    global _ORIGINAL_SIGNAL
    _ORIGINAL_SIGNAL=optimizer._signal
    def signal(game,observations,side,expected_slots):
        out=_ORIGINAL_SIGNAL(game,observations,side,expected_slots); out["v8TrainableFeatures"]=_v8_trainable(game,observations[-1] if observations else {},side); out["supervisedFeatureVersion"]=FEATURE_VERSION; return out
    def search(records,config=None,*,untouched_holdout_dates=None):
        cfg=(config or optimizer.SearchConfig()).validate(); clean=[copy.deepcopy(dict(row)) for row in records]
        if len(clean)<cfg.minimum_settled_games: return {"ok":False,"version":optimizer.VERSION,"searchVersion":VERSION,"status":"ACCUMULATING_HISTORICAL_GAMES","settledGameCount":len(clean),"required":cfg.minimum_settled_games,"requiredTrainingGames":cfg.minimum_training_games,"requiredWalkForwardGames":cfg.minimum_walk_forward_games,"requiredUntouchedAuditGames":cfg.minimum_untouched_holdout_games}
        try: partitions=optimizer.chronological_partitions(clean,cfg,untouched_holdout_dates=untouched_holdout_dates)
        except optimizer.HistoricalOptimizerError as exc: return {"ok":False,"version":optimizer.VERSION,"searchVersion":VERSION,"status":"ACCUMULATING_HISTORICAL_GAMES","settledGameCount":len(clean),"required":cfg.minimum_settled_games,"requiredTrainingGames":cfg.minimum_training_games,"requiredWalkForwardGames":cfg.minimum_walk_forward_games,"requiredUntouchedAuditGames":cfg.minimum_untouched_holdout_games,"partitionReason":str(exc)}
        baseline=copy.deepcopy(policy_runtime.BASELINE_POLICY); baseline["supervisedEnabled"]=0.0
        base_train=optimizer.evaluate_policy(clean,baseline,partitions["train"],daily_target=cfg.minimum_daily_accuracy); base_validation=optimizer.evaluate_policy(clean,baseline,partitions["walkForward"],daily_target=cfg.minimum_daily_accuracy)
        supervised,diagnostics=fit_supervised_policy(optimizer,clean,partitions["train"],baseline); sup_train=optimizer.evaluate_policy(clean,supervised,partitions["train"],daily_target=cfg.minimum_daily_accuracy); sup_validation=optimizer.evaluate_policy(clean,supervised,partitions["walkForward"],daily_target=cfg.minimum_daily_accuracy); sup_overfit=optimizer._overfit_checks(sup_train,sup_validation,base_validation,cfg)
        improved=_rank(sup_validation)>_rank(base_validation) and _f(sup_validation.get("meanDailyAccuracy"))>=_f(base_validation.get("meanDailyAccuracy"))+MIN_WF_MEAN_GAIN
        use_supervised=improved and sup_overfit.get("passed") is True; candidate=supervised if use_supervised else baseline; train=sup_train if use_supervised else base_train; validation=sup_validation if use_supervised else base_validation; overfit=sup_overfit if use_supervised else optimizer._overfit_checks(base_train,base_validation,base_validation,cfg)
        holdout=optimizer.evaluate_policy(clean,candidate,partitions["untouchedHoldout"],daily_target=cfg.minimum_daily_accuracy); base_holdout=optimizer.evaluate_policy(clean,baseline,partitions["untouchedHoldout"],daily_target=cfg.minimum_daily_accuracy); gate=_gate(optimizer,clean,cfg,base_validation,base_holdout,train,validation,holdout,overfit)
        diagnostics.update({"outerWalkForwardAccepted":use_supervised,"outerWalkForwardBaseline":base_validation,"outerWalkForwardSupervised":sup_validation,"outerOverfitChecks":sup_overfit,"holdoutEvaluatedAfterFreeze":True,"probabilityModel":"day-balanced L2 logistic regression","calibration":"nested temperature scaling plus logit-space market blend","randomPolicySearchDisabled":True})
        return {"ok":True,"version":optimizer.VERSION,"searchVersion":VERSION,"status":"PROMOTION_GATE_PASSED" if gate["passed"] else "CANDIDATE_REJECTED","datasetFingerprint":optimizer.dataset_fingerprint(clean),"settledGameCount":len(clean),"slateDateCount":len({str(row.get("slateDateEt") or "") for row in clean}),"partitions":partitions,"holdoutLabelReadPolicy":"candidate frozen after nested training and walk-forward acceptance; untouched labels read once","holdoutDefinition":{"explicitFreshWindow":bool(untouched_holdout_dates),"dates":list(partitions["untouchedHoldout"]),"strictlyAfterDevelopment":bool(partitions["untouchedHoldout"] and partitions["walkForward"] and min(partitions["untouchedHoldout"])>max(partitions["walkForward"]))},"candidateCountEvaluated":diagnostics["hyperparameterCandidateCount"],"compiledCandidateEvaluations":0,"richProofPolicyEvaluations":diagnostics["hyperparameterCandidateCount"]*diagnostics["innerFoldCount"]+6,"searchAcceleration":"nested_day_balanced_logistic_no_random_rule_sweep","overfitCandidateCountRejected":0 if sup_overfit.get("passed") else 1,"retainedImprovementCount":1 if use_supervised else 0,"retainedImprovements":[{"policyDigest":policy_runtime.policy_digest(supervised),"walkForwardDailyPassRate":sup_validation.get("dailyPassRate"),"walkForwardMinimumDailyAccuracy":sup_validation.get("minimumDailyAccuracy"),"walkForwardMeanDailyAccuracy":sup_validation.get("meanDailyAccuracy"),"walkForwardBrierScore":sup_validation.get("brierScore")}] if use_supervised else [],"baseline":{"policy":baseline,"policyDigest":policy_runtime.policy_digest(baseline),"train":base_train,"walkForward":base_validation,"untouchedHoldout":base_holdout},"candidate":{"policy":candidate,"policyDigest":policy_runtime.policy_digest(candidate),"train":train,"walkForward":validation,"untouchedHoldout":holdout},"supervisedDiagnostics":diagnostics,"promotionGate":gate}
    optimizer._signal=signal; optimizer.search=search; optimizer.SUPERVISED_MODEL_VERSION=VERSION; optimizer.SUPERVISED_FEATURE_VERSION=FEATURE_VERSION; optimizer._INQSI_MLB_SUPERVISED_V9_INSTALLED=True
