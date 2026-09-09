from copy import deepcopy

import pytest

import mlb_fundamentals_snapshot_v2 as snapshots
from verify_mlb_starter_context import persisted_observations
from mlb_statsapi_starter_context import VERSION


def recorded_row():
    quality = {"source_status": "PARTIAL", "home_starter_era": 3.5,
               "away_starter_era": 4.5, "home_starter_k_minus_bb_pct": 18.0,
               "away_starter_k_minus_bb_pct": 14.0, "sourceProvenance": {
                   "provider": "MLB Stats API", "dataset": VERSION,
                   "retrievedAtUtc": "2026-09-09T08:15:10+00:00",
                   "sourceEffectiveAtUtc": "2026-09-09T08:15:10+00:00",
                   "payloadFingerprint": "a" * 64}}
    row = {"gameId": "mlb_statsapi:123", "slateDateEt": "2026-09-09",
           "officialGamePk": 123, "homeTeam": "Home", "awayTeam": "Away",
           "predictionSourcePullAt": "2026-09-09T08:15:00+00:00",
           "advanced_context": {"fip_xfip": quality}}
    row["fundamentalsSnapshotV2"] = snapshots.build(row)
    return {"data": row}


class Table:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.pages)


def test_inspects_every_page_without_rebuilding_or_writing():
    table = Table([{"Items": [], "LastEvaluatedKey": {"PK": "next"}},
                   {"Items": [recorded_row()]}])
    proof = persisted_observations(table, "2026-09-09")
    assert proof["gamesWithPersistedStarterRates"] == 1
    assert proof["status"] == "PERSISTED_OBSERVATIONS_VERIFIED"
    assert all(call["ConsistentRead"] for call in table.calls)
    assert table.calls[1]["ExclusiveStartKey"] == {"PK": "next"}


def test_unobserved_snapshot_is_not_claimed_as_persisted_source_success():
    proof = persisted_observations(Table([{"Items": [{"data": {}}]}]), "2026-09-09")
    assert proof["gamesWithPersistedStarterRates"] == 0
    assert proof["status"] == "AWAITING_NEW_SNAPSHOT_CAPTURE"


def test_tampered_persisted_source_is_rejected():
    row = deepcopy(recorded_row())
    row["data"]["fundamentalsSnapshotV2"]["groups"]["starter_quality"]["values"]["homeEra"] = 0
    with pytest.raises(RuntimeError, match="fingerprint"):
        persisted_observations(Table([{"Items": [row]}]), "2026-09-09")
