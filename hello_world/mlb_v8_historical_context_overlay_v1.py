"""Apply immutable target-game point-in-time context after the prior-game V8 overlay.

This overlay uses a separate DynamoDB manifest pointer so a completed prior-game
backfill can never make target-game starter, bullpen, lineup, injury, park, or
weather work appear complete.  The target snapshot is merged with any already
attached strictly-prior snapshot.  It remains shadow-only and cannot change a
champion, production prediction, cutover, or wagering authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from decimal import Decimal
from typing import Any, Dict, Mapping, Sequence, Tuple

import boto3

import mlb_v8_historical_bbs_overlay_v1 as base

VERSION = "MLB-V8-HISTORICAL-CONTEXT-OVERLAY-v1-point-in-time"
POINTER_PK = "MLB_V8_HISTORICAL_CONTEXT#V1"
POINTER_SK = "ACTIVE"
DEFAULT_TABLE = base.DEFAULT_TABLE
AUTHORITY = base.AUTHORITY
TARGET_FAMILY = "targetGame"
PRIOR_FAMILY = "priorGame"
COMPOSITE_ROLE = "HISTORICAL_COMPOSITE_POINT_IN_TIME_AT_T_MINUS_45"
TARGET_ROLE = "HISTORICAL_POINT_IN_TIME_RECONSTRUCTION_AT_T_MINUS_45"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def _identity(value: Mapping[str, Any]) -> Tuple[str, str]:
    return (
        str(value.get("officialGamePk") or ""),
        str(value.get("predictionLockAtUtc") or ""),
    )


def _family_entry(snapshot: Mapping[str, Any], name: str) -> Dict[str, Any]:
    explicit = snapshot.get("featureFamilies")
    if isinstance(explicit, Mapping) and isinstance(explicit.get(name), Mapping):
        return copy.deepcopy(dict(explicit[name]))
    role = str(snapshot.get("snapshotRole") or "")
    home = snapshot.get("home") if isinstance(snapshot.get("home"), Mapping) else {}
    inferred = False
    if name == PRIOR_FAMILY:
        inferred = bool(
            role.startswith("BBD_STRICTLY_PRIOR_COMPLETED_GAME_FEATURES")
            or "bbsHistoryGames" in home
            or "bbsWinRate10" in home
        )
    elif name == TARGET_FAMILY:
        inferred = bool(
            role in {TARGET_ROLE, COMPOSITE_ROLE}
            or "starterQuality" in home
            or "bullpenQuality" in home
            or "lineupQuality" in home
            or snapshot.get("parkRunFactor") is not None
            or snapshot.get("weatherRunFactor") is not None
        )
    if not inferred:
        return {}
    return {
        "available": True,
        "trainingEligible": snapshot.get("trainingEligible") is True,
        "pointInTimeVerified": snapshot.get("pointInTimeVerified") is True,
        "snapshotFingerprint": snapshot.get("fingerprint"),
        "snapshotRole": role,
        "inferredFromLegacySnapshot": True,
    }


def has_family(snapshot: Any, name: str) -> bool:
    if not isinstance(snapshot, Mapping):
        return False
    entry = _family_entry(snapshot, name)
    return bool(entry.get("available") is True and entry.get("trainingEligible") is True)


def _validate_target_snapshot(
    snapshot: Any, record: Mapping[str, Any]
) -> Tuple[bool, list[str]]:
    valid, errors = base._validate_snapshot(snapshot, record)
    if not isinstance(snapshot, Mapping):
        return False, errors
    role = str(snapshot.get("snapshotRole") or "")
    if role != TARGET_ROLE and not has_family(snapshot, TARGET_FAMILY):
        errors.append("target_context_snapshot_role_mismatch")
    if snapshot.get("targetGameOutcomeUsed") is not False:
        errors.append("target_context_outcome_exclusion_missing")
    if snapshot.get("productionAuthorityChanged") is not False:
        errors.append("target_context_production_authority_changed")
    if snapshot.get("parkRunFactor") is None:
        errors.append("target_context_park_missing")
    if snapshot.get("weatherRunFactor") is None:
        errors.append("target_context_weather_missing")
    return not errors, sorted(set(errors))


def merge_snapshots(
    record: Mapping[str, Any], prior: Any, target: Mapping[str, Any]
) -> Dict[str, Any]:
    """Merge two independently validated point-in-time feature families."""
    target_valid, target_errors = _validate_target_snapshot(target, record)
    if not target_valid:
        raise RuntimeError(
            "target context snapshot is invalid:" + ",".join(target_errors)
        )

    prior_snapshot = copy.deepcopy(dict(prior)) if isinstance(prior, Mapping) else {}
    prior_valid = False
    if prior_snapshot:
        prior_valid, _ = base._validate_snapshot(prior_snapshot, record)
    if prior_snapshot and not prior_valid:
        raise RuntimeError("existing historical snapshot is invalid")

    home: Dict[str, Any] = {}
    away: Dict[str, Any] = {}
    if prior_valid:
        home.update(copy.deepcopy(dict(prior_snapshot.get("home") or {})))
        away.update(copy.deepcopy(dict(prior_snapshot.get("away") or {})))
    home.update(copy.deepcopy(dict(target.get("home") or {})))
    away.update(copy.deepcopy(dict(target.get("away") or {})))

    families: Dict[str, Any] = {}
    if prior_valid:
        prior_entry = _family_entry(prior_snapshot, PRIOR_FAMILY)
        if prior_entry:
            families[PRIOR_FAMILY] = prior_entry
    families[TARGET_FAMILY] = {
        "available": True,
        "trainingEligible": True,
        "pointInTimeVerified": True,
        "snapshotFingerprint": target.get("fingerprint"),
        "snapshotRole": target.get("snapshotRole"),
        "providerEvidence": copy.deepcopy(target.get("providerEvidence") or {}),
        "eligibilityErrors": [],
    }

    provider_evidence: Dict[str, Any] = {}
    if prior_valid:
        provider_evidence[PRIOR_FAMILY] = copy.deepcopy(
            prior_snapshot.get("providerEvidence") or {}
        )
    provider_evidence[TARGET_FAMILY] = copy.deepcopy(
        target.get("providerEvidence") or {}
    )

    merged: Dict[str, Any] = {
        "version": base.SNAPSHOT_VERSION,
        "authority": AUTHORITY,
        "snapshotRole": COMPOSITE_ROLE,
        "createdAtUtc": target.get("createdAtUtc"),
        "officialGamePk": str(record.get("officialGamePk") or ""),
        "providerMatchId": target.get("providerMatchId"),
        "predictionLockAtUtc": record.get("predictionLockAtUtc"),
        "slateDateEt": record.get("slateDateEt"),
        "homeTeam": record.get("homeTeam"),
        "awayTeam": record.get("awayTeam"),
        "home": home,
        "away": away,
        "parkRunFactor": target.get("parkRunFactor"),
        "weatherRunFactor": target.get("weatherRunFactor"),
        "providerEvidence": provider_evidence,
        "featureFamilies": families,
        "crosswalkMethod": target.get("crosswalkMethod"),
        "pointInTimeVerified": True,
        "postgameFieldsExcluded": True,
        "sameDayResultsExcluded": True,
        "targetGameOutcomeUsed": False,
        "selectionUsedOutcomes": False,
        "trainingEligible": True,
        "eligibilityErrors": [],
        "productionAuthorityChanged": False,
    }
    merged["fingerprint"] = base.snapshot_fingerprint(merged)
    valid, errors = base._validate_snapshot(merged, record)
    if not valid:
        raise RuntimeError("merged historical snapshot is invalid:" + ",".join(errors))
    return merged


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("version") != base.MANIFEST_VERSION:
        raise RuntimeError("historical target-context manifest version mismatch")
    if manifest.get("authority") != AUTHORITY:
        raise RuntimeError("historical target-context manifest authority mismatch")
    if manifest.get("productionAuthorityChanged") is not False:
        raise RuntimeError("historical target-context manifest changed production authority")
    if manifest.get("selectionUsedOutcomes") is not False:
        raise RuntimeError("historical target-context outcome-selection contract missing")
    if manifest.get("manifestDigest") != base.manifest_digest(manifest):
        raise RuntimeError("historical target-context manifest digest mismatch")


def apply_manifest(
    records: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]
) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    _validate_manifest(manifest)
    by_identity: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for row in manifest.get("records") or []:
        if not isinstance(row, Mapping):
            raise RuntimeError("historical target-context manifest record is invalid")
        identity = _identity(row)
        if not all(identity) or identity in by_identity:
            raise RuntimeError(
                "historical target-context identity is missing or duplicated"
            )
        by_identity[identity] = row

    output: list[Dict[str, Any]] = []
    applied = 0
    ineligible = 0
    invalid = 0
    unmatched = set(by_identity)
    for raw in records:
        record = copy.deepcopy(dict(raw))
        identity = _identity(record)
        manifest_row = by_identity.get(identity)
        if manifest_row is not None:
            unmatched.discard(identity)
            if manifest_row.get("trainingEligible") is not True:
                ineligible += 1
                record["historicalTargetGameContext"] = {
                    "trainingEligible": False,
                    "errors": list(
                        manifest_row.get("eligibilityErrors")
                        or ["historical_target_context_row_ineligible"]
                    ),
                    "manifestDigest": manifest.get("manifestDigest"),
                }
            else:
                snapshot = manifest_row.get("snapshot")
                valid, errors = _validate_target_snapshot(snapshot, record)
                if not valid:
                    invalid += 1
                    record["historicalTargetGameContext"] = {
                        "trainingEligible": False,
                        "errors": errors,
                        "manifestDigest": manifest.get("manifestDigest"),
                    }
                else:
                    try:
                        merged = merge_snapshots(
                            record,
                            record.get("frozenFundamentalsSnapshot"),
                            snapshot,
                        )
                    except Exception as exc:
                        invalid += 1
                        record["historicalTargetGameContext"] = {
                            "trainingEligible": False,
                            "errors": [f"merge_failed:{type(exc).__name__}"],
                            "manifestDigest": manifest.get("manifestDigest"),
                        }
                    else:
                        record["frozenFundamentalsSnapshot"] = merged
                        record["historicalTargetGameContext"] = {
                            "trainingEligible": True,
                            "providerMatchId": manifest_row.get("providerMatchId"),
                            "manifestDigest": manifest.get("manifestDigest"),
                            "snapshotFingerprint": snapshot.get("fingerprint"),
                            "compositeFingerprint": merged.get("fingerprint"),
                        }
                        applied += 1
        output.append(record)

    proof = {
        "version": VERSION,
        "status": "APPLIED",
        "authority": AUTHORITY,
        "manifestDigest": manifest.get("manifestDigest"),
        "manifestRecordCount": len(by_identity),
        "manifestEligibleGameCount": int(manifest.get("eligibleGameCount") or 0),
        "appliedGameCount": applied,
        "ineligibleGameCount": ineligible,
        "invalidGameCount": invalid,
        "unmatchedManifestGameCount": len(unmatched),
        "recordCount": len(output),
        "coverage": round(applied / len(output), 8) if output else 0.0,
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
        "targetGameOutcomeUsed": False,
    }
    return output, proof


def _enabled() -> bool:
    return str(
        os.environ.get("MLB_V8_HISTORICAL_CONTEXT_OVERLAY_ENABLED", "true")
    ).strip().lower() in {"1", "true", "yes", "on"}


def load_and_apply(
    records: Sequence[Mapping[str, Any]],
    *,
    table_name: str | None = None,
    ddb_resource: Any = None,
    s3_client: Any = None,
) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    if not _enabled():
        return [copy.deepcopy(dict(row)) for row in records], {
            "version": VERSION,
            "status": "DISABLED",
            "authority": AUTHORITY,
            "appliedGameCount": 0,
            "recordCount": len(records),
            "coverage": 0.0,
            "productionAuthorityChanged": False,
        }

    table = (ddb_resource or boto3.resource("dynamodb")).Table(
        table_name
        or os.environ.get("MLB_V8_HISTORICAL_CONTEXT_TABLE", DEFAULT_TABLE)
    )
    item = table.get_item(
        Key={"PK": POINTER_PK, "SK": POINTER_SK}, ConsistentRead=True
    ).get("Item")
    required = (
        str(
            os.environ.get(
                "MLB_V8_HISTORICAL_CONTEXT_OVERLAY_REQUIRED", "false"
            )
        ).lower()
        == "true"
    )
    if not item:
        if required:
            raise RuntimeError("historical target-context active manifest pointer is missing")
        return [copy.deepcopy(dict(row)) for row in records], {
            "version": VERSION,
            "status": "MANIFEST_NOT_AVAILABLE",
            "authority": AUTHORITY,
            "appliedGameCount": 0,
            "recordCount": len(records),
            "coverage": 0.0,
            "productionAuthorityChanged": False,
        }

    pointer_data = _plain(item.get("data") or {})
    pointer = pointer_data.get("manifest") or {}
    bucket = str(pointer.get("bucket") or "")
    key = str(pointer.get("key") or "")
    expected_sha = str(pointer.get("sha256") or "")
    if not bucket or not key or not expected_sha:
        raise RuntimeError("historical target-context active manifest pointer is incomplete")
    response = (s3_client or boto3.client("s3")).get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    if hashlib.sha256(body).hexdigest() != expected_sha:
        raise RuntimeError("historical target-context active manifest checksum mismatch")
    manifest = json.loads(body.decode("utf-8"))
    output, proof = apply_manifest(records, manifest)
    proof["pointerRevision"] = int(item.get("revision") or 0)
    proof["manifestPointer"] = {
        "bucket": bucket,
        "key": key,
        "sha256": expected_sha,
    }
    return output, proof


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_HISTORICAL_CONTEXT_OVERLAY_INSTALLED", False):
        return model_module
    original = model_module.train_and_evaluate

    def patched(records: Sequence[Mapping[str, Any]], **kwargs: Any) -> Dict[str, Any]:
        enriched, proof = load_and_apply(records)
        result = original(enriched, **kwargs)
        result["historicalTargetGameContext"] = proof
        digest = getattr(model_module, "_sha", None)
        if callable(digest):
            result["resultDigest"] = digest(
                {key: value for key, value in result.items() if key != "resultDigest"}
            )
        return result

    model_module.train_and_evaluate = patched
    model_module._INQSI_MLB_HISTORICAL_CONTEXT_OVERLAY_INSTALLED = True
    return model_module
