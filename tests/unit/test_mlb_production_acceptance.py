from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "hello_world"))

from enforce_mlb_production_acceptance import (
    LOCK_MINUTES_BEFORE_GAME,
    PRODUCTION_ACCEPTANCE_SCOPE_FINGERPRINT_VERSION,
    PRODUCTION_ACCEPTANCE_SCOPE_VERSION,
    _post_cutoff_scope_valid,
    _scope_fingerprint,
    build_acceptance,
)
import inqsi_pull_history as history
import mlb_canonical_final_labels_v1 as canonical_labels
import run_mlb_ml_v3_audit_report as audit_report
import mlb_ml_aws_training_v1 as trainer
import mlb_ml_experiment_v2 as experiment
import mlb_rolling_24h_audit as rolling_audit


NOW = datetime(2026, 7, 23, 3, 0, tzinfo=timezone.utc)


def _settlement_rows(summary, *, quarantine_all=False):
    completed = int(summary.get("completedFinalGames") or 0)
    graded = int(summary.get("gradedPredictionCount") or 0)
    official = int(summary.get("officialPredictionCount") or 0)
    cutoff = datetime.fromisoformat(experiment.PRODUCTION_RELEASE_CUTOFF_UTC)
    commence = (
        cutoff + timedelta(minutes=30 if quarantine_all else 60)
    ).isoformat()
    scheduled = (
        datetime.fromisoformat(commence)
        - timedelta(minutes=LOCK_MINUTES_BEFORE_GAME)
    ).isoformat()
    official_pct = summary.get("rolling24hOfficialAccuracyPct")
    all_pct = summary.get("rolling24hAllGamesAccuracyPct")
    all_correct = (
        round(float(all_pct) * graded / 100.0) if all_pct is not None else 0
    )
    official_correct = (
        round(float(official_pct) * official / 100.0)
        if official_pct is not None
        else round(float(all_pct or 0.0) * official / 100.0)
    )
    rows = []
    for index in range(completed):
        is_graded = not quarantine_all and index < graded
        is_official = index < official
        correct = index < (official_correct if is_official else all_correct)
        if not is_official:
            remaining_correct = max(0, all_correct - official_correct)
            correct = index - official < remaining_correct
        official_pk = str(700000 + index)
        game_id = (
            f"mlb_statsapi:{official_pk}"
            if is_graded or quarantine_all
            else f"missing-{index - graded}"
        )
        row = {
            "id": game_id,
            "gameId": game_id,
            "officialGamePk": official_pk,
            "slateDateEt": "2026-07-22",
            "commenceTime": commence,
            "awayTeam": "Away Club",
            "homeTeam": "Home Club",
            "awayScore": 2,
            "homeScore": 4,
            "winner": "Home Club",
            "predictedWinner": "Home Club" if correct else "Away Club",
            "completed": True,
            "status": "GRADED" if is_graded else "MISSING_CANONICAL_LOCK",
            "correct": correct if is_graded else None,
            "officialPrediction": bool(is_graded and is_official),
            "lockedCardAudit": {
                "applied": is_graded,
                "lockedFlag": is_graded,
                "lockAtUtc": scheduled if is_graded else None,
                "preventsLateRows": is_graded,
            },
            "canonicalLockAuthority": {
                "version": rolling_audit.CANONICAL_LOCK_AUTHORITY_VERSION,
                "verified": is_graded,
                "consistentRead": is_graded,
                "immutableLocked": is_graded,
                "stageAuthorityVerified": is_graded,
                "persistedStageAuthorityValidated": is_graded,
                "officialAuditEligible": is_graded,
                "exactLockVectorValidated": is_graded,
                "legacyOrDailyCardFallbackUsed": False,
                "sourcePk": (
                    "GAME_WINNERS#mlb#2026-07-22" if is_graded else None
                ),
                "sourceSk": (
                    f"LOCKED#GAME#{commence}#{game_id}" if is_graded else None
                ),
                "recordType": (
                    rolling_audit.CANONICAL_LOCK_RECORD_TYPE
                    if is_graded
                    else None
                ),
                "providerGameId": game_id,
                "providerIdentityMatchMethod": (
                    rolling_audit.EXACT_PROVIDER_MATCH_METHOD
                    if is_graded
                    else None
                ),
                "matchMethod": (
                    rolling_audit.EXACT_PROVIDER_MATCH_METHOD
                    if is_graded
                    else None
                ),
                "exactProviderIdentityMatched": is_graded,
                "verifiedProviderAliasCrosswalkMatched": False,
                "officialGamePk": official_pk,
                "canonicalLockedGameId": game_id,
                "canonicalLockAtUtc": scheduled if is_graded else None,
            },
        }
        rows.append(row)
    return rows


def _acceptance_report(summary, *, quarantine_all=False):
    rows = _settlement_rows(summary, quarantine_all=quarantine_all)
    report = {"rows": rows, "windowHours": 24}

    def loader(slate_date):
        games = []
        if slate_date == "2026-07-22":
            for row in rows:
                game = {
                    "officialGamePk": row["officialGamePk"],
                    "officialDate": slate_date,
                    "gameDate": row["commenceTime"],
                    "awayTeam": row["awayTeam"],
                    "homeTeam": row["homeTeam"],
                    "awayScore": row["awayScore"],
                    "homeScore": row["homeScore"],
                    "winner": row["winner"],
                    "completed": True,
                    "officialStatus": {"abstractGameState": "Final"},
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
            "officialFinalCount": len(games),
            "games": games,
        }

    report["canonicalFinalizedSlateEvidence"] = (
        audit_report._canonical_finalized_slate_evidence(
            report,
            now_utc=NOW,
            official_schedule_loader=loader,
        )
    )
    report["productionAcceptanceScope"] = (
        audit_report._post_cutoff_production_acceptance_scope(report)
    )
    return report


def acceptance_scope(summary, *, quarantine_all=False):
    return _acceptance_report(
        summary,
        quarantine_all=quarantine_all,
    )["productionAcceptanceScope"]


def _replace_canonical_slate_authority(payload, official_game_pks):
    rows = payload.get("rows") or []

    def loader(slate_date):
        games = []
        if slate_date == "2026-07-22":
            for index, official_pk in enumerate(official_game_pks):
                row = next(
                    (
                        item
                        for item in rows
                        if str(item.get("officialGamePk") or "")
                        == str(official_pk)
                    ),
                    {},
                )
                game = {
                    "officialGamePk": str(official_pk),
                    "officialDate": slate_date,
                    "gameDate": row.get("commenceTime")
                    or (
                        datetime(2026, 7, 22, 6, 0, tzinfo=timezone.utc)
                        + timedelta(minutes=index)
                    ).isoformat(),
                    "awayTeam": row.get("awayTeam") or "Away Club",
                    "homeTeam": row.get("homeTeam") or "Home Club",
                    "awayScore": row.get("awayScore", 2),
                    "homeScore": row.get("homeScore", 4),
                    "winner": row.get("winner") or "Home Club",
                    "completed": True,
                    "officialStatus": {"abstractGameState": "Final"},
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
            "officialFinalCount": len(games),
            "games": games,
        }

    payload["canonicalFinalizedSlateEvidence"] = (
        audit_report._canonical_finalized_slate_evidence(
            payload,
            now_utc=NOW,
            official_schedule_loader=loader,
        )
    )
    payload["productionAcceptanceScope"] = (
        audit_report._post_cutoff_production_acceptance_scope(payload)
    )
    return payload


def pull_guard(**updates):
    base = {
        "guardPassed": True,
        "officialScheduleVerified": True,
        "pullsRequired": True,
        "fresh": True,
        "missingCleanScheduledSlots": [],
        "duplicateOrExtraPullsSinceStart": 0,
        "preStartPollutedPullCount": 0,
        "slateDateEt": "2026-07-18",
    }
    base.update(updates)
    return base


def verifier(**updates):
    base = {
        "ok": True,
        "blockers": [],
        "gameCount": 16,
        "predictionCount": 16,
        "allGamesPredicted": True,
        "lock": {"locked": False, "lockDue": False},
    }
    base.update(updates)
    return base


def audit(summary=None, optimization=None, *, quarantine_all=False, **updates):
    deployment = {"gitSha": "a" * 40, "templateSha256": "b" * 64}
    manifest_digest = "c" * 64

    def mode_health(mode, maximum_age_minutes):
        latest = {
            "ok": True,
            "status": (
                "COLLECTING_TRAIN"
                if mode == "training"
                else "WAITING_FOR_PERSISTED_CHALLENGER"
            ),
            "executionMode": mode,
            "version": trainer.VERSION,
            "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
            "manifestDigest": manifest_digest,
            "createdAtUtc": NOW.isoformat(),
            "deploymentIdentity": deployment,
            "statusFingerprintVersion": trainer.STATUS_FINGERPRINT_VERSION,
            "executionConcurrencyControl": trainer.execution_concurrency_control(
                acquired_for_run=True
            ),
        }
        latest["statusFingerprint"] = trainer._status_fingerprint(latest)
        return {
            "ok": True,
            "executionMode": mode,
            "statusPresent": True,
            "latestRunStatus": (
                "COLLECTING_TRAIN"
                if mode == "training"
                else "WAITING_FOR_PERSISTED_CHALLENGER"
            ),
            "latestRunCreatedAtUtc": NOW.isoformat(),
            "latestRunTimestampValid": True,
            "latestRunAgeMinutes": 0.0,
            "latestRunFresh": True,
            "latestRunMaxAgeMinutes": maximum_age_minutes,
            "deploymentIdentity": deployment,
            "deploymentIdentityMatches": True,
            "latestRun": latest,
            "errors": [],
        }

    summary_payload = summary or {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 0,
        "gradedPredictionCount": 0,
        "missingPredictionCount": 0,
        "officialPredictionCount": 0,
        "rolling24hOfficialAccuracyPct": None,
        "rolling24hAllGamesAccuracyPct": None,
    }
    settlement_report = _acceptance_report(
        summary_payload,
        quarantine_all=quarantine_all,
    )
    base = {
        "ok": True,
        "createdAtUtc": NOW.isoformat(),
        "windowHours": settlement_report["windowHours"],
        "summary": summary_payload,
        "rows": settlement_report["rows"],
        "canonicalFinalizedSlateEvidence": settlement_report[
            "canonicalFinalizedSlateEvidence"
        ],
        "productionAcceptanceScope": settlement_report[
            "productionAcceptanceScope"
        ],
        "mlOptimizationV3": optimization or {
            "cleanRowCount": 2,
            "quarantinedRowCount": 226,
            "mode": "SHADOW_CHALLENGER",
            "automaticPromotionEnabled": True,
        },
        "mlTrainingV2": {
            "ok": True,
            "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
            "manifestDigest": manifest_digest,
            "manifestReadStable": True,
            "manifestReadBefore": {
                "revision": 0,
                "manifestDigest": manifest_digest,
            },
            "manifestReadAfter": {
                "revision": 0,
                "manifestDigest": manifest_digest,
            },
            "deployedTrainerIdentity": deployment,
            "statusPresent": True,
            "latestRunStatus": "COLLECTING_TRAIN",
            "latestRunCreatedAtUtc": NOW.isoformat(),
            "latestRunTimestampValid": True,
            "latestRunAgeMinutes": 0.0,
            "latestRunFresh": True,
            "latestRunMaxAgeMinutes": 480.0,
            "trainingHealth": mode_health("training", 480.0),
            "selectionCaptureHealth": mode_health("selection_capture", 45.0),
            "deploymentIdentityAgreement": True,
            "manifestPresent": True,
            "manifestValid": True,
            "releaseActivationValid": True,
            "releaseActivationErrors": [],
            "manifestPhase": "COLLECTING_TRAIN",
            "automaticPromotionEnabled": False,
            "partitionCounts": {
                "train": 0,
                "validation": 0,
                "prospectiveTest": 0,
            },
        },
    }
    base.update(updates)
    return base


def test_pregame_clean_slate_is_infrastructure_accepted_but_accuracy_unmeasurable():
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(),
        now_utc=NOW,
    )
    assert result["ok"] is True
    assert result["infrastructureOk"] is True
    assert result["accuracyStatus"] == "UNMEASURABLE_NO_GRADED_PREDICTIONS"
    assert result["accuracyTargetMet"] is None
    assert "one complete uncontaminated live slate" in result["unproven"]
    assert result["settlementAndAccuracy"][
        "firstCompletePostCutoffSlateProof"
    ]["achieved"] is False


def test_first_clean_slate_milestone_comes_from_trainer_proof_not_infrastructure():
    payload = audit()
    milestones = {
        "firstFullCleanSlateProof": {
            "achieved": True,
            "qualifyingSlateDate": "2026-07-22",
        }
    }
    payload["mlTrainingV2"]["milestones"] = milestones
    latest = payload["mlTrainingV2"]["trainingHealth"]["latestRun"]
    latest["milestones"] = milestones
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["infrastructureOk"] is True
    assert result["mlDisposition"]["v2Training"][
        "firstFullCleanSlateProofAchieved"
    ] is True
    assert "one complete uncontaminated live slate" not in result["unproven"]


def test_acceptance_rebuilds_exact_scope_after_runtime_json_round_trip():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 1,
        "gradedPredictionCount": 1,
        "missingPredictionCount": 0,
        "officialPredictionCount": 1,
        "rolling24hOfficialAccuracyPct": 100.0,
        "rolling24hAllGamesAccuracyPct": 100.0,
    }
    payload = json.loads(json.dumps(audit(summary=summary), default=str))

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is True
    assert result["settlementAndAccuracy"]["scopeValid"] is True
    assert result["settlementAndAccuracy"][
        "postCutoffExactFullSlateCount"
    ] == 1


def test_unattested_top_level_milestone_cannot_claim_a_clean_slate():
    payload = audit()
    payload["mlTrainingV2"]["milestones"] = {
        "firstFullCleanSlateProof": {"achieved": True}
    }

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert "AWS_V2_MILESTONE_STATUS_MISMATCH" in result[
        "infrastructureBlockers"
    ]
    assert "one complete uncontaminated live slate" in result["unproven"]


def test_missing_prediction_is_an_infrastructure_failure():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 14,
        "gradedPredictionCount": 1,
        "missingPredictionCount": 13,
        "officialPredictionCount": 1,
        "rolling24hOfficialAccuracyPct": 0.0,
        "rolling24hAllGamesAccuracyPct": 0.0,
    }
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(summary=summary),
        now_utc=NOW,
    )
    assert result["ok"] is False
    assert "COMPLETED_GAMES_WITHOUT_IMMUTABLE_GRADEABLE_PREDICTIONS" in result["infrastructureBlockers"]


def test_missing_game_from_finalized_official_slate_blocks_acceptance():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 1,
        "gradedPredictionCount": 1,
        "missingPredictionCount": 0,
        "officialPredictionCount": 1,
        "rolling24hOfficialAccuracyPct": 100.0,
        "rolling24hAllGamesAccuracyPct": 100.0,
    }
    payload = _replace_canonical_slate_authority(
        audit(summary=summary),
        ["700000", "700001"],
    )

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert "OFFICIAL_FINALIZED_SLATE_COVERAGE_INCOMPLETE" in result[
        "infrastructureBlockers"
    ]
    assert result["settlementAndAccuracy"]["postCutoffFinalizedSlateProofs"][0][
        "missingOfficialGamePks"
    ] == ["700001"]


def test_empty_partial_feed_cannot_hide_a_fully_finalized_official_slate():
    payload = _replace_canonical_slate_authority(
        audit(),
        ["700000", "700001"],
    )

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert "OFFICIAL_FINALIZED_SLATE_COVERAGE_INCOMPLETE" in result[
        "infrastructureBlockers"
    ]
    settlement = result["settlementAndAccuracy"]
    assert settlement["completedFinalGames"] == 0
    assert settlement["postCutoffFullSlateCoverageDefectCount"] == 1


def test_pre_r3_missing_locks_are_quarantined_from_post_cutoff_acceptance():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 15,
        "gradedPredictionCount": 0,
        "missingPredictionCount": 15,
        "officialPredictionCount": 0,
        "rolling24hOfficialAccuracyPct": None,
        "rolling24hAllGamesAccuracyPct": None,
    }
    payload = audit(summary=summary, quarantine_all=True)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is True
    assert (
        "COMPLETED_GAMES_WITHOUT_IMMUTABLE_GRADEABLE_PREDICTIONS"
        not in result["infrastructureBlockers"]
    )
    settlement = result["settlementAndAccuracy"]
    assert settlement["preCutoffQuarantinedFinalGameCount"] == 15
    assert settlement["completedFinalGames"] == 0
    assert settlement["legacyRolling24hDiagnostic"]["missingPredictionCount"] == 15


def test_post_cutoff_missing_lock_remains_an_infrastructure_blocker():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 1,
        "gradedPredictionCount": 0,
        "missingPredictionCount": 1,
        "officialPredictionCount": 0,
        "rolling24hOfficialAccuracyPct": None,
        "rolling24hAllGamesAccuracyPct": None,
    }
    payload = audit(summary=summary)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "COMPLETED_GAMES_WITHOUT_IMMUTABLE_GRADEABLE_PREDICTIONS"
        in result["infrastructureBlockers"]
    )
    assert result["settlementAndAccuracy"]["postCutoffDefects"][0][
        "gameId"
    ] == "missing-0"


def test_missing_post_cutoff_scope_fails_closed():
    payload = audit()
    payload.pop("productionAcceptanceScope")

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "POST_CUTOFF_SETTLEMENT_SCOPE_MISSING_OR_INVALID"
        in result["infrastructureBlockers"]
    )


def test_post_cutoff_scope_cutoff_mismatch_fails_closed_even_when_resigned():
    payload = audit()
    scope = payload["productionAcceptanceScope"]
    wrong_cutoff = "2026-07-22T04:01:00+00:00"
    scope["releaseCutoffUtc"] = wrong_cutoff
    scope["experimentIdentity"]["releaseCutoffUtc"] = wrong_cutoff
    scope["scopeFingerprint"] = _scope_fingerprint(scope)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "POST_CUTOFF_SETTLEMENT_SCOPE_MISSING_OR_INVALID"
        in result["infrastructureBlockers"]
    )


def test_post_cutoff_scope_semantic_count_tamper_fails_closed_when_resigned():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 1,
        "gradedPredictionCount": 1,
        "missingPredictionCount": 0,
        "officialPredictionCount": 1,
        "rolling24hOfficialAccuracyPct": 100.0,
        "rolling24hAllGamesAccuracyPct": 100.0,
    }
    payload = audit(summary=summary)
    scope = payload["productionAcceptanceScope"]
    scope["postCutoffGradedPredictionCount"] = 0
    scope["scopeFingerprint"] = _scope_fingerprint(scope)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "POST_CUTOFF_SETTLEMENT_SCOPE_MISSING_OR_INVALID"
        in result["infrastructureBlockers"]
    )


def test_internally_valid_resigned_scope_cannot_diverge_from_raw_audit_rows():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 1,
        "gradedPredictionCount": 1,
        "missingPredictionCount": 0,
        "officialPredictionCount": 1,
        "rolling24hOfficialAccuracyPct": 100.0,
        "rolling24hAllGamesAccuracyPct": 100.0,
    }
    payload = audit(summary=summary)
    scope = payload["productionAcceptanceScope"]
    scope["postCutoffGameProofs"][0]["gameId"] = "substituted-provider-id"
    scope["scopeFingerprint"] = _scope_fingerprint(scope)

    # Its self-contained arithmetic and resigned checksum are coherent.  The
    # raw audit row remains authoritative and independently rebuilding from it
    # must reject the substituted result.
    assert _post_cutoff_scope_valid(scope) is True
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert "POST_CUTOFF_SETTLEMENT_SCOPE_MISSING_OR_INVALID" in result[
        "infrastructureBlockers"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("winner", "Away Club"),
        ("homeScore", 1),
        ("correct", False),
    ],
)
def test_resigned_scope_cannot_tamper_official_outcome_or_correctness(
    field, value
):
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 1,
        "gradedPredictionCount": 1,
        "missingPredictionCount": 0,
        "officialPredictionCount": 1,
        "rolling24hOfficialAccuracyPct": 100.0,
        "rolling24hAllGamesAccuracyPct": 100.0,
    }
    payload = audit(summary=summary)
    scope = payload["productionAcceptanceScope"]
    scope["postCutoffGameProofs"][0][field] = value
    scope["scopeFingerprint"] = _scope_fingerprint(scope)

    assert _post_cutoff_scope_valid(scope) is False
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )
    assert result["ok"] is False
    assert "POST_CUTOFF_SETTLEMENT_SCOPE_MISSING_OR_INVALID" in result[
        "infrastructureBlockers"
    ]


def test_resigned_scope_cannot_move_post_cutoff_game_into_legacy_quarantine():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 1,
        "gradedPredictionCount": 0,
        "missingPredictionCount": 1,
        "officialPredictionCount": 0,
        "rolling24hOfficialAccuracyPct": None,
        "rolling24hAllGamesAccuracyPct": None,
    }
    payload = audit(summary=summary)
    scope = acceptance_scope(summary, quarantine_all=True)
    scope["preCutoffQuarantinedGames"][0]["commenceTime"] = (
        "2026-07-22T06:00:00+00:00"
    )
    scope["scopeFingerprint"] = _scope_fingerprint(scope)
    payload["productionAcceptanceScope"] = scope

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "POST_CUTOFF_SETTLEMENT_SCOPE_MISSING_OR_INVALID"
        in result["infrastructureBlockers"]
    )


def test_resigned_scope_cannot_shift_a_scoped_games_scheduled_t45():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 1,
        "gradedPredictionCount": 1,
        "missingPredictionCount": 0,
        "officialPredictionCount": 1,
        "rolling24hOfficialAccuracyPct": 100.0,
        "rolling24hAllGamesAccuracyPct": 100.0,
    }
    payload = audit(summary=summary)
    scope = payload["productionAcceptanceScope"]
    scope["postCutoffGameProofs"][0]["scheduledLockAtUtc"] = (
        "2026-07-22T05:14:00+00:00"
    )
    scope["scopeFingerprint"] = _scope_fingerprint(scope)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "POST_CUTOFF_SETTLEMENT_SCOPE_MISSING_OR_INVALID"
        in result["infrastructureBlockers"]
    )


def test_scope_rejects_missing_official_accuracy_for_official_rows():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 1,
        "gradedPredictionCount": 1,
        "missingPredictionCount": 0,
        "officialPredictionCount": 1,
        "rolling24hOfficialAccuracyPct": 100.0,
        "rolling24hAllGamesAccuracyPct": 100.0,
    }
    scope = acceptance_scope(summary)
    scope["postCutoffOfficialAccuracyPct"] = None
    scope["scopeFingerprint"] = _scope_fingerprint(scope)

    assert _post_cutoff_scope_valid(scope) is False


def test_pre_cutoff_quarantine_never_weakens_current_slate_duplicate_guard():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 15,
        "gradedPredictionCount": 0,
        "missingPredictionCount": 15,
        "officialPredictionCount": 0,
        "rolling24hOfficialAccuracyPct": None,
        "rolling24hAllGamesAccuracyPct": None,
    }
    payload = audit(summary=summary, quarantine_all=True)

    result = build_acceptance(
        pull_guard=pull_guard(duplicateOrExtraPullsSinceStart=1),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert "DUPLICATE_OR_EXTRA_SCHEDULED_PULLS" in result[
        "infrastructureBlockers"
    ]


def test_below_ninety_accuracy_is_dashboard_only_after_clean_coverage():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 10,
        "gradedPredictionCount": 10,
        "missingPredictionCount": 0,
        "officialPredictionCount": 10,
        "rolling24hOfficialAccuracyPct": 80.0,
        "rolling24hAllGamesAccuracyPct": 80.0,
    }
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(summary=summary),
        now_utc=NOW,
    )
    assert result["infrastructureOk"] is True
    assert result["ok"] is True
    assert result["accuracyStatus"] == "BELOW_TARGET"
    assert result["modelBlockers"] == []
    assert "ASPIRATIONAL_90_PCT_DASHBOARD_TARGET_NOT_MET" in result["warnings"]


def test_partial_official_prediction_coverage_fails_closed():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 10,
        "gradedPredictionCount": 10,
        "missingPredictionCount": 0,
        "officialPredictionCount": 9,
        "rolling24hOfficialAccuracyPct": 100.0,
        "rolling24hAllGamesAccuracyPct": 100.0,
    }
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(summary=summary),
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert result["infrastructureOk"] is False
    assert "OFFICIAL_PREDICTION_COVERAGE_INCOMPLETE" in result[
        "infrastructureBlockers"
    ]


def test_ninety_percent_clean_window_passes():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 10,
        "gradedPredictionCount": 10,
        "missingPredictionCount": 0,
        "officialPredictionCount": 10,
        "rolling24hOfficialAccuracyPct": 90.0,
        "rolling24hAllGamesAccuracyPct": 90.0,
    }
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(summary=summary),
        now_utc=NOW,
    )
    assert result["ok"] is True
    assert result["accuracyStatus"] == "TARGET_MET"
    assert result["accuracyTargetMet"] is True


def test_graded_rows_without_authoritative_official_accuracy_fail_closed():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 10,
        "gradedPredictionCount": 10,
        "missingPredictionCount": 0,
        "officialPredictionCount": 10,
        "rolling24hOfficialAccuracyPct": None,
        "rolling24hAllGamesAccuracyPct": 80.0,
    }

    payload = audit(summary=summary)
    payload["productionAcceptanceScope"]["postCutoffOfficialAccuracyPct"] = None
    payload["productionAcceptanceScope"]["scopeFingerprint"] = _scope_fingerprint(
        payload["productionAcceptanceScope"]
    )
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert result["infrastructureOk"] is False
    assert result["accuracyStatus"] == "UNMEASURABLE_OFFICIAL_ACCURACY_MISSING"
    assert (
        "AUTHORITATIVE_OFFICIAL_ACCURACY_MISSING_FOR_GRADED_ROWS"
        in result["infrastructureBlockers"]
    )


def test_missing_v2_trainer_status_is_an_infrastructure_failure():
    payload = audit()
    payload.pop("mlTrainingV2")

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert "AWS_V2_TRAINER_STATUS_MISSING_OR_INVALID" in result["infrastructureBlockers"]
    assert "AWS_V2_EXPERIMENT_MANIFEST_NOT_INITIALIZED" in result["infrastructureBlockers"]


def test_uninitialized_v2_manifest_is_an_infrastructure_failure():
    payload = audit()
    payload["mlTrainingV2"].update(
        {"manifestPresent": False, "manifestValid": None}
    )

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert "AWS_V2_EXPERIMENT_MANIFEST_NOT_INITIALIZED" in result["infrastructureBlockers"]
    assert "AWS_V2_EXPERIMENT_MANIFEST_NOT_CREATED_YET" not in result["warnings"]


def test_missing_latest_v2_status_is_an_infrastructure_failure():
    payload = audit()
    payload["mlTrainingV2"]["ok"] = False
    payload["mlTrainingV2"]["automaticPromotionEnabled"] = None
    payload["mlTrainingV2"]["trainingHealth"] = {}

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert (
        "AWS_V2_TRAINING_STATUS_MISSING_STALE_OR_INVALID"
        in result["infrastructureBlockers"]
    )
    assert "AWS_V2_AUTOMATIC_PROMOTION_STATE_MISSING" in result["infrastructureBlockers"]


def test_seven_hour_training_status_is_healthy_for_six_hour_schedule():
    payload = audit()
    latest = payload["mlTrainingV2"]["trainingHealth"]["latestRun"]
    latest["createdAtUtc"] = (
        NOW - timedelta(hours=7)
    ).isoformat()
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is True
    assert result["mlDisposition"]["v2Training"]["latestRunAgeMinutes"] == 420.0
    assert result["mlDisposition"]["v2Training"]["latestRunFresh"] is True


def test_training_status_older_than_eight_hours_is_an_infrastructure_failure():
    payload = audit()
    payload["mlTrainingV2"]["ok"] = False
    latest = payload["mlTrainingV2"]["trainingHealth"]["latestRun"]
    latest["createdAtUtc"] = (
        NOW - timedelta(minutes=481)
    ).isoformat()
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "AWS_V2_TRAINING_STATUS_MISSING_STALE_OR_INVALID"
        in result["infrastructureBlockers"]
    )
    assert result["mlDisposition"]["v2Training"]["trainingHealth"][
        "latestRunFresh"
    ] is False


def test_selection_capture_status_older_than_45_minutes_fails_independently():
    payload = audit()
    payload["mlTrainingV2"]["ok"] = False
    latest = payload["mlTrainingV2"]["selectionCaptureHealth"]["latestRun"]
    latest["createdAtUtc"] = (NOW - timedelta(minutes=46)).isoformat()
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "AWS_V2_SELECTION_CAPTURE_STATUS_MISSING_STALE_OR_INVALID"
        in result["infrastructureBlockers"]
    )
    assert (
        "AWS_V2_TRAINING_STATUS_MISSING_STALE_OR_INVALID"
        not in result["infrastructureBlockers"]
    )


def test_mode_status_deployment_identity_mismatch_fails_closed():
    payload = audit()
    payload["mlTrainingV2"]["ok"] = False
    payload["mlTrainingV2"]["deploymentIdentityAgreement"] = False
    latest = payload["mlTrainingV2"]["selectionCaptureHealth"]["latestRun"]
    latest["deploymentIdentity"] = {
        "gitSha": "d" * 40,
        "templateSha256": "e" * 64,
    }
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert "AWS_V2_MODE_DEPLOYMENT_IDENTITY_MISMATCH" in result[
        "infrastructureBlockers"
    ]


def test_acceptance_recomputes_status_fingerprint_and_exact_version():
    payload = audit()
    latest = payload["mlTrainingV2"]["trainingHealth"]["latestRun"]
    latest["version"] = "stale-trainer-version"

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert "status_version_mismatch" in result["mlDisposition"]["v2Training"][
        "trainingHealth"
    ]["contractErrors"]
    assert "status_fingerprint_mismatch" in result["mlDisposition"]["v2Training"][
        "trainingHealth"
    ]["contractErrors"]


def test_acceptance_rejects_status_without_acquired_shared_lease():
    payload = audit()
    latest = payload["mlTrainingV2"]["trainingHealth"]["latestRun"]
    latest["executionConcurrencyControl"] = trainer.execution_concurrency_control(
        acquired_for_run=False
    )
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert "status_execution_lease_contract_mismatch" in result[
        "mlDisposition"
    ]["v2Training"]["trainingHealth"]["contractErrors"]


def test_manifest_advance_invalidates_selection_heartbeat_until_recaptured():
    payload = audit()
    latest = payload["mlTrainingV2"]["selectionCaptureHealth"]["latestRun"]
    latest["manifestDigest"] = "d" * 64
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    failed = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )
    assert failed["ok"] is False
    assert "status_manifest_mismatch" in failed["mlDisposition"]["v2Training"][
        "selectionCaptureHealth"
    ]["contractErrors"]

    latest["manifestDigest"] = payload["mlTrainingV2"]["manifestDigest"]
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)
    recaptured = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )
    assert recaptured["ok"] is True


def test_acceptance_independently_rejects_unstable_manifest_status_snapshot():
    payload = audit()
    v2 = payload["mlTrainingV2"]
    v2["manifestReadBefore"] = {
        "revision": 0,
        "manifestDigest": "d" * 64,
    }
    v2["manifestReadAfter"] = {
        "revision": 1,
        "manifestDigest": v2["manifestDigest"],
    }
    # Do not trust the audit's summary boolean without rechecking its evidence.
    v2["manifestReadStable"] = True

    failed = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert failed["ok"] is False
    assert "AWS_V2_MANIFEST_STATUS_SNAPSHOT_UNSTABLE" in failed[
        "infrastructureBlockers"
    ]
    v2_result = failed["mlDisposition"]["v2Training"]
    assert v2_result["manifestReadStable"] is False
    assert "manifest_changed_during_status_read" in v2_result[
        "trainingHealth"
    ]["contractErrors"]
    assert "manifest_changed_during_status_read" in v2_result[
        "selectionCaptureHealth"
    ]["contractErrors"]

    v2["manifestReadBefore"] = dict(v2["manifestReadAfter"])
    recaptured = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )
    assert recaptured["ok"] is True
