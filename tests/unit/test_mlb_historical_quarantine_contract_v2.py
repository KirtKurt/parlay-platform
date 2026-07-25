from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_historical_daily_optimizer_v1 as optimizer
import mlb_historical_optimizer_handler as handler
import mlb_historical_quarantine_contract_v2 as contract


def _grid() -> optimizer.SnapshotGrid:
    timestamps = (
        "2025-04-14T20:15:00Z",
        "2025-04-14T20:30:00Z",
        "2025-04-14T20:45:00Z",
        "2025-04-14T21:00:00Z",
        "2025-04-14T21:15:00Z",
    )
    return optimizer.SnapshotGrid(
        slate_date_et="2025-04-14",
        start_at_et="01:00",
        interval_minutes=15,
        first_game_start_utc="2025-04-14T22:00:00Z",
        last_game_start_utc="2025-04-14T22:00:00Z",
        first_game_lock_at_utc="2025-04-14T21:15:00Z",
        lock_at_utc="2025-04-14T21:15:00Z",
        timestamps_utc=timestamps,
    )


def _official_game():
    return {
        "officialGamePk": "778001",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "gameDate": "2025-04-14T22:00:00Z",
        "winner": "Home Club",
        "completed": True,
    }


def _payload(requested: str, *, provider_timestamp: str | None = None):
    return {
        "timestamp": provider_timestamp or requested,
        "data": [
            {
                "id": "provider-778001",
                "home_team": "Home Club",
                "away_team": "Away Club",
                "commence_time": "2025-04-14T22:00:00Z",
                "bookmakers": [
                    {
                        "key": "fanduel",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Home Club", "price": -120},
                                    {"name": "Away Club", "price": 110},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _ledger_with_one_quarantine():
    rows = []
    for requested in _grid().timestamps_utc:
        if requested == "2025-04-14T20:45:00Z":
            rows.append(
                {
                    "requestedAtUtc": requested,
                    "status": "QUARANTINED_STALE",
                    "usableForFeatures": False,
                    "reason": "historical response is too stale for a 15-minute grid",
                }
            )
        else:
            rows.append(
                {
                    "requestedAtUtc": requested,
                    "status": "VALID",
                    "usableForFeatures": True,
                    "payload": _payload(requested),
                }
            )
    return rows


def test_quarantined_request_completes_ledger_without_entering_features():
    dataset = contract.build_slate_dataset(
        "2025-04-14",
        [_official_game()],
        _ledger_with_one_quarantine(),
        _grid(),
    )
    assert dataset["requestLedgerComplete"] is True
    assert dataset["plannedSnapshotCount"] == 5
    assert dataset["validSnapshotCount"] == 4
    assert dataset["quarantinedSnapshotCount"] == 1
    assert dataset["completeSlate"] is True
    assert dataset["eligibleGameCount"] == 1
    record = dataset["records"][0]
    assert record["requestedSlotCount"] == 5
    assert record["observedHomePullCount"] == 4
    assert record["homeSignal"]["temporalFeatures"]["horizons"]["full"]["coverageRatio"] == 0.8
    quarantine_audit = [
        row for row in dataset["snapshotAudit"] if row.get("usableForFeatures") is False
    ]
    assert len(quarantine_audit) == 1
    assert quarantine_audit[0]["status"] == "QUARANTINED_STALE"


def test_truly_missing_request_remains_a_fatal_ledger_error():
    rows = _ledger_with_one_quarantine()
    rows = [row for row in rows if row["requestedAtUtc"] != "2025-04-14T20:45:00Z"]
    with pytest.raises(optimizer.HistoricalOptimizerError, match="ledger is incomplete"):
        contract.build_slate_dataset(
            "2025-04-14",
            [_official_game()],
            rows,
            _grid(),
        )


def test_fetch_returns_archived_payload_before_freshness_validation(monkeypatch):
    stale = _payload(
        "2025-04-14T20:45:00Z",
        provider_timestamp="2025-04-14T20:00:00Z",
    )
    monkeypatch.setattr(handler, "_http_json", lambda url: (stale, {"x-requests-last": 10}))
    payload, headers = contract._fetch_historical("2025-04-14T20:45:00Z")
    assert payload == stale
    assert headers["x-requests-last"] == 10


def test_complete_slate_quarantines_stale_archive_and_advances(monkeypatch):
    grid = _grid()

    def get_s3_json(key):
        requested = next(value for value in grid.timestamps_utc if value.replace(":", "").replace("-", "").replace("+", "").replace(".", "") in key)
        if requested == "2025-04-14T20:45:00Z":
            payload = _payload(requested, provider_timestamp="2025-04-14T20:00:00Z")
        else:
            payload = _payload(requested)
        return {"payload": payload}, {"bucket": "raw", "key": key, "sha256": "a" * 64}

    monkeypatch.setattr(handler, "_get_s3_json", get_s3_json)
    monkeypatch.setattr(
        handler,
        "_put_immutable_json",
        lambda *args, **kwargs: {"bucket": "datasets", "key": "2025-04-14.json", "sha256": "b" * 64},
    )

    state = {
        "phase": "BACKFILLING",
        "eligibleGameCount": 0,
        "completeSlateCount": 0,
        "completedSlates": [],
        "targetSettledGames": 1600,
        "currentDate": "2025-04-14",
        "currentSlotIndex": 5,
    }
    contract._complete_slate(state, "2025-04-14", {"games": [_official_game()]}, grid)
    assert state["currentDate"] == "2025-04-15"
    assert state["currentSlotIndex"] == 0
    assert state["completeSlateCount"] == 1
    assert state["eligibleGameCount"] == 1
    assert state["quarantinedHistoricalSlotCount"] == 1
    assert state["lastError"] is None


def test_install_replaces_only_historical_runtime_functions(monkeypatch):
    monkeypatch.delattr(handler, "_quarantine_ledger_contract_installed", raising=False)
    contract.install()
    assert handler.VERSION == contract.HANDLER_VERSION
    assert handler._fetch_historical is contract._fetch_historical
    assert handler._complete_slate is contract._complete_slate
    assert optimizer.build_slate_dataset is contract.build_slate_dataset
