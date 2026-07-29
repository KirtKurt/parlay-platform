from __future__ import annotations

import hashlib
import json

import mlb_supervised_daily_objective_v2_1 as objective
import mlb_supervised_features_v2 as features


class Model:
    features = features
    _config_key = None
    VERSION = "old"

    @staticmethod
    def _sha(value):
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def train_and_evaluate(records, **kwargs):
        return {"ok": True, "recordCount": len(records), "resultDigest": "old"}


def test_install_adds_disabled_overlays_without_aws(monkeypatch):
    monkeypatch.delenv("MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED", raising=False)
    monkeypatch.setenv("MLB_V8_HISTORICAL_CONTEXT_OVERLAY_ENABLED", "false")

    objective.install(Model)
    result = Model.train_and_evaluate([{"officialGamePk": "1"}])

    assert result["historicalBbsFundamentals"]["status"] == "DISABLED"
    assert result["historicalTargetGameContext"]["status"] == "DISABLED"
    assert result["resultDigest"] != "old"
    assert (
        Model.SUPERVISED_SELECTION_OBJECTIVE["historicalBbsPointInTimeRequired"]
        is True
    )
    assert (
        Model.SUPERVISED_SELECTION_OBJECTIVE[
            "targetGameContextRequiresStarterBullpenLineupInjuryParkWeather"
        ]
        is True
    )
