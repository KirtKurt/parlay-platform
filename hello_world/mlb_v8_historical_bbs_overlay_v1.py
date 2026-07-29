"""Apply immutable point-in-time BigBallsData fundamentals to V8 historical rows.

The overlay is independent from the canonical odds history. It is read only by the
shadow trainer, is keyed by official MLB game identity plus the immutable T-45 lock,
and cannot write a champion, prediction, cutover, or wagering authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from decimal import Decimal
from typing import Any, Dict, Mapping, Sequence, Tuple

import boto3

VERSION = "MLB-V8-HISTORICAL-BBS-OVERLAY-v1-point-in-time"
MANIFEST_VERSION = "MLB-V8-HISTORICAL-BBS-MANIFEST-v1"
SNAPSHOT_VERSION = "MLB-V8-HISTORICAL-BBS-FUNDAMENTALS-SNAPSHOT-v1"
POINTER_PK = "MLB_V8_HISTORICAL_BBS#V1"
POINTER_SK = "ACTIVE"
DEFAULT_TABLE = "parlay_platform_snapshots"
AUTHORITY = "V8_HISTORICAL_BBS_SHADOW_ONLY"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    material = {str(key): copy.deepcopy(value) for key, value in snapshot.items() if key != "fingerprint"}
    return _sha(material)


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    material = {str(key): copy.deepcopy(value) for key, value in manifest.items() if key != "manifestDigest"}
    return _sha(material)


def _enabled() -> bool:
    return str(os.environ.get("MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED", "false")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def _validate_snapshot(snapshot: Any, record: Mapping[str, Any]) -> Tuple[bool, list[str]]:
    if not isinstance(snapshot, Mapping):
        return False, ["snapshot_missing"]
    errors: list[str] = []
    if snapshot.get("version") != SNAPSHOT_VERSION:
        errors.append("snapshot_version_mismatch")
    if snapshot.get("authority") != AUTHORITY:
        errors.append("snapshot_authority_mismatch")
    if snapshot.get("trainingEligible") is not True:
        errors.append("snapshot_not_training_eligible")
    if snapshot.get("pointInTimeVerified") is not True:
        errors.append("snapshot_point_in_time_unverified")
    if snapshot.get("postgameFieldsExcluded") is not True:
        errors.append("snapshot_postgame_exclusion_missing")
    if str(snapshot.get("officialGamePk") or "") != str(record.get("officialGamePk") or ""):
        errors.append("snapshot_official_game_identity_mismatch")
    if str(snapshot.get("predictionLockAtUtc") or "") != str(record.get("predictionLockAtUtc") or ""):
        errors.append("snapshot_lock_identity_mismatch")
    if not isinstance(snapshot.get("home"), Mapping) or not isinstance(snapshot.get("away"), Mapping):
        errors.append("snapshot_side_payload_missing")
    if snapshot.get("fingerprint") != snapshot_fingerprint(snapshot):
        errors.append("snapshot_fingerprint_mismatch")
    if snapshot.get("selectionUsedOutcomes") is not False:
        errors.append("snapshot_outcome_selection_contract_missing")
    return not errors, sorted(set(errors))


def apply_manifest(
    records: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]
) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    if manifest.get("version") != MANIFEST_VERSION:
        raise RuntimeError("historical BBS manifest version mismatch")
    if manifest.get("authority") != AUTHORITY:
        raise RuntimeError("historical BBS manifest authority mismatch")
    if manifest.get("productionAuthorityChanged") is not False:
        raise RuntimeError("historical BBS manifest changed production authority")
    if manifest.get("selectionUsedOutcomes") is not False:
        raise RuntimeError("historical BBS manifest outcome-selection contract missing")
    if manifest.get("manifestDigest") != manifest_digest(manifest):
        raise RuntimeError("historical BBS manifest digest mismatch")

    by_game: Dict[str, Mapping[str, Any]] = {}
    for row in manifest.get("records") or []:
        if not isinstance(row, Mapping):
            raise RuntimeError("historical BBS manifest record is invalid")
        game_pk = str(row.get("officialGamePk") or "").strip()
        if not game_pk or game_pk in by_game:
            raise RuntimeError("historical BBS manifest game identity is missing or duplicated")
        by_game[game_pk] = row

    output: list[Dict[str, Any]] = []
    applied = 0
    ineligible = 0
    invalid = 0
    unmatched_manifest = set(by_game)
    for raw in records:
        record = copy.deepcopy(dict(raw))
        game_pk = str(record.get("officialGamePk") or "").strip()
        manifest_row = by_game.get(game_pk)
        if manifest_row is not None:
            unmatched_manifest.discard(game_pk)
            if str(manifest_row.get("predictionLockAtUtc") or "") != str(record.get("predictionLockAtUtc") or ""):
                invalid += 1
                record["historicalBbsFundamentals"] = {
                    "trainingEligible": False,
                    "errors": ["manifest_lock_identity_mismatch"],
                    "manifestDigest": manifest.get("manifestDigest"),
                }
            elif manifest_row.get("trainingEligible") is True:
                snapshot = manifest_row.get("snapshot")
                valid, errors = _validate_snapshot(snapshot, record)
                if not valid:
                    invalid += 1
                    record["historicalBbsFundamentals"] = {
                        "trainingEligible": False,
                        "errors": errors,
                        "manifestDigest": manifest.get("manifestDigest"),
                    }
                else:
                    record["frozenFundamentalsSnapshot"] = copy.deepcopy(dict(snapshot))
                    record["historicalBbsFundamentals"] = {
                        "trainingEligible": True,
                        "providerMatchId": manifest_row.get("providerMatchId"),
                        "manifestDigest": manifest.get("manifestDigest"),
                        "snapshotFingerprint": snapshot.get("fingerprint"),
                    }
                    applied += 1
            else:
                ineligible += 1
                record["historicalBbsFundamentals"] = {
                    "trainingEligible": False,
                    "errors": list(manifest_row.get("eligibilityErrors") or ["historical_bbs_row_ineligible"]),
                    "manifestDigest": manifest.get("manifestDigest"),
                }
        output.append(record)

    proof = {
        "version": VERSION,
        "status": "APPLIED",
        "authority": AUTHORITY,
        "manifestDigest": manifest.get("manifestDigest"),
        "manifestRecordCount": len(by_game),
        "manifestEligibleGameCount": int(manifest.get("eligibleGameCount") or 0),
        "appliedGameCount": applied,
        "ineligibleGameCount": ineligible,
        "invalidGameCount": invalid,
        "unmatchedManifestGameCount": len(unmatched_manifest),
        "recordCount": len(output),
        "coverage": round(applied / len(output), 8) if output else 0.0,
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
    }
    return output, proof


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
        table_name or os.environ.get("MLB_V8_HISTORICAL_BBS_TABLE", DEFAULT_TABLE)
    )
    item = table.get_item(
        Key={"PK": POINTER_PK, "SK": POINTER_SK}, ConsistentRead=True
    ).get("Item")
    required = str(os.environ.get("MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED", "false")).lower() == "true"
    if not item:
        if required:
            raise RuntimeError("historical BBS active manifest pointer is missing")
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
        raise RuntimeError("historical BBS active manifest pointer is incomplete")
    response = (s3_client or boto3.client("s3")).get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    if hashlib.sha256(body).hexdigest() != expected_sha:
        raise RuntimeError("historical BBS active manifest checksum mismatch")
    manifest = json.loads(body.decode("utf-8"))
    output, proof = apply_manifest(records, manifest)
    proof["pointerRevision"] = int(item.get("revision") or 0)
    proof["manifestPointer"] = {"bucket": bucket, "key": key, "sha256": expected_sha}
    return output, proof


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_HISTORICAL_BBS_OVERLAY_INSTALLED", False):
        return model_module
    original = model_module.train_and_evaluate

    def patched(records: Sequence[Mapping[str, Any]], **kwargs: Any) -> Dict[str, Any]:
        enriched, proof = load_and_apply(records)
        result = original(enriched, **kwargs)
        result["historicalBbsFundamentals"] = proof
        digest = getattr(model_module, "_sha", None)
        if callable(digest):
            result["resultDigest"] = digest(
                {key: value for key, value in result.items() if key != "resultDigest"}
            )
        return result

    model_module.train_and_evaluate = patched
    model_module._INQSI_MLB_HISTORICAL_BBS_OVERLAY_INSTALLED = True
    return model_module
