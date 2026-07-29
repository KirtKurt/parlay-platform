#!/usr/bin/env python3
"""Populate isolated V8 target-game historical context and publish fail-closed proof.

The existing strictly-prior BBD backfill remains on its original manifest pointer.
This entrypoint gives target-game starter, bullpen, lineup, injury, park, and weather
context a separate pointer, fills it only from T-45-safe sources, and preserves every
existing selection and promotion guard.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from botocore.exceptions import ClientError

import mlb_v8_historical_context_overlay_v1 as target_overlay
import mlb_v8_historical_point_in_time_context_v1 as context_source
import run_mlb_v8_historical_bbs_backfill as backfill
import run_mlb_v8_historical_bbs_backfill_entrypoint as operational

VERSION = "MLB-V8-HISTORICAL-CONTEXT-BACKFILL-v1-isolated-pointer"
REPORT_TYPE = "MLB_V8_HISTORICAL_CONTEXT_BACKFILL"
RECORD_TYPE = "mlb_v8_historical_context_active_manifest_v1"
ARCHIVED_WEATHER_MODEL = "ecmwf_ifs"


def install_weather_archive_contract(module: Any) -> Any:
    """Select the Single Runs archive model identifier proven by its API contract."""
    module.WEATHER_MODEL = ARCHIVED_WEATHER_MODEL
    return module


def install_pointer_isolation(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_TARGET_CONTEXT_POINTER_INSTALLED", False):
        return module
    module.overlay.POINTER_PK = target_overlay.POINTER_PK
    module.overlay.POINTER_SK = target_overlay.POINTER_SK
    module.VERSION = VERSION
    module.REPORT_TYPE = REPORT_TYPE

    def put_immutable(s3: Any, bucket: str, key: str, body: bytes):
        isolated = f"mlb/v8/historical-context/manifests/{Path(key).name}"
        digest = hashlib.sha256(body).hexdigest()
        try:
            response = s3.put_object(
                Bucket=bucket,
                Key=isolated,
                Body=body,
                ContentType="application/json",
                ServerSideEncryption="AES256",
                IfNoneMatch="*",
                Metadata={
                    "sha256": digest,
                    "record-type": "mlb-v8-historical-context-manifest",
                },
            )
            return {
                "bucket": bucket,
                "key": isolated,
                "sha256": digest,
                "versionId": response.get("VersionId"),
                "alreadyExisted": False,
            }
        except ClientError as exc:
            code = str((exc.response.get("Error") or {}).get("Code") or "")
            status = int(
                (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
                or 0
            )
            if code not in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
            } and status not in {409, 412}:
                raise
            head = s3.head_object(Bucket=bucket, Key=isolated)
            existing = str((head.get("Metadata") or {}).get("sha256") or "")
            if existing and existing != digest:
                raise RuntimeError("historical_context_immutable_manifest_collision")
            return {
                "bucket": bucket,
                "key": isolated,
                "sha256": digest,
                "versionId": head.get("VersionId"),
                "alreadyExisted": True,
            }

    def activate(
        table: Any,
        pointer: Mapping[str, Any],
        manifest: Mapping[str, Any],
        previous_revision: int,
    ) -> int:
        revision = int(previous_revision) + 1
        item = {
            "PK": target_overlay.POINTER_PK,
            "SK": target_overlay.POINTER_SK,
            "record_type": RECORD_TYPE,
            "revision": revision,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "data": {
                "version": target_overlay.VERSION,
                "authority": target_overlay.AUTHORITY,
                "manifest": dict(pointer),
                "manifestDigest": manifest.get("manifestDigest"),
                "processedGameCount": manifest.get("processedGameCount"),
                "eligibleGameCount": manifest.get("eligibleGameCount"),
                "featureFamily": target_overlay.TARGET_FAMILY,
                "productionAuthorityChanged": False,
            },
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

    module._put_immutable = put_immutable
    module._activate = activate
    module._INQSI_MLB_TARGET_CONTEXT_POINTER_INSTALLED = True
    return module


def install_resource_shape_compatibility(module: Any) -> Any:
    """Expose reconstructed recent-start form through the fundamentals compiler alias."""
    if getattr(module, "_INQSI_MLB_CONTEXT_SHAPE_COMPAT_INSTALLED", False):
        return module
    fundamentals = getattr(module, "fundamentals", None)
    if fundamentals is None:
        fundamentals = getattr(module, "collector", None)
    if fundamentals is None or not callable(getattr(fundamentals, "normalize_match", None)):
        raise RuntimeError("historical_context_fundamentals_compiler_unavailable")
    original = fundamentals.normalize_match

    def normalize(match: Mapping[str, Any], captured_at: datetime, resources=None):
        copied = copy.deepcopy(resources or {})
        envelope = copied.get("pitchers")
        data = envelope.get("data") if isinstance(envelope, Mapping) else None
        if isinstance(data, Mapping):
            data = copy.deepcopy(dict(data))
            for side in ("away", "home"):
                raw = data.get(side)
                if isinstance(raw, Mapping):
                    raw = copy.deepcopy(dict(raw))
                    if (
                        raw.get("recent") is None
                        and raw.get("recentThreeStarts") is not None
                    ):
                        raw["recent"] = copy.deepcopy(raw.get("recentThreeStarts"))
                    data[side] = raw
            copied["pitchers"] = {**dict(envelope), "data": data}
        return original(match, captured_at, copied)

    fundamentals.normalize_match = normalize
    module._INQSI_MLB_CONTEXT_SHAPE_COMPAT_INSTALLED = True
    return module


def install_snapshot_contract(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_TARGET_CONTEXT_SNAPSHOT_INSTALLED", False):
        return module
    original = module.build_training_snapshot

    def snapshot(
        canonical: Mapping[str, Any],
        provider: Mapping[str, Any],
        normalized_game: Mapping[str, Any],
        resources: Mapping[str, Any],
        *,
        retrieved_at: datetime,
    ):
        value = original(
            canonical,
            provider,
            normalized_game,
            resources,
            retrieved_at=retrieved_at,
        )
        errors = list(value.get("eligibilityErrors") or [])
        coverage = normalized_game.get("coverage") or {}
        pitchers_projected = context_source._projection_verified(
            resources.get("pitchers") or {}
        )
        lineups_projected = context_source._projection_verified(
            resources.get("lineups") or {}
        )
        starter_identity_safe = bool(
            coverage.get("confirmedStarters") is True or pitchers_projected
        )
        lineup_identity_safe = bool(
            coverage.get("confirmedLineups") is True or lineups_projected
        )
        if starter_identity_safe:
            errors = [
                error
                for error in errors
                if str(error) != "confirmed_starters_missing"
            ]
        if lineup_identity_safe:
            errors = [
                error for error in errors if str(error) != "confirmed_lineups_missing"
            ]
        identity_mode = (
            "ARCHIVED_CONFIRMED_T_MINUS_45"
            if coverage.get("confirmedStarters") is True
            and coverage.get("confirmedLineups") is True
            else "STRICTLY_PRIOR_PROJECTION"
            if pitchers_projected and lineups_projected
            else "HYBRID_ARCHIVED_CONFIRMATION_AND_STRICTLY_PRIOR_PROJECTION"
        )
        if value.get("parkRunFactor") is None:
            errors.append("park_run_factor_missing")
        if value.get("weatherRunFactor") is None:
            errors.append("weather_run_factor_missing")
        point_errors = module.point_in_time_errors(
            resources, canonical.get("predictionLockAtUtc")
        )
        errors.extend(point_errors)
        errors = sorted(set(str(error) for error in errors))

        evidence = {}
        for name in (*module.REQUIRED_RESOURCES, *module.OPTIONAL_RESOURCES):
            envelope = resources.get(name) if isinstance(resources, Mapping) else None
            meta = (
                envelope.get("meta")
                if isinstance(envelope, Mapping)
                and isinstance(envelope.get("meta"), Mapping)
                else {}
            )
            transport = (
                envelope.get("_transport")
                if isinstance(envelope, Mapping)
                and isinstance(envelope.get("_transport"), Mapping)
                else {}
            )
            effective = module._effective_at(envelope)
            evidence[name] = {
                "source": meta.get("source")
                or meta.get("provider")
                or "bigballsdata",
                "endpoint": meta.get("endpoint") or transport.get("endpoint") or None,
                "sourceEffectiveAtUtc": effective.isoformat()
                if effective
                else None,
                "payloadFingerprint": module._sha(envelope),
                "derivationVersion": meta.get("derivationVersion"),
                "targetIdentityMode": meta.get("targetIdentityMode"),
                "pointInTimeProjectionVerified": meta.get(
                    "pointInTimeProjectionVerified"
                ),
            }

        value.update(
            {
                "snapshotRole": target_overlay.TARGET_ROLE,
                "providerEvidence": evidence,
                "pointInTimeVerified": not point_errors,
                "postgameFieldsExcluded": True,
                "sameDayResultsExcluded": True,
                "targetGameOutcomeUsed": False,
                "selectionUsedOutcomes": False,
                "targetIdentityMode": identity_mode,
                "confirmedTargetStarters": bool(coverage.get("confirmedStarters")),
                "confirmedTargetLineups": bool(coverage.get("confirmedLineups")),
                "projectedTargetStarters": pitchers_projected,
                "projectedTargetLineups": lineups_projected,
                "trainingEligible": not errors,
                "eligibilityErrors": errors,
                "featureFamilies": {
                    target_overlay.TARGET_FAMILY: {
                        "available": not errors,
                        "trainingEligible": not errors,
                        "pointInTimeVerified": not point_errors,
                        "snapshotRole": target_overlay.TARGET_ROLE,
                        "targetIdentityMode": identity_mode,
                        "eligibilityErrors": errors,
                    }
                },
                "productionAuthorityChanged": False,
            }
        )
        value["fingerprint"] = module.overlay.snapshot_fingerprint(value)
        return value

    module.build_training_snapshot = snapshot
    module._INQSI_MLB_TARGET_CONTEXT_SNAPSHOT_INSTALLED = True
    return module


def install_report_contract(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_TARGET_CONTEXT_REPORT_INSTALLED", False):
        return module
    original = module.run

    def run(*args: Any, **kwargs: Any):
        context_source.OFFICIAL_REQUEST_COUNT = 0
        context_source.SYNTHETIC_OFFICIAL_IDENTITY_COUNT = 0
        context_source.STORED_DISCOVERY_ERROR_COUNT = 0
        context_source._CANONICAL_BY_PROVIDER_ID.clear()
        report = original(*args, **kwargs)
        report.update(
            {
                "proofType": REPORT_TYPE,
                "version": VERSION,
                "manifestPointerPk": target_overlay.POINTER_PK,
                "manifestPointerSk": target_overlay.POINTER_SK,
                "featureFamily": target_overlay.TARGET_FAMILY,
                "provider": "bigballsdata_stored_confirmation_plus_official_prior_context",
                "officialContextRequestsMade": context_source.OFFICIAL_REQUEST_COUNT,
                "syntheticOfficialIdentityCount": context_source.SYNTHETIC_OFFICIAL_IDENTITY_COUNT,
                "storedDiscoveryErrorCount": context_source.STORED_DISCOVERY_ERROR_COUNT,
                "confirmedOrProjectionSafeTargetIdentityRequired": True,
                "requiredDomains": list(module.REQUIRED_RESOURCES),
                "requiredPointInTimeOptionalDomains": list(module.OPTIONAL_RESOURCES),
                "weatherArchiveModel": context_source.WEATHER_MODEL,
                "sameDayResultsExcluded": True,
                "targetGameOutcomeUsed": False,
                "selectionUsedOutcomes": False,
                "productionAuthorityChanged": False,
                "automaticWagerAllowed": False,
            }
        )
        output = kwargs.get("output")
        if isinstance(output, Path):
            output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report

    module.run = run
    module._INQSI_MLB_TARGET_CONTEXT_REPORT_INSTALLED = True
    return module


def install() -> Any:
    historical_stack = os.environ.get(
        "HISTORICAL_STACK", backfill.DEFAULT_HISTORICAL_STACK
    )
    fundamentals_stack = os.environ.get(
        "FUNDAMENTALS_STACK", backfill.DEFAULT_FUNDAMENTALS_STACK
    )
    install_weather_archive_contract(context_source)
    install_pointer_isolation(backfill)
    operational.install_bucket_fallback(
        backfill,
        historical_stack=historical_stack,
        fundamentals_stack=fundamentals_stack,
    )
    operational.install_stored_match_surface(backfill.BigBallsDataClient)
    context_source.install_best_effort_stored_discovery(backfill.BigBallsDataClient)
    operational.install_historical_resource_surfaces(backfill.BigBallsDataClient)
    context_source.install_official_identity_fallback(backfill)
    context_source.install_crosswalk_registry(backfill)
    context_source.install_resource_provider(backfill.BigBallsDataClient)
    context_source.install_strict_optional_point_in_time_gate(backfill)
    install_resource_shape_compatibility(backfill)
    install_snapshot_contract(backfill)
    operational.install_newest_coverage_window(backfill)
    operational.install_safe_diagnostics(backfill)
    install_report_contract(backfill)
    return backfill


def main() -> int:
    install()
    return backfill.main()


if __name__ == "__main__":
    raise SystemExit(main())
