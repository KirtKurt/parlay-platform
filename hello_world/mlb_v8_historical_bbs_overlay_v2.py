"""Apply separate target-game and prior-game historical manifests to MLB V8."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Dict, Mapping, Sequence, Tuple

import boto3

import mlb_v8_historical_bbs_overlay_v1 as base

VERSION = "MLB-V8-HISTORICAL-BBS-OVERLAY-v2-separated-authorities"
PRIOR_POINTER_PK = base.POINTER_PK
TARGET_POINTER_PK = "MLB_V8_HISTORICAL_BBS_TARGET#V2"
POINTER_SK = "ACTIVE"
PRIOR_ROLE = "BBD_STRICTLY_PRIOR_COMPLETED_GAME_FEATURES_AT_T_MINUS_45"
TARGET_ROLE = "HISTORICAL_POINT_IN_TIME_RECONSTRUCTION_AT_T_MINUS_45"


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("version") != base.MANIFEST_VERSION:
        raise RuntimeError("historical BBS manifest version mismatch")
    if manifest.get("authority") != base.AUTHORITY:
        raise RuntimeError("historical BBS manifest authority mismatch")
    if manifest.get("productionAuthorityChanged") is not False:
        raise RuntimeError("historical BBS manifest changed production authority")
    if manifest.get("selectionUsedOutcomes") is not False:
        raise RuntimeError("historical BBS manifest outcome-selection contract missing")
    if manifest.get("manifestDigest") != base.manifest_digest(manifest):
        raise RuntimeError("historical BBS manifest digest mismatch")


def _load(table: Any, s3: Any, pointer_pk: str) -> Tuple[Mapping[str, Any] | None, Dict[str, Any]]:
    item = table.get_item(Key={"PK": pointer_pk, "SK": POINTER_SK}, ConsistentRead=True).get("Item")
    if not item:
        return None, {"status": "MANIFEST_NOT_AVAILABLE", "pointerPk": pointer_pk}
    data = base._plain(item.get("data") or {})
    pointer = data.get("manifest") or {}
    bucket = str(pointer.get("bucket") or "")
    key = str(pointer.get("key") or "")
    expected = str(pointer.get("sha256") or "")
    if not bucket or not key or not expected:
        raise RuntimeError(f"historical BBS active manifest pointer is incomplete:{pointer_pk}")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if hashlib.sha256(body).hexdigest() != expected:
        raise RuntimeError(f"historical BBS active manifest checksum mismatch:{pointer_pk}")
    manifest = json.loads(body.decode("utf-8"))
    _validate_manifest(manifest)
    return manifest, {
        "status": "LOADED",
        "pointerPk": pointer_pk,
        "pointerRevision": int(item.get("revision") or 0),
        "manifestPointer": {"bucket": bucket, "key": key, "sha256": expected},
    }


def _apply(
    records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
    *,
    expected_role: str,
    destination: str,
    evidence_key: str,
) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    if manifest is None:
        return [copy.deepcopy(dict(row)) for row in records], {
            "status": "MANIFEST_NOT_AVAILABLE",
            "appliedGameCount": 0,
            "recordCount": len(records),
            "coverage": 0.0,
        }
    by_game: Dict[str, Mapping[str, Any]] = {}
    for row in manifest.get("records") or []:
        if not isinstance(row, Mapping):
            raise RuntimeError("historical BBS manifest record is invalid")
        game_pk = str(row.get("officialGamePk") or "").strip()
        if not game_pk or game_pk in by_game:
            raise RuntimeError("historical BBS manifest game identity is missing or duplicated")
        by_game[game_pk] = row

    output: list[Dict[str, Any]] = []
    applied = ineligible = invalid = 0
    unmatched = set(by_game)
    for raw in records:
        record = copy.deepcopy(dict(raw))
        game_pk = str(record.get("officialGamePk") or "").strip()
        row = by_game.get(game_pk)
        if row is not None:
            unmatched.discard(game_pk)
            errors: list[str] = []
            if str(row.get("predictionLockAtUtc") or "") != str(record.get("predictionLockAtUtc") or ""):
                errors.append("manifest_lock_identity_mismatch")
            elif row.get("trainingEligible") is not True:
                ineligible += 1
                errors.extend(str(value) for value in row.get("eligibilityErrors") or ["historical_bbs_row_ineligible"])
            else:
                snapshot = row.get("snapshot")
                valid, snapshot_errors = base._validate_snapshot(snapshot, record)
                errors.extend(snapshot_errors)
                if isinstance(snapshot, Mapping) and snapshot.get("snapshotRole") != expected_role:
                    errors.append("snapshot_role_mismatch")
                if valid and not errors:
                    record[destination] = copy.deepcopy(dict(snapshot))
                    record[evidence_key] = {
                        "trainingEligible": True,
                        "manifestDigest": manifest.get("manifestDigest"),
                        "snapshotFingerprint": snapshot.get("fingerprint"),
                    }
                    applied += 1
                else:
                    invalid += 1
            if errors:
                record[evidence_key] = {
                    "trainingEligible": False,
                    "errors": sorted(set(errors)),
                    "manifestDigest": manifest.get("manifestDigest"),
                }
        output.append(record)
    return output, {
        "status": "APPLIED",
        "manifestDigest": manifest.get("manifestDigest"),
        "manifestRecordCount": len(by_game),
        "manifestEligibleGameCount": int(manifest.get("eligibleGameCount") or 0),
        "appliedGameCount": applied,
        "ineligibleGameCount": ineligible,
        "invalidGameCount": invalid,
        "unmatchedManifestGameCount": len(unmatched),
        "recordCount": len(output),
        "coverage": round(applied / len(output), 8) if output else 0.0,
    }


def load_and_apply(
    records: Sequence[Mapping[str, Any]],
    *,
    table_name: str | None = None,
    ddb_resource: Any = None,
    s3_client: Any = None,
) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    if not base._enabled():
        return [copy.deepcopy(dict(row)) for row in records], {
            "version": VERSION,
            "status": "DISABLED",
            "authority": base.AUTHORITY,
            "recordCount": len(records),
            "productionAuthorityChanged": False,
        }
    table = (ddb_resource or boto3.resource("dynamodb")).Table(
        table_name or os.environ.get("MLB_V8_HISTORICAL_BBS_TABLE", base.DEFAULT_TABLE)
    )
    s3 = s3_client or boto3.client("s3")
    prior_manifest, prior_pointer = _load(table, s3, PRIOR_POINTER_PK)
    target_manifest, target_pointer = _load(table, s3, TARGET_POINTER_PK)
    with_prior, prior = _apply(
        records,
        prior_manifest,
        expected_role=PRIOR_ROLE,
        destination="historicalBbsPriorGameSnapshot",
        evidence_key="historicalBbsPriorGame",
    )
    enriched, target = _apply(
        with_prior,
        target_manifest,
        expected_role=TARGET_ROLE,
        destination="frozenFundamentalsSnapshot",
        evidence_key="historicalPointInTimeFundamentals",
    )
    proof = {
        "version": VERSION,
        "status": "APPLIED",
        "authority": base.AUTHORITY,
        "recordCount": len(enriched),
        "priorGame": {**prior, **prior_pointer},
        "targetGame": {**target, **target_pointer},
        "priorGameAppliedCount": int(prior.get("appliedGameCount") or 0),
        "targetGameAppliedCount": int(target.get("appliedGameCount") or 0),
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
    }
    return enriched, proof


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_HISTORICAL_BBS_OVERLAY_V2_INSTALLED", False):
        return model_module
    original = model_module.train_and_evaluate

    def patched(records: Sequence[Mapping[str, Any]], **kwargs: Any) -> Dict[str, Any]:
        enriched, proof = load_and_apply(records)
        result = original(enriched, **kwargs)
        result["historicalBbsFundamentals"] = proof
        result["historicalPointInTimeFundamentals"] = proof.get("targetGame")
        digest = getattr(model_module, "_sha", None)
        if callable(digest):
            result["resultDigest"] = digest(
                {key: value for key, value in result.items() if key != "resultDigest"}
            )
        return result

    model_module.train_and_evaluate = patched
    model_module._INQSI_MLB_HISTORICAL_BBS_OVERLAY_INSTALLED = True
    model_module._INQSI_MLB_HISTORICAL_BBS_OVERLAY_V2_INSTALLED = True
    return model_module
