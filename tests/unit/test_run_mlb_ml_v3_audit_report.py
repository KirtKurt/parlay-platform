from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from scripts import run_mlb_ml_v3_audit_report as audit_report
import inqsi_pull_history as history
import mlb_canonical_final_labels_v1 as canonical_labels
import mlb_ml_aws_training_v1 as trainer
import mlb_ml_experiment_v2 as experiment
import mlb_rolling_24h_audit as rolling_audit


NOW = datetime(2026, 7, 21, 20, 0, tzinfo=timezone.utc)
SETTLEMENT_NOW = datetime(2026, 7, 23, 3, 0, tzinfo=timezone.utc)
EXPERIMENT_ID = experiment.PRODUCTION_EXPERIMENT_ID
PK = f"MLB_ML_EXPERIMENT#V2#{EXPERIMENT_ID}"
DEPLOYMENT = {"gitSha": "a" * 40, "templateSha256": "b" * 64}
MANIFEST = experiment.new_manifest(
    experiment_id=EXPERIMENT_ID,
    release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
    release_cutoff_utc="2026-07-22T04:00:00+00:00",
    feature_vector_version="vector-v2",
    model_feature_schemas={
        "outcome": list(trainer.dual_model.OUTCOME_FEATURES),
        "reliability": list(trainer.dual_model.RELIABILITY_FEATURES),
    },
    created_at_utc="2026-07-21T00:00:00+00:00",
    release_activation=experiment.release_activation(
        experiment_id=EXPERIMENT_ID,
        release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        release_cutoff_utc="2026-07-22T04:00:00+00:00",
        activated_at_utc="2026-07-21T01:00:00+00:00",
        deployment_git_sha=DEPLOYMENT["gitSha"],
        deployment_template_sha256=DEPLOYMENT["templateSha256"],
    ),
)


def _settlement_row(
    *,
    official_game_pk: str = "881001",
    commence_time: str = "2026-07-22T06:00:00+00:00",
    lock_at_utc: str = "2026-07-22T05:15:00+00:00",
) -> dict[str, Any]:
    game_id = f"mlb_statsapi:{official_game_pk}"
    return {
        "id": game_id,
        "gameId": game_id,
        "officialGamePk": official_game_pk,
        "slateDateEt": "2026-07-22",
        "commenceTime": commence_time,
        "awayTeam": "Away Club",
        "homeTeam": "Home Club",
        "awayScore": 2,
        "homeScore": 4,
        "winner": "Home Club",
        "predictedWinner": "Home Club",
        "completed": True,
        "status": "GRADED",
        "correct": True,
        "officialPrediction": True,
        "lockedCardAudit": {
            "applied": True,
            "lockedFlag": True,
            "lockAtUtc": lock_at_utc,
            "preventsLateRows": True,
        },
        "canonicalLockAuthority": {
            "version": rolling_audit.CANONICAL_LOCK_AUTHORITY_VERSION,
            "verified": True,
            "consistentRead": True,
            "immutableLocked": True,
            "stageAuthorityVerified": True,
            "persistedStageAuthorityValidated": True,
            "officialAuditEligible": True,
            "exactLockVectorValidated": True,
            "legacyOrDailyCardFallbackUsed": False,
            "sourcePk": "GAME_WINNERS#mlb#2026-07-22",
            "sourceSk": f"LOCKED#GAME#{commence_time}#{game_id}",
            "recordType": rolling_audit.CANONICAL_LOCK_RECORD_TYPE,
            "providerGameId": game_id,
            "providerIdentityMatchMethod": rolling_audit.EXACT_PROVIDER_MATCH_METHOD,
            "matchMethod": rolling_audit.EXACT_PROVIDER_MATCH_METHOD,
            "exactProviderIdentityMatched": True,
            "verifiedProviderAliasCrosswalkMatched": False,
            "officialGamePk": official_game_pk,
            "canonicalLockedGameId": game_id,
            "canonicalLockAtUtc": lock_at_utc,
        },
    }


def _report_with_canonical_slate(
    rows: list[dict[str, Any]],
    *,
    official_game_pks: list[str] | None = None,
    finalized: bool = True,
) -> dict[str, Any]:
    report = {"rows": copy.deepcopy(rows), "windowHours": 24}
    ids = official_game_pks
    if ids is None:
        ids = sorted(
            {
                str(row.get("officialGamePk") or "")
                for row in rows
                if str(row.get("officialGamePk") or "")
            }
        )

    def loader(slate_date: str) -> dict[str, Any]:
        games = []
        if slate_date == "2026-07-22":
            for index, official_pk in enumerate(ids or []):
                matching = next(
                    (
                        row
                        for row in rows
                        if str(row.get("officialGamePk") or "") == official_pk
                    ),
                    {},
                )
                game_date = matching.get("commenceTime")
                if audit_report._parse_dt(game_date) is None:
                    game_date = (
                        datetime(2026, 7, 22, 6, 0, tzinfo=timezone.utc)
                        + timedelta(minutes=index)
                    ).isoformat()
                game = {
                    "officialGamePk": official_pk,
                    "officialDate": slate_date,
                    "gameDate": game_date,
                    "awayTeam": matching.get("awayTeam") or "Away Club",
                    "homeTeam": matching.get("homeTeam") or "Home Club",
                    "awayScore": 2 if finalized else None,
                    "homeScore": 4 if finalized else None,
                    "winner": "Home Club" if finalized else None,
                    "completed": finalized,
                    "officialStatus": {
                        "abstractGameState": "Final" if finalized else "Live"
                    },
                }
                game["sourcePayloadFingerprint"] = (
                    history.canonical_payload_fingerprint(
                        canonical_labels._official_final_evidence(game)
                    )
                )
                games.append(game)
        return {
            "ok": True,
            "source": canonical_labels.SOURCE,
            "sourceUrl": canonical_labels.official_finals_url(slate_date),
            "slateDateEt": slate_date,
            "officialGameCount": len(games),
            "officialFinalCount": len(games) if finalized else 0,
            "games": games,
        }

    report["canonicalFinalizedSlateEvidence"] = (
        audit_report._canonical_finalized_slate_evidence(
            report,
            now_utc=SETTLEMENT_NOW,
            official_schedule_loader=loader,
        )
    )
    return report


def test_post_cutoff_scope_quarantines_pre_r3_carryover_without_masking_it():
    legacy = {
        "id": "legacy-provider-id",
        "commenceTime": "2026-07-22T04:30:00+00:00",
        "completed": True,
        "status": "MISSING_CANONICAL_LOCK",
    }

    scope = audit_report._post_cutoff_production_acceptance_scope(
        _report_with_canonical_slate([legacy])
    )

    assert scope["ok"] is True
    assert scope["experimentIdentity"] == {
        "experimentVersion": experiment.VERSION,
        "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
        "releaseContractId": experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        "releaseCutoffUtc": experiment.PRODUCTION_RELEASE_CUTOFF_UTC,
    }
    assert scope["preCutoffQuarantinedFinalGameCount"] == 1
    assert scope["postCutoffCompletedFinalGameCount"] == 0
    assert scope["postCutoffMissingPredictionCount"] == 0
    assert scope["settlementCoverageOk"] is True


def test_post_cutoff_scope_proves_clean_exact_official_game_coverage():
    scope = audit_report._post_cutoff_production_acceptance_scope(
        _report_with_canonical_slate([_settlement_row()])
    )

    assert scope["ok"] is True
    assert scope["settlementCoverageOk"] is True
    assert scope["postCutoffGradedPredictionCount"] == 1
    assert scope["postCutoffExactOfficialGameIdCount"] == 1
    assert scope["postCutoffOfficialGamePks"] == ["881001"]
    assert scope["postCutoffCanonicalLockAtOrAfterCutoffCount"] == 1
    assert scope["postCutoffOfficialOutcomeAuthorityCount"] == 1
    assert scope["coverage"]["officialOutcomeAuthorityComplete"] is True
    assert scope["postCutoffGameProofs"][0][
        "officialOutcomeAuthorityProven"
    ] is True
    assert scope["coverage"]["canonicalGradedPct"] == 100.0
    assert scope["coverage"]["exactOfficialGameIdPct"] == 100.0
    assert scope["postCutoffBlockerCodes"] == []
    assert scope["postCutoffExactFullSlateCount"] == 1
    assert scope["firstCompletePostCutoffSlateProof"]["achieved"] is True
    assert scope["scopeFingerprint"] == audit_report._scope_fingerprint(scope)


@pytest.mark.parametrize(
    ("field", "value", "expected_blocker"),
    [
        (
            "winner",
            "Away Club",
            "official_finalized_game_winner_mismatch",
        ),
        (
            "homeScore",
            1,
            "official_finalized_game_score_mismatch",
        ),
        (
            "correct",
            False,
            "official_correctness_derivation_mismatch",
        ),
    ],
)
def test_post_cutoff_scope_binds_rows_to_official_outcome_and_correctness(
    field, value, expected_blocker
):
    report = _report_with_canonical_slate([_settlement_row()])
    report["rows"][0][field] = value

    scope = audit_report._post_cutoff_production_acceptance_scope(report)

    assert scope["ok"] is True
    assert scope["settlementCoverageOk"] is False
    assert scope["postCutoffGradedPredictionCount"] == 0
    assert scope["postCutoffOfficialOutcomeAuthorityCount"] == 0
    assert expected_blocker in scope["postCutoffBlockerCodes"]
    assert scope["postCutoffGameProofs"][0][
        "officialOutcomeAuthorityProven"
    ] is False
    assert scope["postCutoffExactFullSlateCount"] == 0


def test_finalized_official_slate_missing_one_audit_game_fails_closed():
    scope = audit_report._post_cutoff_production_acceptance_scope(
        _report_with_canonical_slate(
            [_settlement_row(official_game_pk="881001")],
            official_game_pks=["881001", "881002"],
        )
    )

    assert scope["ok"] is True
    assert scope["settlementCoverageOk"] is False
    assert scope["postCutoffFullSlateCoverageDefectCount"] == 1
    proof = scope["postCutoffFinalizedSlateProofs"][0]
    assert proof["missingOfficialGamePks"] == ["881002"]
    assert "official_finalized_slate_missing_audit_rows" in proof["errors"]


def test_fully_finalized_slate_rejects_empty_partial_score_feed():
    scope = audit_report._post_cutoff_production_acceptance_scope(
        _report_with_canonical_slate(
            [],
            official_game_pks=["881001", "881002"],
        )
    )

    assert scope["ok"] is True
    assert scope["postCutoffCompletedFinalGameCount"] == 0
    assert scope["settlementCoverageOk"] is False
    proof = scope["postCutoffFinalizedSlateProofs"][0]
    assert proof["missingOfficialGamePks"] == ["881001", "881002"]
    assert proof["observedAuditRowCount"] == 0


def test_zero_game_pregame_scope_is_healthy_but_not_a_full_slate_proof():
    scope = audit_report._post_cutoff_production_acceptance_scope(
        _report_with_canonical_slate([], official_game_pks=[])
    )

    assert scope["ok"] is True
    assert scope["settlementCoverageOk"] is True
    assert scope["postCutoffFinalizedSlateCount"] == 0
    assert scope["firstCompletePostCutoffSlateProof"] == {
        "achieved": False,
        "qualifyingSlateDateEt": None,
        "evaluatedFinalizedSlateCount": 0,
        "exactFullSlateCount": 0,
        "authority": scope["firstCompletePostCutoffSlateProof"]["authority"],
    }


@pytest.mark.parametrize("tamper", ["missing", "before_cutoff", "mismatch"])
def test_post_cutoff_scope_fails_closed_on_missing_or_tampered_lock_at(tamper):
    row = _settlement_row()
    if tamper == "missing":
        row["lockedCardAudit"]["lockAtUtc"] = None
        row["canonicalLockAuthority"]["canonicalLockAtUtc"] = None
    elif tamper == "before_cutoff":
        row["lockedCardAudit"]["lockAtUtc"] = "2026-07-22T03:59:59+00:00"
        row["canonicalLockAuthority"]["canonicalLockAtUtc"] = (
            "2026-07-22T03:59:59+00:00"
        )
    else:
        row["lockedCardAudit"]["lockAtUtc"] = "2026-07-22T05:16:00+00:00"

    scope = audit_report._post_cutoff_production_acceptance_scope(
        _report_with_canonical_slate([row])
    )

    assert scope["ok"] is True
    assert scope["settlementCoverageOk"] is False
    assert scope["postCutoffGradedPredictionCount"] == 0
    assert scope["postCutoffMissingPredictionCount"] == 1
    assert scope["postCutoffDefects"][0]["blockers"]
    if tamper == "before_cutoff":
        assert "canonical_lock_before_release_cutoff" in scope[
            "postCutoffBlockerCodes"
        ]
    else:
        assert "canonical_lock_at_authority_missing_or_mismatch" in scope[
            "postCutoffBlockerCodes"
        ]


def test_post_cutoff_scope_rejects_missing_exact_official_game_identity():
    row = _settlement_row()
    row["id"] = row["gameId"] = "provider-only-id"
    row.pop("officialGamePk")
    authority = row["canonicalLockAuthority"]
    authority["providerGameId"] = "provider-only-id"
    authority["sourceSk"] = (
        "LOCKED#GAME#2026-07-22T06:00:00+00:00#provider-only-id"
    )
    authority["officialGamePk"] = None
    authority["canonicalLockedGameId"] = "provider-only-id"

    scope = audit_report._post_cutoff_production_acceptance_scope(
        _report_with_canonical_slate(
            [row], official_game_pks=["881001"]
        )
    )

    assert scope["postCutoffMissingPredictionCount"] == 1
    assert "exact_official_game_pk_missing_or_conflicting" in scope[
        "postCutoffBlockerCodes"
    ]


def test_post_cutoff_scope_invalid_commence_time_fails_scope_construction():
    row = _settlement_row()
    row["commenceTime"] = "not-a-timestamp"

    scope = audit_report._post_cutoff_production_acceptance_scope(
        _report_with_canonical_slate([row])
    )

    assert scope["ok"] is False
    assert scope["unscopedInvalidRowCount"] == 1
    assert scope["scopeConstructionBlockers"] == [
        "audit_rows_cannot_be_cutoff_scoped"
    ]


class FakeTable:
    def __init__(self, records: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.records = records

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        assert kwargs["ConsistentRead"] is True
        data = self.records.get((key["PK"], key["SK"]))
        return {"Item": {"data": data}} if data is not None else {}


class FakeDynamoResource:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def Table(self, name: str) -> FakeTable:
        assert name == "snapshots"
        return self.table


def _ddb_resource_round_trip(value: Any) -> Any:
    """Return the value shape produced by a real boto3 DynamoDB resource read."""
    serializer = TypeSerializer()
    deserializer = TypeDeserializer()
    return deserializer.deserialize(serializer.serialize(trainer._ddb_safe(value)))


class AdvancingManifestTable(FakeTable):
    def __init__(
        self,
        records: dict[tuple[str, str], dict[str, Any]],
        manifest_before: dict[str, Any],
        manifest_after: dict[str, Any],
    ) -> None:
        super().__init__(records)
        self.manifests = [manifest_before, manifest_after]
        self.manifest_reads = 0

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        if (key["PK"], key["SK"]) == (PK, "MANIFEST"):
            assert kwargs["ConsistentRead"] is True
            index = min(self.manifest_reads, len(self.manifests) - 1)
            self.manifest_reads += 1
            return {"Item": {"data": self.manifests[index]}}
        return super().get_item(**kwargs)


def _install_table(
    monkeypatch,
    records: dict[tuple[str, str], dict[str, Any]],
    *,
    include_manifest: bool = True,
) -> None:
    monkeypatch.setenv("SNAPSHOTS_TABLE", "snapshots")
    records = dict(records)
    if include_manifest:
        records.setdefault((PK, "MANIFEST"), MANIFEST)
    resource = FakeDynamoResource(FakeTable(records))
    monkeypatch.setattr("boto3.resource", lambda service: resource)
    monkeypatch.setattr(
        audit_report, "_read_deployed_trainer_identity", lambda: DEPLOYMENT
    )


def _install_advancing_manifest_table(
    monkeypatch,
    records: dict[tuple[str, str], dict[str, Any]],
    manifest_before: dict[str, Any],
    manifest_after: dict[str, Any],
) -> None:
    monkeypatch.setenv("SNAPSHOTS_TABLE", "snapshots")
    table = AdvancingManifestTable(records, manifest_before, manifest_after)
    resource = FakeDynamoResource(table)
    monkeypatch.setattr("boto3.resource", lambda service: resource)
    monkeypatch.setattr(
        audit_report, "_read_deployed_trainer_identity", lambda: DEPLOYMENT
    )


def test_v2_status_freshness_and_automatic_promotion_come_from_latest_status(
    monkeypatch,
) -> None:
    _install_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): _status(
                "training", 15, automatic_promotion=True
            ),
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 10
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is True
    assert result["statusPresent"] is True
    assert result["latestRunTimestampValid"] is True
    assert result["latestRunAgeMinutes"] == 15.0
    assert result["latestRunFresh"] is True
    assert result["latestRunMaxAgeMinutes"] == 480.0
    assert result["selectionCaptureHealth"]["latestRunFresh"] is True
    assert result["deploymentIdentityAgreement"] is True
    assert result["automaticPromotionEnabled"] is True


def test_missing_or_stale_v2_status_is_not_reported_healthy(monkeypatch) -> None:
    _install_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): _status("training", 15),
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 46
            ),
        },
    )

    stale = audit_report._read_v2_training_state(now_utc=NOW)
    assert stale["ok"] is False
    assert stale["statusPresent"] is True
    assert stale["latestRunFresh"] is True
    assert stale["selectionCaptureHealth"]["latestRunFresh"] is False

    _install_table(monkeypatch, {}, include_manifest=False)
    missing = audit_report._read_v2_training_state(now_utc=NOW)
    assert missing["ok"] is False
    assert missing["statusPresent"] is False
    assert missing["latestRunTimestampValid"] is False
    assert missing["latestRunAgeMinutes"] is None
    assert missing["latestRunFresh"] is False
    assert missing["selectionCaptureHealth"]["statusPresent"] is False
    assert missing["automaticPromotionEnabled"] is None


def _status(
    mode: str,
    age_minutes: int,
    *,
    ok: bool = True,
    git_sha: str = "a" * 40,
    automatic_promotion: bool = False,
    manifest_digest: str | None = None,
) -> dict[str, Any]:
    value = {
        "ok": ok,
        "status": f"{mode.upper()}_STATUS",
        "executionMode": mode,
        "createdAtUtc": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "automaticPromotionEnabled": automatic_promotion,
        "milestones": {"source": mode},
        "version": trainer.VERSION,
        "experimentId": EXPERIMENT_ID,
        "manifestDigest": manifest_digest or MANIFEST["manifestDigest"],
        "deploymentIdentity": {
            "gitSha": git_sha,
            "templateSha256": "b" * 64,
        },
        "statusFingerprintVersion": trainer.STATUS_FINGERPRINT_VERSION,
        "executionConcurrencyControl": trainer.execution_concurrency_control(
            acquired_for_run=True
        ),
    }
    value["statusFingerprint"] = trainer._status_fingerprint(value)
    return value


def test_generic_latest_never_overrides_mode_specific_authority(monkeypatch) -> None:
    experiment_id = "mlb-v2-2026-07-22-future-prospective-r3"
    pk = f"MLB_ML_EXPERIMENT#V2#{experiment_id}"
    generic = _status("selection_capture", 1, ok=False)
    generic["automaticPromotionEnabled"] = True
    _install_table(
        monkeypatch,
        {
            (pk, "STATUS#LATEST"): generic,
            (pk, "STATUS#LATEST#TRAINING"): _status("training", 300),
            (pk, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 15
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is True
    assert result["latestRunAgeMinutes"] == 300.0
    assert result["latestRunMaxAgeMinutes"] == 480.0
    assert result["milestones"] == {"source": "training"}
    assert result["automaticPromotionEnabled"] is False
    assert result["genericLatestStatusDiagnosticOnly"] == generic


def test_fresh_capture_cannot_mask_stale_or_failed_training(monkeypatch) -> None:
    experiment_id = "mlb-v2-2026-07-22-future-prospective-r3"
    pk = f"MLB_ML_EXPERIMENT#V2#{experiment_id}"
    _install_table(
        monkeypatch,
        {
            (pk, "STATUS#LATEST"): _status("selection_capture", 1),
            (pk, "STATUS#LATEST#TRAINING"): _status("training", 481),
            (pk, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1
            ),
        },
    )

    stale = audit_report._read_v2_training_state(now_utc=NOW)

    assert stale["ok"] is False
    assert stale["trainingHealth"]["ok"] is False
    assert stale["selectionCaptureHealth"]["ok"] is True

    _install_table(
        monkeypatch,
        {
            (pk, "STATUS#LATEST"): _status("selection_capture", 1),
            (pk, "STATUS#LATEST#TRAINING"): _status("training", 1, ok=False),
            (pk, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1
            ),
        },
    )
    failed = audit_report._read_v2_training_state(now_utc=NOW)
    assert failed["ok"] is False
    assert "status_not_ok" in failed["trainingHealth"]["errors"]


def test_matching_fresh_modes_require_same_deployment_identity(monkeypatch) -> None:
    experiment_id = "mlb-v2-2026-07-22-future-prospective-r3"
    pk = f"MLB_ML_EXPERIMENT#V2#{experiment_id}"
    _install_table(
        monkeypatch,
        {
            (pk, "STATUS#LATEST#TRAINING"): _status("training", 1),
            (pk, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1, git_sha="c" * 40
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["trainingHealth"]["ok"] is True
    assert result["selectionCaptureHealth"]["ok"] is False
    assert "status_deployment_identity_mismatch" in result[
        "selectionCaptureHealth"
    ]["errors"]
    assert result["deploymentIdentityAgreement"] is False
    assert result["ok"] is False


def test_status_contract_tamper_and_manifest_advance_fail_closed(monkeypatch) -> None:
    tampered = _status("training", 1)
    tampered["version"] = "stale-version"
    old_capture = _status("selection_capture", 1, manifest_digest="c" * 64)
    _install_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): tampered,
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): old_capture,
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is False
    assert "status_version_mismatch" in result["trainingHealth"]["errors"]
    assert "status_fingerprint_mismatch" in result["trainingHealth"]["errors"]
    assert "status_manifest_mismatch" in result["selectionCaptureHealth"]["errors"]


def test_status_without_acquired_shared_lease_fails_audit_even_with_valid_fingerprint(
    monkeypatch,
) -> None:
    training = _status("training", 1)
    training["executionConcurrencyControl"] = trainer.execution_concurrency_control(
        acquired_for_run=False
    )
    training["statusFingerprint"] = trainer._status_fingerprint(training)
    _install_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): training,
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is False
    assert "status_execution_lease_contract_mismatch" in result[
        "trainingHealth"
    ]["errors"]


def test_manifest_advance_during_status_reads_fails_closed(monkeypatch) -> None:
    advanced_manifest = copy.deepcopy(MANIFEST)
    advanced_manifest["revision"] = MANIFEST["revision"] + 1
    advanced_manifest["manifestDigest"] = experiment.manifest_digest(
        advanced_manifest
    )
    _install_advancing_manifest_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): _status("training", 1),
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1
            ),
        },
        MANIFEST,
        advanced_manifest,
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is False
    assert result["manifestReadStable"] is False
    assert result["manifestReadBefore"] == {
        "revision": MANIFEST["revision"],
        "manifestDigest": MANIFEST["manifestDigest"],
    }
    assert result["manifestReadAfter"] == {
        "revision": advanced_manifest["revision"],
        "manifestDigest": advanced_manifest["manifestDigest"],
    }
    assert "manifest_changed_during_status_read" in result["trainingHealth"][
        "errors"
    ]
    assert "manifest_changed_during_status_read" in result[
        "selectionCaptureHealth"
    ]["errors"]


def test_stable_manifest_and_recaptured_statuses_pass(monkeypatch) -> None:
    advanced_manifest = copy.deepcopy(MANIFEST)
    advanced_manifest["revision"] = MANIFEST["revision"] + 1
    advanced_manifest["manifestDigest"] = experiment.manifest_digest(
        advanced_manifest
    )
    _install_table(
        monkeypatch,
        {
            (PK, "MANIFEST"): advanced_manifest,
            (PK, "STATUS#LATEST#TRAINING"): _status(
                "training",
                1,
                manifest_digest=advanced_manifest["manifestDigest"],
            ),
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture",
                1,
                manifest_digest=advanced_manifest["manifestDigest"],
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is True
    assert result["manifestReadStable"] is True
    assert result["manifestReadBefore"] == result["manifestReadAfter"]


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("missing", "release_activation_missing"),
        ("tampered_identity", "release_activation_git_identity_invalid"),
        (
            "at_cutoff",
            "release_activation_not_strictly_before_cutoff",
        ),
    ),
)
def test_release_activation_invalidity_fails_recurring_audit(
    monkeypatch,
    mutation,
    expected_error,
) -> None:
    manifest = copy.deepcopy(MANIFEST)
    if mutation == "missing":
        manifest.pop("releaseActivation", None)
    elif mutation == "tampered_identity":
        manifest["releaseActivation"]["deploymentIdentity"]["gitSha"] = (
            "not-a-valid-git-sha"
        )
    else:
        manifest["releaseActivation"]["activatedAtUtc"] = (
            manifest["releaseCutoffUtc"]
        )
    manifest["manifestDigest"] = experiment.manifest_digest(manifest)
    _install_table(
        monkeypatch,
        {
            (PK, "MANIFEST"): manifest,
            (PK, "STATUS#LATEST#TRAINING"): _status(
                "training",
                1,
                manifest_digest=manifest["manifestDigest"],
            ),
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture",
                1,
                manifest_digest=manifest["manifestDigest"],
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is False
    assert result["manifestValid"] is True
    assert result["releaseActivationValid"] is False
    assert expected_error in result["releaseActivationErrors"]
    assert "current_manifest_release_activation_invalid" in result[
        "trainingHealth"
    ]["errors"]
    assert "current_manifest_release_activation_invalid" in result[
        "selectionCaptureHealth"
    ]["errors"]


def test_real_dynamodb_numeric_round_trip_preserves_manifest_and_status_health(
    monkeypatch,
) -> None:
    training = _status("training", 15)
    training["metrics"] = {
        "accuracyPct": 90.0,
        "calibrationError": 0.08,
        "selectedCount": 15,
    }
    training["statusFingerprint"] = trainer._status_fingerprint(training)
    records = {
        (PK, "MANIFEST"): _ddb_resource_round_trip(MANIFEST),
        (PK, "STATUS#LATEST#TRAINING"): _ddb_resource_round_trip(
            training
        ),
        (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _ddb_resource_round_trip(
            _status("selection_capture", 10)
        ),
    }
    _install_table(monkeypatch, records)

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is True
    assert result["manifestValid"] is True
    assert result["manifestReadStable"] is True
    assert result["trainingHealth"]["ok"] is True
    assert result["selectionCaptureHealth"]["ok"] is True
    assert result["manifestReadBefore"]["revision"] == 0
    assert result["trainingHealth"]["latestRun"]["metrics"] == {
        "accuracyPct": 90,
        "calibrationError": 0.08,
        "selectedCount": 15,
    }
    json.dumps(result)


def test_deployed_trainer_identity_rejects_non_hex_attestation(monkeypatch) -> None:
    class CloudFormation:
        def describe_stack_resource(self, **kwargs):
            return {"StackResourceDetail": {"PhysicalResourceId": "trainer"}}

    class Lambda:
        def get_function_configuration(self, **kwargs):
            return {
                "Environment": {
                    "Variables": {
                        "INQSI_DEPLOY_GIT_SHA": "G" * 40,
                        "INQSI_DEPLOY_TEMPLATE_SHA256": "z" * 64,
                    }
                }
            }

    clients = {"cloudformation": CloudFormation(), "lambda": Lambda()}
    monkeypatch.setattr("boto3.client", lambda service, **kwargs: clients[service])

    with pytest.raises(RuntimeError, match="release identity is invalid"):
        audit_report._read_deployed_trainer_identity()
