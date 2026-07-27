"""Season-aware, day-balanced MLB supervised shadow challenger.

This challenger fixes two concrete distribution-shift defects in V2.1:

* team form and Elo state crossed the offseason without any reset; and
* optimization treated every game equally even though promotion is measured by
  complete-slate daily accuracy.

The model remains retrospective, shadow-only, and ineligible for production
promotion until a new prospective audit begins after architecture freeze.
"""
from __future__ import annotations

import copy
import math
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import mlb_supervised_features_v2 as features
import mlb_supervised_model_v2 as base

VERSION = "MLB-SUPERVISED-SHADOW-v2.2-season-aware-day-balanced-recency-robust"
FEATURE_VERSION = "MLB-SUPERVISED-FEATURES-v2.2-season-boundary-reset"
DAILY_TARGET = 0.80
MAX_BRIER_DEGRADATION = 0.005
MAX_LOG_LOSS_DEGRADATION = 0.010
MAX_ECE = 0.080


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _logit(probability: float) -> float:
    probability = _clip(float(probability), 1e-6, 1.0 - 1e-6)
    return math.log(probability / (1.0 - probability))


def _sigmoid(value: float) -> float:
    value = _clip(value, -35.0, 35.0)
    return 1.0 / (1.0 + math.exp(-value))


@dataclass
class SeasonLedger:
    elo: float = 1500.0
    season_games: int = 0
    recent: Tuple[int, ...] = ()
    streak: int = 0
    last_date: Optional[str] = None


def _rate(row: SeasonLedger, window: int) -> float:
    values = row.recent[-window:]
    return sum(values) / len(values) if values else 0.5


def _rest(row: SeasonLedger, game_day: str) -> float:
    if not row.last_date:
        return 3.0
    try:
        return float(
            _clip(
                (date.fromisoformat(game_day) - date.fromisoformat(row.last_date)).days,
                0,
                10,
            )
        )
    except Exception:
        return 3.0


def _season_history(
    ledger: Mapping[str, SeasonLedger], home: str, away: str, game_day: str
) -> Dict[str, float]:
    h = ledger.get(home, SeasonLedger())
    a = ledger.get(away, SeasonLedger())
    return {
        "team_elo_diff": (h.elo - a.elo) / 400.0,
        "team_home_elo": (h.elo - 1500.0) / 400.0,
        "team_away_elo": (a.elo - 1500.0) / 400.0,
        "team_recent10_diff": _rate(h, 10) - _rate(a, 10),
        "team_recent30_diff": _rate(h, 30) - _rate(a, 30),
        "team_home_recent10": _rate(h, 10) - 0.5,
        "team_away_recent10": _rate(a, 10) - 0.5,
        "team_streak_diff": _clip(h.streak - a.streak, -10, 10) / 10.0,
        "team_rest_diff": (_rest(h, game_day) - _rest(a, game_day)) / 7.0,
        "team_history_experience": math.log1p(min(h.season_games, a.season_games))
        / math.log(170.0),
        "team_history_available": 1.0
        if h.season_games >= 5 and a.season_games >= 5
        else 0.0,
    }


def _shrink_for_new_season(row: SeasonLedger) -> SeasonLedger:
    # Retain only a small, explicit prior-season Elo prior. Recent form, streak,
    # rest, and experience restart so roster turnover is not treated as current
    # season evidence.
    return SeasonLedger(
        elo=1500.0 + 0.25 * (row.elo - 1500.0),
        season_games=0,
        recent=(),
        streak=0,
        last_date=None,
    )


def add_season_aware_team_history(
    records: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for raw in records:
        row = copy.deepcopy(dict(raw))
        game_day = str(row.get("slateDateEt") or "")
        if game_day:
            by_day.setdefault(game_day, []).append(row)

    ledger: Dict[str, SeasonLedger] = {}
    output: List[Dict[str, Any]] = []
    active_year: Optional[int] = None
    for game_day in sorted(by_day):
        year = date.fromisoformat(game_day).year
        if active_year is not None and year != active_year:
            ledger = {team: _shrink_for_new_season(value) for team, value in ledger.items()}
        active_year = year

        slate = sorted(
            by_day[game_day], key=lambda row: str(row.get("officialGamePk") or "")
        )
        for row in slate:
            home = features._team(row.get("homeTeam"))
            away = features._team(row.get("awayTeam"))
            row["teamHistoryFeatures"] = _season_history(ledger, home, away, game_day)
            row["teamHistoryLeakageBoundary"] = (
                "strictly_prior_complete_slate_days_with_offseason_reset"
            )
            row["teamHistoryFeatureVersion"] = FEATURE_VERSION
            output.append(row)

        # Update only after every game on the date has received features. This is
        # conservative for doubleheaders and prohibits same-slate outcome leakage.
        for row in slate:
            home = features._team(row.get("homeTeam"))
            away = features._team(row.get("awayTeam"))
            home_won = int(row.get("homeWon") or 0)
            old_home = ledger.get(home, SeasonLedger())
            old_away = ledger.get(away, SeasonLedger())
            expected = 1.0 / (1.0 + 10.0 ** ((old_away.elo - old_home.elo) / 400.0))

            def update(old: SeasonLedger, won: int, elo: float) -> SeasonLedger:
                if won:
                    streak = old.streak + 1 if old.streak >= 0 else 1
                else:
                    streak = old.streak - 1 if old.streak <= 0 else -1
                return SeasonLedger(
                    elo=elo,
                    season_games=old.season_games + 1,
                    recent=tuple((list(old.recent) + [won])[-30:]),
                    streak=streak,
                    last_date=game_day,
                )

            ledger[home] = update(
                old_home, home_won, old_home.elo + 20.0 * (home_won - expected)
            )
            ledger[away] = update(
                old_away,
                1 - home_won,
                old_away.elo + 20.0 * ((1 - home_won) - (1.0 - expected)),
            )
    return sorted(
        output, key=lambda row: (str(row.get("slateDateEt") or ""), str(row.get("officialGamePk") or ""))
    )


def prepare_examples(records: Sequence[Mapping[str, Any]]) -> List[features.Example]:
    examples: List[features.Example] = []
    for row in add_season_aware_team_history(records):
        game_day = str(row.get("slateDateEt") or "")
        if not game_day or row.get("homeWon") not in {0, 1}:
            continue
        examples.append(
            features.Example(
                day=game_day,
                game_id=str(row.get("officialGamePk") or ""),
                outcome=int(row.get("homeWon") or 0),
                market_probability=features._market_home_probability(row),
                features=features.feature_map(row),
                home_team=str(row.get("homeTeam") or ""),
                away_team=str(row.get("awayTeam") or ""),
            )
        )
    return sorted(examples, key=lambda row: (row.day, row.game_id))


@dataclass(frozen=True)
class WeightingConfig:
    name: str
    day_balanced: bool
    half_life_days: Optional[float] = None
    current_season_boost: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "dayBalanced": self.day_balanced,
            "halfLifeDays": self.half_life_days,
            "currentSeasonBoost": self.current_season_boost,
        }


WEIGHTING_CONFIGS: Tuple[WeightingConfig, ...] = (
    WeightingConfig("uniform", False),
    WeightingConfig("day_balanced", True),
    WeightingConfig("day_balanced_recent_180", True, half_life_days=180.0),
    WeightingConfig("day_balanced_current_season_3x", True, current_season_boost=3.0),
)


def _example_weights(
    examples: Sequence[features.Example], config: WeightingConfig
) -> List[float]:
    if not examples:
        return []
    counts = Counter(row.day for row in examples)
    reference = max(date.fromisoformat(row.day) for row in examples)
    reference_year = reference.year
    values: List[float] = []
    for row in examples:
        value = 1.0 / counts[row.day] if config.day_balanced else 1.0
        row_date = date.fromisoformat(row.day)
        if config.half_life_days:
            age = max(0, (reference - row_date).days)
            value *= 0.5 ** (age / config.half_life_days)
        if row_date.year == reference_year:
            value *= config.current_season_boost
        values.append(value)
    normalizer = len(values) / max(1e-12, sum(values))
    return [value * normalizer for value in values]


def _weighted_standardizer(
    examples: Sequence[features.Example], names: Sequence[str], weights: Sequence[float]
) -> base.Standardizer:
    feature_names = tuple(names)
    total = max(1e-12, sum(weights))
    means: List[float] = []
    scales: List[float] = []
    for name in feature_names:
        values = [float(row.features.get(name, 0.0)) for row in examples]
        mean = sum(weight * value for weight, value in zip(weights, values)) / total
        variance = (
            sum(weight * (value - mean) ** 2 for weight, value in zip(weights, values))
            / total
        )
        scale = math.sqrt(max(0.0, variance))
        means.append(mean)
        scales.append(scale if scale >= 1e-8 else 1.0)
    return base.Standardizer(feature_names, tuple(means), tuple(scales))


def fit_weighted_residual_logistic(
    examples: Sequence[features.Example],
    *,
    feature_group: str,
    l2: float,
    seed: int,
    weighting: WeightingConfig,
    steps: int = 180,
    batch_size: int = 256,
    learning_rate: float = 0.025,
) -> base.ResidualLogisticModel:
    if feature_group not in features.FEATURE_GROUPS:
        raise ValueError("unknown feature group")
    if not examples:
        raise ValueError("cannot fit without examples")
    names = tuple(features.FEATURE_GROUPS[feature_group])
    sample_weights = _example_weights(examples, weighting)
    standardizer = _weighted_standardizer(examples, names, sample_weights)
    vectors = [standardizer.transform(row) for row in examples]
    outcomes = [float(row.outcome) for row in examples]
    offsets = [_logit(row.market_probability) for row in examples]
    dimension = len(names)
    coefficients = [0.0] * dimension
    intercept = 0.0
    m = [0.0] * (dimension + 1)
    v = [0.0] * (dimension + 1)
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    indices = list(range(len(examples)))
    random.Random(seed).shuffle(indices)
    batch_size = max(16, min(batch_size, len(indices)))

    for step in range(1, max(1, steps) + 1):
        start = ((step - 1) * batch_size) % len(indices)
        batch = (indices + indices)[start : start + batch_size]
        gradient = [0.0] * dimension
        gradient_intercept = 0.0
        batch_weight = 0.0
        for index in batch:
            vector = vectors[index]
            probability = _sigmoid(
                offsets[index]
                + intercept
                + sum(weight * value for weight, value in zip(coefficients, vector))
            )
            sample_weight = sample_weights[index]
            error = (probability - outcomes[index]) * sample_weight
            gradient_intercept += error
            batch_weight += sample_weight
            for position, value in enumerate(vector):
                gradient[position] += error * value
        scale = 1.0 / max(1e-12, batch_weight)
        gradients = [gradient_intercept * scale] + [
            value * scale + l2 * coefficients[index]
            for index, value in enumerate(gradient)
        ]
        parameters = [intercept] + coefficients
        for position, value in enumerate(gradients):
            m[position] = beta1 * m[position] + (1.0 - beta1) * value
            v[position] = beta2 * v[position] + (1.0 - beta2) * value * value
            m_hat = m[position] / (1.0 - beta1**step)
            v_hat = v[position] / (1.0 - beta2**step)
            parameters[position] -= learning_rate * m_hat / (math.sqrt(v_hat) + epsilon)
        intercept = _clip(parameters[0], -2.5, 2.5)
        coefficients = [_clip(value, -3.0, 3.0) for value in parameters[1:]]

    return base.ResidualLogisticModel(
        feature_group,
        standardizer,
        tuple(coefficients),
        intercept,
        float(l2),
        int(steps),
        int(seed),
    )


def _calibration_eligible(metrics: Mapping[str, Any], market: Mapping[str, Any]) -> bool:
    return bool(
        _f(metrics.get("brierScore"), 1.0)
        <= _f(market.get("brierScore"), 1.0) + MAX_BRIER_DEGRADATION
        and _f(metrics.get("logLoss"), 10.0)
        <= _f(market.get("logLoss"), 10.0) + MAX_LOG_LOSS_DEGRADATION
        and _f(metrics.get("expectedCalibrationError"), 1.0) <= MAX_ECE
    )


def _robust_key(candidate: Mapping[str, Any]) -> Tuple[float, ...]:
    metrics = candidate["oofMetrics"]
    market = candidate["oofMarketBaseline"]
    folds = candidate["folds"]
    fold_means = [_f(row["metrics"].get("meanDailyAccuracy")) for row in folds]
    fold_passes = [_f(row["metrics"].get("dailyPassRate")) for row in folds]
    recent = folds[-1]["metrics"]
    regression = max(
        0.0,
        _f(market.get("meanDailyAccuracy")) - _f(metrics.get("meanDailyAccuracy")),
        _f(market.get("overallAccuracy")) - _f(metrics.get("overallAccuracy")),
    )
    return (
        0.0 if _calibration_eligible(metrics, market) else 1.0,
        1.0 if regression > 0.005 else 0.0,
        -min(fold_passes),
        -min(fold_means),
        -_f(recent.get("dailyPassRate")),
        -_f(recent.get("meanDailyAccuracy")),
        -_f(metrics.get("dailyPassRate")),
        -_f(metrics.get("minimumDailyAccuracy")),
        -_f(metrics.get("meanDailyAccuracy")),
        -_f(metrics.get("overallAccuracy")),
        _f(metrics.get("logLoss"), 10.0),
        _f(metrics.get("brierScore"), 1.0),
    )


def nested_select(
    examples: Sequence[features.Example], train_days: Sequence[str], *, seed: int = 260726
) -> Dict[str, Any]:
    folds = base.inner_expanding_folds(train_days)
    l2_values = (0.02, 0.20)
    # The fundamentals-only group is equivalent to the team group while frozen
    # fundamentals coverage is zero. The full group remains so V8 H2H features
    # can compete immediately and future frozen fundamentals can enter safely.
    groups = (
        "market",
        "market_temporal",
        "market_temporal_team",
        "market_temporal_team_fundamentals_v8",
    )
    candidates: List[Dict[str, Any]] = []
    for group_index, group in enumerate(groups):
        for l2_index, l2 in enumerate(l2_values):
            for weighting_index, weighting in enumerate(WEIGHTING_CONFIGS):
                fold_rows: List[Dict[str, Any]] = []
                probabilities: List[float] = []
                outcomes: List[int] = []
                oof_examples: List[features.Example] = []
                for fold_index, (inner_train_days, validation_days) in enumerate(folds):
                    inner_train = base._subset(examples, inner_train_days)
                    validation = base._subset(examples, validation_days)
                    model = fit_weighted_residual_logistic(
                        inner_train,
                        feature_group=group,
                        l2=l2,
                        weighting=weighting,
                        seed=(
                            seed
                            + group_index * 10000
                            + l2_index * 1000
                            + weighting_index * 100
                            + fold_index
                        ),
                        steps=160,
                    )
                    values = [model.raw_probability(row) for row in validation]
                    metrics = base.evaluate_probabilities(validation, values)
                    market = base._market_metrics(validation)
                    fold_rows.append(
                        {
                            "fold": fold_index + 1,
                            "trainFirstDate": min(inner_train_days),
                            "trainLastDate": max(inner_train_days),
                            "validationFirstDate": min(validation_days),
                            "validationLastDate": max(validation_days),
                            "metrics": metrics,
                            "marketBaseline": market,
                        }
                    )
                    probabilities.extend(values)
                    outcomes.extend(row.outcome for row in validation)
                    oof_examples.extend(validation)
                aggregate = base.evaluate_probabilities(oof_examples, probabilities)
                market_aggregate = base._market_metrics(oof_examples)
                candidate = {
                    "featureGroup": group,
                    "l2": l2,
                    "weighting": weighting,
                    "folds": fold_rows,
                    "oofMetrics": aggregate,
                    "oofMarketBaseline": market_aggregate,
                    "oofProbabilities": probabilities,
                    "oofOutcomes": outcomes,
                }
                candidate["selectionKey"] = _robust_key(candidate)
                candidates.append(candidate)

    selected = min(candidates, key=lambda row: tuple(row["selectionKey"]))
    ablations: Dict[str, Any] = {}
    for group in groups:
        row = min(
            (candidate for candidate in candidates if candidate["featureGroup"] == group),
            key=lambda candidate: tuple(candidate["selectionKey"]),
        )
        ablations[group] = {
            "l2": row["l2"],
            "weighting": row["weighting"].to_dict(),
            "selectionKey": list(row["selectionKey"]),
            "oofMetrics": row["oofMetrics"],
            "oofMarketBaseline": row["oofMarketBaseline"],
            "folds": row["folds"],
        }
    return {
        "selectedFeatureGroup": selected["featureGroup"],
        "selectedL2": selected["l2"],
        "selectedWeighting": selected["weighting"].to_dict(),
        "selectedRobustKey": list(selected["selectionKey"]),
        "selectedOofMetrics": selected["oofMetrics"],
        "selectedOofMarketBaseline": selected["oofMarketBaseline"],
        "selectedOofProbabilities": selected["oofProbabilities"],
        "selectedOofOutcomes": selected["oofOutcomes"],
        "ablation": ablations,
        "candidateCount": len(candidates),
        "foldCount": len(folds),
        "selectionUsedUntouchedAudit": False,
        "selectionObjective": {
            "calibrationFailClosed": True,
            "directionalRegressionTolerance": 0.005,
            "foldRobustnessRequired": True,
            "mostRecentInnerFoldExplicitlyRanked": True,
            "dailySlateMetricsPrimary": True,
        },
    }


def train_and_evaluate(
    records: Sequence[Mapping[str, Any]],
    *,
    explicit_audit_days: Optional[Iterable[str]] = None,
    seed: int = 260726,
) -> Dict[str, Any]:
    examples = prepare_examples(records)
    partitions = base.chronological_partitions(
        examples, explicit_audit_days=explicit_audit_days
    )
    train = base._subset(examples, partitions["train"])
    walk_forward = base._subset(examples, partitions["walkForward"])
    audit = base._subset(examples, partitions["untouchedAudit"])
    selection = nested_select(examples, partitions["train"], seed=seed)
    calibrator = base.fit_platt(
        selection["selectedOofProbabilities"], selection["selectedOofOutcomes"]
    )
    weighting = next(
        config
        for config in WEIGHTING_CONFIGS
        if config.name == selection["selectedWeighting"]["name"]
    )
    model = fit_weighted_residual_logistic(
        train,
        feature_group=selection["selectedFeatureGroup"],
        l2=float(selection["selectedL2"]),
        weighting=weighting,
        seed=seed + 9000,
        steps=550,
    )
    train_metrics = base.evaluate_probabilities(train, base._predict(model, calibrator, train))
    walk_metrics = base.evaluate_probabilities(
        walk_forward, base._predict(model, calibrator, walk_forward)
    )
    audit_metrics = base.evaluate_probabilities(
        audit, base._predict(model, calibrator, audit)
    )
    market = {
        "train": base._market_metrics(train),
        "walkForward": base._market_metrics(walk_forward),
        "untouchedAudit": base._market_metrics(audit),
    }

    gate_errors: List[str] = []
    for name, metrics in (
        ("walk_forward", walk_metrics),
        ("untouched_audit", audit_metrics),
    ):
        if metrics["dailyPassRate"] < 1.0 - 1e-12:
            gate_errors.append(f"{name}_contains_day_below_80_percent")
        if metrics["meanDailyAccuracy"] < DAILY_TARGET - 1e-12:
            gate_errors.append(f"{name}_mean_daily_accuracy_below_80_percent")
        if metrics["minimumDailyAccuracy"] < DAILY_TARGET - 1e-12:
            gate_errors.append(f"{name}_minimum_daily_accuracy_below_80_percent")
        if metrics["expectedCalibrationError"] > MAX_ECE + 1e-12:
            gate_errors.append(f"{name}_calibration_error_above_0_08")
    if walk_metrics["brierScore"] > market["walkForward"]["brierScore"] + 1e-12:
        gate_errors.append("walk_forward_brier_worse_than_market")
    if walk_metrics["logLoss"] > market["walkForward"]["logLoss"] + 1e-12:
        gate_errors.append("walk_forward_log_loss_worse_than_market")
    if audit_metrics["brierScore"] > market["untouchedAudit"]["brierScore"] + 1e-12:
        gate_errors.append("untouched_audit_brier_worse_than_market")
    if audit_metrics["logLoss"] > market["untouchedAudit"]["logLoss"] + 1e-12:
        gate_errors.append("untouched_audit_log_loss_worse_than_market")
    if len(train) < 1000 or len(walk_forward) < 200 or len(audit) < 200:
        gate_errors.append("evidence_game_floor_not_met")

    model_payload = model.to_dict()
    model_payload.update(
        {
            "version": VERSION,
            "weighting": weighting.to_dict(),
            "calibrator": calibrator.to_dict(),
            "featureCompilerVersion": FEATURE_VERSION,
        }
    )
    model_payload["modelDigest"] = base._sha(model_payload)

    selection_public = {
        key: value
        for key, value in selection.items()
        if key not in {"selectedOofProbabilities", "selectedOofOutcomes"}
    }
    result = {
        "ok": True,
        "version": VERSION,
        "authority": "SHADOW_ONLY",
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "architecture": {
            "modelType": "season-aware weighted regularized logistic residual over market prior",
            "marketIsOffsetNotSoleDirectionAuthority": True,
            "nestedChronologicalSelection": True,
            "wholeSlateDateIsolation": True,
            "sameDayOutcomeLeakagePrevented": True,
            "offseasonRecentFormReset": True,
            "priorSeasonEloCarryoverFraction": 0.25,
            "dayBalancedTrainingCandidate": True,
            "recencyWeightedTrainingCandidate": True,
            "foldRobustSelection": True,
            "calibrationSource": "out_of_fold_development_predictions_only",
            "untouchedAuditUsedForSelection": False,
            "probabilityBounds": [base.PROBABILITY_FLOOR, base.PROBABILITY_CEILING],
        },
        "partitions": {
            name: {
                "dates": values,
                "dayCount": len(values),
                "gameCount": len(base._subset(examples, values)),
                "firstDate": min(values),
                "lastDate": max(values),
            }
            for name, values in partitions.items()
        },
        "selection": selection_public,
        "model": model_payload,
        "metrics": {
            "train": train_metrics,
            "walkForward": walk_metrics,
            "untouchedAudit": audit_metrics,
            "marketBaseline": market,
        },
        "featureCoverage": {
            "exampleCount": len(examples),
            "v8Any": round(
                sum(row.features.get("v8_available", 0.0) > 0.5 for row in examples)
                / len(examples),
                8,
            ),
            "v8FirstFive": round(
                sum(row.features.get("v8_f5_available", 0.0) > 0.5 for row in examples)
                / len(examples),
                8,
            ),
            "frozenFundamentals": round(
                sum(
                    row.features.get("fundamentals_available", 0.0) > 0.5
                    for row in examples
                )
                / len(examples),
                8,
            ),
            "strictlyPastTeamHistory": round(
                sum(
                    row.features.get("team_history_available", 0.0) > 0.5
                    for row in examples
                )
                / len(examples),
                8,
            ),
        },
        "promotionGate": {
            "version": "MLB-SUPERVISED-SHADOW-PROMOTION-GATE-v2.2",
            "passed": not gate_errors,
            "dailyAccuracyRequirement": DAILY_TARGET,
            "calibrationErrorMaximum": MAX_ECE,
            "errors": gate_errors,
        },
        "retrospectiveArchitectureEvaluation": True,
        "freshProspectiveAuditRequired": True,
        "productionPromotionEligible": False,
    }
    result["resultDigest"] = base._sha(result)
    return result
