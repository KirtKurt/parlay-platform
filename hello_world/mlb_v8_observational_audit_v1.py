"""Independent observational audit for the best learned MLB V8 residual.

The production prospective lifecycle intentionally does not freeze a candidate until
one clears every retrospective promotion gate.  That is correct for promotion but
previously left the best learned challenger with no live, settled comparison ledger.

This module closes that evidence gap without weakening any guard:

* the best non-baseline configuration is selected from development-fold evidence;
* a deterministic residual model and calibrator are fit without prospective labels;
* the candidate is content-addressed and permanently SHADOW_ONLY;
* only settled games after the frozen retrospective boundary are graded;
* the candidate and same-time market baseline are recorded row by row;
* the observational result can never request or authorize promotion or wagering.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence, Tuple

from botocore.exceptions import ClientError

import mlb_supervised_features_v2 as features
import mlb_supervised_model_v2 as supervised
import mlb_v8_model_runtime as runtime


VERSION = "MLB-V8-OBSERVATIONAL-AUDIT-v1-independent-non-promotable"
POINTER_PK = "MLB_V8_OBSERVATIONAL_AUDIT#V1"
POINTER_SK = "ACTIVE"
POINTER_RECORD_TYPE = "mlb_v8_observational_audit_pointer_v1"
MIN_EVIDENCE_GAMES = 200
MIN_EVIDENCE_DAYS = 15
SELECTED_PICK_MIN_CONFIDENCE = 0.55
INNER_FIT_STEPS = 220
FINAL_FIT_STEPS = 700
SEED = 260726


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _last_corpus_day(training: Mapping[str, Any]) -> str:
    values = []
    for partition in (training.get("partitions") or {}).values():
        if not isinstance(partition, Mapping):
            continue
        if partition.get("lastDate"):
            values.append(str(partition["lastDate"]))
        values.extend(str(day) for day in partition.get("dates") or [] if day)
    if not values:
        raise ValueError("observational candidate has no frozen corpus boundary")
    return max(values)


def _candidate_key(group: str, raw: Mapping[str, Any]) -> Tuple[Any, ...]:
    guard = raw.get("guard") or {}
    stability = guard.get("stability") or {}
    metrics = raw.get("oofMetrics") or {}
    return (
        0 if guard.get("eligible") is True else 1,
        -_i(stability.get("positiveFoldCount")),
        -_f(stability.get("overallAccuracyUplift"), -1.0),
        -_f(stability.get("meanDailyAccuracyUplift"), -1.0),
        -_f(metrics.get("overallAccuracy"), -1.0),
        _f(metrics.get("logLoss"), 10.0),
        _f(metrics.get("brierScore"), 1.0),
        str(group),
        _f(raw.get("l2"), 0.0),
    )


def best_learned_configuration(training: Mapping[str, Any]) -> Dict[str, Any]:
    selection = training.get("selection") or {}
    ablation = selection.get("ablation") or {}
    if not isinstance(ablation, Mapping):
        raise ValueError("training report has no learned-candidate ablation")
    values = [
        (str(group), raw)
        for group, raw in ablation.items()
        if str(group) != "market_baseline" and isinstance(raw, Mapping)
    ]
    if not values:
        raise ValueError("training report has no non-baseline candidate")
    group, raw = min(values, key=lambda item: _candidate_key(item[0], item[1]))
    guard = copy.deepcopy(dict(raw.get("guard") or {}))
    return {
        "featureGroup": group,
        "l2": float(raw.get("l2") or 0.0),
        "guardEligible": guard.get("eligible") is True,
        "guardErrors": sorted(set(str(item) for item in guard.get("errors") or [])),
        "guard": guard,
        "oofMetrics": copy.deepcopy(dict(raw.get("oofMetrics") or {})),
        "oofMarketBaseline": copy.deepcopy(
            dict(raw.get("oofMarketBaseline") or {})
        ),
        "folds": copy.deepcopy(list(raw.get("folds") or [])),
        "selectionKey": list(_candidate_key(group, raw)),
    }


def _source_identity(training: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    selection = training.get("selection") or {}
    return _sha(
        {
            "version": VERSION,
            "frozenCorpusLastDate": _last_corpus_day(training),
            "recordCountLoaded": training.get("recordCountLoaded"),
            "featureCompilerVersion": (
                (training.get("model") or {}).get("featureCompilerVersion")
                or features.VERSION
            ),
            "selectionGuardVersion": (
                (selection.get("selectionGuard") or {}).get("version")
            ),
            "featureGroup": config.get("featureGroup"),
            "l2": config.get("l2"),
            "guardErrors": config.get("guardErrors"),
            "oofMetrics": config.get("oofMetrics"),
            "oofMarketBaseline": config.get("oofMarketBaseline"),
        }
    )


def _model_payload(
    *,
    training: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    model_module: Any,
    feature_module: Any,
) -> Dict[str, Any]:
    examples = feature_module.prepare_examples(records)
    partitions = model_module.chronological_partitions(examples)
    train_days = list(partitions["train"])
    train = model_module._subset(examples, train_days)
    folds = model_module.inner_expanding_folds(train_days)
    group = str(config["featureGroup"])
    l2 = float(config["l2"])
    group_names = list(model_module.features.FEATURE_GROUPS)
    if group not in group_names:
        raise ValueError(f"observational feature group unavailable:{group}")
    group_index = group_names.index(group)

    probabilities = []
    outcomes = []
    fold_rows = []
    for fold_index, (inner_train_days, validation_days) in enumerate(folds):
        inner_train = model_module._subset(examples, inner_train_days)
        validation = model_module._subset(examples, validation_days)
        fitted = model_module.fit_residual_logistic(
            inner_train,
            feature_group=group,
            l2=l2,
            seed=SEED + group_index * 1000 + fold_index,
            steps=INNER_FIT_STEPS,
        )
        fold_probabilities = [fitted.raw_probability(row) for row in validation]
        probabilities.extend(fold_probabilities)
        outcomes.extend(row.outcome for row in validation)
        fold_rows.append(
            {
                "fold": fold_index + 1,
                "trainFirstDate": min(inner_train_days),
                "trainLastDate": max(inner_train_days),
                "validationFirstDate": min(validation_days),
                "validationLastDate": max(validation_days),
                "metrics": model_module.evaluate_probabilities(
                    validation, fold_probabilities
                ),
                "marketBaseline": model_module._market_metrics(validation),
            }
        )

    # Baseline selection installs a one-use identity calibrator.  This is an
    # independent learned residual, so it must fit its own OOF calibrator.
    model_module._INQSI_MLB_IDENTITY_CALIBRATOR_ONCE = False
    calibrator = model_module.fit_platt(probabilities, outcomes)
    fitted = model_module.fit_residual_logistic(
        train,
        feature_group=group,
        l2=l2,
        seed=SEED + 9000,
        steps=FINAL_FIT_STEPS,
    )
    model = fitted.to_dict()
    model["calibrator"] = calibrator.to_dict()
    model["featureCompilerVersion"] = feature_module.VERSION
    model["modelDigest"] = model_module._sha(model)
    return {
        "model": model,
        "partitions": partitions,
        "folds": fold_rows,
        "oofMetrics": model_module.evaluate_probabilities(
            [
                row
                for _, validation_days in folds
                for row in model_module._subset(examples, validation_days)
            ],
            probabilities,
        ),
        "oofMarketBaseline": model_module.evaluate_probabilities(
            [
                row
                for _, validation_days in folds
                for row in model_module._subset(examples, validation_days)
            ],
            [
                row.market_probability
                for _, validation_days in folds
                for row in model_module._subset(examples, validation_days)
            ],
        ),
        "trainingGameCount": len(train),
    }


def build_candidate(
    training: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    model_module: Any = supervised,
    feature_module: Any = features,
    runtime_module: Any = runtime,
) -> Dict[str, Any]:
    if training.get("ok") is not True:
        raise ValueError("observational source training report is unhealthy")
    learning = training.get("learningExecution") or {}
    if learning.get("learningExecuted") is not True:
        raise ValueError("observational source learning execution is unproven")
    config = best_learned_configuration(training)
    source_identity = _source_identity(training, config)
    fitted = _model_payload(
        training=training,
        records=records,
        config=config,
        model_module=model_module,
        feature_module=feature_module,
    )
    synthetic = {
        "createdAtUtc": f"{_last_corpus_day(training)}T23:59:59+00:00",
        "architecture": copy.deepcopy(dict(training.get("architecture") or {})),
        "selection": {
            "selectedFeatureGroup": config["featureGroup"],
            "selectedL2": config["l2"],
        },
        "model": fitted["model"],
        "resultDigest": source_identity,
    }
    bundle = runtime_module.build_bundle(synthetic)
    runtime_module.verify_bundle(bundle)
    candidate = {
        "proofType": "MLB_V8_FROZEN_OBSERVATIONAL_CANDIDATE",
        "version": VERSION,
        "authority": "SHADOW_ONLY",
        "observationalOnly": True,
        "promotionEligible": False,
        "promotionRequested": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
        "sourceTrainingIdentity": source_identity,
        "sourceTrainingResultDigest": training.get("resultDigest"),
        "frozenCorpusLastDate": _last_corpus_day(training),
        "featureGroup": config["featureGroup"],
        "l2": config["l2"],
        "retrospectiveGuardEligible": config["guardEligible"],
        "retrospectiveGuardErrors": config["guardErrors"],
        "retrospectiveOofMetrics": config["oofMetrics"],
        "retrospectiveOofMarketBaseline": config["oofMarketBaseline"],
        "independentRefitOofMetrics": fitted["oofMetrics"],
        "independentRefitOofMarketBaseline": fitted["oofMarketBaseline"],
        "configurationSelectedFromDevelopmentFoldsOnly": True,
        "modelFitUsedProspectiveOutcomes": False,
        "selectionUsedProspectiveOutcomes": False,
        "trainingGameCount": fitted["trainingGameCount"],
        "modelBundle": bundle,
        "modelDigest": bundle["modelDigest"],
    }
    candidate["candidateDigest"] = _sha(candidate)
    return candidate


def verify_candidate(candidate: Mapping[str, Any]) -> None:
    if candidate.get("version") != VERSION:
        raise ValueError("observational candidate version mismatch")
    if (
        candidate.get("authority") != "SHADOW_ONLY"
        or candidate.get("observationalOnly") is not True
        or candidate.get("promotionEligible") is not False
        or candidate.get("promotionRequested") is not False
        or candidate.get("automaticWagerAllowed") is not False
        or candidate.get("productionAuthorityChanged") is not False
    ):
        raise ValueError("observational candidate attempted to change authority")
    material = {
        key: item for key, item in candidate.items() if key != "candidateDigest"
    }
    if candidate.get("candidateDigest") != _sha(material):
        raise ValueError("observational candidate digest mismatch")
    runtime.verify_bundle(candidate.get("modelBundle") or {})
    if candidate.get("modelDigest") != (
        candidate.get("modelBundle") or {}
    ).get("modelDigest"):
        raise ValueError("observational model digest mismatch")
    if not candidate.get("frozenCorpusLastDate"):
        raise ValueError("observational corpus boundary is missing")


def _confidence_band(probability: float) -> str:
    confidence = max(probability, 1.0 - probability)
    lower = min(95, int(math.floor(confidence * 20.0) * 5))
    lower = max(50, lower)
    upper = min(100, lower + 5)
    return f"{lower:02d}-{upper:02d}"


def _grade_row(
    candidate: Mapping[str, Any], row: Any, probability: float
) -> Dict[str, Any]:
    model_pick_home = probability >= 0.5
    market_pick_home = row.market_probability >= 0.5
    outcome_home = int(row.outcome) == 1
    model_result = "WIN" if model_pick_home == outcome_home else "LOSS"
    market_result = "WIN" if market_pick_home == outcome_home else "LOSS"
    confidence = max(probability, 1.0 - probability)
    value = {
        "proofType": "MLB_V8_OBSERVATIONAL_GRADE_ROW",
        "version": VERSION,
        "authority": "SHADOW_ONLY",
        "observationalOnly": True,
        "promotionEligible": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
        "candidateDigest": candidate["candidateDigest"],
        "modelDigest": candidate["modelDigest"],
        "frozenCorpusLastDate": candidate["frozenCorpusLastDate"],
        "slateDateEt": row.day,
        "officialGamePk": row.game_id,
        "homeTeam": row.home_team,
        "awayTeam": row.away_team,
        "outcome": "home" if outcome_home else "away",
        "modelProbabilityHome": round(float(probability), 12),
        "modelPick": "home" if model_pick_home else "away",
        "modelResult": model_result,
        "marketProbabilityHome": round(float(row.market_probability), 12),
        "marketPick": "home" if market_pick_home else "away",
        "marketResult": market_result,
        "selectedSideConfidence": round(confidence, 12),
        "selectedForDiagnostic": confidence + 1e-12
        >= SELECTED_PICK_MIN_CONFIDENCE,
        "confidenceBand": _confidence_band(probability),
        "push": False,
        "void": False,
        "modelRefitDuringGrading": False,
        "selectionUsedProspectiveOutcomes": False,
    }
    value["rowDigest"] = _sha(value)
    return value


def _accuracy(wins: int, losses: int) -> Optional[float]:
    denominator = wins + losses
    return round(wins / denominator, 12) if denominator else None


def _band_summary(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for row in rows:
        band = str(row["confidenceBand"])
        summary = result.setdefault(
            band, {"sampleSize": 0, "wins": 0, "losses": 0, "pushes": 0, "voids": 0}
        )
        summary["sampleSize"] += 1
        summary["wins"] += int(row["modelResult"] == "WIN")
        summary["losses"] += int(row["modelResult"] == "LOSS")
    for summary in result.values():
        summary["accuracy"] = _accuracy(summary["wins"], summary["losses"])
    return dict(sorted(result.items()))


def evaluate_candidate(
    candidate: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    feature_module: Any = features,
    model_module: Any = supervised,
    runtime_module: Any = runtime,
) -> Dict[str, Any]:
    verify_candidate(candidate)
    examples = [
        row
        for row in feature_module.prepare_examples(records)
        if str(row.day) > str(candidate["frozenCorpusLastDate"])
    ]
    probabilities = []
    grades = []
    bundle = candidate["modelBundle"]
    for row in examples:
        vector = dict(row.features)
        vector[str(bundle.get("marketProbabilityFeature"))] = row.market_probability
        probability = float(runtime_module.score(bundle, vector)["probability"])
        probabilities.append(probability)
        grades.append(_grade_row(candidate, row, probability))

    model_metrics = model_module.evaluate_probabilities(examples, probabilities)
    market_metrics = model_module.evaluate_probabilities(
        examples, [row.market_probability for row in examples]
    )
    wins = sum(row["modelResult"] == "WIN" for row in grades)
    losses = sum(row["modelResult"] == "LOSS" for row in grades)
    market_wins = sum(row["marketResult"] == "WIN" for row in grades)
    market_losses = sum(row["marketResult"] == "LOSS" for row in grades)
    selected = [row for row in grades if row["selectedForDiagnostic"]]
    selected_wins = sum(row["modelResult"] == "WIN" for row in selected)
    selected_losses = sum(row["modelResult"] == "LOSS" for row in selected)
    day_count = len({row["slateDateEt"] for row in grades})
    evidence_complete = len(grades) >= MIN_EVIDENCE_GAMES and day_count >= MIN_EVIDENCE_DAYS
    result = {
        "proofType": "MLB_V8_FROZEN_OBSERVATIONAL_AUDIT",
        "version": VERSION,
        "authority": "SHADOW_ONLY",
        "observationalOnly": True,
        "promotionEligible": False,
        "promotionRequested": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
        "candidateDigest": candidate["candidateDigest"],
        "modelDigest": candidate["modelDigest"],
        "frozenCorpusLastDate": candidate["frozenCorpusLastDate"],
        "firstGradedDate": min((row["slateDateEt"] for row in grades), default=None),
        "lastGradedDate": max((row["slateDateEt"] for row in grades), default=None),
        "sampleSize": len(grades),
        "dayCount": day_count,
        "wins": wins,
        "losses": losses,
        "pushes": 0,
        "voids": 0,
        "overallAccuracy": _accuracy(wins, losses),
        "marketWins": market_wins,
        "marketLosses": market_losses,
        "marketAccuracy": _accuracy(market_wins, market_losses),
        "selectedPickThreshold": SELECTED_PICK_MIN_CONFIDENCE,
        "selectedPickSampleSize": len(selected),
        "selectedPickWins": selected_wins,
        "selectedPickLosses": selected_losses,
        "selectedPickAccuracy": _accuracy(selected_wins, selected_losses),
        "calibrationEce": model_metrics.get("expectedCalibrationError"),
        "marketCalibrationEce": market_metrics.get("expectedCalibrationError"),
        "confidenceBands": _band_summary(grades),
        "modelMetrics": model_metrics,
        "sameTimeMarketBaseline": market_metrics,
        "minimumEvidenceGames": MIN_EVIDENCE_GAMES,
        "minimumEvidenceDays": MIN_EVIDENCE_DAYS,
        "observationalEvidenceComplete": evidence_complete,
        "status": (
            "OBSERVATIONAL_EVIDENCE_COMPLETE"
            if evidence_complete
            else "OBSERVATIONAL_COLLECTING"
        ),
        "gradedRows": grades,
        "modelRefitDuringGrading": False,
        "selectionUsedProspectiveOutcomes": False,
    }
    result["auditDigest"] = _sha(result)
    return result


def _json_bytes(value: Any) -> bytes:
    return _canonical(value) + b"\n"


def _put_immutable(
    s3: Any,
    *,
    bucket: str,
    key: str,
    value: Mapping[str, Any],
    record_type: str,
) -> Dict[str, Any]:
    body = _json_bytes(value)
    digest = hashlib.sha256(body).hexdigest()
    try:
        response = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
            Metadata={"sha256": digest, "record-type": record_type},
        )
        return {
            "bucket": bucket,
            "key": key,
            "sha256": digest,
            "versionId": response.get("VersionId"),
            "alreadyExisted": False,
        }
    except ClientError as exc:
        error = exc.response.get("Error") or {}
        status = int(
            (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0
        )
        if str(error.get("Code") or "") not in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
        } and status not in {409, 412}:
            raise
        head = s3.head_object(Bucket=bucket, Key=key)
        existing = str((head.get("Metadata") or {}).get("sha256") or "")
        if existing != digest:
            raise RuntimeError("V8 observational immutable artifact collision") from exc
        return {
            "bucket": bucket,
            "key": key,
            "sha256": digest,
            "versionId": head.get("VersionId"),
            "alreadyExisted": True,
        }


def _load_pointer_value(s3: Any, pointer: Mapping[str, Any]) -> Dict[str, Any]:
    bucket = str(pointer.get("bucket") or "")
    key = str(pointer.get("key") or "")
    digest = str(pointer.get("sha256") or "")
    if not bucket or not key or not digest:
        raise RuntimeError("observational artifact pointer is incomplete")
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    if hashlib.sha256(body).hexdigest() != digest:
        raise RuntimeError("observational artifact checksum mismatch")
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("observational artifact is not an object")
    return value


def _current_pointer(table: Any) -> Tuple[Dict[str, Any], int]:
    item = table.get_item(
        Key={"PK": POINTER_PK, "SK": POINTER_SK}, ConsistentRead=True
    ).get("Item") or {}
    return copy.deepcopy(dict(item.get("data") or {})), int(item.get("revision") or 0)


def _write_pointer(
    table: Any,
    *,
    previous_revision: int,
    data: Mapping[str, Any],
    created_at: str,
) -> int:
    revision = previous_revision + 1
    item = {
        "PK": POINTER_PK,
        "SK": POINTER_SK,
        "record_type": POINTER_RECORD_TYPE,
        "revision": revision,
        "updated_at": created_at,
        "data": copy.deepcopy(dict(data)),
    }
    if previous_revision:
        table.put_item(
            Item=item,
            ConditionExpression="#revision = :expected",
            ExpressionAttributeNames={"#revision": "revision"},
            ExpressionAttributeValues={":expected": previous_revision},
        )
    else:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    return revision


def _safe_game_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))


def advance(
    *,
    training: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    table: Any,
    s3: Any,
    bucket: str,
    created_at: str,
) -> Dict[str, Any]:
    config = best_learned_configuration(training)
    source_identity = _source_identity(training, config)
    pointer, revision = _current_pointer(table)
    candidate_pointer = pointer.get("candidateArtifact") or {}
    candidate: Optional[Dict[str, Any]] = None
    if pointer.get("sourceTrainingIdentity") == source_identity and candidate_pointer:
        candidate = _load_pointer_value(s3, candidate_pointer)
        verify_candidate(candidate)
    if candidate is None:
        candidate = build_candidate(training, records)
        candidate_key = (
            "mlb/v8/observational-candidates/"
            f"{candidate['modelDigest']}/{candidate['candidateDigest']}.json"
        )
        candidate_pointer = _put_immutable(
            s3,
            bucket=bucket,
            key=candidate_key,
            value=candidate,
            record_type="mlb-v8-observational-candidate",
        )

    audit = evaluate_candidate(candidate, records)
    grade_pointers = []
    for row in audit.get("gradedRows") or []:
        key = (
            "mlb/v8/observational-grades/"
            f"{candidate['candidateDigest']}/{row['slateDateEt']}/"
            f"{_safe_game_id(row['officialGamePk'])}.json"
        )
        grade_pointers.append(
            _put_immutable(
                s3,
                bucket=bucket,
                key=key,
                value=row,
                record_type="mlb-v8-observational-grade-row",
            )
        )
    audit["gradeArtifacts"] = grade_pointers
    audit["gradeArtifactCount"] = len(grade_pointers)
    audit["auditDigest"] = _sha(
        {key: item for key, item in audit.items() if key != "auditDigest"}
    )
    audit_key = (
        "mlb/v8/observational-audits/"
        f"{candidate['candidateDigest']}/{audit['auditDigest']}.json"
    )
    audit_pointer = _put_immutable(
        s3,
        bucket=bucket,
        key=audit_key,
        value=audit,
        record_type="mlb-v8-observational-audit",
    )

    if pointer.get("auditDigest") == audit["auditDigest"]:
        pointer_revision = revision
    else:
        pointer_revision = _write_pointer(
            table,
            previous_revision=revision,
            created_at=created_at,
            data={
                "version": VERSION,
                "status": audit["status"],
                "sourceTrainingIdentity": source_identity,
                "candidateDigest": candidate["candidateDigest"],
                "modelDigest": candidate["modelDigest"],
                "frozenCorpusLastDate": candidate["frozenCorpusLastDate"],
                "candidateArtifact": candidate_pointer,
                "auditDigest": audit["auditDigest"],
                "auditArtifact": audit_pointer,
                "sampleSize": audit["sampleSize"],
                "wins": audit["wins"],
                "losses": audit["losses"],
                "pushes": audit["pushes"],
                "voids": audit["voids"],
                "promotionEligible": False,
                "promotionRequested": False,
                "automaticWagerAllowed": False,
                "productionAuthorityChanged": False,
            },
        )

    report = {
        key: copy.deepcopy(value)
        for key, value in audit.items()
        if key != "gradedRows"
    }
    report.update(
        {
            "createdAtUtc": created_at,
            "pointerRevision": pointer_revision,
            "sourceTrainingIdentity": source_identity,
            "candidateArtifact": candidate_pointer,
            "auditArtifact": audit_pointer,
            "retrospectiveGuardEligible": candidate[
                "retrospectiveGuardEligible"
            ],
            "retrospectiveGuardErrors": candidate["retrospectiveGuardErrors"],
            "promotionEligible": False,
            "promotionRequested": False,
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
        }
    )
    report["reportDigest"] = _sha(report)
    return report
