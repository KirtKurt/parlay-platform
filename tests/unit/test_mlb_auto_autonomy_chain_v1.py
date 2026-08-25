from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import mlb_ml_autonomy_chain_v1 as autonomy


def _official(day: str, *, final: bool = True):
    games = [
        {
            "officialGamePk": f"{day}-1",
            "officialDate": day,
        },
        {
            "officialGamePk": f"{day}-2",
            "officialDate": day,
        },
    ]
    return {
        "ok": True,
        "slateDateEt": day,
        "officialGameCount": 2,
        "officialFinalCount": 2 if final else 1,
        "games": games,
        "source": "MLB Stats API exact-date official FINAL",
        "sourceUrl": f"https://example.invalid/{day}",
    }


def test_gap_tolerant_continuity_does_not_block_later_finalized_slate():
    dates = ["2026-08-04", "2026-08-05", "2026-08-06"]

    def schedule(day: str):
        return _official(day, final=day != "2026-08-06")

    def finalization(day: str, official):
        if day == "2026-08-04":
            return {
                "ok": False,
                "requestedSlateDates": [day],
                "finalizedSlateDates": [],
                "slates": [{"slateDateEt": day, "slateFinalized": False}],
            }
        return {
            "ok": True,
            "requestedSlateDates": [day],
            "finalizedSlateDates": [day],
            "slates": [
                {
                    "slateDateEt": day,
                    "slateFinalized": True,
                    "officialGameCount": 2,
                }
            ],
        }

    training_dates, proof = autonomy.gap_tolerant_finalized_slate_scan(
        dates,
        official_schedule_loader=schedule,
        slate_finalization_loader=finalization,
        expected_schedule_source="MLB Stats API exact-date official FINAL",
    )

    assert training_dates == ["2026-08-05"]
    assert proof["ok"] is True
    assert proof["trainingMayContinuePastQuarantinedDates"] is True
    assert proof["quarantinedSlateDates"] == ["2026-08-04"]
    assert proof["deferredSlateDates"] == ["2026-08-06"]
    assert proof["officialFinalizedGameSlateDates"] == [
        "2026-08-04",
        "2026-08-05",
    ]
    assert proof["trainingEligibleSlateDates"] == ["2026-08-05"]


def test_unproven_schedule_is_quarantined_without_authorizing_rows():
    dates = ["2026-08-04", "2026-08-05"]

    def schedule(day: str):
        if day == "2026-08-04":
            raise RuntimeError("provider unavailable")
        return _official(day)

    def finalization(day: str, official):
        return {
            "ok": True,
            "requestedSlateDates": [day],
            "finalizedSlateDates": [day],
            "slates": [
                {
                    "slateDateEt": day,
                    "slateFinalized": True,
                    "officialGameCount": 2,
                }
            ],
        }

    training_dates, proof = autonomy.gap_tolerant_finalized_slate_scan(
        dates,
        official_schedule_loader=schedule,
        slate_finalization_loader=finalization,
        expected_schedule_source="MLB Stats API exact-date official FINAL",
    )

    assert training_dates == ["2026-08-05"]
    assert proof["unprovenScheduleDates"] == ["2026-08-04"]
    assert proof["strictPerRowFailClosed"] is True
    assert proof["gapCount"] == 1


def test_aspirational_accuracy_is_not_a_training_gate_contract():
    assert autonomy.VERSION.startswith("MLB-ML-AUTONOMY-CHAIN-v1")
    assert autonomy.PROMOTION_VERSION.startswith("MLB-ML-AUTONOMOUS-PROMOTION-v1")
    assert autonomy.MISSINGNESS_VERSION.startswith("MLB-ML-MISSINGNESS-TRAINING-v1")
