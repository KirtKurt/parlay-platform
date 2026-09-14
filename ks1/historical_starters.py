"""Leakage-safe pitcher context from retained pregame evidence.

This module has no provider client.  It admits only version-proven KS1 locks or
the active, checksum-bound V8 point-in-time historical context manifest.
Postgame starter identities and target-game outcomes are never inputs.
"""
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import io
import json
import re

import pyarrow.parquet as pq

from ks1.features import finite, pitcher_context, utc
from ks1.inventory import encode

V8_POINTER_PK = "MLB_V8_HISTORICAL_CONTEXT#V1"
V8_POINTER_SK = "ACTIVE"
V8_POINTER_TYPES = frozenset({
    "mlb_v8_historical_official_context_active_manifest_v2",
    "mlb_v8_historical_official_context_active_manifest_v3",
})
V8_AUTHORITY = "V8_HISTORICAL_OFFICIAL_CONTEXT_SHADOW_ONLY"
V8_MANIFEST_VERSION = "MLB-V8-HISTORICAL-BBS-MANIFEST-v1"
V8_SNAPSHOT_VERSION = "MLB-V8-HISTORICAL-BBS-FUNDAMENTALS-SNAPSHOT-v1"
V8_MANIFEST_PREFIX = "mlb/v8/historical-context/manifests/"
CONTEXT_FIELDS = {
    "starterQuality": "quality",
    "starterRecentForm": "recent_form",
    "starterVelocity": "velocity",
    "starterCommand": "command",
    "starterExpectedInnings": "expected_innings",
}
KS1_PREDICTION_PREFIX = "mlb/ks1/predictions-v1/"
KS1_STARTER_PROFILE_CONTRACT = "KS1-starter-profile-v2"


def frozen_profile_context(row):
    """Recover only checksum-bound pitcher values retained in a KS1 lock."""
    raw = row.get("starter_profile_json")
    if not raw:
        return {}
    try:
        profile = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    claimed = profile.pop("sha256", None)
    semantic_claimed = profile.get("semantic_sha256")
    semantic = {key: value for key, value in profile.items()
                if key not in ("as_of", "history_as_of", "semantic_sha256")}
    valid = (
        row.get("starter_profile_contract") == KS1_STARTER_PROFILE_CONTRACT
        and profile.get("contract") == KS1_STARTER_PROFILE_CONTRACT
        and row.get("starter_profile_sha256") == claimed
        and row.get("starter_profile_semantic_sha256") == semantic_claimed
        and hashlib.sha256(encode(semantic)).hexdigest() == semantic_claimed
        and hashlib.sha256(encode(profile)).hexdigest() == claimed
        and profile.get("as_of") == row.get("as_of")
        and (not profile.get("history_as_of")
             or utc(profile["history_as_of"]) <= utc(row["as_of"]))
    )
    if not valid:
        return {}
    result = {}
    for side in ("home", "away"):
        source = (profile.get("sides") or {}).get(side) or {}
        if str(source.get("starter_id") or "") != str(row.get(side+"_starter_id") or ""):
            return {}
        metrics, statuses = source.get("metrics") or {}, source.get("window_statuses") or {}
        current_season_complete = (profile.get("coverage") or {}).get(
            "current_season_context") is True
        safe = {}
        for name, value in metrics.items():
            if ((name.endswith("_30d") and statuses.get("30d") == "COMPLETE")
                    or (name.endswith("_last3") and statuses.get("last3") == "COMPLETE")
                    or (name.startswith("context_") and current_season_complete)
                    or (name == "expected_innings_last5" and current_season_complete)):
                safe["starter_"+name] = value
        result[side] = pitcher_context(safe)
    return result if all(result[side].get("quality") is not None for side in result) else {}


def read_locked_predictions(s3, bucket, as_of, *, target_date=None):
    """Read unchanged, versioned KS1 rows whose bytes were stored by T-10."""
    prefix = KS1_PREDICTION_PREFIX
    if target_date is not None:
        if date.fromisoformat(target_date).isoformat() != target_date:
            raise ValueError("invalid prediction date")
        prefix += "date="+target_date+"/"
    versions, deleted = defaultdict(list), set()
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
        deleted.update(value["Key"] for value in page.get("DeleteMarkers", []) if value.get("IsLatest"))
        for value in page.get("Versions", []):
            if re.fullmatch(re.escape(KS1_PREDICTION_PREFIX)+r"date=\d{4}-\d{2}-\d{2}/predictions.parquet", value["Key"]):
                versions[value["Key"]].append(value)
    admitted, excluded, sources = [], [], []
    for key, entries in sorted(versions.items()):
        if key in deleted:
            excluded.append({"key": key, "reason": "current_object_deleted"})
            continue
        candidates, current = {}, None
        for value in sorted(entries, key=lambda item: item["LastModified"]):
            body = s3.get_object(Bucket=bucket, Key=key, VersionId=value["VersionId"])["Body"].read()
            source = {"key": key, "bucket": bucket, "version_id": value["VersionId"],
                      "stored_at": value["LastModified"].isoformat(),
                      "sha256": hashlib.sha256(body).hexdigest()}
            sources.append(source)
            rows = pq.ParquetFile(io.BytesIO(body)).read().to_pylist()
            if len({row["game_id"] for row in rows}) != len(rows):
                raise ValueError("duplicate stored prediction IDs")
            if value.get("IsLatest"):
                current = {row["game_id"]: row for row in rows}
            for row in rows:
                cutoff = utc(row["commence_time"])-timedelta(minutes=10)
                if (value["VersionId"] != "null"
                        and row["date"] == key.split("date=")[1].split("/")[0]
                        and utc(row["as_of"]) <= utc(source["stored_at"]) <= cutoff < utc(as_of)):
                    candidates[row["game_id"]] = {"row": row, "evidence": source}
        for pk, entry in candidates.items():
            latest = (current or {}).get(pk, {})
            if not latest or any(latest.get(field) != field_value
                                 for field, field_value in entry["row"].items()):
                excluded.append({"game_id": pk, "reason": "changed_or_missing_frozen_row"})
                continue
            admitted.append(entry)
        for pk, row in (current or {}).items():
            if pk not in candidates and utc(row["commence_time"])-timedelta(minutes=10) < utc(as_of):
                excluded.append({"game_id": pk, "reason": "no_original_pre_cutoff_version"})
    return admitted, {"prediction_keys": len(versions), "locked_rows": len(admitted),
                      "versions_read": len(sources), "sources": sources, "excluded": excluded}


def published_starter_index(entries):
    """Index immutable pre-T10 KS1 rows returned by read_locked_predictions."""
    latest = {}
    for entry in entries or []:
        row, proof = entry.get("row", {}), entry.get("evidence", {})
        pk = str(row.get("game_id") or "")
        start, observed, stored = row.get("commence_time"), row.get("as_of"), proof.get("stored_at")
        version, digest = proof.get("version_id"), str(proof.get("sha256") or "")
        if not pk or not start or not observed or not stored or not version or version == "null":
            continue
        cutoff = utc(start)-timedelta(minutes=10)
        if not (utc(observed) <= utc(stored) <= cutoff) or len(digest) != 64:
            continue
        sides = {}
        for side in ("home", "away"):
            pid = row.get(side+"_starter_id")
            if pid not in (None, "", "None"):
                sides[side] = {"id": str(pid), "name": row.get(side+"_starter_name")}
        if not sides:
            continue
        value = {"game_id": pk, "date": row.get("date"),
                 "commence_time": start, "as_of": observed,
                 "teams": {side: str(row.get(side+"_id") or "") for side in ("home", "away")},
                 "sides": sides, "contexts": frozen_profile_context(row),
                 "source": {**proof, "source_type": "versioned_ks1_t10_prediction"}}
        previous = latest.get(pk)
        if previous is None or utc(value["as_of"]) >= utc(previous["as_of"]):
            latest[pk] = value
    return latest


def _digest(value, excluded):
    material = {str(key): item for key, item in value.items() if key != excluded}
    return hashlib.sha256(encode(material)).hexdigest()


def historical_context_index(manifest, pointer):
    """Validate and index V8's strictly point-in-time pitcher summaries."""
    if (manifest.get("version") != V8_MANIFEST_VERSION
            or manifest.get("authority") != V8_AUTHORITY
            or manifest.get("productionAuthorityChanged") is not False
            or manifest.get("selectionUsedOutcomes") is not False
            or manifest.get("manifestDigest") != _digest(manifest, "manifestDigest")):
        raise ValueError("invalid historical pitcher-context manifest")
    records = manifest.get("records", [])
    identities = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("invalid historical pitcher-context record")
        identity = (str(record.get("officialGamePk") or ""),
                    str(record.get("predictionLockAtUtc") or ""))
        if not all(identity) or identity in identities:
            raise ValueError("historical pitcher-context identity missing or duplicated")
        identities.add(identity)
    result = {}
    for record in records:
        snapshot = record.get("snapshot") or {}
        pk = str(record.get("officialGamePk") or "")
        lock = record.get("predictionLockAtUtc")
        start = record.get("commenceTime") or snapshot.get("commenceTime")
        start_derivation = "retained_commence_time"
        if (not start and lock
                and str(snapshot.get("snapshotRole") or "").endswith("_AT_T_MINUS_45")):
            # The V8 manifest deliberately retains the immutable T-45 lock but
            # its row projection omits commenceTime.  The signed role makes the
            # inverse exact; no schedule or postgame record is consulted.
            start = (utc(lock)+timedelta(minutes=45)).isoformat()
            start_derivation = "signed_t_minus_45_lock"
        if not pk or not start or not lock or utc(lock) > utc(start)-timedelta(minutes=10):
            continue
        valid = (
            record.get("trainingEligible") is True
            and snapshot.get("trainingEligible") is True
            and snapshot.get("version") == V8_SNAPSHOT_VERSION
            and snapshot.get("authority") == V8_AUTHORITY
            and str(snapshot.get("officialGamePk") or "") == pk
            and snapshot.get("predictionLockAtUtc") == lock
            and snapshot.get("pointInTimeVerified") is True
            and snapshot.get("postgameFieldsExcluded") is True
            and snapshot.get("sameDayResultsExcluded") is True
            and snapshot.get("targetGameOutcomeUsed") is False
            and snapshot.get("selectionUsedOutcomes") is False
            and snapshot.get("productionAuthorityChanged") is False
            and snapshot.get("fingerprint") == _digest(snapshot, "fingerprint")
        )
        if not valid:
            continue
        mode = (snapshot.get("featureAvailabilityMode") or {}).get("pitchers")
        if mode not in ("confirmed_archive", "strict_prior_projection"):
            continue
        sides = {}
        for side in ("home", "away"):
            raw = snapshot.get(side) or {}
            sides[side] = {target: finite(raw.get(source)) for source, target in CONTEXT_FIELDS.items()}
        if not all(any(value is not None for value in sides[side].values()) for side in sides):
            continue
        if pk in result:
            raise ValueError("multiple historical pitcher-context locks for game")
        result[pk] = {"game_id": pk, "commence_time": start, "as_of": lock,
                      "teams": {side: record.get(side+"Team") for side in ("home", "away")},
                      "identity_mode": mode, "sides": sides,
                      "source": {**pointer, "source_type": "v8_point_in_time_pitcher_context",
                                 "commence_time_derivation": start_derivation}}
    return result


def load_active_historical_context(s3, table):
    """Read the exact active V8 pointer and fail atomically on integrity errors."""
    item = table.get_item(Key={"PK": V8_POINTER_PK, "SK": V8_POINTER_SK},
                          ConsistentRead=True).get("Item")
    if not item or item.get("record_type") not in V8_POINTER_TYPES:
        raise ValueError("historical pitcher-context pointer missing or unsupported")
    data = json.loads(json.dumps(item.get("data") or {}, default=str))
    if data.get("authority") != V8_AUTHORITY or not str(data.get("provider") or "").startswith("official_mlb"):
        raise ValueError("historical pitcher-context pointer authority mismatch")
    pointer = data.get("manifest") or {}
    bucket, key, expected = pointer.get("bucket"), pointer.get("key"), pointer.get("sha256")
    version = pointer.get("versionId")
    if (not bucket or not str(key).startswith(V8_MANIFEST_PREFIX)
            or len(str(expected or "")) != 64 or not version or version == "null"):
        raise ValueError("historical pitcher-context pointer incomplete")
    request = {"Bucket": bucket, "Key": key, "VersionId": version}
    response = s3.get_object(**request)
    body = response["Body"].read()
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected or response.get("VersionId") != version:
        raise ValueError("historical pitcher-context checksum mismatch")
    manifest = json.loads(body)
    proof = {"bucket": bucket, "key": key, "version_id": response.get("VersionId") or pointer.get("versionId"),
             "sha256": actual, "pointer_revision": int(item.get("revision") or 0)}
    indexed = historical_context_index(manifest, proof)
    return indexed, {"status": "read", "rows": len(indexed), "source": V8_MANIFEST_PREFIX,
                     "pointer_revision": proof["pointer_revision"]}, proof
