#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_dual_model_v2 as dual_v2
import mlb_ml_experiment_v2 as experiment_v2
import mlb_ml_promotion_policy_v2 as promotion_v2


R3_EXPERIMENT_ID = "mlb-v2-2026-07-22-future-prospective-r3"
R3_RELEASE_CUTOFF_UTC = "2026-07-22T04:00:00+00:00"


class _Key:
    def __init__(self, name: str):
        self.name = name

    def eq(self, value: str):
        return self.name, value


class _PagedTable:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        page_index = len(self.calls) - 1
        if page_index >= len(self.pages):
            return {"Items": []}
        page = dict(self.pages[page_index])
        requested = int(kwargs.get("Limit") or len(page.get("Items") or []))
        page["Items"] = list(page.get("Items") or [])[:requested]
        return page


def _audit_item(created_at: datetime, game_id: str):
    slate = created_at.date().isoformat()
    return {
        "created_at": created_at.isoformat(),
        "data": {
            "createdAt": created_at.isoformat(),
            "rows": [
                {
                    "status": "GRADED",
                    "id": game_id,
                    "slateDateEt": slate,
                    "gameKeyBase": game_id,
                    "commenceTime": created_at.isoformat(),
                    "predictedWinner": "Home",
                    "canonicalLockAuthority": {
                        "version": "MLB-ROLLING-AUDIT-CANONICAL-LOCK-AUTHORITY-v1",
                        "verified": True,
                        "consistentRead": True,
                        "sourcePk": f"GAME_WINNERS#mlb#{slate}",
                        "sourceSk": f"LOCKED#GAME#{created_at.isoformat()}#{game_id}",
                        "recordType": "mlb_immutable_locked_single_game_prediction",
                        "immutableLocked": True,
                        "stageAuthorityVerified": True,
                        "persistedStageAuthorityValidated": True,
                        "exactLockVectorValidated": True,
                        "exactProviderIdentityMatched": True,
                        "matchMethod": "exact_provider_game_id_and_teams",
                        "legacyOrDailyCardFallbackUsed": False,
                    },
                }
            ],
        },
    }


def _load_audit_module(table: _PagedTable):
    fake_history = types.ModuleType("inqsi_pull_history")
    fake_history.PULLS = table
    fake_history.Key = _Key
    sys.modules["inqsi_pull_history"] = fake_history
    sys.modules.pop("mlb_rolling_24h_audit", None)
    import mlb_rolling_24h_audit as audit

    audit.history = fake_history
    return audit


def verify_r3_future_manual_first_contract() -> None:
    assert experiment_v2.PARTITION_MINIMUMS == {
        "train": 300,
        "validation": 100,
        "prospectiveTest": 100,
    }
    manifest = experiment_v2.new_manifest(
        experiment_id=R3_EXPERIMENT_ID,
        release_contract_id=R3_EXPERIMENT_ID,
        release_cutoff_utc=R3_RELEASE_CUTOFF_UTC,
        feature_vector_version=experiment_v2.REQUIRED_FUNDAMENTALS_VERSION,
        model_feature_schemas={
            "outcome": dual_v2.OUTCOME_FEATURES,
            "reliability": dual_v2.RELIABILITY_FEATURES,
        },
        created_at_utc=R3_RELEASE_CUTOFF_UTC,
    )
    assert manifest["experimentId"] == R3_EXPERIMENT_ID
    assert manifest["releaseContractId"] == R3_EXPERIMENT_ID
    assert manifest["releaseCutoffUtc"] == R3_RELEASE_CUTOFF_UTC
    assert manifest["partitions"]["train"]["minimumRows"] == 300
    assert manifest["partitions"]["validation"]["minimumRows"] == 100
    assert manifest["partitions"]["prospectiveTest"]["minimumRows"] == 100

    gate = promotion_v2.evaluate(
        {}, manifest, current_champion=None, automatic_promotion_enabled=False
    )
    assert gate["firstPromotionRequiresManualReview"] is True
    assert gate["automaticPromotionEnabled"] is False
    assert gate["runtimeAuthorityActivationEligible"] is False


def verify_durable_paginated_history() -> None:
    fixed_now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    os.environ.pop("INQSI_MLB_HISTORICAL_AUDIT_RUN_LIMIT", None)
    os.environ.pop("INQSI_MLB_HISTORICAL_AUDIT_WINDOW_DAYS", None)
    table = _PagedTable(
        [
            {"Items": [_audit_item(fixed_now, "game-1")], "LastEvaluatedKey": {"page": 1}},
            {"Items": [_audit_item(fixed_now - timedelta(days=1), "game-2")], "LastEvaluatedKey": {"page": 2}},
            {"Items": [_audit_item(fixed_now - timedelta(days=2), "game-3")]},
        ]
    )
    audit = _load_audit_module(table)
    audit.now_utc = lambda: fixed_now

    assert audit.HISTORICAL_AUDIT_WINDOW_DAYS >= 45
    assert audit.HISTORICAL_AUDIT_RUN_LIMIT == 0
    rows = audit.historical_audit_rows()
    assert {row.get("id") for row in rows} == {"game-1", "game-2", "game-3"}, rows
    assert len(table.calls) == 3, table.calls
    assert "ExclusiveStartKey" not in table.calls[0]
    assert table.calls[1].get("ExclusiveStartKey") == {"page": 1}
    assert table.calls[2].get("ExclusiveStartKey") == {"page": 2}

    capped_table = _PagedTable(
        [
            {"Items": [_audit_item(fixed_now, "game-1")], "LastEvaluatedKey": {"page": 1}},
            {"Items": [_audit_item(fixed_now - timedelta(days=1), "game-2")]},
        ]
    )
    audit.history.PULLS = capped_table
    capped = audit.historical_audit_rows(limit=1)
    assert {row.get("id") for row in capped} == {"game-1"}, capped
    assert len(capped_table.calls) == 1, capped_table.calls
    assert capped_table.calls[0].get("Limit") == 1

    old_table = _PagedTable(
        [
            {"Items": [_audit_item(fixed_now, "game-1")], "LastEvaluatedKey": {"page": 1}},
            {
                "Items": [_audit_item(fixed_now - timedelta(days=audit.HISTORICAL_AUDIT_WINDOW_DAYS + 1), "too-old")],
                "LastEvaluatedKey": {"page": 2},
            },
            {"Items": [_audit_item(fixed_now - timedelta(days=audit.HISTORICAL_AUDIT_WINDOW_DAYS + 2), "older")]},
        ]
    )
    audit.history.PULLS = old_table
    within_window = audit.historical_audit_rows()
    assert {row.get("id") for row in within_window} == {"game-1"}, within_window
    assert len(old_table.calls) == 2, old_table.calls


def main() -> int:
    verify_r3_future_manual_first_contract()
    verify_durable_paginated_history()
    print(
        "MLB ML training readiness verified: r3 starts at its future release cutoff, "
        "uses fixed whole-slate 300/100/100 partitions with manual-first shadow approval, "
        "and historical audit evidence paginates across a durable diagnostic window"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
