"""Read-only, reproducible development benchmark; never grants production authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hello_world"))
import mlb_ml_dual_model_v2 as dual

VERSION = "MLB-CHALLENGER-BENCHMARK-v1-frozen-source-development-only"
MOVEMENT = ["deltaGapHome", "bookAgreementGapHome", "reversalGapHome", "homeAwayVelocityPpHr60mDiff"]
BASEBALL = ["starterCompositeGapHome", "bullpenCompositeGapHome", "lineupWrcPlusGapHome",
            "fundamentalPitchingMissing", "fundamentalOffenseLineupMissing"]
PENALTIES = (0.01, 0.1, 1.0, 10.0)


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def logit(probability):
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p) - np.log1p(-p)


def fit_adjustment(rows, features, penalty, base_key="marketHomeProbability"):
    """Learn a regularized correction to a fixed probability baseline."""
    if not rows or penalty <= 0:
        raise ValueError("nonempty training data and positive regularization required")
    names, means, scales, inactive = [], {}, {}, []
    for name in features:
        values = [number(row.get(name)) for row in rows]
        observed = [value for value in values if value is not None]
        if not observed or np.std(observed) < 1e-12:
            inactive.append(name)
            continue
        names.append(name)
        means[name], scales[name] = float(np.mean(observed)), float(np.std(observed))
    model = {"features": names, "means": means, "scales": scales,
             "inactiveFeatures": inactive, "penalty": penalty, "baseKey": base_key}
    x = design(rows, model)
    offset = logit([row[base_key] for row in rows])
    y = np.asarray([row["homeWon"] for row in rows], dtype=float)
    if set(y) != {0.0, 1.0}:
        raise ValueError("both outcomes required in training")

    def objective(weights):
        z = offset + x @ weights
        loss = np.mean(np.logaddexp(0, z) - y * z) + penalty * np.dot(weights, weights) / 2
        gradient = x.T @ (expit(z) - y) / len(y) + penalty * weights
        return float(loss), gradient

    result = minimize(objective, np.zeros(x.shape[1]), method="L-BFGS-B", jac=True,
                      options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-8})
    if not result.success and np.linalg.norm(result.jac) > 1e-5:
        raise ValueError("model optimization did not converge")
    if not np.isfinite(result.x).all():
        raise ValueError("nonfinite model weights")
    model["weights"] = result.x.tolist()
    model["trainingCount"] = len(rows)
    return model


def design(rows, model):
    columns = [np.ones(len(rows))]
    for name in model["features"]:
        mean, scale = model["means"][name], model["scales"][name]
        values = [number(row.get(name)) for row in rows]
        columns.append(np.clip([(mean if value is None else value) - mean for value in values],
                               -10 * scale, 10 * scale) / scale)
    return np.column_stack(columns)


def predict(rows, model):
    if model is None:
        return np.asarray([row["marketHomeProbability"] for row in rows])
    return expit(logit([row[model["baseKey"]] for row in rows]) + design(rows, model) @ model["weights"])


def calibrated_rows(rows, probabilities):
    return [{**row, "rawModelProbability": float(p), "rawModelLogit": float(logit(p))}
            for row, p in zip(rows, probabilities)]


def metrics(rows, probabilities):
    y = np.asarray([row["homeWon"] for row in rows])
    p = np.clip(np.asarray(probabilities), 1e-6, 1 - 1e-6)
    if len(y) != len(p) or not len(y) or not np.isfinite(p).all():
        raise ValueError("complete finite evaluation predictions required")
    ece = 0.0
    bins = []
    bin_ids = np.minimum((p * 10).astype(int), 9)
    for bin_id in range(10):
        low = bin_id / 10
        selected = bin_ids == bin_id
        count = int(selected.sum())
        if count:
            observed, predicted = float(y[selected].mean()), float(p[selected].mean())
            ece += count / len(y) * abs(observed - predicted)
            bins.append({"lower": round(float(low), 1), "count": count,
                         "meanProbability": predicted, "observedHomeWinRate": observed})
    return {"count": len(y), "accuracy": float(np.mean((p >= 0.5) == y)),
            "brier": float(np.mean((p - y) ** 2)),
            "logLoss": float(np.mean(np.logaddexp(0, logit(p)) - y * logit(p))),
            "calibrationError": ece, "calibrationBins": bins}


def audit_features(rows):
    report = {}
    for feature in ["homeMarketDeVigProbability", *MOVEMENT, *BASEBALL]:
        values = [number(row.get(feature)) for row in rows]
        observed = [value for value in values if value is not None]
        report[feature] = {"observed": len(observed), "missing": len(rows) - len(observed),
                           "coverage": len(observed) / len(rows) if rows else 0,
                           "distinct": len(set(observed)),
                           "standardDeviation": float(np.std(observed)) if observed else None}
    return report


def partition_records(dataset):
    partitions = {}
    identities, previous_dates = set(), set()
    for name in ("train", "validation", "prospectiveTest"):
        source = dataset["partitions"][name]
        rows = dual.records_from_clean_rows(source)
        if len(rows) != len(source) or not rows:
            raise ValueError(f"{name}: exact accepted rows could not be reproduced")
        dates = {row["slateDateEt"] for row in rows}
        if previous_dates and min(dates) <= max(previous_dates):
            raise ValueError("partition chronology or whole-slate isolation failed")
        previous_dates.update(dates)
        for row in rows:
            identity = (row["slateDateEt"], row["gameId"])
            if not all(identity) or identity in identities:
                raise ValueError("duplicate or missing game identity")
            identities.add(identity)
            p = number(row["marketHomeProbability"])
            if p is None or not 0 < p < 1:
                raise ValueError("valid same-time market probability required")
        partitions[name] = rows
    return partitions


def split_validation(rows):
    dates = sorted({row["slateDateEt"] for row in rows})
    if len(dates) < 2:
        raise ValueError("two whole validation dates required for calibration and selection")
    calibration_dates = set(dates[:max(1, len(dates) // 2)])
    return ([row for row in rows if row["slateDateEt"] in calibration_dates],
            [row for row in rows if row["slateDateEt"] not in calibration_dates])


def paired_brier_interval(rows, probabilities, seed=20260909):
    y = np.asarray([row["homeWon"] for row in rows])
    market = np.asarray([row["marketHomeProbability"] for row in rows])
    difference = (market - y) ** 2 - (np.asarray(probabilities) - y) ** 2
    dates = sorted({row["slateDateEt"] for row in rows})
    sums = np.asarray([sum(difference[i] for i, row in enumerate(rows) if row["slateDateEt"] == d) for d in dates])
    counts = np.asarray([sum(row["slateDateEt"] == d for row in rows) for d in dates])
    picks = np.random.default_rng(seed).integers(0, len(dates), size=(2000, len(dates)))
    draws = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return {"marketMinusModelBrier": float(difference.mean()),
            "wholeSlateBootstrap95Pct": np.quantile(draws, [0.025, 0.975]).tolist(),
            "slateCount": len(dates), "developmentEvidenceOnly": True}


def benchmark(dataset, frozen, evaluation):
    partitions = partition_records(dataset)
    train, test = partitions["train"], partitions["prospectiveTest"]
    calibration, selection = split_validation(partitions["validation"])
    reference = np.asarray([dual.score(row, frozen["outcomeModel"]) for row in test])
    replay = metrics(test, reference)
    expected = evaluation["prospectiveTest"]["outcome"]
    expected_brier = number(expected.get("brierScore", expected.get("brier")))
    if expected_brier is None or abs(replay["brier"] - expected_brier) > 2e-6:
        raise ValueError("frozen R8 replay does not reproduce its recorded Brier score")
    families = {"market": [], "market_movement": MOVEMENT,
                "market_movement_baseball": [*MOVEMENT, *BASEBALL]}
    candidates = []
    for family, features in families.items():
        for penalty in ((None,) if family == "market" else PENALTIES):
            model = None if penalty is None else fit_adjustment(train, features, penalty)
            for calibrate in (False, True):
                calibrator = None
                if calibrate:
                    cal_rows = calibrated_rows(calibration, predict(calibration, model))
                    calibrator = fit_adjustment(cal_rows, ["rawModelLogit"], 0.1, "rawModelProbability")
                def score(rows):
                    raw = predict(rows, model)
                    return predict(calibrated_rows(rows, raw), calibrator) if calibrator else raw
                candidates.append({"name": f"{family}_l2_{penalty}_cal_{calibrate}",
                                   "family": family, "model": model, "calibrator": calibrator,
                                   "selection": metrics(selection, score(selection)),
                                   "testPredictions": score(test)})
    # Select solely on the later validation dates. Reviewed R8 test outcomes
    # are a development diagnostic and never choose a configuration.
    chosen = min(candidates, key=lambda row: (row["selection"]["brier"], row["selection"]["logLoss"], row["name"]))
    reports = []
    for candidate in candidates:
        p = candidate["testPredictions"]
        reports.append({"name": candidate["name"], "family": candidate["family"],
                        "selection": candidate["selection"], "reviewedTest": metrics(test, p),
                        "pairedMarketComparison": paired_brier_interval(test, p),
                        "inactiveFeatures": (candidate["model"] or {}).get("inactiveFeatures", [])})
    return {"version": VERSION, "ok": True, "createdAtUtc": datetime.now(timezone.utc).isoformat(),
            "sourceExperimentId": dataset["experimentId"],
            "partitionCounts": {k: len(v) for k, v in partitions.items()},
            "calibrationCount": len(calibration), "selectionCount": len(selection),
            "featureAudit": {k: audit_features(v) for k, v in partitions.items()},
            "frozenR8Replay": replay, "frozenReplayMatchesRecordedEvaluation": True,
            "marketReference": metrics(test, predict(test, None)),
            "selectionRule": "minimum_brier_then_logloss_on_later_validation_dates_only",
            "chosenConfiguration": chosen["name"], "comparisons": reports,
            "challenger": {"model": chosen["model"], "calibrator": chosen["calibrator"]},
            "trainingAndCalibrationDisjoint": True, "wholeSlatesKeptTogether": True,
            "prospectiveQualificationEvidence": False, "promotionEligible": False,
            "productionAuthorityChanged": False, "immutableHistoryRewritten": False,
            "nextRequiredEvidence": "new pre-outcome predictions after a separate challenger cutover"}


def verified_json(s3, pointer, bucket):
    if pointer.get("bucket") != bucket or not pointer.get("key") or not pointer.get("versionId") or len(pointer.get("sha256", "")) != 64:
        raise ValueError("exact in-bucket versioned source pointer required")
    response = s3.get_object(Bucket=bucket, Key=pointer["key"], VersionId=pointer["versionId"])
    body = response["Body"].read()
    if hashlib.sha256(body).hexdigest() != pointer["sha256"] or response.get("Metadata", {}).get("sha256") != pointer["sha256"]:
        raise ValueError("source artifact checksum mismatch")
    return json.loads(body)


def load_sources(stack):
    import boto3
    from botocore.config import Config
    config = Config(read_timeout=120, retries={"max_attempts": 2})
    cf, lam, s3 = (boto3.client(name, config=config) for name in ("cloudformation", "lambda", "s3"))
    function = cf.describe_stack_resource(StackName=stack, LogicalResourceId="MLBMLTrainingFunction")["StackResourceDetail"]["PhysicalResourceId"]
    cfg = lam.get_function_configuration(FunctionName=function)
    bucket = cfg["Environment"]["Variables"]["MLB_ML_ARTIFACTS_BUCKET"]
    response = lam.invoke(FunctionName=function, Payload=b'{"sport":"mlb","mode":"status"}')
    if response.get("FunctionError"):
        raise ValueError("trainer status failed")
    status = json.loads(response["Payload"].read())
    candidate = status["latestCandidate"]
    sources = {name: verified_json(s3, candidate["artifacts"][name], bucket)
               for name in ("dataset", "frozenChallenger", "evaluation")}
    if sources["dataset"]["experimentId"] != status["experimentId"] or hashlib.sha256(canonical_bytes(sources["dataset"])).hexdigest() != candidate["datasetDigest"]:
        raise ValueError("dataset is not bound to the current candidate")
    return sources, {"candidateDigest": candidate["artifactDigest"], "datasetDigest": candidate["datasetDigest"],
                     "artifactChecksums": {k: candidate["artifacts"][k]["sha256"] for k in sources}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack", default="parlay-platform-dev")
    parser.add_argument("--output", default="runtime_reports/mlb_challenger_benchmark_latest.json")
    args = parser.parse_args()
    sources, provenance = load_sources(args.stack)
    report = benchmark(sources["dataset"], sources["frozenChallenger"], sources["evaluation"])
    report["sourceProvenance"] = provenance
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in {"challenger", "featureAudit"}}, indent=2))


if __name__ == "__main__":
    main()
