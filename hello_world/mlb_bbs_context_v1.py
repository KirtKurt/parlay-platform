"""Write-once BBS MLB shadow evidence bound to a canonical pull slot.

Nothing in this module can change a pick, a T-45 vector, training eligibility,
or fundamentals completeness. The bounded inline request is explicitly a
single-UTC-date probe, not proof of complete Eastern-slate coverage.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional

import boto3
from botocore.exceptions import ClientError

try:  # Package import for tests; Lambda imports modules from the task root.
    from .bigballsdata_client import BBSClientError, BigBallsDataClient
except ImportError:  # pragma: no cover - exercised by the Lambda import layout
    from bigballsdata_client import BBSClientError, BigBallsDataClient


VERSION = "MLB-BBS-SHADOW-CAPTURE-v2-canonical-bound-raw-only"
ACTIVATION_POLICY = "MLB-BBS-PARTIAL-UTC-DATE-PROBE-v2-no-slate-or-ml-credit"
PROVIDER = "Big Balls Sports Data"
ENDPOINT = "/v1/matches?sport=baseball&league=mlb&date={date}"
COVERAGE_MODE = "PARTIAL_SINGLE_UTC_DATE_PROBE"


def _parse_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def payload_fingerprint(value: Any) -> str:
    material = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _slot_start(value: Any) -> datetime:
    parsed = _parse_utc(value)
    if parsed is None:
        raise ValueError("BBS_CAPTURE_CANONICAL_SLOT_INVALID")
    return parsed.replace(minute=(parsed.minute // 15) * 15, second=0, microsecond=0)


def _team_name(row: Dict[str, Any], side: str) -> Optional[str]:
    value = row.get(side)
    if isinstance(value, dict):
        value = value.get("team_name") or value.get("name") or value.get("display_name")
    return str(value) if value not in (None, "") else (
        str(row.get(f"{side}_team") or row.get(f"{side}Team") or "") or None
    )


def _provider_start(row: Dict[str, Any]) -> Optional[str]:
    value = row.get("kickoff_utc")
    return str(value) if value not in (None, "") else None


def _official_game(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "officialGamePk": str(row.get("official_game_pk") or row.get("officialGamePk") or ""),
        "homeTeam": row.get("home_team") or row.get("homeTeam"),
        "awayTeam": row.get("away_team") or row.get("awayTeam"),
        "startTimeUtc": row.get("official_commence_time") or row.get("commence_time") or row.get("startTimeUtc"),
    }


def _official_game_utc_dates(official_games: Iterable[Dict[str, Any]]) -> List[str]:
    dates: set[str] = set()
    for raw in official_games or []:
        parsed = _parse_utc(_official_game(raw).get("startTimeUtc"))
        if parsed is not None:
            dates.add(parsed.date().isoformat())
    return sorted(dates)


def crosswalk_matches(
    provider_rows: Iterable[Dict[str, Any]],
    official_games: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Quarantine documented BBS rows without inventing an MLB identity join.

    BBS currently documents ``match_id`` and ``kickoff_utc`` but no MLB
    ``gamePk`` or external-ID map. Team/time similarity cannot receive official
    identity credit, especially for schedule revisions and doubleheaders.
    """

    rows = list(provider_rows or [])
    official = [_official_game(raw) for raw in (official_games or [])]
    quarantined: List[Dict[str, Any]] = []
    seen_provider_ids: set[str] = set()
    documented_row_count = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            quarantined.append({"providerIndex": index, "reason": "PROVIDER_ROW_NOT_OBJECT"})
            continue
        provider_id = str(row.get("match_id") or "")
        provider_start = _provider_start(row)
        parsed_start = _parse_utc(provider_start)
        home = _team_name(row, "home")
        away = _team_name(row, "away")
        if not provider_id:
            reason = "MISSING_PROVIDER_MATCH_ID"
        elif provider_id in seen_provider_ids:
            reason = "DUPLICATE_PROVIDER_MATCH_ID"
        elif parsed_start is None:
            reason = "MISSING_OR_INVALID_PROVIDER_KICKOFF_UTC"
        elif not home or not away:
            reason = "MISSING_PROVIDER_TEAMS"
        else:
            documented_row_count += 1
            reason = "PROVIDER_OFFICIAL_GAME_IDENTITY_UNAVAILABLE"
        if provider_id:
            seen_provider_ids.add(provider_id)
        quarantined.append(
            {
                "providerIndex": index,
                "providerMatchId": provider_id or None,
                "providerKickoffUtc": (
                    parsed_start.isoformat() if parsed_start is not None else provider_start
                ),
                "providerRowFingerprint": payload_fingerprint(row),
                "reason": reason,
            }
        )
    return {
        "accepted": [],
        "quarantined": quarantined,
        "acceptedCount": 0,
        "quarantinedCount": len(quarantined),
        "providerRowCount": len(rows),
        "documentedRowCount": documented_row_count,
        "officialGameCount": len(official),
        "providerOfficialGameIdentityDocumented": False,
        "officialIdentityCredit": False,
        "completeOfficialCrosswalk": False,
    }


def _artifact_key(game_date: str, slot: datetime) -> str:
    compact_slot = slot.strftime("%Y%m%dT%H%M%SZ")
    return f"mlb/providers/bbs/{game_date}/slot-{compact_slot}.json"


def _canonical_binding(canonical_pull: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pulled_at = canonical_pull.get("canonicalPulledAtUtc")
    try:
        slot = _slot_start(pulled_at)
    except ValueError:
        return None
    binding = {
        "canonicalPullId": str(canonical_pull.get("canonicalPullId") or ""),
        "canonicalPulledAtUtc": str(pulled_at or ""),
        "canonicalSlotStartUtc": slot.isoformat(),
        "canonicalPullPayloadFingerprint": str(
            canonical_pull.get("canonicalPullPayloadFingerprint") or ""
        ),
        "canonicalPullPk": str(canonical_pull.get("canonicalPullPk") or ""),
        "canonicalPullSk": str(canonical_pull.get("canonicalPullSk") or ""),
        "canonicalManifestFingerprint": str(
            canonical_pull.get("providerManifestFingerprint") or ""
        ),
        "retryReturnedExistingCanonicalPull": (
            canonical_pull.get("retryReturnedExistingCanonicalPull") is True
        ),
    }
    required = (
        "canonicalPullId",
        "canonicalPulledAtUtc",
        "canonicalSlotStartUtc",
        "canonicalPullPayloadFingerprint",
        "canonicalPullPk",
        "canonicalPullSk",
        "canonicalManifestFingerprint",
    )
    return binding if all(binding.get(key) for key in required) else None


def _binding_metadata(binding: Dict[str, Any]) -> Dict[str, str]:
    return {
        "canonical-pull-id": str(binding["canonicalPullId"]),
        "canonical-pulled-at-utc": str(binding["canonicalPulledAtUtc"]),
        "canonical-slot-start-utc": str(binding["canonicalSlotStartUtc"]),
        "canonical-pull-fingerprint": str(
            binding["canonicalPullPayloadFingerprint"]
        ),
        "canonical-pull-pk": str(binding["canonicalPullPk"]),
        "canonical-pull-sk": str(binding["canonicalPullSk"]),
        "canonical-manifest-fingerprint": str(
            binding["canonicalManifestFingerprint"]
        ),
    }


def _head_existing(
    s3: Any,
    bucket: str,
    key: str,
    *,
    expected_binding: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    try:
        result = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    metadata = {
        str(name).lower(): str(value)
        for name, value in (result.get("Metadata") or {}).items()
    }
    expected = {
        **_binding_metadata(expected_binding),
        "schema-version": VERSION,
        "shadow-only": "true",
    }
    missing = sorted(
        name for name in (*expected.keys(), "fingerprint", "retrieved-at-utc")
        if not metadata.get(name)
    )
    mismatched = sorted(
        name for name, value in expected.items()
        if metadata.get(name) not in (None, "") and metadata.get(name) != value
    )
    if missing or mismatched:
        return {
            "ok": False,
            "status": "BBS_ARTIFACT_CANONICAL_BINDING_COLLISION",
            "bucket": bucket,
            "key": key,
            "missingMetadataFields": missing,
            "mismatchedMetadataFields": mismatched,
            "shadowOnly": True,
            "trainingEligible": False,
            "completenessCredit": False,
            "officialIdentityCredit": False,
        }
    return {
        "ok": True,
        "status": "REUSED_WRITE_ONCE_ARTIFACT",
        "bucket": bucket,
        "key": key,
        "versionId": result.get("VersionId"),
        "artifactFingerprint": metadata.get("fingerprint"),
        "retrievedAtUtc": metadata.get("retrieved-at-utc"),
        "canonicalBindingVerified": True,
        "shadowOnly": True,
        "trainingEligible": False,
        "completenessCredit": False,
        "officialIdentityCredit": False,
    }


def capture_shadow_slot(
    *,
    game_date: str,
    canonical_pull: Dict[str, Any],
    official_games: List[Dict[str, Any]],
    client_factory: Callable[..., BigBallsDataClient] = BigBallsDataClient,
    s3_client: Any = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Dict[str, Any]:
    if os.environ.get("BBS_SHADOW_CAPTURE_ENABLED", "false").lower() != "true":
        return {"ok": True, "status": "DISABLED", "shadowOnly": True}
    bucket = os.environ.get("BBS_SHADOW_S3_BUCKET", "")
    if not bucket:
        return {"ok": False, "status": "CONFIGURATION_ERROR", "error": "BBS_SHADOW_S3_BUCKET_MISSING", "shadowOnly": True}
    if canonical_pull.get("ok") is not True or canonical_pull.get("providerManifestBound") is not True:
        return {"ok": False, "status": "CANONICAL_PULL_NOT_ELIGIBLE", "shadowOnly": True}

    binding = _canonical_binding(canonical_pull)
    if binding is None:
        return {
            "ok": False,
            "status": "CANONICAL_PULL_BINDING_INVALID",
            "shadowOnly": True,
            "trainingEligible": False,
            "completenessCredit": False,
            "officialIdentityCredit": False,
        }
    slot = _slot_start(binding["canonicalPulledAtUtc"])
    key = _artifact_key(game_date, slot)
    s3 = s3_client or boto3.client("s3")
    existing = _head_existing(s3, bucket, key, expected_binding=binding)
    if existing:
        return existing
    if binding["retryReturnedExistingCanonicalPull"]:
        return {
            "ok": True,
            "status": "SKIPPED_CANONICAL_SLOT_RETRY_NO_ARTIFACT",
            "bucket": bucket,
            "key": key,
            "canonicalBindingVerified": True,
            "shadowOnly": True,
            "trainingEligible": False,
            "completenessCredit": False,
            "officialIdentityCredit": False,
        }

    retrieved_at = now().astimezone(timezone.utc).isoformat()
    try:
        provider = client_factory(timeout_seconds=4, max_attempts=1)
        response = provider.list_mlb_matches(game_date)
    except BBSClientError as exc:
        return {
            "ok": False,
            "status": "PROVIDER_UNAVAILABLE",
            "error": str(exc),
            "retrievedAtUtc": retrieved_at,
            "shadowOnly": True,
            "trainingEligible": False,
            "completenessCredit": False,
        }

    raw_rows = response.get("data") or []
    crosswalk = crosswalk_matches(raw_rows, official_games)
    official_game_utc_dates = _official_game_utc_dates(official_games)
    unqueried_official_game_utc_dates = [
        value for value in official_game_utc_dates if value != game_date
    ]
    source_payload_fingerprint = payload_fingerprint(response)
    artifact = {
        "version": VERSION,
        "activationPolicy": ACTIVATION_POLICY,
        "provider": PROVIDER,
        "endpoint": ENDPOINT.format(date=game_date),
        "gameDateEt": game_date,
        "coverageMode": COVERAGE_MODE,
        "providerDateFilterSemantics": "UTC",
        "requestedProviderDatesUtc": [game_date],
        "officialGameUtcDates": official_game_utc_dates,
        "unqueriedOfficialGameUtcDates": unqueried_official_game_utc_dates,
        "completeSlateCoverageClaimed": False,
        "canonicalBinding": binding,
        "canonicalSlotStartUtc": binding["canonicalSlotStartUtc"],
        "canonicalPulledAtUtc": binding["canonicalPulledAtUtc"],
        "canonicalPullId": binding["canonicalPullId"],
        "canonicalPullPayloadFingerprint": binding[
            "canonicalPullPayloadFingerprint"
        ],
        "canonicalManifestFingerprint": binding[
            "canonicalManifestFingerprint"
        ],
        "retrievedAtUtc": retrieved_at,
        "sourcePayloadFingerprint": source_payload_fingerprint,
        "sourceResponse": response,
        "crosswalk": crosswalk,
        "shadowOnly": True,
        "predictionAuthority": False,
        "trainingEligible": False,
        "completenessCredit": False,
        "officialIdentityCredit": False,
        "providerIdentityGateSatisfied": False,
        "reviewMilestoneDefined": False,
        "reviewBlocker": "MULTI_UTC_DATE_SLATE_CAPTURE_NOT_IMPLEMENTED",
    }
    artifact_fingerprint = payload_fingerprint(artifact)
    artifact["artifactFingerprint"] = artifact_fingerprint
    body = json.dumps(artifact, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    try:
        result = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
            Metadata={
                **_binding_metadata(binding),
                "fingerprint": artifact_fingerprint,
                "retrieved-at-utc": retrieved_at,
                "schema-version": VERSION,
                "shadow-only": "true",
            },
        )
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        status = int((exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
        if code in {"PreconditionFailed", "ConditionalRequestConflict"} or status in {409, 412}:
            existing = _head_existing(
                s3,
                bucket,
                key,
                expected_binding=binding,
            )
            if existing:
                return existing
        raise

    return {
        "ok": True,
        "status": "CAPTURED_WRITE_ONCE_ARTIFACT",
        "bucket": bucket,
        "key": key,
        "versionId": result.get("VersionId"),
        "artifactFingerprint": artifact_fingerprint,
        "sourcePayloadFingerprint": source_payload_fingerprint,
        "retrievedAtUtc": retrieved_at,
        "acceptedCrosswalkCount": 0,
        "quarantinedCrosswalkCount": crosswalk["quarantinedCount"],
        "completeOfficialCrosswalk": False,
        "coverageMode": COVERAGE_MODE,
        "requestedProviderDatesUtc": [game_date],
        "officialGameUtcDates": official_game_utc_dates,
        "unqueriedOfficialGameUtcDates": unqueried_official_game_utc_dates,
        "completeSlateCoverageClaimed": False,
        "canonicalBindingVerified": True,
        "shadowOnly": True,
        "trainingEligible": False,
        "completenessCredit": False,
        "officialIdentityCredit": False,
    }
