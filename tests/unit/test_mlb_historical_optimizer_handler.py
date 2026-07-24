from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))


class _FakeBoto3:
    @staticmethod
    def resource(name):
        return SimpleNamespace(Table=lambda table_name: None)

    @staticmethod
    def client(name):
        return SimpleNamespace()


def _load_handler():
    module_name = "mlb_historical_optimizer_handler_unit"
    path = HELLO / "mlb_historical_optimizer_handler.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    originals = {
        name: sys.modules.get(name)
        for name in ("boto3", "mlb_canonical_final_labels_v1")
    }
    try:
        sys.modules["boto3"] = _FakeBoto3()
        sys.modules["mlb_canonical_final_labels_v1"] = SimpleNamespace()
        assert spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        for name, value in originals.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module


handler = _load_handler()


def _grid(day="2025-07-01"):
    return handler.optimizer.build_snapshot_grid(
        day,
        datetime(2025, 7, 1, 19, 10, tzinfo=ZoneInfo("America/New_York")),
    )


def test_authorized_paid_request_must_match_fingerprinted_exact_slate_ledger():
    grid = _grid()
    slates = [
        {
            "slateDateEt": "2025-07-01",
            "historicalRequestCount": len(grid.timestamps_utc),
            "firstRequestUtc": grid.timestamps_utc[0],
            "lastRequestUtc": grid.timestamps_utc[-1],
        }
    ]
    plan = {
        "startDate": "2025-07-01",
        "endDate": "2025-07-01",
        "plannedThroughDate": "2025-07-01",
        "targetSettledGames": 1600,
        "plannedOfficialGames": 15,
        "plannedCompleteSlateDays": 1,
        "historicalRequestCount": len(grid.timestamps_utc),
        "estimatedCredits": len(grid.timestamps_utc) * 10,
        "maximumCredits": 120000,
        "snapshotStartAtEt": "01:00",
        "snapshotIntervalMinutes": 15,
        "snapshotGridEndsAt": "last_game_t_minus_45",
        "perGameFeatureCutoff": "each_game_t_minus_45",
        "maximumAuthorizedOfficialGames": 15,
        "maximumOptimizationRounds": 6,
        "freshAuditIncrementGames": 250,
        "slateLedgerDigest": handler._sha256(handler._json_bytes(slates)),
        "completeDateRangeLedger": True,
        "planningErrorCount": 0,
        "rejectedDates": [],
        "slates": slates,
    }
    plan["fingerprint"] = handler._plan_fingerprint(plan)
    state = {
        "paidBackfillAuthorized": True,
        "authorizedPlanFingerprint": plan["fingerprint"],
        "plan": plan,
    }
    assert handler._authorized_plan_slate(state, "2025-07-01", grid) == slates[0]

    plan["slates"][0]["lastRequestUtc"] = "2099-01-01T00:00:00Z"
    with pytest.raises(handler.OrchestrationError, match="content changed|ledger changed"):
        handler._authorized_plan_slate(state, "2025-07-01", grid)


def test_fresh_audit_window_uses_only_strictly_later_completed_slates():
    state = {
        "freshAuditStartDate": "2025-07-03",
        "completedSlates": [
            {"slateDateEt": "2025-07-01", "eligibleGameCount": 15},
            {"slateDateEt": "2025-07-03", "eligibleGameCount": 14},
            {"slateDateEt": "2025-07-04", "eligibleGameCount": 16},
        ],
    }
    dates, games = handler._fresh_audit_window(state)
    assert dates == ["2025-07-03", "2025-07-04"]
    assert games == 30


def test_evaluated_untouched_audit_dates_can_never_be_reused():
    state = {"optimizationRound": 0, "evaluatedAuditWindows": []}
    result = {
        "holdoutDefinition": {"dates": ["2025-07-01", "2025-07-02"]},
        "promotionGate": {
            "passed": False,
            "untouchedHoldoutGameCount": 30,
            "untouchedHoldoutMinimumDailyAccuracy": 0.70,
            "untouchedHoldoutMeanDailyAccuracy": 0.75,
        },
    }
    artifact = {"bucket": "bucket", "key": "one.json", "versionId": "1"}
    handler._record_evaluated_audit_window(state, result, artifact)
    assert state["evaluatedAuditWindows"][0]["dates"] == ["2025-07-01", "2025-07-02"]

    state["optimizationRound"] = 1
    with pytest.raises(handler.OrchestrationError, match="reused"):
        handler._record_evaluated_audit_window(
            state,
            {
                "holdoutDefinition": {"dates": ["2025-07-02", "2025-07-03"]},
                "promotionGate": result["promotionGate"],
            },
            {"bucket": "bucket", "key": "two.json", "versionId": "1"},
        )


def test_planning_covers_full_authorized_date_range_not_only_initial_target(monkeypatch):
    monkeypatch.setattr(handler, "TARGET_GAMES", 1)
    monkeypatch.setattr(handler, "START_DATE", "2025-07-01")
    monkeypatch.setattr(handler, "END_DATE", "2025-07-03")
    monkeypatch.setattr(handler, "MAX_CREDITS", 120000)
    monkeypatch.setattr(handler, "QUOTA_RESERVE", 100)
    monkeypatch.setattr(handler, "ARTIFACTS_BUCKET", "bucket")
    monkeypatch.setattr(handler, "ODDS_API_KEY", "secret")

    def finals(day):
        return (
            {
                "officialGameCount": 1,
                "officialFinalCount": 1,
                "games": [
                    {
                        "gameDate": f"{day}T23:10:00Z",
                    }
                ],
            },
            {"bucket": "bucket", "key": f"{day}.json"},
        )

    monkeypatch.setattr(handler, "_load_or_fetch_finals", finals)
    monkeypatch.setattr(
        handler,
        "_quota_status",
        lambda: {"x-requests-remaining": 999999, "x-requests-used": 0},
    )
    state = {
        "phase": "PLANNING",
        "startDate": "2025-07-01",
        "endDate": "2025-07-03",
        "targetSettledGames": 1,
        "maximumCredits": 120000,
        "completedSlates": [],
        "networkRequestCount": 0,
        "paidBackfillAuthorized": False,
    }
    planned = handler._plan(state)
    plan = planned["plan"]
    assert [row["slateDateEt"] for row in plan["slates"]] == [
        "2025-07-01",
        "2025-07-02",
        "2025-07-03",
    ]
    assert plan["targetSettledGames"] == 1
    assert plan["maximumAuthorizedOfficialGames"] == 3
    assert plan["plannedThroughDate"] == "2025-07-03"
    assert plan["completeDateRangeLedger"] is True
    assert plan["planningErrorCount"] == 0
    assert plan["rejectedDates"] == []
    assert plan["slateLedgerDigest"] == handler._sha256(
        handler._json_bytes(plan["slates"])
    )
    assert plan["fingerprint"] == handler._plan_fingerprint(plan)


def test_planning_blocks_paid_authorization_when_any_configured_date_is_unresolved(monkeypatch):
    monkeypatch.setattr(handler, "TARGET_GAMES", 1)
    monkeypatch.setattr(handler, "START_DATE", "2025-07-01")
    monkeypatch.setattr(handler, "END_DATE", "2025-07-03")
    monkeypatch.setattr(handler, "MAX_CREDITS", 120000)
    monkeypatch.setattr(handler, "QUOTA_RESERVE", 100)
    monkeypatch.setattr(handler, "ARTIFACTS_BUCKET", "bucket")
    monkeypatch.setattr(handler, "ODDS_API_KEY", "secret")

    def finals(day):
        if day == "2025-07-02":
            raise handler.OrchestrationError("temporary official schedule failure")
        return (
            {
                "officialGameCount": 1,
                "officialFinalCount": 1,
                "games": [{"gameDate": f"{day}T23:10:00Z"}],
            },
            {"bucket": "bucket", "key": f"{day}.json"},
        )

    monkeypatch.setattr(handler, "_load_or_fetch_finals", finals)
    monkeypatch.setattr(
        handler,
        "_quota_status",
        lambda: {"x-requests-remaining": 999999, "x-requests-used": 0},
    )
    state = {
        "phase": "PLANNING",
        "startDate": "2025-07-01",
        "endDate": "2025-07-03",
        "targetSettledGames": 1,
        "maximumCredits": 120000,
        "completedSlates": [],
        "networkRequestCount": 0,
        "paidBackfillAuthorized": False,
    }
    planned = handler._plan(state)
    plan = planned["plan"]
    assert planned["phase"] == "PLAN_BLOCKED_INCOMPLETE_LEDGER"
    assert plan["completeDateRangeLedger"] is False
    assert plan["planningErrorCount"] == 1
    assert plan["rejectedDates"][0]["slateDateEt"] == "2025-07-02"
    with pytest.raises(handler.OrchestrationError, match="passing credit plan"):
        handler._authorize_backfill(
            planned,
            {
                "confirm": handler.AUTHORIZATION_CONFIRMATION,
                "planFingerprint": plan["fingerprint"],
            },
        )


def test_planning_cannot_replace_an_authorized_or_partially_spent_ledger():
    with pytest.raises(handler.OrchestrationError, match="cannot replace"):
        handler._plan(
            {
                "phase": "BACKFILLING",
                "targetSettledGames": 1600,
                "paidBackfillAuthorized": True,
                "networkRequestCount": 1,
                "completedSlates": [],
            }
        )


def _valid_champion_payload():
    policy = handler.policy_runtime
    gate = {
        "version": policy.PROMOTION_GATE_VERSION,
        "passed": True,
        "settledGameCount": 1400,
        "trainingGameCount": 1000,
        "walkForwardGameCount": 200,
        "untouchedHoldoutGameCount": 200,
        "walkForwardDayCount": 20,
        "untouchedHoldoutDayCount": 15,
        "walkForwardMinimumDailyAccuracy": 0.80,
        "walkForwardMeanDailyAccuracy": 0.84,
        "untouchedHoldoutMinimumDailyAccuracy": 0.80,
        "untouchedHoldoutMeanDailyAccuracy": 0.83,
        "walkForwardSlateCoverage": 1.0,
        "untouchedHoldoutSlateCoverage": 1.0,
        "holdoutWasUntouchedDuringSearch": True,
        "chronologicalWholeSlateSplits": True,
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
        "overfitChecksPassed": True,
    }
    artifact = {
        "bucket": "bucket",
        "key": "artifact.json",
        "versionId": "1",
        "sha256": "a" * 64,
    }
    return {
        "version": policy.VERSION,
        "recordType": policy.CHAMPION_RECORD_TYPE,
        "liveAuthorityEnabled": True,
        "shadowOnly": False,
        "policy": dict(policy.BASELINE_POLICY),
        "policyDigest": policy.policy_digest(policy.BASELINE_POLICY),
        "promotionGate": gate,
        "artifact": artifact,
        "activatedAtUtc": "2026-07-24T04:00:00+00:00",
    }


def test_first_promotion_atomically_writes_champion_and_historical_only_cutover(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = []

        def transact_write_items(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    monkeypatch.setattr(
        handler,
        "_DDB",
        SimpleNamespace(meta=SimpleNamespace(client=client)),
    )
    monkeypatch.setattr(handler, "TARGET_TABLE", "snapshots-table")
    monkeypatch.setattr(handler, "_existing_champion", lambda: None)
    monkeypatch.setattr(handler, "_get_item", lambda sk, **kwargs: None)

    handler._write_champion(_valid_champion_payload())
    assert len(client.calls) == 1
    writes = client.calls[0]["TransactItems"]
    assert len(writes) == 2
    sks = {entry["Put"]["Item"]["SK"]["S"] for entry in writes}
    assert sks == {
        handler.policy_runtime.CHAMPION_SK,
        handler.policy_runtime.CUTOVER_SK,
    }
    cutover_put = next(
        entry["Put"]
        for entry in writes
        if entry["Put"]["Item"]["SK"]["S"] == handler.policy_runtime.CUTOVER_SK
    )
    assert cutover_put["ConditionExpression"] == "attribute_not_exists(PK)"
    assert cutover_put["Item"]["PK"]["S"] == handler.policy_runtime.CUTOVER_PK
    champion_put = next(
        entry["Put"]
        for entry in writes
        if entry["Put"]["Item"]["SK"]["S"] == handler.policy_runtime.CHAMPION_SK
    )
    assert champion_put["ConditionExpression"] == "attribute_not_exists(PK)"
    assert champion_put["Item"]["PK"]["S"] == handler.policy_runtime.CHAMPION_PK
    assert handler.policy_runtime.CUTOVER_PK != handler.policy_runtime.CHAMPION_PK
    assert handler.policy_runtime.CHAMPION_PK != handler.STATE_PK
    assert handler.policy_runtime.CUTOVER_PK != handler.STATE_PK
    assert client.calls[0]["ClientRequestToken"].startswith("mlb-historical-cutover-")
    assert cutover_put["Item"]["legacyFallbackAllowed"]["BOOL"] is False


def test_later_champion_update_preserves_existing_write_once_cutover(monkeypatch):
    payload = _valid_champion_payload()
    cutover = handler.policy_runtime.build_cutover_payload(payload)
    cutover_item = {
        "record_type": handler.policy_runtime.CUTOVER_RECORD_TYPE,
        "data": cutover,
    }

    class Table:
        def __init__(self):
            self.items = []

        def put_item(self, **kwargs):
            self.items.append(kwargs["Item"])

    table = Table()
    monkeypatch.setattr(handler, "_existing_champion", lambda: None)
    monkeypatch.setattr(
        handler,
        "_get_item",
        lambda sk, **kwargs: (
            cutover_item
            if sk == handler.policy_runtime.CUTOVER_SK
            and kwargs.get("pk") == handler.policy_runtime.CUTOVER_PK
            else None
        ),
    )
    monkeypatch.setattr(handler, "_table", lambda: table)

    handler._write_champion(payload)
    assert len(table.items) == 1
    assert table.items[0]["SK"] == handler.policy_runtime.CHAMPION_SK


def test_first_promotion_is_blocked_without_atomic_transaction_client(monkeypatch):
    monkeypatch.setattr(handler, "_DDB", SimpleNamespace())
    monkeypatch.setattr(handler, "TARGET_TABLE", "snapshots-table")
    monkeypatch.setattr(handler, "_existing_champion", lambda: None)
    monkeypatch.setattr(handler, "_existing_cutover", lambda: None)
    with pytest.raises(handler.OrchestrationError, match="transaction client is unavailable"):
        handler._write_champion(_valid_champion_payload())


def test_invalid_existing_champion_is_not_treated_as_absent(monkeypatch):
    invalid = {
        "record_type": handler.policy_runtime.CHAMPION_RECORD_TYPE,
        "data": {"version": "tampered"},
    }
    monkeypatch.setattr(
        handler,
        "_get_item",
        lambda sk, **kwargs: (
            invalid
            if sk == handler.policy_runtime.CHAMPION_SK
            and kwargs.get("pk") == handler.policy_runtime.CHAMPION_PK
            else None
        ),
    )
    with pytest.raises(handler.OrchestrationError, match="existing historical champion is invalid"):
        handler._existing_champion()


def test_status_proves_historical_only_authority_after_atomic_cutover(monkeypatch):
    champion = _valid_champion_payload()
    cutover = handler.policy_runtime.build_cutover_payload(champion)
    state = {"phase": "PROMOTED"}

    def item(sk, **kwargs):
        if (
            sk == handler.policy_runtime.CHAMPION_SK
            and kwargs.get("pk") == handler.policy_runtime.CHAMPION_PK
        ):
            return {
                "record_type": handler.policy_runtime.CHAMPION_RECORD_TYPE,
                "data": champion,
            }
        if (
            sk == handler.policy_runtime.CUTOVER_SK
            and kwargs.get("pk") == handler.policy_runtime.CUTOVER_PK
        ):
            return {
                "record_type": handler.policy_runtime.CUTOVER_RECORD_TYPE,
                "data": cutover,
            }
        return None

    monkeypatch.setattr(handler, "_load_state", lambda: state)
    monkeypatch.setattr(handler, "_get_item", item)
    status = handler._status()
    assert status["championValidation"]["ok"] is True
    assert status["cutoverValidation"]["ok"] is True
    assert status["historicalOnlyCutover"]["historicalOnly"] is True
    assert status["historicalOnlyCutover"]["legacyFallbackAllowed"] is False
    assert status["productionAuthority"]["historicalChampionOnly"] is True
    assert status["productionAuthority"]["incumbentProductionAuthorityDestroyed"] is True


def test_sam_role_is_append_only_for_evidence_and_cannot_delete_authority_records():
    template_text = (ROOT / "mlb_historical_optimizer" / "template.yaml").read_text(
        encoding="utf-8"
    )
    assert "DynamoDBCrudPolicy" not in template_text
    assert "S3CrudPolicy" not in template_text
    assert "dynamodb:TransactWriteItems" in template_text
    assert "DeleteLeaseOnlyFromOptimizerStatePartition" in template_text
    assert "dynamodb:LeadingKeys" in template_text
    assert "MLB_HISTORICAL_OPTIMIZER#V1" in template_text
    assert "s3:PutObject" in template_text
    assert "s3:GetObjectVersion" in template_text
    assert "s3:DeleteObject" not in template_text
    assert handler.policy_runtime.CHAMPION_PK != handler.STATE_PK
    assert handler.policy_runtime.CUTOVER_PK != handler.STATE_PK
