import hashlib
import io
import json

import pytest

from ks1.historical_starters import (
    V8_AUTHORITY, historical_context_index, load_active_historical_context,
    published_starter_index,
)
from ks1.inventory import encode


def _signed(value, field):
    material = {key: item for key, item in value.items() if key != field}
    value[field] = hashlib.sha256(encode(material)).hexdigest()
    return value


def context_manifest():
    snapshot = _signed({
        "authority": V8_AUTHORITY, "officialGamePk": "3",
        "snapshotRole": "HISTORICAL_POINT_IN_TIME_RECONSTRUCTION_AT_T_MINUS_45",
        "predictionLockAtUtc": "2026-08-03T19:15:00Z",
        "trainingEligible": True, "pointInTimeVerified": True,
        "postgameFieldsExcluded": True, "sameDayResultsExcluded": True,
        "targetGameOutcomeUsed": False, "selectionUsedOutcomes": False,
        "productionAuthorityChanged": False,
        "featureAvailabilityMode": {"pitchers": "strict_prior_projection"},
        "home": {"starterQuality": -3.2, "starterCommand": 19.0,
                 "starterExpectedInnings": 5.8},
        "away": {"starterQuality": -4.1, "starterRecentForm": -3.8,
                 "starterExpectedInnings": 5.1},
    }, "fingerprint")
    return _signed({
        "authority": V8_AUTHORITY, "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False, "eligibleGameCount": 1,
        "records": [{"officialGamePk": "3", "commenceTime": "2026-08-03T20:00:00Z",
                     "predictionLockAtUtc": "2026-08-03T19:15:00Z",
                     "homeTeam": "Home", "awayTeam": "Away",
                     "trainingEligible": True, "snapshot": snapshot}],
    }, "manifestDigest")


def test_published_index_requires_versioned_storage_before_t10():
    entry = {"row": {"game_id": "3", "commence_time": "2026-08-03T20:00:00Z",
                     "as_of": "2026-08-03T19:40:00Z", "home_id": "1", "away_id": "2",
                     "home_starter_id": "99", "home_starter_name": "Observed"},
             "evidence": {"bucket": "b", "key": "k", "version_id": "v1",
                          "stored_at": "2026-08-03T19:45:00Z", "sha256": "a"*64}}
    assert published_starter_index([entry])["3"]["sides"]["home"]["id"] == "99"
    entry["evidence"]["stored_at"] = "2026-08-03T19:50:01Z"
    assert published_starter_index([entry]) == {}


def test_historical_context_validates_and_retains_projection_mode():
    result = historical_context_index(context_manifest(), {"bucket": "b", "key": "k"})
    assert result["3"]["identity_mode"] == "strict_prior_projection"
    assert result["3"]["sides"]["home"]["quality"] == -3.2
    assert result["3"]["sides"]["home"]["velocity"] is None
    broken = context_manifest()
    broken["records"][0]["snapshot"]["home"]["starterQuality"] = 999
    broken = _signed(broken, "manifestDigest")
    assert historical_context_index(broken, {}) == {}


def test_signed_t_minus_45_role_exactly_recovers_omitted_commence_time():
    value = context_manifest()
    del value["records"][0]["commenceTime"]
    value = _signed(value, "manifestDigest")
    row = historical_context_index(value, {})["3"]
    assert row["commence_time"] == "2026-08-03T20:00:00+00:00"
    assert row["source"]["commence_time_derivation"] == "signed_t_minus_45_lock"

    value = context_manifest()
    del value["records"][0]["commenceTime"]
    value["records"][0]["snapshot"]["snapshotRole"] = "UNKNOWN"
    value["records"][0]["snapshot"] = _signed(
        value["records"][0]["snapshot"], "fingerprint")
    value = _signed(value, "manifestDigest")
    assert historical_context_index(value, {}) == {}


class Table:
    def __init__(self, item):
        self.item = item

    def get_item(self, **_kwargs):
        return {"Item": self.item}


class S3:
    def __init__(self, body):
        self.body = body

    def get_object(self, **kwargs):
        assert kwargs["VersionId"] == "manifest-version"
        return {"Body": io.BytesIO(self.body), "VersionId": kwargs["VersionId"]}


def test_active_manifest_read_is_version_and_checksum_bound():
    body = json.dumps(context_manifest()).encode()
    pointer = {"bucket": "archive", "key": "mlb/v8/historical-context/manifests/a.json",
               "versionId": "manifest-version", "sha256": hashlib.sha256(body).hexdigest()}
    item = {"record_type": "mlb_v8_historical_official_context_active_manifest_v3",
            "revision": 7, "data": {"authority": V8_AUTHORITY,
                                     "provider": "official_mlb_plus_internal_canonical_context",
                                     "manifest": pointer}}
    rows, status, proof = load_active_historical_context(S3(body), Table(item))
    assert len(rows) == status["rows"] == 1
    assert proof["version_id"] == "manifest-version"
    item["data"]["manifest"]["sha256"] = "0"*64
    with pytest.raises(ValueError, match="checksum"):
        load_active_historical_context(S3(body), Table(item))
