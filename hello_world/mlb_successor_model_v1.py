"""A separate, source-honest successor. Never changes R8 features or partitions.

The locked market log odds are an offset, not a target to learn from scratch.
Only named, observed starter rates and two recorded movement features adjust it.
No third-party numerical dependency is needed in the Lambda runtime.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

import mlb_fundamentals_snapshot_v2 as snapshots
import mlb_ml_dual_model_v2 as r8
import mlb_ml_walk_forward_v2 as metrics
import mlb_statsapi_starter_context as starter_source

VERSION = "MLB-SUCCESSOR-MARKET-OFFSET-STARTER-RATES-v1"
EXPERIMENT_ID = "mlb-successor-2026-09-09-starter-rates-v1"
FEATURES = ("deltaGapHome", "homeAwayVelocityPpHr60mDiff", "starterEraGapHome",
            "starterKMinusBbGapHome", "starterRatesMissing")
PROTOCOL = {
    "version": VERSION, "experimentId": EXPERIMENT_ID,
    "features": list(FEATURES), "penalties": [0.1, 1.0, 10.0],
    "trainMinimum": 300, "validationMinimum": 100, "testMinimum": 100,
    "starterObservedTrainMinimum": 100, "starterObservedCalibrationMinimum": 25,
    "starterObservedSelectionMinimum": 25, "maximumDevelopmentRows": 750,
    "selectionRule": "later_validation_brier_then_logloss_then_configuration_name",
    "calibrationRule": "earlier_validation_only_regularized_logit_adjustment",
    "prospectiveRule": "first_complete_whole_slates_after_durable_freeze_next_ET_day",
    "testCanBeReopened": False, "r8ReviewedTestIsDevelopmentOnly": True,
    "firstActivationRequiresManualReview": True, "automaticWagerAllowed": False,
}


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def fingerprint(value):
    # Numeric equivalence survives DynamoDB's Decimal round trip; bool stays bool.
    def canonical(v):
        if isinstance(v, bool) or v is None or isinstance(v, str):
            return v
        if isinstance(v, (int, float, Decimal)):
            if number(v) is None:
                raise ValueError("nonfinite fingerprint material")
            return {"number": format(Decimal(str(v)).normalize(), "f")}
        if isinstance(v, dict):
            return {k: canonical(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [canonical(x) for x in v]
        raise ValueError("unsupported fingerprint material")
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def timestamp(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return result.astimezone(timezone.utc)


def record(row, *, labeled):
    """Use accepted canonical rows and their original snapshots, without fetching."""
    vector = row.get("featureSnapshot") or row.get("frozenFeatureVector") or {}
    snap = row.get("fundamentalsSnapshotV2") or vector.get("fundamentalsSnapshotV2") or {}
    if snapshots.validate(snap):
        raise ValueError("invalid immutable fundamentals snapshot")
    locked = timestamp(vector.get("lockAtUtc"))
    # Canonical acceptance already verifies actual durable persistence. Also
    # enforce every source timestamp here before admitting newly named metrics.
    if not snapshots.provenance_is_lock_safe(snap, prediction_persisted_at=locked.isoformat(),
                                            lock_at=locked.isoformat()):
        raise ValueError("fundamentals observation after lock")
    if labeled:
        records = r8.records_from_clean_rows([row])
        if len(records) != 1:
            raise ValueError("accepted official label and locked market pair required")
        result = records[0]
    else:
        if any(k in row for k in ("winner", "correct", "success", "homeWon", "pickCorrect")):
            raise ValueError("outcome fields forbidden at capture")
        if any(v is not None for v in (vector.get("labels") or {}).values()):
            raise ValueError("labeled vector forbidden at capture")
        if (row.get("canonicalLockAuthority") or {}).get("learningEligible") is not True:
            raise ValueError("canonical lock authority required")
        result = r8.record_from_unlabeled_lock(row)
    p = number(result.get("marketHomeProbability"))
    if p is None or not 0 < p < 1:
        raise ValueError("strict interior market probability required")
    game_pk = str(row.get("officialGamePk") or "")
    if not game_pk.isdigit() or int(game_pk) <= 0 or not result.get("gameId"):
        raise ValueError("exact official game identity required")
    if str((snap.get("game") or {}).get("officialGamePk")) != game_pk:
        raise ValueError("fundamentals official game identity mismatch")
    group = (snap.get("groups") or {}).get("starter_quality") or {}
    values = group.get("values") or {}
    rates = [number(values.get(k)) for k in ("homeEra", "awayEra", "homeKMinusBbPct", "awayKMinusBbPct")]
    observed = (group.get("status") in snapshots.SOURCE_PRESENT_STATUSES
                and group.get("dataset") in (starter_source.VERSION, starter_source.DATASET)
                and all(v is not None for v in rates)
                and all(v >= 0 for v in rates[:2])
                and all(-100 <= v <= 100 for v in rates[2:]))
    result.update({"officialGamePk": game_pk, "featureLockAtUtc": locked.isoformat(),
                   "fundamentalsSnapshotFingerprint": snap["fingerprint"],
                   "starterEraGapHome": rates[0] - rates[1] if observed else None,
                   "starterKMinusBbGapHome": rates[2] - rates[3] if observed else None,
                   "starterRatesMissing": 0.0 if observed else 1.0})
    # Only model inputs/identity contribute; final labels are deliberately absent.
    result["inputFingerprint"] = input_fingerprint(result)
    return result


def input_fingerprint(result):
    return fingerprint({k: result.get(k) for k in (
        "gameId", "officialGamePk", "slateDateEt", "commenceTime", "homeTeam", "awayTeam",
        "marketHomeProbability", "marketAwayProbability", "featureFingerprint",
        "featureLockAtUtc", "fundamentalsSnapshotFingerprint", *FEATURES)})


def logit(p):
    p = min(1 - 1e-6, max(1e-6, float(p)))
    return math.log(p) - math.log1p(-p)


def sigmoid(z):
    return 1 / (1 + math.exp(-max(-35, min(35, z))))


def design(row, model):
    values = [1.0]
    for name in model["features"]:
        mean, scale = float(model["means"][name]), float(model["scales"][name])
        value = number(row.get(name))
        # Train-mean imputation is explicit. No source value is fabricated.
        values.append(max(-10, min(10, ((mean if value is None else value) - mean) / scale)))
    return values


def solve(matrix, rhs):
    a = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for i in range(len(a)):
        pivot = max(range(i, len(a)), key=lambda j: abs(a[j][i]))
        a[i], a[pivot] = a[pivot], a[i]
        if abs(a[i][i]) < 1e-12:
            raise ValueError("singular penalized fit")
        divisor = a[i][i]
        a[i] = [v / divisor for v in a[i]]
        for j in range(len(a)):
            if j != i:
                factor = a[j][i]
                a[j] = [v - factor * w for v, w in zip(a[j], a[i])]
    return [row[-1] for row in a]


def fit(rows, features, penalty, base="marketHomeProbability"):
    if not rows or set(r.get("homeWon") for r in rows) != {0, 1} or penalty <= 0:
        raise ValueError("both outcomes and positive regularization required")
    model = {"features": [], "means": {}, "scales": {}, "inactiveFeatures": [],
             "base": base, "penalty": penalty, "trainingCount": len(rows)}
    for name in features:
        values = [number(r.get(name)) for r in rows]
        observed = [v for v in values if v is not None]
        mean = sum(observed) / len(observed) if observed else 0
        scale = math.sqrt(sum((v - mean) ** 2 for v in observed) / len(observed)) if observed else 0
        if scale < 1e-12:
            model["inactiveFeatures"].append(name)
        else:
            model["features"].append(name)
            model["means"][name], model["scales"][name] = mean, scale
    x = [design(r, model) for r in rows]
    offsets = [logit(r[base]) for r in rows]
    y = [r["homeWon"] for r in rows]
    n, width = len(rows), len(x[0])
    weights = [0.0] * width
    def objective(w):
        z = [offset + sum(a*b for a, b in zip(v, w)) for offset, v in zip(offsets, x)]
        return sum(max(t, 0) + math.log1p(math.exp(-abs(t))) - label*t for t, label in zip(z, y)) / n + penalty * sum(v*v for v in w) / 2
    for _ in range(60):
        probabilities = [sigmoid(o + sum(a*b for a, b in zip(v, weights))) for o, v in zip(offsets, x)]
        gradient = [sum(v[j]*(p-label) for v, p, label in zip(x, probabilities, y))/n + penalty*weights[j] for j in range(width)]
        if max(abs(v) for v in gradient) < 1e-8:
            break
        hessian = [[sum(v[j]*v[k]*p*(1-p) for v, p in zip(x, probabilities))/n + (penalty if j == k else 0) for k in range(width)] for j in range(width)]
        direction = solve(hessian, gradient)
        step, before = 1.0, objective(weights)
        while step > 1e-8:
            proposal = [v - step*d for v, d in zip(weights, direction)]
            if objective(proposal) <= before:
                weights = proposal
                break
            step /= 2
        else:
            raise ValueError("fit line search failed")
    else:
        raise ValueError("fit convergence failed")
    model["weights"] = weights
    return model


def predict(row, model):
    return sigmoid(logit(row[model["base"]]) + sum(a*float(b) for a, b in zip(design(row, model), model["weights"])))


def score(row, candidate):
    raw = predict(row, candidate["model"])
    calibrator = candidate.get("calibrator")
    return predict({**row, "rawProbability": raw, "rawLogit": logit(raw)}, calibrator) if calibrator else raw


def evaluate(rows, candidate):
    scored = [{**r, "probability": score(r, candidate)} for r in rows]
    return metrics.evaluate(scored, "probability", "homeWon", baseline_probability_key="marketHomeProbability")


def development(rows):
    ordered = sorted(rows, key=lambda r: (r["slateDateEt"], r["officialGamePk"]))
    if len({(r["slateDateEt"], r["officialGamePk"]) for r in rows}) != len(rows):
        raise ValueError("duplicate development identity")
    # A rolling window admits newly collected features without changing old R8.
    while len(ordered) > PROTOCOL["maximumDevelopmentRows"]:
        first = ordered[0]["slateDateEt"]
        ordered = [r for r in ordered if r["slateDateEt"] != first]
    dates = sorted({r["slateDateEt"] for r in ordered})
    validation_dates, count = [], 0
    for day in reversed(dates):
        validation_dates.append(day)
        count += sum(r["slateDateEt"] == day for r in ordered)
        if count >= PROTOCOL["validationMinimum"]:
            break
    validation_dates.sort()
    train = [r for r in ordered if r["slateDateEt"] not in validation_dates]
    split = len(validation_dates) // 2
    calibration = [r for r in ordered if r["slateDateEt"] in validation_dates[:split]]
    selection = [r for r in ordered if r["slateDateEt"] in validation_dates[split:]]
    groups = {"train": train, "calibration": calibration, "selection": selection}
    counts = {k: len(v) for k, v in groups.items()}
    observed = {k: sum(r["starterRatesMissing"] == 0 for r in v) for k, v in groups.items()}
    blockers = []
    if len(train) < PROTOCOL["trainMinimum"] or len(calibration) + len(selection) < PROTOCOL["validationMinimum"] or not calibration or not selection:
        blockers.append("INSUFFICIENT_WHOLE_SLATE_DEVELOPMENT_ROWS")
    for group, requirement in (("train", "starterObservedTrainMinimum"), ("calibration", "starterObservedCalibrationMinimum"), ("selection", "starterObservedSelectionMinimum")):
        if observed[group] < PROTOCOL[requirement]:
            blockers.append("INSUFFICIENT_OBSERVED_STARTER_RATES_" + group.upper())
    report = {"ok": True, "status": "ACCUMULATING_STARTER_RATE_DEVELOPMENT_DATA",
              "counts": counts, "observedStarterCounts": observed, "blockers": blockers,
              "protocol": PROTOCOL, "productionAuthorityChanged": False}
    if blockers:
        return report
    candidates = []
    for penalty in PROTOCOL["penalties"]:
        model = fit(train, FEATURES, penalty)
        for calibrated in (False, True):
            candidate = {"name": f"starter_offset_l2_{penalty}_cal_{calibrated}", "model": model, "calibrator": None}
            if calibrated:
                cal_rows = [{**r, "rawProbability": predict(r, model), "rawLogit": logit(predict(r, model))} for r in calibration]
                candidate["calibrator"] = fit(cal_rows, ["rawLogit"], 0.1, "rawProbability")
            candidate["validation"] = evaluate(selection, candidate)
            candidates.append(candidate)
    chosen = min(candidates, key=lambda c: (c["validation"]["brierScore"], c["validation"]["logLoss"], c["name"]))
    report.update({"status": "DEVELOPMENT_CANDIDATE_EVALUATED", "candidate": chosen,
                   "developmentFingerprint": fingerprint(ordered),
                   "partitionFingerprints": {k: fingerprint(v) for k, v in groups.items()},
                   "comparisons": [{"name": c["name"], "validation": c["validation"]} for c in candidates]})
    return report


def direction_blockers(outcome, total, test_count):
    """Same market-skill limits as V2; no playability or wager authority."""
    import mlb_ml_promotion_policy_v2 as policy
    reasons = []
    if total < policy.MIN_TOTAL_CLEAN_ROWS: reasons.append("INSUFFICIENT_CLEAN_ROWS")
    if test_count < policy.MIN_PROSPECTIVE_TEST_ROWS: reasons.append("INSUFFICIENT_FRESH_TEST_ROWS")
    checks = (("brierSkillPct", lambda v: v > 0, "NO_POSITIVE_BRIER_SKILL"),
              ("calibrationError", lambda v: v <= policy.MAX_CALIBRATION_ERROR, "CALIBRATION_ERROR_TOO_HIGH"),
              ("accuracyLiftPctPoints", lambda v: v >= policy.MIN_ACCURACY_LIFT_PCT_POINTS, "ACCURACY_LIFT_TOO_LOW"))
    for key, predicate, reason in checks:
        value = number(outcome.get(key))
        if value is None or not predicate(value): reasons.append(reason)
    loss, baseline = number(outcome.get("logLoss")), number((outcome.get("baseline") or {}).get("logLoss"))
    if loss is None or baseline is None or loss >= baseline: reasons.append("LOG_LOSS_NOT_LOWER_THAN_MARKET")
    paired = outcome.get("pairedAccuracyRegression") or {}
    if paired.get("ok") is not True or paired.get("statisticallySignificantRegression") is not False:
        reasons.append("PAIRED_MARKET_REGRESSION_CHECK_FAILED")
    return reasons
