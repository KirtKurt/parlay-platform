from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from hello_world import bigballsdata_client
from hello_world import mlb_bbs_context_v1 as bbs_context


def _official(game_pk, start, *, game_number=1):
    return {
        "official_game_pk": game_pk,
        "official_game_number": game_number,
        "home_team": "New York Yankees",
        "away_team": "Boston Red Sox",
        "official_commence_time": start,
        "commence_time": start,
    }


def _provider(start, *, provider_id="bb_match_abcdefghij"):
    return {
        "match_id": provider_id,
        "kickoff_utc": start,
        "sport": "baseball",
        "home": {"display_name": "New York Yankees"},
        "away": {"display_name": "Boston Red Sox"},
        "status": "scheduled",
    }


def test_documented_rows_receive_no_invented_official_identity_credit():
    official = [_official(777001, "2026-07-22T23:05:00Z")]
    result = bbs_context.crosswalk_matches(
        [_provider("2026-07-22T23:05:00Z")], official
    )

    assert result["accepted"] == []
    assert result["acceptedCount"] == 0
    assert result["completeOfficialCrosswalk"] is False
    assert result["officialIdentityCredit"] is False
    assert result["providerOfficialGameIdentityDocumented"] is False
    assert result["documentedRowCount"] == 1
    assert result["quarantined"][0]["reason"] == (
        "PROVIDER_OFFICIAL_GAME_IDENTITY_UNAVAILABLE"
    )
    assert "officialGamePk" not in result["quarantined"][0]


def test_doubleheader_rows_remain_raw_without_team_date_fallback():
    official = [
        _official(777001, "2026-07-22T17:05:00Z", game_number=1),
        _official(777002, "2026-07-22T23:05:00Z", game_number=2),
    ]
    result = bbs_context.crosswalk_matches(
        [
            _provider("2026-07-22T17:05:00Z", provider_id="match-one"),
            _provider("2026-07-22T23:05:00Z", provider_id="match-two"),
        ],
        official,
    )

    assert result["acceptedCount"] == 0
    assert result["quarantinedCount"] == 2
    assert result["officialIdentityCredit"] is False


def test_documented_identity_fields_are_required_before_raw_review():
    missing_id = _provider("2026-07-22T23:05:00Z")
    missing_id.pop("match_id")
    invalid_time = _provider("not-a-time", provider_id="second")
    duplicate = _provider("2026-07-22T23:05:00Z", provider_id="second")

    result = bbs_context.crosswalk_matches(
        [missing_id, invalid_time, duplicate],
        [_official(777001, "2026-07-22T23:05:00Z")],
    )

    assert [row["reason"] for row in result["quarantined"]] == [
        "MISSING_PROVIDER_MATCH_ID",
        "MISSING_OR_INVALID_PROVIDER_KICKOFF_UTC",
        "DUPLICATE_PROVIDER_MATCH_ID",
    ]


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.head_calls = []

    def head_object(self, *, Bucket, Key):
        self.head_calls.append((Bucket, Key))
        if (Bucket, Key) not in self.objects:
            raise ClientError(
                {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "HeadObject",
            )
        item = self.objects[(Bucket, Key)]
        return {"Metadata": copy.deepcopy(item["Metadata"]), "VersionId": "version-1"}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        target = (kwargs["Bucket"], kwargs["Key"])
        if target in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.objects[target] = copy.deepcopy(kwargs)
        return {"VersionId": "version-1"}


class Client:
    calls = 0
    init_kwargs = []

    def __init__(self, **kwargs):
        type(self).init_kwargs.append(kwargs)

    def list_mlb_matches(self, game_date):
        type(self).calls += 1
        return {
            "data": [_provider("2026-07-22T23:05:00Z")],
            "meta": {
                "source": "official-league",
                "confidence": 0.85,
                "cached": False,
                "cache_age_ms": 0,
                "request_id": "req-test",
            },
            "error": None,
            "_transport": {"rateRemaining": "999"},
        }


def _canonical(*, retry=False, fingerprint="canonical-pull-fingerprint"):
    return {
        "ok": True,
        "pull_id": "mlb_v1_2026-07-22_slot",
        "providerManifestBound": True,
        "providerManifestFingerprint": "canonical-manifest-fingerprint",
        "canonicalPullId": "mlb_v1_2026-07-22_slot",
        "canonicalPulledAtUtc": "2026-07-22T20:07:10+00:00",
        "canonicalSlotStartUtc": "2026-07-22T20:00:00+00:00",
        "canonicalPullPayloadFingerprint": fingerprint,
        "canonicalPullPk": "PULLS#mlb#2026-07-22",
        "canonicalPullSk": "PULL#SLOT#2026-07-22T20:00:00+00:00",
        "retryReturnedExistingCanonicalPull": retry,
    }


def _capture_args(s3, *, canonical=None):
    return {
        "game_date": "2026-07-22",
        "canonical_pull": canonical or _canonical(),
        "official_games": [_official(777001, "2026-07-22T23:05:00Z")],
        "client_factory": Client,
        "s3_client": s3,
        "now": lambda: datetime(2026, 7, 22, 20, 7, 15, tzinfo=timezone.utc),
    }


def test_capture_is_canonical_bound_write_once_and_never_ml_eligible(monkeypatch):
    monkeypatch.setenv("BBS_SHADOW_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("BBS_SHADOW_S3_BUCKET", "artifacts")
    s3 = FakeS3()
    Client.calls = 0
    Client.init_kwargs = []

    first = bbs_context.capture_shadow_slot(**_capture_args(s3))
    second = bbs_context.capture_shadow_slot(
        **_capture_args(s3, canonical=_canonical(retry=True))
    )

    assert first["status"] == "CAPTURED_WRITE_ONCE_ARTIFACT"
    assert second["status"] == "REUSED_WRITE_ONCE_ARTIFACT"
    assert second["canonicalBindingVerified"] is True
    assert Client.calls == 1
    assert Client.init_kwargs == [{"timeout_seconds": 4, "max_attempts": 1}]
    assert len(s3.put_calls) == 1
    assert first["trainingEligible"] is False
    assert first["completenessCredit"] is False
    assert first["officialIdentityCredit"] is False
    stored = json.loads(s3.put_calls[0]["Body"])
    assert stored["predictionAuthority"] is False
    assert stored["trainingEligible"] is False
    assert stored["completenessCredit"] is False
    assert stored["officialIdentityCredit"] is False
    assert stored["coverageMode"] == "PARTIAL_SINGLE_UTC_DATE_PROBE"
    assert stored["completeSlateCoverageClaimed"] is False
    assert stored["reviewMilestoneDefined"] is False
    assert stored["canonicalSlotStartUtc"] == "2026-07-22T20:00:00+00:00"
    assert stored["crosswalk"]["accepted"] == []
    assert stored["crosswalk"]["officialIdentityCredit"] is False
    assert s3.put_calls[0]["IfNoneMatch"] == "*"
    metadata = s3.put_calls[0]["Metadata"]
    assert metadata["canonical-pull-id"] == "mlb_v1_2026-07-22_slot"
    assert metadata["canonical-pull-fingerprint"] == "canonical-pull-fingerprint"
    assert metadata["canonical-manifest-fingerprint"] == (
        "canonical-manifest-fingerprint"
    )


def test_capture_discloses_unqueried_utc_date_for_cross_midnight_et_slate(monkeypatch):
    monkeypatch.setenv("BBS_SHADOW_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("BBS_SHADOW_S3_BUCKET", "artifacts")
    s3 = FakeS3()
    args = _capture_args(s3)
    args["official_games"] = [
        _official(777001, "2026-07-22T23:05:00Z"),
        _official(777002, "2026-07-23T02:10:00Z"),
    ]

    result = bbs_context.capture_shadow_slot(**args)

    assert result["status"] == "CAPTURED_WRITE_ONCE_ARTIFACT"
    assert result["requestedProviderDatesUtc"] == ["2026-07-22"]
    assert result["officialGameUtcDates"] == ["2026-07-22", "2026-07-23"]
    assert result["unqueriedOfficialGameUtcDates"] == ["2026-07-23"]
    assert result["completeSlateCoverageClaimed"] is False
    stored = json.loads(s3.put_calls[0]["Body"])
    assert stored["providerDateFilterSemantics"] == "UTC"
    assert stored["unqueriedOfficialGameUtcDates"] == ["2026-07-23"]
    assert stored["reviewBlocker"] == "MULTI_UTC_DATE_SLATE_CAPTURE_NOT_IMPLEMENTED"


def test_same_slot_binding_mismatch_fails_closed_before_provider_call(monkeypatch):
    monkeypatch.setenv("BBS_SHADOW_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("BBS_SHADOW_S3_BUCKET", "artifacts")
    s3 = FakeS3()
    Client.calls = 0
    bbs_context.capture_shadow_slot(**_capture_args(s3))

    result = bbs_context.capture_shadow_slot(
        **_capture_args(
            s3,
            canonical=_canonical(retry=True, fingerprint="different-canonical-pull"),
        )
    )

    assert result["ok"] is False
    assert result["status"] == "BBS_ARTIFACT_CANONICAL_BINDING_COLLISION"
    assert result["mismatchedMetadataFields"] == ["canonical-pull-fingerprint"]
    assert Client.calls == 1


def test_missing_binding_metadata_fails_closed(monkeypatch):
    monkeypatch.setenv("BBS_SHADOW_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("BBS_SHADOW_S3_BUCKET", "artifacts")
    s3 = FakeS3()
    Client.calls = 0
    bbs_context.capture_shadow_slot(**_capture_args(s3))
    stored = next(iter(s3.objects.values()))
    stored["Metadata"].pop("canonical-pull-sk")

    result = bbs_context.capture_shadow_slot(
        **_capture_args(s3, canonical=_canonical(retry=True))
    )

    assert result["status"] == "BBS_ARTIFACT_CANONICAL_BINDING_COLLISION"
    assert result["missingMetadataFields"] == ["canonical-pull-sk"]
    assert Client.calls == 1


def test_retry_without_artifact_never_calls_provider(monkeypatch):
    monkeypatch.setenv("BBS_SHADOW_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("BBS_SHADOW_S3_BUCKET", "artifacts")
    Client.calls = 0

    result = bbs_context.capture_shadow_slot(
        **_capture_args(FakeS3(), canonical=_canonical(retry=True))
    )

    assert result["ok"] is True
    assert result["status"] == "SKIPPED_CANONICAL_SLOT_RETRY_NO_ARTIFACT"
    assert Client.calls == 0


def test_provider_outage_fails_soft_without_writing(monkeypatch):
    monkeypatch.setenv("BBS_SHADOW_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("BBS_SHADOW_S3_BUCKET", "artifacts")

    class BrokenClient:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout_seconds": 4, "max_attempts": 1}

        def list_mlb_matches(self, _game_date):
            raise bigballsdata_client.BBSTransientError("BBS_NETWORK_UNAVAILABLE")

    s3 = FakeS3()
    args = _capture_args(s3)
    args["client_factory"] = BrokenClient
    result = bbs_context.capture_shadow_slot(**args)

    assert result["ok"] is False
    assert result["status"] == "PROVIDER_UNAVAILABLE"
    assert result["trainingEligible"] is False
    assert result["completenessCredit"] is False
    assert s3.put_calls == []


def test_capture_refuses_incomplete_canonical_binding_before_provider(monkeypatch):
    monkeypatch.setenv("BBS_SHADOW_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("BBS_SHADOW_S3_BUCKET", "artifacts")
    Client.calls = 0
    canonical = _canonical()
    canonical.pop("canonicalPullPayloadFingerprint")

    result = bbs_context.capture_shadow_slot(
        **_capture_args(FakeS3(), canonical=canonical)
    )

    assert result["status"] == "CANONICAL_PULL_BINDING_INVALID"
    assert Client.calls == 0
