#!/usr/bin/env python3
"""Incrementally enrich V8 historical games with point-in-time BigBallsData inputs.

Only pregame values whose provider effective timestamp is at or before each game's
immutable T-45 lock can become training eligible. Selection never uses outcomes.
The job writes an immutable manifest and a separate shadow-only DynamoDB pointer;
it cannot alter production predictions, champions, cutovers, or wagering authority.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import boto3
from botocore.exceptions import ClientError

from bigballsdata_client import BBSClientError, BigBallsDataClient
import mlb_v8_fundamentals_collector as fundamentals
import mlb_v8_historical_bbs_overlay_v1 as overlay

VERSION = "MLB-V8-HISTORICAL-BBS-BACKFILL-v1-point-in-time"
REPORT_TYPE = "MLB_V8_HISTORICAL_BBS_BACKFILL"
STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
STATE_SK = "STATE"
DEFAULT_TABLE = "parlay_platform_snapshots"
DEFAULT_HISTORICAL_STACK = "parlay-platform-mlb-historical-optimizer"
DEFAULT_FUNDAMENTALS_STACK = "parlay-platform-mlb-v8-fundamentals-shadow"
REQUIRED_RESOURCES = fundamentals.REQUIRED_DOMAINS
OPTIONAL_RESOURCES = ("weather", "park")
EFFECTIVE_TIME_KEYS = (
    "asOfUtc", "asOf", "sourceEffectiveAtUtc", "effectiveAtUtc", "effectiveAt",
    "snapshotAtUtc", "snapshotAt", "dataAsOfUtc", "dataAsOf", "updatedAt",
    "lastUpdated", "generatedAt", "timestamp",
)
DIRECT_GAME_ID_KEYS = ("official_game_pk", "officialGamePk", "mlb_game_pk", "mlbGamePk", "gamePk")

TEAM_ALIASES = {
    "oakland athletics": "athletics",
    "oakland a's": "athletics",
    "a's": "athletics",
    "az diamondbacks": "arizona diamondbacks",
    "la angels": "los angeles angels",
    "la dodgers": "los angeles dodgers",
    "ny mets": "new york mets",
    "ny yankees": "new york yankees",
    "sd padres": "san diego padres",
    "sf giants": "san francisco giants",
    "tb rays": "tampa bay rays",
}


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _team(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("display_name") or value.get("team_name") or value.get("name")
    text = " ".join(str(value or "").strip().lower().split())
    return TEAM_ALIASES.get(text, text)


def _provider_id(row: Mapping[str, Any]) -> str:
    return str(
        row.get("match_id") or row.get("matchId") or row.get("id") or row.get("eventId") or ""
    ).strip()


def _provider_start(row: Mapping[str, Any]) -> Optional[datetime]:
    return _parse_time(
        row.get("kickoff_utc") or row.get("kickoffUtc") or row.get("startTime")
        or row.get("commenceTime") or row.get("scheduledAt")
    )


def _provider_side(row: Mapping[str, Any], side: str) -> str:
    return _team(row.get(side) or row.get(f"{side}_team") or row.get(f"{side}Team"))


def _direct_official_game_pk(row: Mapping[str, Any]) -> str:
    for key in DIRECT_GAME_ID_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    external = row.get("external_ids") or row.get("externalIds")
    if isinstance(external, Mapping):
        for key in ("mlb", "mlb_game_pk", "mlbGamePk", "gamePk"):
            if external.get(key) not in (None, ""):
                return str(external[key]).strip()
    return ""


def crosswalk_provider_rows(
    provider_rows: Sequence[Mapping[str, Any]],
    canonical_games: Sequence[Mapping[str, Any]],
    *,
    tolerance_minutes: int = 5,
) -> Dict[str, Any]:
    """Create a one-to-one, outcome-free BBS-to-MLB identity crosswalk."""
    canonical = {str(row.get("officialGamePk") or ""): row for row in canonical_games}
    accepted: Dict[str, Dict[str, Any]] = {}
    quarantined: List[Dict[str, Any]] = []
    used_provider_ids: set[str] = set()
    used_game_pks: set[str] = set()
    tolerance = timedelta(minutes=max(0, int(tolerance_minutes)))

    for index, raw in enumerate(provider_rows):
        row = dict(raw) if isinstance(raw, Mapping) else {}
        provider_id = _provider_id(row)
        start = _provider_start(row)
        home = _provider_side(row, "home")
        away = _provider_side(row, "away")
        reasons: List[str] = []
        if not provider_id:
            reasons.append("provider_match_id_missing")
        if provider_id and provider_id in used_provider_ids:
            reasons.append("provider_match_id_duplicate")
        if start is None:
            reasons.append("provider_kickoff_invalid")
        if not home or not away:
            reasons.append("provider_team_identity_missing")

        direct = _direct_official_game_pk(row)
        candidates: List[str] = []
        method = None
        if direct:
            target = canonical.get(direct)
            if target is None:
                reasons.append("provider_direct_official_game_not_in_corpus")
            elif _team(target.get("homeTeam")) != home or _team(target.get("awayTeam")) != away:
                reasons.append("provider_direct_official_game_team_mismatch")
            elif start is not None and _parse_time(target.get("commenceTime")) is not None and abs(_parse_time(target.get("commenceTime")) - start) > tolerance:
                reasons.append("provider_direct_official_game_start_time_mismatch")
            else:
                candidates = [direct]
                method = "DIRECT_PROVIDER_OFFICIAL_GAME_ID"
        elif start is not None and home and away:
            for game_pk, target in canonical.items():
                target_start = _parse_time(target.get("commenceTime"))
                if target_start is None:
                    continue
                if _team(target.get("homeTeam")) != home or _team(target.get("awayTeam")) != away:
                    continue
                if abs(target_start - start) <= tolerance:
                    candidates.append(game_pk)
            method = "UNIQUE_EXACT_TEAM_AND_START_TIME"

        if len(candidates) != 1:
            reasons.append("provider_official_game_crosswalk_not_unique")
        elif candidates[0] in used_game_pks:
            reasons.append("canonical_game_already_crosswalked")

        if reasons:
            quarantined.append({
                "providerIndex": index,
                "providerMatchId": provider_id or None,
                "providerKickoffUtc": start.isoformat() if start else None,
                "candidateOfficialGamePks": sorted(candidates),
                "reasons": sorted(set(reasons)),
                "providerRowFingerprint": _sha(row),
            })
            continue

        game_pk = candidates[0]
        used_provider_ids.add(provider_id)
        used_game_pks.add(game_pk)
        accepted[game_pk] = {
            "providerMatchId": provider_id,
            "providerKickoffUtc": start.isoformat(),
            "crosswalkMethod": method,
            "providerRowFingerprint": _sha(row),
            "providerRow": copy.deepcopy(row),
        }

    return {
        "accepted": accepted,
        "acceptedCount": len(accepted),
        "quarantined": quarantined,
        "quarantinedCount": len(quarantined),
        "completeCanonicalCoverage": len(accepted) == len(canonical_games),
        "selectionUsedOutcomes": False,
    }


def _meta(envelope: Any) -> Mapping[str, Any]:
    return envelope.get("meta") if isinstance(envelope, Mapping) and isinstance(envelope.get("meta"), Mapping) else {}


def _effective_at(envelope: Any) -> Optional[datetime]:
    meta = _meta(envelope)
    for key in EFFECTIVE_TIME_KEYS:
        parsed = _parse_time(meta.get(key))
        if parsed is not None:
            return parsed
    return None


def point_in_time_errors(resources: Mapping[str, Any], lock_at: Any) -> List[str]:
    lock = _parse_time(lock_at)
    if lock is None:
        return ["prediction_lock_invalid"]
    errors: List[str] = []
    for name in REQUIRED_RESOURCES:
        envelope = resources.get(name)
        if not isinstance(envelope, Mapping) or envelope.get("error") is not None:
            errors.append(f"{name}_resource_unavailable")
            continue
        effective = _effective_at(envelope)
        if effective is None:
            errors.append(f"{name}_source_effective_time_missing")
        elif effective > lock + timedelta(seconds=1):
            errors.append(f"{name}_source_effective_time_after_lock")
    return sorted(set(errors))


def _f(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _mapping_number(value: Any, names: Iterable[str]) -> Optional[float]:
    if isinstance(value, (int, float, Decimal)):
        return _f(value)
    if isinstance(value, Mapping):
        for name in names:
            parsed = _f(value.get(name))
            if parsed is not None:
                return parsed
        for child in value.values():
            parsed = _mapping_number(child, names)
            if parsed is not None:
                return parsed
    if isinstance(value, list):
        for child in value:
            parsed = _mapping_number(child, names)
            if parsed is not None:
                return parsed
    return None


def _quality(primary: Any, *fallbacks: Any) -> Optional[float]:
    for value in (primary,) + fallbacks:
        parsed = _f(value)
        if parsed is not None:
            return -parsed
    return None


def _recent_quality(value: Any) -> Optional[float]:
    positive = _mapping_number(value, ("kMinusBbPct", "k_bb_pct", "wrcPlus", "rating", "qualityScore"))
    if positive is not None:
        return positive
    negative = _mapping_number(value, ("xera", "xERA", "fip", "FIP", "era", "ERA", "whip", "WHIP"))
    return -negative if negative is not None else None


def _lineup_quality(lineup: Mapping[str, Any]) -> Optional[float]:
    players = [row for row in lineup.get("players") or [] if isinstance(row, Mapping)]
    wrc = [_f(row.get("wrcPlus")) for row in players]
    wrc = [value for value in wrc if value is not None]
    if wrc:
        return sum(wrc) / len(wrc)
    ops = [_f(row.get("ops")) for row in players]
    ops = [value for value in ops if value is not None]
    return (sum(ops) / len(ops)) * 100.0 if ops else None


def _injury_impact(injuries: Mapping[str, Any]) -> float:
    total = 0.0
    for row in injuries.get("players") or []:
        if not isinstance(row, Mapping):
            total += 1.0
            continue
        impact = _mapping_number(row, ("impact", "impactScore", "war", "estimatedWar", "severityScore"))
        total += impact if impact is not None else 1.0
    return total


def _travel_rest(team: Mapping[str, Any]) -> Optional[float]:
    rest = _f(team.get("restDays"))
    travel = _mapping_number(team.get("travel"), ("miles", "distance", "travelMiles", "travelDistance"))
    if rest is None and travel is None:
        return None
    return (rest or 0.0) - (travel or 0.0) / 1000.0


def _side_features(game: Mapping[str, Any], side: str) -> Dict[str, Any]:
    pitcher = dict((game.get("pitchers") or {}).get(side) or {})
    bullpen = dict((game.get("bullpens") or {}).get(side) or {})
    lineup = dict((game.get("lineups") or {}).get(side) or {})
    injuries = dict((game.get("injuries") or {}).get(side) or {})
    team = dict((game.get("teamContext") or {}).get(side) or {})
    bullpen_quality = _f(bullpen.get("qualityScore"))
    if bullpen_quality is None:
        bullpen_quality = _quality(bullpen.get("fip"), bullpen.get("era"))
    bullpen_freshness = _f(bullpen.get("freshnessScore"))
    if bullpen_freshness is None:
        innings = _f(bullpen.get("last3DaysInnings")) or 0.0
        pitches = _f(bullpen.get("last2DaysPitches")) or 0.0
        bullpen_freshness = -(innings + pitches / 50.0)
    return {
        "starterQuality": _quality(pitcher.get("xera"), pitcher.get("fip"), pitcher.get("era")),
        "starterRecentForm": _recent_quality(pitcher.get("recentThreeStarts")),
        "starterVelocity": _f(pitcher.get("velocity")),
        "starterCommand": _f(pitcher.get("kMinusBbPct")),
        "starterExpectedInnings": _f(pitcher.get("expectedInnings")),
        "bullpenQuality": bullpen_quality,
        "bullpenFreshness": bullpen_freshness,
        "lineupQuality": _lineup_quality(lineup),
        "lineupAbsenceImpact": _injury_impact(injuries),
        "platoonMatchup": _mapping_number(team.get("handednessSplits"), ("wrcPlus", "wRC+", "ops", "rating")),
        "defenseRating": _mapping_number(team.get("defense"), ("rating", "defenseRating", "drs", "defensiveRunsSaved", "outsAboveAverage")),
        "travelRestRating": _travel_rest(team),
    }


def _run_factor(value: Any) -> Optional[float]:
    return _mapping_number(value, ("runFactor", "runsFactor", "parkFactorRuns", "weatherRunFactor"))


def build_training_snapshot(
    canonical: Mapping[str, Any],
    provider: Mapping[str, Any],
    normalized_game: Mapping[str, Any],
    resources: Mapping[str, Any],
    *,
    retrieved_at: datetime,
) -> Dict[str, Any]:
    errors = point_in_time_errors(resources, canonical.get("predictionLockAtUtc"))
    coverage = normalized_game.get("coverage") or {}
    if coverage.get("trainingEligible") is not True:
        errors.extend(str(value) for value in coverage.get("missingDomains") or [])
        if coverage.get("confirmedLineups") is not True:
            errors.append("confirmed_lineups_missing")
        if coverage.get("confirmedStarters") is not True:
            errors.append("confirmed_starters_missing")
    errors = sorted(set(errors))
    snapshot: Dict[str, Any] = {
        "version": overlay.SNAPSHOT_VERSION,
        "authority": overlay.AUTHORITY,
        "snapshotRole": "HISTORICAL_POINT_IN_TIME_RECONSTRUCTION_AT_T_MINUS_45",
        "createdAtUtc": retrieved_at.astimezone(timezone.utc).isoformat(),
        "officialGamePk": str(canonical.get("officialGamePk") or ""),
        "providerMatchId": provider.get("providerMatchId"),
        "predictionLockAtUtc": canonical.get("predictionLockAtUtc"),
        "slateDateEt": canonical.get("slateDateEt"),
        "homeTeam": canonical.get("homeTeam"),
        "awayTeam": canonical.get("awayTeam"),
        "home": _side_features(normalized_game, "home"),
        "away": _side_features(normalized_game, "away"),
        "parkRunFactor": _run_factor(normalized_game.get("park")),
        "weatherRunFactor": _run_factor(normalized_game.get("weather")),
        "providerEvidence": {
            name: {
                "source": "bigballsdata",
                "endpoint": f"/v1/matches/{{match_id}}/{name}",
                "sourceEffectiveAtUtc": _effective_at(resources.get(name)).isoformat()
                if _effective_at(resources.get(name)) else None,
                "payloadFingerprint": _sha(resources.get(name)),
            }
            for name in (*REQUIRED_RESOURCES, *OPTIONAL_RESOURCES)
        },
        "crosswalkMethod": provider.get("crosswalkMethod"),
        "pointInTimeVerified": not point_in_time_errors(resources, canonical.get("predictionLockAtUtc")),
        "postgameFieldsExcluded": True,
        "selectionUsedOutcomes": False,
        "trainingEligible": not errors,
        "eligibilityErrors": errors,
        "productionAuthorityChanged": False,
    }
    snapshot["fingerprint"] = overlay.snapshot_fingerprint(snapshot)
    return snapshot


def _outputs(cf: Any, stack_name: str) -> Dict[str, str]:
    stack = (cf.describe_stacks(StackName=stack_name).get("Stacks") or [])[0]
    return {
        str(row.get("OutputKey")): str(row.get("OutputValue"))
        for row in stack.get("Outputs") or []
        if row.get("OutputKey") and row.get("OutputValue")
    }


def _load_canonical_games(state: Mapping[str, Any], s3: Any) -> List[Dict[str, Any]]:
    games: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for slate in state.get("completedSlates") or []:
        if not isinstance(slate, Mapping):
            continue
        pointer = slate.get("artifact") or {}
        bucket = str(pointer.get("bucket") or "")
        key = str(pointer.get("key") or "")
        expected_sha = str(pointer.get("sha256") or "")
        if not bucket or not key or not expected_sha:
            raise RuntimeError("completed_slate_artifact_pointer_incomplete")
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if hashlib.sha256(body).hexdigest() != expected_sha:
            raise RuntimeError(f"completed_slate_checksum_mismatch:{key}")
        dataset = json.loads(body.decode("utf-8"))
        if dataset.get("completeSlate") is not True or dataset.get("postLockDataExcluded") is not True:
            raise RuntimeError(f"completed_slate_integrity_invalid:{key}")
        for raw in dataset.get("records") or []:
            if not isinstance(raw, Mapping):
                continue
            game_pk = str(raw.get("officialGamePk") or "").strip()
            if not game_pk or game_pk in seen:
                raise RuntimeError(f"canonical_game_identity_missing_or_duplicate:{game_pk}")
            seen.add(game_pk)
            games.append({
                "slateDateEt": str(raw.get("slateDateEt") or dataset.get("slateDateEt") or ""),
                "officialGamePk": game_pk,
                "homeTeam": raw.get("homeTeam"),
                "awayTeam": raw.get("awayTeam"),
                "commenceTime": raw.get("commenceTime"),
                "predictionLockAtUtc": raw.get("predictionLockAtUtc"),
                "sourceDataset": {"bucket": bucket, "key": key, "sha256": expected_sha},
            })
    return sorted(games, key=lambda row: (row["slateDateEt"], row["predictionLockAtUtc"], row["officialGamePk"]))


def _load_previous_manifest(table: Any, s3: Any) -> Tuple[Optional[Dict[str, Any]], int]:
    item = table.get_item(Key={"PK": overlay.POINTER_PK, "SK": overlay.POINTER_SK}, ConsistentRead=True).get("Item")
    if not item:
        return None, 0
    data = _plain(item.get("data") or {})
    pointer = data.get("manifest") or {}
    body = s3.get_object(Bucket=pointer["bucket"], Key=pointer["key"])["Body"].read()
    if hashlib.sha256(body).hexdigest() != str(pointer.get("sha256") or ""):
        raise RuntimeError("previous_historical_bbs_manifest_checksum_mismatch")
    manifest = json.loads(body.decode("utf-8"))
    if manifest.get("manifestDigest") != overlay.manifest_digest(manifest):
        raise RuntimeError("previous_historical_bbs_manifest_digest_mismatch")
    return manifest, int(item.get("revision") or 0)


def _put_immutable(s3: Any, bucket: str, key: str, body: bytes) -> Dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    try:
        response = s3.put_object(
            Bucket=bucket, Key=key, Body=body, ContentType="application/json",
            ServerSideEncryption="AES256", IfNoneMatch="*",
            Metadata={"sha256": digest, "record-type": "mlb-v8-historical-bbs-manifest"},
        )
        return {"bucket": bucket, "key": key, "sha256": digest, "versionId": response.get("VersionId"), "alreadyExisted": False}
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        status = int((exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
        if code not in {"PreconditionFailed", "ConditionalRequestConflict"} and status not in {409, 412}:
            raise
        head = s3.head_object(Bucket=bucket, Key=key)
        existing = str((head.get("Metadata") or {}).get("sha256") or "")
        if existing and existing != digest:
            raise RuntimeError("historical_bbs_immutable_manifest_collision")
        return {"bucket": bucket, "key": key, "sha256": digest, "versionId": head.get("VersionId"), "alreadyExisted": True}


def _activate(table: Any, pointer: Mapping[str, Any], manifest: Mapping[str, Any], previous_revision: int) -> int:
    revision = previous_revision + 1
    item = {
        "PK": overlay.POINTER_PK,
        "SK": overlay.POINTER_SK,
        "record_type": "mlb_v8_historical_bbs_active_manifest_v1",
        "revision": revision,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "version": overlay.VERSION,
            "authority": overlay.AUTHORITY,
            "manifest": dict(pointer),
            "manifestDigest": manifest.get("manifestDigest"),
            "processedGameCount": manifest.get("processedGameCount"),
            "eligibleGameCount": manifest.get("eligibleGameCount"),
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


def _canonical_match(provider_row: Mapping[str, Any], game_date: str) -> Dict[str, Any]:
    return {
        "id": _provider_id(provider_row),
        "date": game_date,
        "startTime": (_provider_start(provider_row) or datetime.min.replace(tzinfo=timezone.utc)).isoformat(),
        "home": {"name": _provider_side(provider_row, "home")},
        "away": {"name": _provider_side(provider_row, "away")},
    }


def run(
    *,
    region: str,
    table_name: str,
    historical_stack: str,
    fundamentals_stack: str,
    limit: int,
    start_tolerance_minutes: int,
    output: Path,
    client_factory: Any = BigBallsDataClient,
) -> Dict[str, Any]:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    created = datetime.now(timezone.utc)
    cf = boto3.client("cloudformation", region_name=region)
    ddb = boto3.resource("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    table = ddb.Table(table_name)
    historical_outputs = _outputs(cf, historical_stack)
    fundamentals_outputs = _outputs(cf, fundamentals_stack)
    bucket = str(fundamentals_outputs.get("FundamentalsArtifactsBucketName") or "")
    if not historical_outputs.get("HistoricalOptimizerFunctionName") or not bucket:
        raise RuntimeError("required historical or fundamentals stack output is missing")
    item = table.get_item(Key={"PK": STATE_PK, "SK": STATE_SK}, ConsistentRead=True).get("Item")
    if not item:
        raise RuntimeError("historical optimizer state is missing")
    state = _plain(item.get("data") or {})
    games = _load_canonical_games(state, s3)
    previous, previous_revision = _load_previous_manifest(table, s3)
    current_identity = {
        (row["officialGamePk"], row["predictionLockAtUtc"]): row for row in games
    }
    previous_records = []
    for row in (previous or {}).get("records") or []:
        if not isinstance(row, Mapping):
            continue
        identity = (str(row.get("officialGamePk") or ""), str(row.get("predictionLockAtUtc") or ""))
        if identity in current_identity:
            previous_records.append(copy.deepcopy(dict(row)))
    processed = {str(row.get("officialGamePk") or "") for row in previous_records}
    pending = [row for row in games if row["officialGamePk"] not in processed]
    selected = pending[:limit]
    selected_by_date: Dict[str, List[Dict[str, Any]]] = {}
    all_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in games:
        all_by_date.setdefault(row["slateDateEt"], []).append(row)
    for row in selected:
        selected_by_date.setdefault(row["slateDateEt"], []).append(row)

    client = client_factory()
    new_records: List[Dict[str, Any]] = []
    crosswalk_quarantine: List[Dict[str, Any]] = []
    provider_calls = 0
    for day in sorted(selected_by_date):
        matches_envelope = client.list_mlb_matches(day, limit=200)
        provider_calls += 1
        provider_rows = [row for row in matches_envelope.get("data") or [] if isinstance(row, Mapping)]
        crosswalk = crosswalk_provider_rows(
            provider_rows, all_by_date.get(day) or [], tolerance_minutes=start_tolerance_minutes
        )
        crosswalk_quarantine.extend(crosswalk["quarantined"])
        for canonical in selected_by_date[day]:
            game_pk = canonical["officialGamePk"]
            provider = crosswalk["accepted"].get(game_pk)
            if provider is None:
                new_records.append({
                    **{key: canonical.get(key) for key in ("slateDateEt", "officialGamePk", "predictionLockAtUtc", "homeTeam", "awayTeam")},
                    "providerMatchId": None,
                    "trainingEligible": False,
                    "eligibilityErrors": ["provider_official_game_crosswalk_unavailable"],
                    "snapshot": None,
                })
                continue
            resources: Dict[str, Any] = {}
            for name in (*REQUIRED_RESOURCES, *OPTIONAL_RESOURCES):
                try:
                    resources[name] = client.get_mlb_match_resource(
                        provider["providerMatchId"], name, game_date=day,
                        as_of=str(canonical.get("predictionLockAtUtc") or ""),
                    )
                except BBSClientError as exc:
                    resources[name] = {"data": None, "meta": {"source": "bigballsdata"}, "error": str(exc)}
                provider_calls += 1
            normalized = fundamentals.normalize_match(
                _canonical_match(provider["providerRow"], day), created, resources
            )
            snapshot = build_training_snapshot(
                canonical, provider, normalized, resources, retrieved_at=created
            )
            new_records.append({
                **{key: canonical.get(key) for key in ("slateDateEt", "officialGamePk", "predictionLockAtUtc", "homeTeam", "awayTeam")},
                "providerMatchId": provider.get("providerMatchId"),
                "crosswalkMethod": provider.get("crosswalkMethod"),
                "trainingEligible": snapshot["trainingEligible"],
                "eligibilityErrors": snapshot["eligibilityErrors"],
                "snapshot": snapshot if snapshot["trainingEligible"] else None,
            })

    records = sorted(
        previous_records + new_records,
        key=lambda row: (str(row.get("slateDateEt") or ""), str(row.get("predictionLockAtUtc") or ""), str(row.get("officialGamePk") or "")),
    )
    eligible = sum(row.get("trainingEligible") is True for row in records)
    corpus_material = [
        {key: row.get(key) for key in ("slateDateEt", "officialGamePk", "predictionLockAtUtc", "homeTeam", "awayTeam", "sourceDataset")}
        for row in games
    ]
    manifest: Dict[str, Any] = {
        "version": overlay.MANIFEST_VERSION,
        "backfillVersion": VERSION,
        "authority": overlay.AUTHORITY,
        "createdAtUtc": created.isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "sourceHistoricalStateRevision": state.get("revision"),
        "sourceFeatureDatasetVersion": state.get("featureDatasetVersion"),
        "sourceCorpusFingerprint": _sha(corpus_material),
        "selectionRule": "chronological unprocessed canonical games only",
        "selectionUsedOutcomes": False,
        "pointInTimeRequired": True,
        "processedGameCount": len(records),
        "eligibleGameCount": eligible,
        "ineligibleGameCount": len(records) - eligible,
        "totalCanonicalGameCount": len(games),
        "remainingGameCount": max(0, len(games) - len(records)),
        "trainingCoverage": round(eligible / len(games), 8) if games else 0.0,
        "productionAuthorityChanged": False,
        "records": records,
    }
    manifest["manifestDigest"] = overlay.manifest_digest(manifest)
    body = _json_bytes(manifest, pretty=True)
    key = f"mlb/v8/historical-bbs/manifests/{manifest['manifestDigest']}.json"
    pointer = _put_immutable(s3, bucket, key, body)
    blockers: List[str] = []
    activated_revision = previous_revision
    if eligible > 0:
        activated_revision = _activate(table, pointer, manifest, previous_revision)
    else:
        blockers.append("no_training_eligible_point_in_time_bbs_rows")
    if new_records and not any(row.get("trainingEligible") is True for row in new_records):
        blockers.append("current_batch_added_zero_training_eligible_rows")

    report = {
        "proofType": REPORT_TYPE,
        "version": VERSION,
        "createdAtUtc": created.isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "authority": overlay.AUTHORITY,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "selectionUsedOutcomes": False,
        "pointInTimeRequired": True,
        "provider": "bigballsdata",
        "providerCallsMade": provider_calls,
        "selectedGameCount": len(selected),
        "newRecordCount": len(new_records),
        "newEligibleGameCount": sum(row.get("trainingEligible") is True for row in new_records),
        "processedGameCount": len(records),
        "eligibleGameCount": eligible,
        "ineligibleGameCount": len(records) - eligible,
        "remainingGameCount": manifest["remainingGameCount"],
        "trainingCoverage": manifest["trainingCoverage"],
        "manifest": pointer,
        "manifestDigest": manifest["manifestDigest"],
        "activePointerRevision": activated_revision,
        "crosswalkQuarantineCount": len(crosswalk_quarantine),
        "crosswalkQuarantine": crosswalk_quarantine[:100],
        "blockers": sorted(set(blockers)),
        "ok": not blockers,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--table-name", default=DEFAULT_TABLE)
    parser.add_argument("--historical-stack", default=DEFAULT_HISTORICAL_STACK)
    parser.add_argument("--fundamentals-stack", default=DEFAULT_FUNDAMENTALS_STACK)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--start-tolerance-minutes", type=int, default=5)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = run(
            region=args.region,
            table_name=args.table_name,
            historical_stack=args.historical_stack,
            fundamentals_stack=args.fundamentals_stack,
            limit=args.limit,
            start_tolerance_minutes=args.start_tolerance_minutes,
            output=Path(args.output),
        )
    except Exception as exc:
        report = {
            "proofType": REPORT_TYPE,
            "version": VERSION,
            "createdAtUtc": datetime.now(timezone.utc).isoformat(),
            "sourceSha": os.environ.get("GITHUB_SHA"),
            "runId": os.environ.get("GITHUB_RUN_ID"),
            "authority": overlay.AUTHORITY,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
            "selectionUsedOutcomes": False,
            "pointInTimeRequired": True,
            "blockers": [f"{type(exc).__name__}:{str(exc)[:500]}"],
            "ok": False,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
