from __future__ import annotations

import copy
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import mlb_game_winner_engine as engine
import mlb_signal_policy_v12 as signal_policy
import mlb_slate_coverage_patch as public_authority


ROOT = Path(__file__).resolve().parents[2]


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, *, Item, ConditionExpression=None):
        key = (str(Item["PK"]), str(Item["SK"]))
        if ConditionExpression and key in self.items:
            exc = RuntimeError("conditional collision")
            exc.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise exc
        self.items[key] = copy.deepcopy(Item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_item(self, *, Key, ConsistentRead=False):
        key = (str(Key["PK"]), str(Key["SK"]))
        item = self.items.get(key)
        return {"Item": copy.deepcopy(item)} if item is not None else {}


def _base_prediction():
    return {
        "ok": True,
        "sport": "mlb",
        "slate_date": "2026-07-23",
        "gameId": "test-event-id",
        "gameIdentity": "test-event-id",
        "gameKey": "mlb|2026-07-23|away|home",
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "commenceTime": "2026-07-23T18:00:00+00:00",
        "predictedWinner": "Home Team",
        "predictedSide": "home",
        "score": 62.0,
        "confidenceTier": "Solid",
        "winProbability": 0.58,
        "winProbabilityPct": 58.0,
        "createdAt": "2026-07-23T12:00:00+00:00",
        "tags": ["BOOK_AGREEMENT"],
        "homeSignal": {
            "marketConsensusProbability": 0.58,
            "probLatest": 0.58,
            "delta": 0.01,
            "latestGap": 0.16,
            "reversalCount": 1,
            "tags": ["BOOK_AGREEMENT"],
        },
        "awaySignal": {
            "marketConsensusProbability": 0.42,
            "probLatest": 0.42,
            "delta": -0.01,
            "latestGap": 0.16,
            "reversalCount": 1,
            "tags": [],
        },
    }


def test_signal_policy_is_installed_before_public_prelock_authority():
    source = (ROOT / "hello_world" / "mlb_ml_runtime_install_v3.py").read_text(
        encoding="utf-8"
    )
    signal_install = source.index("mlb_signal_policy_v12.apply(engine)")
    public_install = source.index(
        "mlb_slate_coverage_patch.install_public_authority(engine, mlb_slate_prediction_lock)"
    )
    finalizer_install = source.index(
        "mlb_locked_prediction_storage_finalizer_v1.apply(engine)"
    )

    assert signal_install < public_install < finalizer_install
    assert '"signalPolicyV13Installed"' in source
    assert "MLB-ML-RUNTIME-INSTALL-v4.2-signal-policy-prelock-persistence" in source


def test_versioned_signal_policy_survives_public_prelock_and_is_durably_stored():
    module = SimpleNamespace()
    module.predict_all = lambda *_args, **_kwargs: {
        "ok": True,
        "modelVersion": "test-model",
        "predictions": [_base_prediction()],
    }
    signal_policy.apply(module)
    policy_result = module.predict_all()
    policy_row = policy_result["predictions"][0]

    assert policy_row["signalPolicyV13"]["version"] == signal_policy.VERSION
    assert policy_row["predictedWinner"] == "Home Team"
    assert policy_row["predictedSide"] == "home"

    public_row = public_authority._prelock_row(
        policy_row,
        {},
        "2026-07-23T17:15:00+00:00",
        "OPEN_PRE_LOCK",
    )
    assert public_row["officialPredictionStatus"] == engine.PREGAME_DISPLAY_STATUS
    assert public_row["officialPrediction"] is False
    assert public_row["lockedPrediction"] is False
    assert public_row["displayPrediction"] is True
    assert public_row["displayGroup"] == "pre_lock_prediction"
    assert public_row["signalPolicyV13"]["version"] == signal_policy.VERSION

    previous_table = engine.history.PULLS
    previous_contract_flag = getattr(
        engine,
        "_INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED",
        None,
    )
    fake_table = FakeTable()
    try:
        engine.history.PULLS = fake_table
        engine._INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED = False
        stored = engine._store_prediction(public_row)
    finally:
        engine.history.PULLS = previous_table
        if previous_contract_flag is None:
            try:
                delattr(
                    engine,
                    "_INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED",
                )
            except AttributeError:
                pass
        else:
            engine._INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED = (
                previous_contract_flag
            )

    assert stored["ok"] is True
    assert stored["storageClass"] == "LIVE_MUTABLE"
    assert stored["pregameSnapshot"]["ok"] is True
    assert len(fake_table.items) == 2

    live_rows = [
        item
        for item in fake_table.items.values()
        if item.get("record_type") == "mlb_single_game_moneyline_prediction"
    ]
    snapshot_rows = [
        item
        for item in fake_table.items.values()
        if item.get("record_type") == engine.PREGAME_SNAPSHOT_RECORD_TYPE
    ]
    assert len(live_rows) == 1
    assert len(snapshot_rows) == 1
    assert (
        live_rows[0]["data"]["signalPolicyV13"]["version"]
        == signal_policy.VERSION
    )
    assert snapshot_rows[0]["signal_policy_version"] == signal_policy.VERSION
