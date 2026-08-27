from __future__ import annotations

import json

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import verify_mlb_postdeploy_scheduled_pull as observer
from scripts.verify_mlb_authority_response import AUTHORITY_CONTRACT


def _event(stream, stamp, message):
    return {
        "logStreamName": stream,
        "timestamp": int(stamp.timestamp() * 1000),
        "message": message,
    }


def _row(game_id, start, *, winner=None, locked=False, status="OPEN_PRE_LOCK"):
    return {
        "gameId": game_id,
        "gameIdentity": game_id,
        "commenceTime": start.isoformat().replace("+00:00", "Z"),
        "predictedWinner": winner,
        "lockedPrediction": locked,
        "lockStatus": status,
        "officialPredictionStatus": status,
        "perGameCanonicalLock": {"status": status},
    }


def _no_champion_payload():
    return {
        "ok": False,
        "sport": "mlb",
        "status": "NO_QUALIFIED_CHAMPION",
        "error": "NO_QUALIFIED_CHAMPION",
        "publicationClosed": True,
        "productionSelectionAllowed": False,
        "model_version": None,
        "primaryAlgorithm": None,
        "primaryAlgorithmActive": False,
        "soleProductionAlgorithm": None,
        "game_winner_model": None,
        "requestedAuthority": "AWS_ML_PROSPECTIVE_R7",
        "qualifiedChampionRequired": True,
        "qualifiedChampionPresent": False,
        "r7ChampionQualified": False,
        "r7DeploymentIdentity": None,
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "legacyRecommendationAuthority": False,
        "retiredAuthoritySuppressed": True,
        "retiredV15_10Eligible": False,
        "automaticWagerAllowed": False,
        "rowLevelAutomaticWagerAllowed": False,
        "authorityContractVersion": AUTHORITY_CONTRACT,
        "winner_predictions": [],
        "predictions": [],
        "count": 0,
    }


def test_select_fresh_pull_uses_first_slot_after_baseline():
    baseline = datetime(2026, 7, 23, 2, 0, tzinfo=timezone.utc)
    rows = [
        {"record_type": "pull_run", "SK": "PULL#SLOT#2026-07-23T02:00:00+00:00"},
        {"record_type": "pull_run", "SK": "PULL#SLOT#2026-07-23T02:30:00+00:00"},
        {"record_type": "pull_run", "SK": "PULL#SLOT#2026-07-23T02:15:00+00:00"},
    ]

    selected = observer.select_fresh_pull(rows, baseline)

    assert selected["SK"].endswith("02:15:00+00:00")


def test_matching_invocation_completion_binds_start_and_report_to_same_request():
    pull_at = datetime(2026, 7, 23, 2, 15, 10, tzinfo=timezone.utc)
    events = [
        _event(
            "stream-a",
            pull_at - timedelta(seconds=5),
            "START RequestId: request-a Version: $LATEST",
        ),
        _event(
            "stream-a",
            pull_at + timedelta(seconds=120),
            "REPORT RequestId: request-a Duration: 120000 ms",
        ),
        _event(
            "stream-b",
            pull_at + timedelta(seconds=1),
            "REPORT RequestId: unrelated Duration: 1 ms",
        ),
    ]

    result = observer.matching_invocation_completion(events, pull_at)

    assert result["complete"] is True
    assert result["failed"] is False
    assert result["requestId"] == "request-a"


def test_matching_invocation_completion_fails_closed_on_protected_writer_error():
    pull_at = datetime(2026, 7, 23, 2, 15, 10, tzinfo=timezone.utc)
    events = [
        _event(
            "stream-a",
            pull_at - timedelta(seconds=5),
            "START RequestId: request-a Version: $LATEST",
        ),
        _event(
            "stream-a",
            pull_at + timedelta(seconds=100),
            "MLB_SCHEDULED_PULL_FAILED:injected",
        ),
        _event(
            "stream-a",
            pull_at + timedelta(seconds=101),
            "REPORT RequestId: request-a Duration: 101000 ms",
        ),
    ]

    result = observer.matching_invocation_completion(events, pull_at)

    assert result["complete"] is False
    assert result["failed"] is True
    assert "MLB_SCHEDULED_PULL_FAILED" in result["failureMessage"]


def test_disposition_requires_every_open_candidate_to_have_persisted_winner():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(hours=4)
    status = [_row("g1", start, status="OPEN_PRE_LOCK")]
    predictions = [_row("g1", start, winner=None, status="OPEN_PRE_LOCK")]

    result = observer.classify_dispositions(status, predictions, now=now)

    assert result["complete"] is False
    assert "g1:open_prelock_prediction_missing" in result["errors"]


def test_disposition_accepts_locked_winner_and_explicit_no_backfill_lifecycle():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(hours=4)
    status = [
        _row(
            "g1",
            past,
            winner="Home",
            locked=True,
            status="OFFICIAL_LOCKED_PREDICTION",
        ),
        _row(
            "g2",
            past,
            winner=None,
            locked=False,
            status="MISSED_NOT_BACKFILLED",
        ),
    ]
    predictions = [
        _row(
            "g1",
            past,
            winner="Home",
            locked=True,
            status="OFFICIAL_LOCKED_PREDICTION",
        ),
        _row(
            "g2",
            past,
            winner=None,
            locked=False,
            status="MISSED_NOT_BACKFILLED",
        ),
    ]

    result = observer.classify_dispositions(status, predictions, now=now)

    assert result == {
        "gameCount": 2,
        "candidateCount": 0,
        "storedCandidateCount": 0,
        "canonicalLockedCount": 1,
        "lifecycleCount": 1,
        "dispositionCount": 2,
        "complete": True,
        "errors": [],
    }


def test_postdeploy_policy_accepts_exact_no_champion_503_without_public_winners():
    now = datetime(2026, 8, 27, 2, 0, tzinfo=timezone.utc)
    past = now - timedelta(hours=4)
    status_rows = [
        _row(
            "g1",
            past,
            winner="Home",
            locked=True,
            status="OFFICIAL_LOCKED_PREDICTION",
        )
    ]

    result = observer.reconcile_public_prediction_lifecycle(
        503,
        _no_champion_payload(),
        status_rows,
        1,
        now=now,
    )

    assert result["authority"]["state"] == "NO_QUALIFIED_CHAMPION"
    assert result["publicWinnerCount"] == 0
    assert result["publicPayload"]["predictions"] == []
    assert result["lifecyclePayload"]["predictions"][0]["predictedWinner"] == "Home"
    assert result["statusProjectionPersisted"] is False


def test_postdeploy_source_accepts_only_verified_200_or_503_prediction_contract():
    source = Path(observer.__file__).read_text(encoding="utf-8")

    assert "prediction_response = fetch_json_response(" in source
    assert "accepted_http_statuses=(200, 503)" in source
    assert "reconcile_public_prediction_lifecycle(" in source
    assert 'public_reconciliation["authority"].get("state")' in source
    assert 'public_reconciliation.get("publicWinnerCount")' in source
    assert '"statusProjectionPersisted": False' in source

def test_direct_storage_evidence_passes_with_closed_public_authority():
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(hours=4)
    status_rows = [_row("g1", start, winner="status-must-not-count")]
    public = observer.reconcile_public_prediction_lifecycle(
        503,
        _no_champion_payload(),
        status_rows,
        1,
        now=now,
    )
    direct_rows = [
        {
            "gameId": "g1",
            "predictedWinner": "Home",
            "commenceTime": start.isoformat(),
        }
    ]

    storage = observer._storage_disposition_rows(status_rows, direct_rows)
    result = observer.classify_dispositions(status_rows, storage["rows"], now=now)

    assert public["publicWinnerCount"] == 0
    assert public["authority"]["state"] == "NO_QUALIFIED_CHAMPION"
    assert result["complete"] is True
    assert result["storedCandidateCount"] == 1


def test_status_projection_winner_cannot_substitute_for_missing_direct_storage():
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(hours=4)
    status_rows = [_row("g1", start, winner="Home")]

    storage = observer._storage_disposition_rows(status_rows, [])
    result = observer.classify_dispositions(status_rows, storage["rows"], now=now)

    assert result["complete"] is False
    assert "g1:open_prelock_prediction_missing" in result["errors"]


def test_missing_direct_storage_is_allowed_only_for_terminal_lifecycle():
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(hours=4)
    locked = [
        _row(
            "locked",
            past,
            winner="status-must-not-count",
            locked=True,
            status="OFFICIAL_LOCKED_PREDICTION",
        )
    ]
    terminal = [
        _row(
            "terminal",
            past,
            winner="status-must-not-count",
            locked=False,
            status="MISSED_NOT_BACKFILLED",
        )
    ]

    locked_storage = observer._storage_disposition_rows(locked, [])
    terminal_storage = observer._storage_disposition_rows(terminal, [])

    locked_result = observer.classify_dispositions(
        locked, locked_storage["rows"], now=now
    )
    terminal_result = observer.classify_dispositions(
        terminal, terminal_storage["rows"], now=now
    )
    assert locked_result["complete"] is False
    assert "locked:canonical_locked_winner_missing" in locked_result["errors"]
    assert terminal_result["complete"] is True


def test_strongly_consistent_prediction_query_is_paginated_and_game_scoped():
    class FakeTable:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "Items": [
                        {
                            "record_type": observer.PREDICTION_RECORD_TYPE,
                            "data": {
                                "game_id": "g1",
                                "predicted_winner": "Home",
                            },
                        }
                    ],
                    "LastEvaluatedKey": {"PK": "p", "SK": "GAME#g1"},
                }
            return {
                "Items": [
                    {
                        "record_type": "mlb_immutable_prelock_prediction_snapshot",
                        "gameId": "ignored",
                    },
                    {
                        "record_type": observer.PREDICTION_RECORD_TYPE,
                        "game_identity": "g2",
                        "predicted_winner": "Away",
                    },
                ]
            }

    table = FakeTable()
    rows = observer._query_live_prediction_items(
        table, "GAME_WINNERS#mlb#2026-08-27"
    )

    assert [(row["gameId"], row["predictedWinner"]) for row in rows] == [
        ("g1", "Home"),
        ("g2", "Away"),
    ]
    assert len(table.calls) == 2
    assert all(call["ConsistentRead"] is True for call in table.calls)
    assert table.calls[1]["ExclusiveStartKey"] == {"PK": "p", "SK": "GAME#g1"}
    expression = table.calls[0]["KeyConditionExpression"].get_expression()
    assert expression["operator"] == "AND"
    sort_expression = expression["values"][1].get_expression()
    assert sort_expression["operator"] == "begins_with"
    assert sort_expression["values"][1] == "GAME#"


def test_duplicate_direct_prediction_identity_fails_closed():
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(hours=4)
    status = [_row("g1", start)]
    with pytest.raises(RuntimeError, match="live_prediction_storage_identity_ambiguous"):
        observer._storage_disposition_rows(
            status,
            [
                {
                    "gameId": "g1",
                    "predictedWinner": "Home",
                    "commenceTime": start.isoformat(),
                },
                {
                    "gameId": "g1",
                    "predictedWinner": "Away",
                    "commenceTime": start.isoformat(),
                },
            ],
        )


def test_storage_identity_aliases_match_without_trusting_public_projection():
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(hours=4)
    status = [_row("provider:g1", start)]
    direct = [
        {
            "provider_event_id": "g1",
            "predicted_winner": "Home",
            "commence_time": start.isoformat(),
        }
    ]

    storage = observer._storage_disposition_rows(status, direct)
    result = observer.classify_dispositions(status, storage["rows"], now=now)

    assert storage["matchedCount"] == 1
    assert result["complete"] is True


def test_unmatched_direct_rows_are_reported_without_masking_official_coverage():
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(hours=4)
    status = [_row("g1", start)]
    direct = [
        {
            "gameId": "g1",
            "predictedWinner": "Home",
            "commenceTime": start.isoformat(),
        },
        {
            "gameId": "cancelled",
            "predictedWinner": "Away",
            "commenceTime": start.isoformat(),
        },
    ]

    storage = observer._storage_disposition_rows(status, direct)
    result = observer.classify_dispositions(status, storage["rows"], now=now)

    assert result["complete"] is True
    assert storage["unmatchedCount"] == 1
    assert storage["unmatchedIdentities"] == ["cancelled"]


def test_stale_rescheduled_row_cannot_satisfy_current_storage_evidence():
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    current_start = now + timedelta(hours=4)
    stale_start = current_start - timedelta(hours=1)
    status = [_row("g1", current_start)]
    direct = [
        {
            "gameId": "g1",
            "predictedWinner": "Away",
            "commenceTime": stale_start.isoformat(),
        },
        {
            "gameId": "g1",
            "predictedWinner": "Home",
            "commenceTime": current_start.isoformat(),
        },
    ]

    storage = observer._storage_disposition_rows(status, direct)
    result = observer.classify_dispositions(status, storage["rows"], now=now)

    assert result["complete"] is True
    assert storage["matchedCount"] == 1
    assert storage["unmatchedCount"] == 1
    assert storage["rows"][0]["predictedWinner"] == "Home"


def test_main_writes_structured_observer_failure_before_returning_nonzero(
    monkeypatch, tmp_path
):
    output = tmp_path / "observer.json"

    def fail_observe(**_kwargs):
        raise RuntimeError("injected_observer_failure:details")

    monkeypatch.setattr(observer, "observe", fail_observe)
    code = observer.main(
        [
            "--target-deploy-sha",
            "abc123",
            "--output",
            str(output),
            "--max-wait-seconds",
            "60",
            "--poll-seconds",
            "1",
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert code == 1
    assert payload["ok"] is False
    assert payload["status"] == "OBSERVATION_FAILED"
    assert payload["failure"]["code"] == "injected_observer_failure"
    assert payload["manualPullInvoked"] is False


def test_postdeploy_workflow_preserves_structured_observer_failure():
    source = Path(
        ".github/workflows/mlb-post-deploy-fix-verification.yml"
    ).read_text(encoding="utf-8")

    assert "deployed_at = None" in source
    assert "deployed_at is None or stamp >= deployed_at" in source
    assert "scheduled_pull_observation_failed" in source
    assert "'scheduledPullObservationFailure': invocation.get('failure')" in source

