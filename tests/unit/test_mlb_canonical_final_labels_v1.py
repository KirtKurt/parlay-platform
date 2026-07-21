from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_canonical_final_labels_v1 as labels
import mlb_fundamentals_snapshot_v2 as fundamentals_v2
import mlb_rolling_24h_audit as rolling_audit


SLATE = "2026-07-21"


class ConditionalCollision(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
        super().__init__("conditional collision")


class OutcomeTable:
    def __init__(self):
        self.items = {}
        self.put_calls = []

    def put_item(self, *, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        self.put_calls.append(
            {
                "item": copy.deepcopy(Item),
                "condition": ConditionExpression,
            }
        )
        if ConditionExpression and key in self.items:
            raise ConditionalCollision()
        self.items[key] = copy.deepcopy(Item)
        return {}

    def get_item(self, *, Key, ConsistentRead=False):
        return {"Item": copy.deepcopy(self.items.get((Key["PK"], Key["SK"])))}

    def query(self, **kwargs):
        return {"Items": [copy.deepcopy(item) for item in self.items.values()]}


def canonical_lock(
    official_pk: str,
    *,
    game_id: str | None = None,
    provider_event_id: str | None = None,
    away: str = "Boston Red Sox",
    home: str = "New York Yankees",
    predicted_winner: str | None = None,
):
    identity = game_id or provider_event_id or f"mlb_statsapi:{official_pk}"
    predicted = predicted_winner or home
    source_sk = f"LOCKED#GAME#2026-07-21T23:05:00+00:00#{identity}"
    authority = {
        "version": rolling_audit.CANONICAL_LOCK_AUTHORITY_VERSION,
        "verified": True,
        "consistentRead": True,
        "sourcePk": f"GAME_WINNERS#mlb#{SLATE}",
        "sourceSk": source_sk,
        "recordType": rolling_audit.CANONICAL_LOCK_RECORD_TYPE,
        "immutableLocked": True,
        "stageAuthorityVerified": True,
        "stageAuthorityVersion": "test-stage-version",
        "stageFingerprint": f"stage-{official_pk}",
        "persistedStageAuthorityValidated": True,
        "officialAuditEligible": True,
        "learningEligible": True,
        "selectionLockIndependentOfTrainingVector": True,
        "exactLockVectorValidated": True,
        "trainingExclusionReasons": [],
        "legacyOrDailyCardFallbackUsed": False,
    }
    row = {
        "slate_date": SLATE,
        "slateDateEt": SLATE,
        "gameId": identity,
        "gameIdentity": identity,
        "officialGamePk": official_pk,
        "providerEventId": provider_event_id,
        "commenceTime": "2026-07-21T23:05:00+00:00",
        "awayTeam": away,
        "homeTeam": home,
        "predictedWinner": predicted,
        "predictedSide": "home" if predicted == home else "away",
        "frozenFeatureVector": {
            "fingerprint": f"vector-{official_pk}",
            "labels": {"homeWon": None, "pickCorrect": None},
        },
        "canonicalLockAuthority": authority,
    }
    return row


def provider_alias_lock(official_pk: str, provider_id: str):
    row = canonical_lock(official_pk, game_id=f"mlb_statsapi:{official_pk}")
    proof = {
        "officialGamePk": official_pk,
        "providerEventId": provider_id,
        "awayTeamNormalized": "boston red sox",
        "homeTeamNormalized": "new york yankees",
        "manifestFingerprints": ["manifest-one"],
        "evidenceCount": 1,
        "slateDateEt": SLATE,
        "immutableManifestValidated": True,
        "uniqueBidirectionalCrosswalk": True,
    }
    row["providerEventId"] = provider_id
    row["canonicalLockAuthority"].update(
        {
            "providerGameId": provider_id,
            "canonicalLockedGameId": f"mlb_statsapi:{official_pk}",
            "officialGamePk": official_pk,
            "providerIdentityMatchMethod": rolling_audit.VERIFIED_PROVIDER_ALIAS_MATCH_METHOD,
            "matchMethod": rolling_audit.VERIFIED_PROVIDER_ALIAS_MATCH_METHOD,
            "exactProviderIdentityMatched": False,
            "verifiedProviderAliasCrosswalkMatched": True,
            "providerAliasCrosswalk": proof,
        }
    )
    return row


def official_game(
    official_pk: str,
    *,
    away: str = "Boston Red Sox",
    home: str = "New York Yankees",
    away_score: int | None = 2,
    home_score: int | None = 5,
    completed: bool = True,
):
    winner = None
    if completed and away_score is not None and home_score is not None:
        winner = home if home_score > away_score else away
    row = {
        "officialGamePk": official_pk,
        "officialDate": SLATE,
        "gameDate": "2026-07-21T23:05:00Z",
        "awayTeam": away,
        "homeTeam": home,
        "awayScore": away_score,
        "homeScore": home_score,
        "winner": winner,
        "completed": completed,
        "officialStatus": {
            "abstractGameState": "Final" if completed else "Live",
            "codedGameState": "F" if completed else "I",
            "statusCode": "F" if completed else "I",
            "detailedState": "Final" if completed else "In Progress",
        },
    }
    row["sourcePayloadFingerprint"] = labels.history.canonical_payload_fingerprint(
        labels._official_final_evidence(row)
    )
    return row


def official_report(*games):
    return {
        "ok": True,
        "source": labels.SOURCE,
        "sourceUrl": labels.official_finals_url(SLATE),
        "slateDateEt": SLATE,
        "officialGameCount": len(games),
        "officialFinalCount": sum(game.get("completed") is True for game in games),
        "games": list(games),
    }


def install_inputs(monkeypatch, locks, games, terminals=None, rejected_locks=None):
    table = OutcomeTable()
    monkeypatch.setattr(labels, "outcomes_tbl", table)
    monkeypatch.setattr(
        labels,
        "_validated_canonical_locks",
        lambda slate: (copy.deepcopy(locks), copy.deepcopy(rejected_locks or [])),
    )
    monkeypatch.setattr(
        labels,
        "_validated_terminal_outcomes",
        lambda slate: (copy.deepcopy(terminals or {}), []),
    )
    monkeypatch.setattr(
        labels,
        "fetch_official_schedule",
        lambda slate: copy.deepcopy(official_report(*games)),
    )
    return table


def terminal_outcome(official_pk: str):
    return {
        "PK": f"LOCKED_PICKS#mlb#{SLATE}",
        "SK": f"PER_GAME_LOCK_OUTCOME#TMINUS45#{official_pk}",
        "record_type": labels.per_game_lock.LOCK_OUTCOME_RECORD_TYPE,
        "version": labels.per_game_lock.LOCK_OUTCOME_VERSION,
        "slate_date": SLATE,
        "game_id": f"mlb_statsapi:{official_pk}",
        "lock_status": "LOCKED_NO_PREDICTION_DATA",
        "lock_outcome_recorded": True,
        "locked_prediction": False,
        "training_eligible": False,
        "write_once": True,
    }


def complete_v2_lock(official_pk: str):
    row = canonical_lock(official_pk)
    source_at = "2026-07-21T20:00:00+00:00"
    retrieved_at = "2026-07-21T20:00:10+00:00"
    persisted_at = "2026-07-21T20:00:20+00:00"
    lock_at = "2026-07-21T22:20:00+00:00"
    row.update(
        {
            "predictionSourcePullAt": source_at,
            "predictionSourcePullId": "canonical-slot-1",
            "predictionPersistedAtUtc": persisted_at,
            "lockedAtUtc": lock_at,
            "trainingEligible": True,
            "trainingExclusionReasons": [],
            "mlFeatureFreeze": {
                "trainingEligible": True,
                "trainingExclusionReasons": [],
            },
            "advanced_context": {},
        }
    )
    for output_name, context_name, fields in fundamentals_v2.GROUP_SPECS:
        required = set(fundamentals_v2.REQUIRED_VALUE_KEYS[output_name])
        group = {
            "source_status": "CONNECTED",
            "sourceProvenance": {
                "provider": "fixture-provider",
                "endpoint": "https://example.invalid/pregame",
                "dataset": context_name,
                "retrievedAtUtc": retrieved_at,
                "sourceEffectiveAtUtc": source_at,
                "payloadFingerprint": f"fixture-{context_name}",
            },
        }
        for output_key, input_key in fields:
            if output_key in required:
                group[input_key] = 1
        row["advanced_context"][context_name] = group
    snapshot = fundamentals_v2.build(row, captured_at_utc=persisted_at)
    row["fundamentalsSnapshotV2"] = snapshot
    row["fundamentalsSnapshotV2Ref"] = {
        "version": snapshot["version"],
        "schemaCohort": snapshot["schemaCohort"],
        "gameId": snapshot["game"]["gameId"],
        "sourcePullId": snapshot["sourcePullId"],
        "evidenceCutoffUtc": snapshot["evidenceCutoffUtc"],
        "fingerprintVersion": snapshot["fingerprintVersion"],
        "fingerprint": snapshot["fingerprint"],
    }
    row["frozenFeatureVector"].update(
        {
            "fundamentalsSnapshotV2Version": snapshot["version"],
            "fundamentalsSnapshotV2Fingerprint": snapshot["fingerprint"],
            "fundamentalsSnapshotV2AtOrBeforeLock": True,
            "fundamentalsSnapshotV2TrainingEligible": True,
            "fundamentalsSnapshotV2Ref": copy.deepcopy(
                row["fundamentalsSnapshotV2Ref"]
            ),
        }
    )
    return row


def test_live_game_is_skipped_and_never_labeled(monkeypatch):
    game = official_game("700001", completed=False, away_score=None, home_score=None)
    table = install_inputs(monkeypatch, [canonical_lock("700001")], [game])

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is True
    assert report["status"] == "PENDING_OFFICIAL_FINALS"
    assert report["skippedNotFinalCount"] == 1
    assert report["labelWriteCount"] == 0
    assert table.items == {}


def test_official_stats_payload_requires_final_before_exposing_scores():
    payload = {
        "totalGames": 2,
        "dates": [
            {
                "date": SLATE,
                "games": [
                    {
                        "gamePk": 700002,
                        "officialDate": SLATE,
                        "gameDate": "2026-07-21T23:05:00Z",
                        "status": {
                            "abstractGameState": "Final",
                            "codedGameState": "F",
                            "statusCode": "F",
                            "detailedState": "Final",
                        },
                        "teams": {
                            "away": {"team": {"name": "Boston Red Sox"}, "score": 2},
                            "home": {"team": {"name": "New York Yankees"}, "score": 5},
                        },
                    },
                    {
                        "gamePk": 700003,
                        "officialDate": SLATE,
                        "gameDate": "2026-07-22T01:05:00Z",
                        "status": {
                            "abstractGameState": "Live",
                            "codedGameState": "I",
                            "statusCode": "I",
                            "detailedState": "In Progress",
                        },
                        "teams": {
                            "away": {"team": {"name": "Chicago Cubs"}, "score": 1},
                            "home": {"team": {"name": "St. Louis Cardinals"}, "score": 1},
                        },
                    },
                ],
            }
        ],
    }

    report = labels.validate_official_schedule_payload(payload, SLATE)

    assert report["officialGameCount"] == 2
    assert report["officialFinalCount"] == 1
    final, live = report["games"]
    assert final["officialGamePk"] == "700002"
    assert final["winner"] == "New York Yankees"
    assert final["sourcePayloadFingerprint"]
    assert live["officialGamePk"] == "700003"
    assert live["completed"] is False
    assert live["winner"] is None


def test_same_team_doubleheader_uses_distinct_official_game_pk_labels(monkeypatch):
    locks = [
        canonical_lock("700010", predicted_winner="New York Yankees"),
        canonical_lock("700011", predicted_winner="Boston Red Sox"),
    ]
    games = [
        official_game("700010", away_score=2, home_score=5),
        official_game("700011", away_score=7, home_score=3),
    ]
    table = install_inputs(monkeypatch, locks, games)

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is True
    assert report["labelCreatedCount"] == 2
    assert set(table.items) == {
        (f"{labels.LABEL_PK_PREFIX}{SLATE}", "GAME_PK#700010"),
        (f"{labels.LABEL_PK_PREFIX}{SLATE}", "GAME_PK#700011"),
    }
    assert all(item["correct"] is True for item in table.items.values())


def test_fallback_lock_preserves_only_verified_provider_alias(monkeypatch):
    lock = provider_alias_lock("700020", "odds-event-late")
    table = install_inputs(monkeypatch, [lock], [official_game("700020")])

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is True
    stored = next(iter(table.items.values()))
    assert stored["official_game_pk"] == "700020"
    assert stored["provider_event_id"] == "odds-event-late"
    assert stored["provider_identity_match_method"] == (
        rolling_audit.VERIFIED_PROVIDER_ALIAS_MATCH_METHOD
    )
    assert stored["provider_alias_crosswalk"]["uniqueBidirectionalCrosswalk"] is True
    assert stored["canonical_lock_sk"].endswith("mlb_statsapi:700020")


def test_vector_ineligible_lock_is_labeled_for_accuracy_but_not_training(monkeypatch):
    lock = canonical_lock("700021")
    lock.pop("frozenFeatureVector")
    lock["canonicalLockAuthority"].update(
        {
            "learningEligible": False,
            "exactLockVectorValidated": False,
            "trainingExclusionReasons": ["missing_frozen_feature_vector"],
        }
    )
    table = install_inputs(monkeypatch, [lock], [official_game("700021")])

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is True
    stored = next(iter(table.items.values()))
    assert stored["accuracy_eligible"] is True
    assert stored["training_eligible"] is False
    assert stored["training_exclusion_reasons"] == [
        "fundamentals_v2_missing",
        "missing_frozen_feature_vector",
    ]
    assert "frozen_feature_vector_fingerprint" not in stored


def test_complete_v2_snapshot_is_preserved_across_exact_label_join(monkeypatch):
    lock = complete_v2_lock("700022")
    frozen_snapshot = copy.deepcopy(lock["fundamentalsSnapshotV2"])
    game = official_game("700022")
    table = install_inputs(monkeypatch, [lock], [game])

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is True
    stored = next(iter(table.items.values()))
    assert stored["training_eligible"] is True
    assert stored["fundamentals_snapshot_v2_version"] == fundamentals_v2.VERSION
    assert stored["fundamentals_snapshot_v2_fingerprint"] == frozen_snapshot["fingerprint"]
    assert lock["fundamentalsSnapshotV2"] == frozen_snapshot

    loaded = labels.load_canonical_training_rows(
        slate_date=SLATE,
        official_fetcher=lambda slate: copy.deepcopy(official_report(game)),
    )
    assert loaded["ok"] is True
    assert loaded["rowCount"] == 1
    assert loaded["rows"][0]["trainingEligible"] is True
    assert loaded["rows"][0]["predictionPersistedAtUtc"] == lock[
        "predictionPersistedAtUtc"
    ]
    assert loaded["rows"][0]["fundamentalsSnapshotV2"] == frozen_snapshot
    assert loaded["rows"][0]["fundamentalsSnapshotV2Ref"] == lock[
        "fundamentalsSnapshotV2Ref"
    ]


def test_v2_lock_without_post_write_ack_is_gradeable_but_not_trainable(monkeypatch):
    lock = complete_v2_lock("700023")
    lock.pop("predictionPersistedAtUtc")
    table = install_inputs(monkeypatch, [lock], [official_game("700023")])

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is True
    stored = next(iter(table.items.values()))
    assert stored["accuracy_eligible"] is True
    assert stored["training_eligible"] is False
    assert "fundamentals_v2_prediction_persistence_proof_missing" in stored[
        "training_exclusion_reasons"
    ]


def test_current_stage_validator_rejection_never_writes_a_label(monkeypatch):
    item = {
        "PK": f"GAME_WINNERS#mlb#{SLATE}",
        "SK": "LOCKED#GAME#time#game",
        "data": canonical_lock("700030"),
    }
    monkeypatch.setattr(labels, "_query_partition", lambda table, pk, prefix: [item])
    monkeypatch.setattr(
        labels.rolling_audit,
        "_canonical_lock_item_errors",
        lambda candidate, slate: ["stage_fingerprint_envelope_payload_mismatch"],
    )

    valid, rejected = labels._validated_canonical_locks(SLATE)

    assert valid == []
    assert rejected[0]["errors"] == ["stage_fingerprint_envelope_payload_mismatch"]


def test_write_once_retry_is_idempotent(monkeypatch):
    lock = canonical_lock("700040")
    table = install_inputs(monkeypatch, [lock], [official_game("700040")])

    first = labels.settle_mlb_slate(SLATE)
    second = labels.settle_mlb_slate(SLATE)

    assert first["labelCreatedCount"] == 1
    assert second["ok"] is True
    assert second["labelIdempotentCount"] == 1
    assert len(table.items) == 1
    assert len(table.put_calls) == 2


def test_differing_official_final_is_a_fail_closed_correction_conflict(monkeypatch):
    lock = canonical_lock("700041")
    game = official_game("700041", away_score=2, home_score=5)
    table = install_inputs(monkeypatch, [lock], [game])
    assert labels.settle_mlb_slate(SLATE)["ok"] is True
    monkeypatch.setattr(
        labels,
        "fetch_official_schedule",
        lambda slate: official_report(
            official_game("700041", away_score=6, home_score=5)
        ),
    )

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is False
    assert report["status"] == "FAILED_CLOSED"
    assert report["labelConflictCount"] == 1
    assert report["labelWrites"][0]["status"] == "OFFICIAL_FINAL_CORRECTION_CONFLICT"
    assert next(iter(table.items.values()))["winner"] == "New York Yankees"


def test_settlement_does_not_mutate_lock_or_blank_vector_labels(monkeypatch):
    lock = canonical_lock("700050")
    before = copy.deepcopy(lock)
    install_inputs(monkeypatch, [lock], [official_game("700050")])

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is True
    assert report["immutablePregameRowsMutated"] is False
    assert lock == before
    assert lock["frozenFeatureVector"]["labels"] == {
        "homeWon": None,
        "pickCorrect": None,
    }


def test_stored_proof_revalidates_the_current_immutable_lock(monkeypatch):
    locks = [canonical_lock("700051")]
    install_inputs(monkeypatch, locks, [official_game("700051")])
    assert labels.settle_mlb_slate(SLATE)["ok"] is True

    valid = labels.settlement_proof_report(SLATE, fetch_scores=False)
    locks[0]["frozenFeatureVector"]["fingerprint"] = "changed-vector"
    rejected = labels.settlement_proof_report(SLATE, fetch_scores=False)

    assert valid["ok"] is True
    assert valid["storedCanonicalLabelCount"] == 1
    assert rejected["ok"] is False
    assert rejected["invalidStoredCanonicalLabelCount"] == 1
    assert "canonical_label_current_frozen_feature_vector_fingerprint_mismatch" in (
        rejected["invalidStoredCanonicalLabels"][0]["errors"]
    )


def test_stored_proof_with_zero_labels_is_not_a_false_green(monkeypatch):
    install_inputs(monkeypatch, [canonical_lock("700052")], [])

    report = labels.settlement_proof_report(SLATE, fetch_scores=False)

    assert report["ok"] is False
    assert report["verificationComplete"] is False
    assert report["status"] == "WAITING_FOR_FIRST_CANONICAL_FINAL_LABEL"
    assert report["storedCanonicalLabelCount"] == 0
    assert report["readOnlyProof"] is True

    requested_fetch = labels.settlement_proof_report(SLATE, fetch_scores=True)
    assert requested_fetch["ok"] is False
    assert requested_fetch["verificationComplete"] is False
    assert requested_fetch["dryRunCanSatisfyProof"] is False
    assert requested_fetch["officialFinalDryRun"]["ok"] is True


def test_terminal_no_prediction_outcome_is_excluded_not_labeled(monkeypatch):
    terminal = terminal_outcome("700060")
    table = install_inputs(
        monkeypatch,
        [],
        [official_game("700060")],
        terminals={"700060": terminal},
    )

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is True
    assert report["terminalNoPredictionExcludedCount"] == 1
    excluded = report["terminalNoPredictionExclusions"][0]
    assert excluded["accuracyEligible"] is False
    assert excluded["trainingEligible"] is False
    assert report["missingCanonicalLockCount"] == 0
    assert report["labelWriteCount"] == 0
    assert table.items == {}


def test_lock_and_no_prediction_terminal_conflict_fails_closed(monkeypatch):
    terminal = terminal_outcome("700061")
    table = install_inputs(
        monkeypatch,
        [canonical_lock("700061")],
        [official_game("700061")],
        terminals={"700061": terminal},
    )

    report = labels.settle_mlb_slate(SLATE)

    assert report["ok"] is False
    assert report["lockTerminalConflictCount"] == 1
    assert report["identityRejections"][0]["reason"] == (
        "CANONICAL_LOCK_AND_NO_PREDICTION_TERMINAL_CONFLICT"
    )
    assert report["labelWriteCount"] == 0
    assert table.items == {}


def test_scheduler_routes_settlement_through_canonical_authority(monkeypatch):
    import mlb_results_scheduler as scheduler

    monkeypatch.setattr(
        scheduler.canonical_settlement,
        "settle_mlb_slate",
        lambda **kwargs: {
            "ok": True,
            "slateDateEt": SLATE,
            "authoritativeSettlement": True,
        },
    )
    monkeypatch.setattr(
        scheduler,
        "legacy_settle_mlb_slate",
        lambda **kwargs: {"ok": True, "overall_status": "DIAGNOSTIC"},
    )

    response = scheduler.lambda_handler(
        {
            "httpMethod": "POST",
            "path": "/v1/results/mlb/settlement",
            "body": json.dumps({"date": SLATE, "fetch_scores": True}),
        },
        None,
    )
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["authoritativeSettlement"] is True
    assert body["settlementAuthority"] == "CANONICAL_IMMUTABLE_LOCK_OFFICIAL_GAME_PK"
    assert body["legacyDiagnosticCompatibility"]["authoritative"] is False
    assert body["legacyDiagnosticCompatibility"]["executed"] is False


def test_scheduler_never_calls_mutating_legacy_settlement_on_schedule(monkeypatch):
    import mlb_results_scheduler as scheduler

    monkeypatch.setattr(
        scheduler.canonical_settlement,
        "settle_recent_mlb_slates",
        lambda **kwargs: {
            "ok": True,
            "slateDateEt": SLATE,
            "authoritativeSettlement": True,
        },
    )

    def forbidden_legacy(**kwargs):
        raise AssertionError("scheduled canonical settlement called legacy writer")

    monkeypatch.setattr(scheduler, "legacy_settle_mlb_slate", forbidden_legacy)
    monkeypatch.setattr(
        scheduler,
        "build_signal_learning_report",
        lambda **kwargs: {"ok": True, "diagnostic": True},
    )
    monkeypatch.setattr(
        scheduler,
        "build_result_signals",
        lambda *args, **kwargs: {"ok": True, "diagnostic": True},
    )

    response = scheduler.lambda_handler(
        {"sport": "mlb", "days_from": 3, "run": "results_pull_15m"},
        None,
    )
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["legacyDiagnosticCompatibility"] == {
        "ok": True,
        "executed": False,
        "authoritative": False,
        "status": "LEGACY_DIAGNOSTIC_DISABLED",
    }


def test_results_scheduler_can_read_current_immutable_snapshots_in_sam():
    template = (ROOT / "template.yaml").read_text()
    start = template.index("  MLBResultsSchedulerFunction:\n")
    end = template.index("\n  MLBMLTrainingFunction:\n", start)
    block = template[start:end]

    assert "Handler: mlb_results_scheduler.lambda_handler" in block
    assert (
        "- DynamoDBReadPolicy:\n"
        "            TableName: !Ref SnapshotsTable"
    ) in block
    assert (
        "- DynamoDBCrudPolicy:\n"
        "            TableName: !Ref OutcomesTable"
    ) in block


def test_recent_slate_scheduler_attempts_each_date_independently(monkeypatch):
    attempted = []
    monkeypatch.setattr(
        labels,
        "_now",
        lambda: datetime(2026, 7, 22, 5, 0, tzinfo=timezone.utc),
    )

    def settle(**kwargs):
        attempted.append(kwargs["slate_date"])
        return {
            "ok": kwargs["slate_date"] != "2026-07-20",
            "slateDateEt": kwargs["slate_date"],
            "status": "COMPLETE" if kwargs["slate_date"] != "2026-07-20" else "FAILED_CLOSED",
        }

    monkeypatch.setattr(labels, "settle_mlb_slate", settle)

    report = labels.settle_recent_mlb_slates(days_from=3)

    assert attempted == ["2026-07-22", "2026-07-21", "2026-07-20"]
    assert report["ok"] is True
    assert report["recentSlateAllOk"] is False
    assert report["recentSlateFailureCount"] == 1
    assert report["recentSlateFailures"][0]["slateDateEt"] == "2026-07-20"


def test_training_loader_requires_complete_final_slate_and_exact_labels(monkeypatch):
    lock = canonical_lock("700070")
    game = official_game("700070")
    install_inputs(monkeypatch, [lock], [game])

    pregame = labels.load_canonical_locked_rows_without_labels(slate_date=SLATE)
    assert pregame["ok"] is True
    assert pregame["rowCount"] == 1
    assert pregame["rows"][0]["labelStatus"] == "PREGAME_UNLABELED"
    assert "winner" not in pregame["rows"][0]

    assert labels.settle_mlb_slate(SLATE)["ok"] is True
    loaded = labels.load_canonical_training_rows(
        slate_date=SLATE,
        official_fetcher=lambda slate: copy.deepcopy(official_report(game)),
    )

    assert loaded["ok"] is True
    assert loaded["finalizedSlateDates"] == [SLATE]
    assert loaded["rowCount"] == 1
    assert loaded["rows"][0]["officialGamePk"] == "700070"
    assert loaded["rows"][0]["winner"] == "New York Yankees"
    assert loaded["rows"][0]["slateFinalized"] is True
    # The outcome is valid for grading, but a legacy lock without the new
    # source-complete fundamentals vector cannot enter training.
    assert loaded["rows"][0]["trainingEligible"] is False
    assert "fundamentals_v2_missing" in loaded["rows"][0]["trainingExclusionReasons"]

    no_longer_pregame = labels.load_canonical_locked_rows_without_labels(
        slate_date=SLATE
    )
    assert no_longer_pregame["rowCount"] == 0
    assert no_longer_pregame["ok"] is False


def test_training_loader_emits_no_rows_until_every_official_game_is_final(monkeypatch):
    final = official_game("700071")
    live = official_game(
        "700072", completed=False, away_score=None, home_score=None
    )
    install_inputs(
        monkeypatch,
        [canonical_lock("700071"), canonical_lock("700072")],
        [final, live],
    )
    assert labels.settle_mlb_slate(SLATE)["ok"] is True

    report = labels.load_canonical_training_rows(
        slate_date=SLATE,
        official_fetcher=lambda slate: copy.deepcopy(official_report(final, live)),
    )

    assert report["ok"] is False
    assert report["finalizedSlateDates"] == []
    assert report["rows"] == []
    assert report["slates"][0]["slateFinalized"] is False
