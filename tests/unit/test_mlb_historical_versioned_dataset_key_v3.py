from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_historical_optimizer_handler as handler
import mlb_historical_versioned_dataset_key_v3 as contract


def test_dataset_key_is_content_addressed_and_under_authorized_prefix():
    dataset = {
        "slateDateEt": "2025-04-14",
        "fingerprint": "f" * 64,
        "records": [{"officialGamePk": "1"}],
    }
    first = contract.dataset_key(dataset)
    second = contract.dataset_key(dict(dataset))
    changed = contract.dataset_key({**dataset, "records": [{"officialGamePk": "2"}]})
    assert first == second
    assert first != changed
    assert first.startswith(
        "mlb/historical-daily-v1/datasets-versioned/2025-04-14/"
    )
    assert first.endswith(".json")


def test_install_routes_only_complete_slate_datasets(monkeypatch):
    calls = []

    def original(key, value, *, record_type):
        calls.append((key, value, record_type))
        return {"key": key}

    monkeypatch.setattr(handler, "_put_immutable_json", original)
    monkeypatch.delattr(handler, "_versioned_dataset_key_v3_installed", raising=False)
    monkeypatch.delattr(handler, "_versioned_dataset_key_v3_original_put", raising=False)

    contract.install()

    dataset = {"slateDateEt": "2025-04-14", "records": []}
    result = handler._put_immutable_json(
        "mlb/historical-daily-v1/datasets/2025-04-14.json",
        dataset,
        record_type=contract.DATASET_RECORD_TYPE,
    )
    assert result["key"].startswith(
        "mlb/historical-daily-v1/datasets-versioned/2025-04-14/"
    )

    raw_key = "mlb/historical-daily-v1/raw/2025-04-14/example.json"
    raw_result = handler._put_immutable_json(
        raw_key,
        {"payload": {}},
        record_type="mlb_historical_odds_snapshot",
    )
    assert raw_result["key"] == raw_key
    assert handler.VERSION == contract.HANDLER_VERSION
    assert calls[0][2] == contract.DATASET_RECORD_TYPE
    assert calls[1][2] == "mlb_historical_odds_snapshot"
