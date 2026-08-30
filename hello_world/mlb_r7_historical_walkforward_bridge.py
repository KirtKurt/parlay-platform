"""Point-in-time historical MLB admission for the canonical AWS trainer.

This is a training-only bridge. It reads checksum-bound complete-slate
artifacts from the historical optimizer, freezes a label-blind R7 feature
snapshot, and joins the final outcome afterward. Historical rows may fill
train/validation; they never receive prospective or production authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


VERSION = "MLB-R7-HISTORICAL-WALKFORWARD-BRIDGE-v1"
STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
STATE_SK = "STATE"
DATASET_VERSION = "MLB-HISTORICAL-DAILY-DATASET-v1.1-per-game-t45-clipped"
ARTIFACT_PREFIX = "mlb/historical-daily-v1/"
FUNDAMENTALS_VERSION = "MLB-FUNDAMENTALS-SNAPSHOT-v2-immutable-source-provenance"
FUNDAMENTALS_FINGERPRINT_VERSION = "INQSI-EXACT-TYPED-JSON-SHA256-v1"
FEATURE_FINGERPRINT_VERSION = "MLB-R7-HISTORICAL-FEATURE-SHA256-v1"


class HistoricalAdmissionError(RuntimeError):
    pass


def _plain(value: Any) -> Any:
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
    except Exception:
        pass
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _parse_dt(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _float(value: Any) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise HistoricalAdmissionError("historical feature is not finite")
    return parsed


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        raise HistoricalAdmissionError("historical count is boolean")
    parsed = int(value)
    if parsed < 0:
        raise HistoricalAdmissionError("historical count is negative")
    return parsed


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _horizon(signal: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    temporal = signal.get("temporalFeatures") or {}
    horizons = temporal.get("horizons") if isinstance(temporal, Mapping) else {}
    value = (horizons or {}).get(name) if isinstance(horizons, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def _score(signal: Mapping[str, Any]) -> float:
    latest = _float(signal.get("fairProbability"))
    delta = _float(signal.get("delta"))
    divergence = _float(signal.get("bookDivergence"))
    reversals = _integer(signal.get("reversalCount"))
    return round(
        max(
            0.0,
            min(
                100.0,
                50
                + delta * 700
                + (latest - 0.5) * 80
                - divergence * 300
                - reversals * 8,
            ),
        ),
        2,
    )


def _missing_fundamentals(
    record: Mapping[str, Any], *, source_at: str, artifact_ref: str
) -> Dict[str, Any]:
    import mlb_fundamentals_snapshot_v2 as fundamentals

    groups: Dict[str, Any] = {}
    for output_name, _context_name, values in fundamentals.GROUP_SPECS:
        value_map = {output_key: None for output_key, _input_key in values}
        required = list(
            fundamentals.REQUIRED_VALUE_KEYS.get(output_name, tuple(value_map))
        )
        groups[output_name] = {
            "status": "MISSING",
            "provider": None,
            "endpoint": None,
            "dataset": None,
            "retrievedAtUtc": None,
            "sourceEffectiveAtUtc": None,
            "payloadFingerprint": None,
            "identifiers": {
                "gameId": str(record.get("officialGamePk") or ""),
                "officialGamePk": str(record.get("officialGamePk") or ""),
                "providerEventId": None,
                "homeTeam": record.get("homeTeam"),
                "awayTeam": record.get("awayTeam"),
                "homeEntityId": None,
                "awayEntityId": None,
            },
            "values": value_map,
            "requiredValueKeys": required,
            "missingValueKeys": required,
            "complete": False,
            "missingReason": (
                "Source was absent from the immutable historical artifact; "
                "values were not fabricated."
            ),
        }
    snapshot: Dict[str, Any] = {
        "version": FUNDAMENTALS_VERSION,
        "recordType": "mlb_historical_missingness_only_fundamentals_snapshot",
        "schemaCohort": "MLB-ML-FUNDAMENTALS-v2",
        "snapshotRole": "HISTORICAL_POINT_IN_TIME_EXPLICIT_MISSINGNESS_ONLY",
        "createdAtUtc": None,
        "evidenceCutoffUtc": source_at,
        "sourcePullAtUtc": None,
        "sourcePullId": None,
        "game": {
            "gameId": str(record.get("officialGamePk") or ""),
            "officialGamePk": str(record.get("officialGamePk") or ""),
            "providerEventId": None,
            "slateDateEt": record.get("slateDateEt"),
            "commenceTimeUtc": record.get("commenceTime"),
            "homeTeam": record.get("homeTeam"),
            "awayTeam": record.get("awayTeam"),
        },
        "groups": groups,
        "connectedGroups": [],
        "partialGroups": [],
        "missingGroups": sorted(groups),
        "pregameCompletenessNumerator": 0,
        "pregameCompletenessDenominator": len(groups),
        "completenessRatio": 0.0,
        "sourceAvailabilityRatio": 0.0,
        "allConnectedGroupsTimestamped": True,
        "pregameComplete": False,
        "trainingEligibleAtCapture": False,
        "trainingExclusions": [
            *[f"fundamentals_v2_incomplete:{name}" for name in sorted(groups)],
            "historical_missingness_only",
        ],
        "fingerprintVersion": FUNDAMENTALS_FINGERPRINT_VERSION,
        "snapshotRef": artifact_ref,
        "historicalMissingnessOnly": True,
        "fabricatedValues": False,
        "captureTimeUnavailable": True,
    }
    snapshot["fingerprint"] = fundamentals.fingerprint_for_snapshot(snapshot)
    return snapshot


def _source_at(
    record: Mapping[str, Any], audit: Sequence[Mapping[str, Any]], official_count: int
) -> str:
    lock_at = _parse_dt(record.get("predictionLockAtUtc"))
    if lock_at is None:
        raise HistoricalAdmissionError("historical lock timestamp is invalid")
    candidates: List[datetime] = []
    for entry in audit:
        provider_at = _parse_dt(entry.get("providerTimestampUtc"))
        if provider_at is None or provider_at > lock_at:
            continue
        candidates.append(provider_at)
    required = max(
        _integer(record.get("observedHomePullCount")),
        _integer(record.get("observedAwayPullCount")),
    )
    if len(candidates) < required or required < 4:
        raise HistoricalAdmissionError(
            "historical audit cannot bound every lock-clipped source pull"
        )
    # Existing immutable v1.1 rows retain pull counts and a slate request audit,
    # not a per-record list of source timestamps. This is therefore the latest
    # verified provider-time upper bound, never represented as an exact pull.
    return max(candidates).isoformat()


def _feature_material(
    record: Mapping[str, Any], *, source_at: str, artifact_ref: str, feature_version: str
) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """Freeze features without reading an outcome field."""
    home = record.get("homeSignal") or {}
    away = record.get("awaySignal") or {}
    if not isinstance(home, Mapping) or not isinstance(away, Mapping):
        raise HistoricalAdmissionError("historical signals are missing")
    home_probability = _float(home.get("fairProbability"))
    away_probability = _float(away.get("fairProbability"))
    if (
        not 0 < home_probability < 1
        or not 0 < away_probability < 1
        or abs(home_probability + away_probability - 1.0) > 1e-6
    ):
        raise HistoricalAdmissionError(
            "historical de-vigged probability pair is invalid"
        )
    selected_side = "home" if home_probability >= away_probability else "away"
    selected = home if selected_side == "home" else away
    selected_full = _horizon(selected, "full")
    selected_180 = _horizon(selected, "180m")
    home_60 = _horizon(home, "60m")
    away_60 = _horizon(away, "60m")
    home_tags = {str(value) for value in home.get("tags") or []}
    away_tags = {str(value) for value in away.get("tags") or []}
    features = {
        "deltaGapHome": _float(home.get("delta")) - _float(away.get("delta")),
        "bookAgreementGapHome": (
            (1.0 if "BOOK_AGREEMENT" in home_tags else 0.0)
            - (1.0 if "BOOK_AGREEMENT" in away_tags else 0.0)
        ),
        "reversalGapHome": (
            float(_integer(home.get("reversalCount")))
            - float(_integer(away.get("reversalCount")))
        ),
        "homeAwayVelocityPpHr60mDiff": (
            _float(home_60.get("velocityPpHr") or 0.0)
            - _float(away_60.get("velocityPpHr") or 0.0)
        ),
        "selectedScore": _score(selected),
        "selectedDelta": _float(selected.get("delta")),
        "selectedBookDivergence": _float(selected.get("bookDivergence")),
        "selectedReversalCountFull": _float(
            selected_full.get("reversalCount") or 0.0
        ),
        "selectedCoverageRatioFull": _float(
            selected_full.get("coverageRatio")
        ),
        "selectedVolatilityPpPerPull180m": _float(
            selected_180.get("volatilityPpPerPull") or 0.0
        ),
        "selectedHome": 1.0 if selected_side == "home" else 0.0,
    }
    vector: Dict[str, Any] = {
        "version": feature_version,
        "fingerprintVersion": FEATURE_FINGERPRINT_VERSION,
        "gameId": str(record.get("officialGamePk") or ""),
        "slateDateEt": str(record.get("slateDateEt") or ""),
        "commenceTime": record.get("commenceTime"),
        "lockAtUtc": str(record.get("predictionLockAtUtc") or ""),
        "sourcePullAtUtc": source_at,
        "sourceTimeSemantics": "LATEST_VERIFIED_PROVIDER_TIME_UPPER_BOUND",
        "temporalFeaturesAtOrBeforeLock": True,
        "fundamentalMasksAtOrBeforeLock": True,
        "historicalPointInTime": True,
        "selectionUsedOutcomes": False,
        "features": features,
        "homeMarketDeVigProbability": home_probability,
        "awayMarketDeVigProbability": away_probability,
        "predictedSide": selected_side,
        "artifactRef": artifact_ref,
    }
    vector["fingerprint"] = _digest(
        {key: value for key, value in vector.items() if key != "fingerprint"}
    )
    fundamentals = _missing_fundamentals(
        record, source_at=source_at, artifact_ref=artifact_ref
    )
    return vector, fundamentals, selected_side


def materialize_record(
    record: Mapping[str, Any],
    *,
    dataset: Mapping[str, Any],
    artifact: Mapping[str, Any],
    feature_version: str,
) -> Dict[str, Any]:
    """Join the final label after the label-blind feature material is frozen."""
    official_count = _integer(dataset.get("officialGameCount"))
    artifact_ref = (
        f"s3://{artifact.get('bucket')}/{artifact.get('key')}"
        f"?versionId={artifact.get('versionId')}#game={record.get('officialGamePk')}"
    )
    source_at = _source_at(
        record, dataset.get("snapshotAudit") or [], official_count
    )
    vector, fundamentals, selected_side = _feature_material(
        record,
        source_at=source_at,
        artifact_ref=artifact_ref,
        feature_version=feature_version,
    )
    winner = str(record.get("winner") or "")
    home = str(record.get("homeTeam") or "")
    away = str(record.get("awayTeam") or "")
    if _norm(winner) not in {_norm(home), _norm(away)}:
        raise HistoricalAdmissionError("historical final winner is invalid")
    commence = _parse_dt(record.get("commenceTime"))
    lock_at = _parse_dt(record.get("predictionLockAtUtc"))
    if commence is None or lock_at is None or lock_at >= commence:
        raise HistoricalAdmissionError("historical lock is not strictly pregame")
    predicted_winner = home if selected_side == "home" else away
    correct = _norm(predicted_winner) == _norm(winner)
    attestation = {
        "version": VERSION,
        "datasetVersion": dataset.get("version"),
        "datasetFingerprint": dataset.get("fingerprint"),
        "artifactSha256": artifact.get("sha256"),
        "artifactKey": artifact.get("key"),
        "slateDateEt": record.get("slateDateEt"),
        "officialGamePk": str(record.get("officialGamePk") or ""),
        "completeSlate": True,
        "exactSlateCoverage": 1.0,
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
        "selectionUsedOutcomes": False,
        "featureMaterializedBeforeLabelJoin": True,
        "sourceTimeSemantics": "LATEST_VERIFIED_PROVIDER_TIME_UPPER_BOUND",
        "prospectiveAuthority": False,
        "productionAuthority": False,
    }
    attestation["fingerprint"] = _digest(attestation)
    return {
        "gameId": str(record.get("officialGamePk") or ""),
        "officialGamePk": str(record.get("officialGamePk") or ""),
        "slateDateEt": str(record.get("slateDateEt") or ""),
        "slateFinalized": True,
        "commenceTime": record.get("commenceTime"),
        "homeTeam": home,
        "awayTeam": away,
        "predictedWinner": predicted_winner,
        "predictedSide": selected_side,
        "lockedAmericanOdds": (
            (record.get("homeSignal") or {}).get("americanOdds")
            if selected_side == "home"
            else (record.get("awaySignal") or {}).get("americanOdds")
        ),
        "trainingEligible": True,
        "featureSnapshot": vector,
        "frozenFeatureVector": copy.deepcopy(vector),
        "fundamentalsSnapshotV2": fundamentals,
        "fundamentalsSnapshotV2Ref": artifact_ref,
        "homeMarketDeVigProbability": vector["homeMarketDeVigProbability"],
        "awayMarketDeVigProbability": vector["awayMarketDeVigProbability"],
        "marketProbabilitySourceAtUtc": source_at,
        "marketProbabilityVersion": "MLB-HISTORICAL-DEVIGGED-H2H-v1",
        "marketProbabilitySourceTimeSemantics": (
            "LATEST_VERIFIED_PROVIDER_TIME_UPPER_BOUND"
        ),
        "marketProbabilityFingerprint": _digest(
            {
                "sourceAtUtc": source_at,
                "home": vector["homeMarketDeVigProbability"],
                "away": vector["awayMarketDeVigProbability"],
                "artifactSha256": artifact.get("sha256"),
            }
        ),
        "winner": winner,
        "homeWon": _norm(winner) == _norm(home),
        "correct": bool(correct),
        "pickCorrect": bool(correct),
        "labelStatus": "FINAL",
        "labelSource": "immutable_historical_optimizer_final_settlement_join",
        "labelObservationTimeUnavailable": True,
        "r7HistoricalWalkForward": True,
        "historicalTrainingOnly": True,
        "selectionUsedOutcomes": False,
        "prospectiveAuthority": False,
        "liveInferenceAuthority": False,
        "productionAuthority": False,
        "historicalAdmissionAttestation": attestation,
    }


def _verify_dataset(
    dataset: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> None:
    import mlb_historical_daily_optimizer_v1 as optimizer

    records = dataset.get("records") or []
    expected = _integer(dataset.get("officialGameCount"))
    errors = []
    if dataset.get("version") != DATASET_VERSION:
        errors.append("dataset_version")
    if dataset.get("completeSlate") is not True:
        errors.append("complete_slate")
    if _float(dataset.get("exactSlateCoverage")) != 1.0:
        errors.append("exact_slate_coverage")
    if dataset.get("postLockDataExcluded") is not True:
        errors.append("post_lock_exclusion")
    if dataset.get("gameSpecificLockClipping") is not True:
        errors.append("game_lock_clipping")
    if dataset.get("exclusions") not in ([], None):
        errors.append("dataset_exclusions")
    if not isinstance(records, list) or len(records) != expected:
        errors.append("record_count")
    if (
        _integer(ledger.get("officialGameCount")) != expected
        or _integer(ledger.get("eligibleGameCount")) != expected
    ):
        errors.append("ledger_count")
    computed = optimizer.dataset_fingerprint(
        records if isinstance(records, list) else []
    )
    if (
        computed != dataset.get("fingerprint")
        or computed != ledger.get("fingerprint")
    ):
        errors.append("dataset_fingerprint")
    if str(dataset.get("slateDateEt") or "") != str(
        ledger.get("slateDateEt") or ""
    ):
        errors.append("slate_date")
    game_pks = [
        str(row.get("officialGamePk") or "")
        for row in records
        if isinstance(row, Mapping)
    ]
    if (
        len(game_pks) != expected
        or any(not value for value in game_pks)
        or len(set(game_pks)) != len(game_pks)
    ):
        errors.append("official_game_set")
    for record in records if isinstance(records, list) else []:
        if (
            not isinstance(record, Mapping)
            or record.get("postLockDataExcluded") is not True
            or record.get("gameSpecificLockClipping") is not True
            or str(record.get("slateDateEt") or "")
            != str(dataset.get("slateDateEt") or "")
        ):
            errors.append("record_point_in_time_proof")
            break
    if errors:
        raise HistoricalAdmissionError(
            "historical dataset attestation failed:"
            + ",".join(sorted(set(errors)))
        )


def _get_artifact(s3: Any, artifact: Mapping[str, Any]) -> Dict[str, Any]:
    bucket = str(artifact.get("bucket") or "")
    key = str(artifact.get("key") or "")
    sha = str(artifact.get("sha256") or "")
    version_id = str(artifact.get("versionId") or "")
    if (
        not bucket
        or not key.startswith(ARTIFACT_PREFIX)
        or len(sha) != 64
        or any(character not in "0123456789abcdef" for character in sha.lower())
        or not version_id
    ):
        raise HistoricalAdmissionError(
            "historical artifact pointer is incomplete"
        )
    request: Dict[str, Any] = {"Bucket": bucket, "Key": key}
    if version_id != "unversioned":
        request["VersionId"] = version_id
    response = s3.get_object(**request)
    body = response["Body"].read()
    if hashlib.sha256(body).hexdigest() != sha:
        raise HistoricalAdmissionError("historical artifact checksum mismatch")
    metadata_sha = str((response.get("Metadata") or {}).get("sha256") or "")
    if metadata_sha and metadata_sha != sha:
        raise HistoricalAdmissionError(
            "historical artifact metadata checksum mismatch"
        )
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise HistoricalAdmissionError(
            "historical artifact payload is not an object"
        )
    return value


def load_historical_rows(
    config: Any,
    *,
    dynamodb: Any = None,
    s3: Any = None,
    max_rows: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if (
        str(
            os.environ.get("MLB_R7_HISTORICAL_TRAINING_ENABLED", "true")
        ).lower()
        != "true"
    ):
        return []
    import boto3

    table_name = str(os.environ.get("SNAPSHOTS_TABLE") or "")
    if not table_name:
        raise HistoricalAdmissionError(
            "SNAPSHOTS_TABLE is required for historical training"
        )
    dynamodb = dynamodb or boto3.resource("dynamodb")
    s3 = s3 or boto3.client("s3")
    item = _plain(
        dynamodb.Table(table_name)
        .get_item(
            Key={"PK": STATE_PK, "SK": STATE_SK}, ConsistentRead=True
        )
        .get("Item")
        or {}
    )
    state = item.get("data") if isinstance(item, Mapping) else {}
    if not isinstance(state, Mapping):
        raise HistoricalAdmissionError("historical optimizer state is missing")
    ledgers = state.get("completedSlates") or []
    if not isinstance(ledgers, list) or not ledgers:
        raise HistoricalAdmissionError(
            "historical optimizer has no completed slates"
        )
    release_cutoff = _parse_dt(getattr(config, "release_cutoff_utc", None))
    if release_cutoff is None:
        raise HistoricalAdmissionError("trainer release cutoff is invalid")
    limit = max_rows
    if limit is None:
        try:
            limit = int(
                os.environ.get("MLB_R7_HISTORICAL_MAX_ROWS", "500")
            )
        except Exception:
            limit = 500
    limit = max(400, min(2000, int(limit)))
    output: List[Dict[str, Any]] = []
    for ledger in sorted(
        (value for value in ledgers if isinstance(value, Mapping)),
        key=lambda value: str(value.get("slateDateEt") or ""),
    ):
        try:
            slate_date = date.fromisoformat(
                str(ledger.get("slateDateEt") or "")
            )
        except Exception as exc:
            raise HistoricalAdmissionError(
                "historical ledger slate date is invalid"
            ) from exc
        if slate_date >= release_cutoff.date():
            continue
        artifact = ledger.get("artifact") or {}
        if not isinstance(artifact, Mapping):
            raise HistoricalAdmissionError(
                "historical ledger artifact pointer is invalid"
            )
        dataset = _get_artifact(s3, artifact)
        _verify_dataset(dataset, ledger)
        slate_rows = [
            materialize_record(
                record,
                dataset=dataset,
                artifact=artifact,
                feature_version=str(
                    getattr(config, "feature_vector_version", "") or ""
                ),
            )
            for record in dataset.get("records") or []
        ]
        if output and len(output) + len(slate_rows) > limit:
            break
        output.extend(slate_rows)
        if len(output) >= limit:
            break
    if len(output) < 400:
        raise HistoricalAdmissionError(
            f"historical optimizer supplied only {len(output)} admissible rows; "
            "400 required"
        )
    return sorted(
        output,
        key=lambda row: (
            str(row.get("slateDateEt") or ""),
            str(row.get("commenceTime") or ""),
            str(row.get("gameId") or ""),
        ),
    )


def validate_historical_record(
    row: Mapping[str, Any], manifest: Mapping[str, Any]
) -> Tuple[bool, List[str]]:
    import mlb_fundamentals_snapshot_v2 as fundamentals

    reasons: List[str] = []
    vector = row.get("featureSnapshot") or {}
    snapshot = row.get("fundamentalsSnapshotV2") or {}
    attestation = row.get("historicalAdmissionAttestation") or {}
    lock_at = _parse_dt(
        vector.get("lockAtUtc") if isinstance(vector, Mapping) else None
    )
    commence = _parse_dt(row.get("commenceTime"))
    source_at = _parse_dt(
        vector.get("sourcePullAtUtc") if isinstance(vector, Mapping) else None
    )
    if (
        row.get("r7HistoricalWalkForward") is not True
        or row.get("historicalTrainingOnly") is not True
    ):
        reasons.append("historical_bridge_identity_missing")
    for field in (
        "selectionUsedOutcomes",
        "prospectiveAuthority",
        "liveInferenceAuthority",
        "productionAuthority",
    ):
        if row.get(field) is not False:
            reasons.append(f"{field}_must_be_false")
    if (
        row.get("trainingEligible") is not True
        or row.get("slateFinalized") is not True
    ):
        reasons.append("historical_training_or_finalization_proof_missing")
    if (
        not isinstance(vector, Mapping)
        or vector.get("version") != manifest.get("featureVectorVersion")
    ):
        reasons.append("historical_feature_vector_version_mismatch")
    else:
        material = {
            key: value
            for key, value in vector.items()
            if key != "fingerprint"
        }
        if vector.get("fingerprint") != _digest(material):
            reasons.append("historical_feature_fingerprint_mismatch")
        if vector.get("selectionUsedOutcomes") is not False:
            reasons.append("historical_feature_selection_leakage")
    if (
        not lock_at
        or not commence
        or not source_at
        or not (source_at <= lock_at < commence)
    ):
        reasons.append("historical_feature_chronology_invalid")
    if (
        row.get("labelObservationTimeUnavailable") is not True
        or any(
            row.get(field)
            for field in (
                "labelFinalAtUtc",
                "labelRetrievedAtUtc",
                "outcomeFinalAtUtc",
                "settledAtUtc",
            )
        )
    ):
        reasons.append("historical_label_time_must_remain_unstated")
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("version") != FUNDAMENTALS_VERSION
        or snapshot.get("fingerprintVersion")
        != FUNDAMENTALS_FINGERPRINT_VERSION
        or snapshot.get("historicalMissingnessOnly") is not True
        or snapshot.get("fabricatedValues") is not False
        or snapshot.get("captureTimeUnavailable") is not True
        or snapshot.get("fingerprint")
        != fundamentals.fingerprint_for_snapshot(dict(snapshot))
    ):
        reasons.append(
            "historical_fundamentals_missingness_attestation_invalid"
        )
    if not isinstance(attestation, Mapping):
        reasons.append("historical_admission_attestation_missing")
    else:
        material = {
            key: value
            for key, value in attestation.items()
            if key != "fingerprint"
        }
        if (
            attestation.get("version") != VERSION
            or attestation.get("completeSlate") is not True
            or _float(attestation.get("exactSlateCoverage") or 0.0) != 1.0
            or attestation.get("postLockDataExcluded") is not True
            or attestation.get("gameSpecificLockClipping") is not True
            or attestation.get("selectionUsedOutcomes") is not False
            or attestation.get("featureMaterializedBeforeLabelJoin") is not True
            or attestation.get("sourceTimeSemantics")
            != "LATEST_VERIFIED_PROVIDER_TIME_UPPER_BOUND"
            or attestation.get("prospectiveAuthority") is not False
            or attestation.get("productionAuthority") is not False
            or attestation.get("fingerprint") != _digest(material)
        ):
            reasons.append("historical_admission_attestation_invalid")
    if _norm(row.get("winner")) not in {
        _norm(row.get("homeTeam")),
        _norm(row.get("awayTeam")),
    }:
        reasons.append("historical_final_label_invalid")
    return not reasons, sorted(set(reasons))


def install(*, canonical: Any, experiment: Any) -> None:
    if getattr(canonical, "_MLB_R7_HISTORICAL_BRIDGE_INSTALLED", False):
        return
    live_loader = canonical.load_canonical_training_rows
    original_init = canonical.TrainingService.__init__

    @wraps(original_init)
    def init_with_historical(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if self.row_loader is not live_loader:
            return

        def combined_loader(config):
            live_result = live_loader(config)
            historical = load_historical_rows(config)
            combined = [*historical, *list(live_result or [])]
            return canonical.CanonicalTrainingRows(
                sorted(
                    combined,
                    key=lambda row: (
                        str(row.get("slateDateEt") or ""),
                        str(row.get("commenceTime") or ""),
                        str(row.get("gameId") or ""),
                    ),
                ),
                copy.deepcopy(
                    getattr(live_result, "continuity", None)
                    or {"ok": True}
                ),
            )

        self.row_loader = combined_loader

    canonical.TrainingService.__init__ = init_with_historical
    original_validate = experiment.validate_record

    @wraps(original_validate)
    def validate_with_historical(
        row, manifest, snapshot_validator=None
    ):
        if (
            isinstance(row, Mapping)
            and row.get("r7HistoricalWalkForward") is True
        ):
            return validate_historical_record(row, manifest)
        return original_validate(
            row, manifest, snapshot_validator=snapshot_validator
        )

    experiment.validate_record = validate_with_historical
    original_advance = experiment.advance_manifest

    @wraps(original_advance)
    def advance_without_historical_prospective(manifest, rows, **kwargs):
        material = list(rows or [])
        updated = original_advance(manifest, material, **kwargs)
        historical_dates = {
            str(row.get("slateDateEt") or "")
            for row in material
            if isinstance(row, Mapping)
            and row.get("r7HistoricalWalkForward") is True
        }
        prospective_dates = set(
            (
                (updated.get("partitions") or {}).get("prospectiveTest")
                or {}
            ).get("slateDates")
            or []
        )
        if historical_dates & prospective_dates:
            raise experiment.ExperimentContractError(
                "historical rows cannot enter the prospective partition"
            )
        return updated

    experiment.advance_manifest = advance_without_historical_prospective
    canonical._MLB_R7_HISTORICAL_BRIDGE_INSTALLED = True
