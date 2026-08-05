#!/usr/bin/env python3
"""Populate leakage-safe MLB historical context without BBD/BBS.

This entrypoint reuses the immutable historical-manifest machinery while replacing
its provider client with an official MLB Stats API/Open-Meteo adapter. It never
reads a BBD credential, never calls a BBD endpoint, never uses same-day results,
and never changes production authority.

Eligibility is feature-family aware: strictly point-in-time starter, bullpen and
team context form the core row contract; optional lineup, injury, park and weather
features are removed from an individual row when unavailable instead of causing a
safe core row to be discarded. Policy-version changes replay previously skipped
rows through a new immutable shadow manifest.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

import boto3
from botocore.exceptions import ClientError

import mlb_v8_historical_context_eligibility_v2 as eligibility
import mlb_v8_historical_context_overlay_v1 as target_overlay
import mlb_v8_historical_point_in_time_context_v1 as context_source
import run_mlb_v8_historical_bbs_backfill as backfill

VERSION = "MLB-V8-HISTORICAL-CONTEXT-BACKFILL-v5-feature-aware-replay"
REPORT_TYPE = "MLB_V8_HISTORICAL_CONTEXT_BACKFILL"
AUTHORITY = "V8_HISTORICAL_OFFICIAL_CONTEXT_SHADOW_ONLY"
RECORD_TYPE = "mlb_v8_historical_official_context_active_manifest_v3"
ARCHIVED_WEATHER_MODEL = "ecmwf_ifs"
MIGRATION_VERSION = "MLB-V8-CONTEXT-POINTER-MIGRATION-v2-feature-aware-replay"
EXPECTED_EMPTY_ELIGIBILITY_BLOCKERS = {
    "no_training_eligible_point_in_time_bbs_rows",
    "current_batch_added_zero_training_eligible_rows",
}
_BATCH_DIAGNOSTICS: Dict[str, Dict[str, Any]] = {}


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _team_name(game: Mapping[str, Any], side: str) -> str:
    return str(
        _dict(_dict(_dict(game.get("teams")).get(side)).get("team")).get("name")
        or ""
    )


class OfficialContextClient:
    """Provider-compatible surface backed only by official/public sources."""

    def __init__(self) -> None:
        self.source = context_source.OfficialContextSource(timeout_seconds=20)
        self.games: Dict[tuple[str, str], Dict[str, Any]] = {}
        self.bundles: Dict[tuple[str, str, str], Dict[str, Any]] = {}

    def list_mlb_matches(
        self, game_date: str, *, limit: int = 200, **_: Any
    ) -> Dict[str, Any]:
        day = date.fromisoformat(str(game_date)[:10])
        payload: Mapping[str, Any] = {}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                payload = self.source.schedule(day, day)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        if last_error is not None:
            raise RuntimeError(
                f"official_schedule_unavailable:{type(last_error).__name__}"
            ) from None

        rows = []
        for game in context_source._schedule_games(payload):
            game_pk = str(game.get("gamePk") or "").strip()
            start = str(game.get("gameDate") or "").strip()
            home = _team_name(game, "home")
            away = _team_name(game, "away")
            if not game_pk or not start or not home or not away:
                continue
            row = {
                "id": game_pk,
                "match_id": game_pk,
                "officialGamePk": game_pk,
                "gamePk": game_pk,
                "startTime": start,
                "home": {"name": home},
                "away": {"name": away},
            }
            self.games[(day.isoformat(), game_pk)] = copy.deepcopy(row)
            rows.append(row)
        rows.sort(key=lambda row: (str(row["startTime"]), str(row["id"])))
        return {
            "data": rows[: max(1, min(int(limit), 200))],
            "meta": {
                "source": "MLB Stats API",
                "provider": "official_mlb",
                "complete": True,
                "asOfUtc": datetime.now(timezone.utc).isoformat(),
            },
            "error": None,
        }

    def _canonical(
        self, match_id: str, game_date: str, as_of: str
    ) -> Dict[str, Any]:
        key = (str(game_date)[:10], str(match_id))
        row = self.games.get(key)
        if row is None:
            self.list_mlb_matches(key[0], limit=200)
            row = self.games.get(key)
        if row is None:
            raise RuntimeError("official_game_identity_unavailable")
        return {
            "officialGamePk": str(match_id),
            "slateDateEt": key[0],
            "predictionLockAtUtc": str(as_of),
            "commenceTime": row.get("startTime"),
            "homeTeam": _dict(row.get("home")).get("name"),
            "awayTeam": _dict(row.get("away")).get("name"),
        }

    def get_mlb_match_resource(
        self,
        match_id: str,
        resource: str,
        *,
        game_date: str,
        as_of: str | None = None,
        **_: Any,
    ) -> Dict[str, Any]:
        lock_at = str(as_of or "")
        key = (str(game_date)[:10], str(match_id), lock_at)
        if key not in self.bundles:
            canonical = self._canonical(match_id, game_date, lock_at)
            self.bundles[key] = self.source.build_bundle(canonical, {}, {})
        envelope = copy.deepcopy(self.bundles[key].get(str(resource)) or {})
        if not envelope:
            return {
                "data": None,
                "meta": {
                    "source": "official_mlb_prior_context",
                    "provider": "official_mlb",
                    "asOfUtc": lock_at,
                    "complete": False,
                },
                "error": f"OFFICIAL_CONTEXT_RESOURCE_UNAVAILABLE:{resource}",
            }
        envelope.setdefault("meta", {})
        envelope["meta"].setdefault("source", "official_mlb_prior_context")
        envelope["meta"].setdefault("provider", "official_mlb")
        envelope["meta"].setdefault("asOfUtc", lock_at)
        return envelope


def install_pointer_isolation(module: Any) -> Any:
    module.overlay.POINTER_PK = target_overlay.POINTER_PK
    module.overlay.POINTER_SK = target_overlay.POINTER_SK
    module.overlay.AUTHORITY = AUTHORITY
    target_overlay.AUTHORITY = AUTHORITY
    target_overlay.base.AUTHORITY = AUTHORITY
    module.VERSION = VERSION
    module.REPORT_TYPE = REPORT_TYPE
    original_load_previous_manifest = getattr(
        module, "_load_previous_manifest", lambda _table, _s3: (None, 0)
    )

    def load_previous_manifest(table: Any, s3: Any):
        item = table.get_item(
            Key={"PK": target_overlay.POINTER_PK, "SK": target_overlay.POINTER_SK},
            ConsistentRead=True,
        ).get("Item")
        if not item:
            module._v8_context_replay_from_start = False
            return None, 0
        revision = int(item.get("revision") or 0)
        plain = getattr(module, "_plain", None)
        raw_data = item.get("data") or {}
        data = plain(raw_data) if callable(plain) else dict(raw_data)
        provider = str(data.get("provider") or "")
        official_pointer = bool(
            item.get("record_type") == RECORD_TYPE
            and data.get("authority") == AUTHORITY
            and provider.startswith("official_mlb")
        )
        policy_current = bool(
            data.get("eligibilityPolicyVersion") == eligibility.VERSION
            and data.get("materializerVersion") == eligibility.MATERIALIZER_VERSION
        )
        if not official_pointer or not policy_current:
            module._v8_context_replay_from_start = True
            return None, revision
        module._v8_context_replay_from_start = False
        return original_load_previous_manifest(table, s3)

    module._load_previous_manifest = load_previous_manifest

    def put_immutable(
        s3: Any, bucket: str, key: str, body: bytes
    ) -> Dict[str, Any]:
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
                    "record-type": "mlb-v8-historical-official-context-manifest",
                    "provider": "official-mlb",
                    "eligibility-policy": eligibility.VERSION,
                    "materializer-version": eligibility.MATERIALIZER_VERSION,
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
                "authority": AUTHORITY,
                "manifest": dict(pointer),
                "manifestDigest": manifest.get("manifestDigest"),
                "processedGameCount": manifest.get("processedGameCount"),
                "eligibleGameCount": manifest.get("eligibleGameCount"),
                "featureFamily": target_overlay.TARGET_FAMILY,
                "provider": "official_mlb_plus_internal_canonical",
                "eligibilityPolicyVersion": eligibility.VERSION,
                "materializerVersion": eligibility.MATERIALIZER_VERSION,
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
    return module


def install_snapshot_contract(module: Any) -> Any:
    original = module.build_training_snapshot

    def build_snapshot(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        snapshot = original(*args, **kwargs)
        canonical = args[0] if args and isinstance(args[0], Mapping) else {}
        resources = args[3] if len(args) > 3 else kwargs.get("resources") or {}
        snapshot = eligibility.apply_to_snapshot(
            snapshot,
            resources if isinstance(resources, Mapping) else {},
            canonical.get("predictionLockAtUtc"),
        )
        evidence: Dict[str, Any] = {}
        for name in (*module.REQUIRED_RESOURCES, *module.OPTIONAL_RESOURCES):
            envelope = resources.get(name) if isinstance(resources, Mapping) else None
            meta = _dict(_dict(envelope).get("meta"))
            effective = module._effective_at(envelope)
            evidence[name] = {
                "source": meta.get("source") or "official_mlb_prior_context",
                "provider": "official_mlb",
                "sourceEffectiveAtUtc": effective.isoformat() if effective else None,
                "payloadFingerprint": module._sha(envelope),
                "derivationVersion": meta.get("derivationVersion"),
                "pointInTimeProjectionVerified": meta.get(
                    "pointInTimeProjectionVerified"
                ),
            }
        snapshot.update(
            {
                "authority": AUTHORITY,
                "providerEvidence": evidence,
                "sameDayResultsExcluded": True,
                "targetGameOutcomeUsed": False,
                "selectionUsedOutcomes": False,
                "productionAuthorityChanged": False,
            }
        )
        game_pk = str(canonical.get("officialGamePk") or "")
        if game_pk:
            _BATCH_DIAGNOSTICS[game_pk] = {
                key: copy.deepcopy(snapshot.get(key))
                for key in (
                    "trainingEligibleCore",
                    "trainingEligible",
                    "featureEligibility",
                    "featureMissingness",
                    "featureAvailabilityMode",
                    "featureEvidence",
                    "eligibilityErrors",
                    "eligibilityWarnings",
                )
            }
        snapshot["fingerprint"] = module.overlay.snapshot_fingerprint(snapshot)
        return snapshot

    module.build_training_snapshot = build_snapshot
    return module


def _advance_ineligible_cursor(
    module: Any,
    report: Dict[str, Any],
    kwargs: Mapping[str, Any],
) -> bool:
    if int(report.get("newRecordCount") or 0) <= 0:
        return False
    if int(report.get("eligibleGameCount") or 0) != 0:
        return False
    pointer = _dict(report.get("manifest"))
    bucket = str(pointer.get("bucket") or "")
    key = str(pointer.get("key") or "")
    region = str(kwargs.get("region") or "")
    table_name = str(kwargs.get("table_name") or "")
    if not bucket or not key or not region or not table_name:
        raise RuntimeError("official_context_cursor_activation_inputs_missing")
    s3 = boto3.client("s3", region_name=region)
    response = s3.get_object(Bucket=bucket, Key=key)
    manifest = json.loads(response["Body"].read().decode("utf-8"))
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    previous_revision = int(report.get("activePointerRevision") or 0)
    report["activePointerRevision"] = module._activate(
        table,
        pointer,
        manifest,
        previous_revision,
    )
    return True


def install_run_contract(module: Any) -> Any:
    original = module.run

    def run(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        _BATCH_DIAGNOSTICS.clear()
        kwargs["client_factory"] = OfficialContextClient
        report = original(*args, **kwargs)
        cursor_advanced = _advance_ineligible_cursor(module, report, kwargs)
        inherited_blockers = [str(value) for value in report.get("blockers") or []]
        warnings = []
        blockers = []
        for blocker in inherited_blockers:
            if blocker in EXPECTED_EMPTY_ELIGIBILITY_BLOCKERS:
                warnings.append(
                    {
                        "no_training_eligible_point_in_time_bbs_rows":
                            "no_training_eligible_point_in_time_official_context_rows_yet",
                        "current_batch_added_zero_training_eligible_rows":
                            "current_batch_added_zero_training_eligible_official_context_rows",
                    }[blocker]
                )
            else:
                blockers.append(blocker)
        if cursor_advanced:
            warnings.append("cursor_advanced_across_training_ineligible_historical_rows")
        telemetry = eligibility.summarize_batch(_BATCH_DIAGNOSTICS)
        report.update(
            {
                "proofType": REPORT_TYPE,
                "version": VERSION,
                "authority": AUTHORITY,
                "provider": "official_mlb_plus_internal_canonical_context",
                "bbsApiUsed": False,
                "bbsCredentialRead": False,
                "sameDayResultsExcluded": True,
                "targetGameOutcomeUsed": False,
                "selectionUsedOutcomes": False,
                "productionAuthorityChanged": False,
                "legacyBbsCarryForwardAllowed": False,
                "pointerMigrationVersion": MIGRATION_VERSION,
                "eligibilityPolicyVersion": eligibility.VERSION,
                "materializerVersion": eligibility.MATERIALIZER_VERSION,
                "replayFromStartApplied": bool(
                    getattr(module, "_v8_context_replay_from_start", False)
                ),
                "officialContextPointerAuthoritative": True,
                "automaticWagerAllowed": False,
                "cursorAdvanced": bool(
                    cursor_advanced
                    or int(report.get("newRecordCount") or 0) > 0
                    and int(report.get("eligibleGameCount") or 0) > 0
                ),
                "progressMade": int(report.get("newRecordCount") or 0) > 0,
                "warnings": sorted(set(warnings)),
                "blockers": sorted(set(blockers)),
                "ok": not blockers,
                **telemetry,
            }
        )
        output = kwargs.get("output")
        if isinstance(output, Path):
            output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report

    module.run = run
    return module


def install() -> Any:
    context_source.WEATHER_MODEL = ARCHIVED_WEATHER_MODEL
    install_pointer_isolation(backfill)
    install_snapshot_contract(backfill)
    install_run_contract(backfill)
    return backfill


def main() -> int:
    install()
    return backfill.main()


if __name__ == "__main__":
    raise SystemExit(main())
