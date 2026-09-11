from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_reliability_calibration_v1 as calibration


def _rows():
    rows = []
    for day_index, day in enumerate(("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04")):
        for game_index in range(20):
            rows.append(
                {
                    "gameId": f"{day}-{game_index}",
                    "slateDateEt": day,
                    "reliabilityProbability": 0.90,
                    "pickCorrect": 1 if game_index % 2 == 0 else 0,
                    "sentinel": {"dayIndex": day_index},
                }
            )
    return rows


def test_validation_split_keeps_whole_slates_and_strict_chronology() -> None:
    fit_rows, selection_rows, proof = calibration.split_validation_slates(_rows())
    fit_dates = {row["slateDateEt"] for row in fit_rows}
    selection_dates = {row["slateDateEt"] for row in selection_rows}
    assert fit_dates == {"2026-09-01", "2026-09-02"}
    assert selection_dates == {"2026-09-03", "2026-09-04"}
    assert fit_dates.isdisjoint(selection_dates)
    assert max(fit_dates) < min(selection_dates)
    assert proof["prospectiveRowsUsedForCalibration"] == 0
    assert proof["prospectiveRowsUsedForThresholdSelection"] == 0
    assert proof["wholeSlatesKeptTogether"] is True


def test_regularized_logit_reduces_known_overconfidence_on_later_validation() -> None:
    rows = _rows()
    result = calibration.prepare_validation_for_threshold_selection(rows)
    raw = result["diagnostics"]["rawLaterValidation"]
    adjusted = result["diagnostics"]["calibratedLaterValidation"]
    assert raw["calibrationError"] > 0.35
    assert adjusted["calibrationError"] < raw["calibrationError"]
    assert adjusted["brierScore"] < raw["brierScore"]
    assert adjusted["logLoss"] < raw["logLoss"]
    assert result["calibrator"]["fitSource"] == "earlier_validation_only"
    assert result["calibrationRule"] == calibration.CALIBRATION_RULE


def test_shadow_preparation_does_not_mutate_source_or_grant_authority() -> None:
    rows = _rows()
    before = copy.deepcopy(rows)
    result = calibration.prepare_validation_for_threshold_selection(rows)
    assert rows == before
    assert result["prospectiveQualificationEvidence"] is False
    assert result["promotionEligible"] is False
    assert result["productionAuthorityChanged"] is False
    assert result["immutableHistoryRewritten"] is False
    assert result["automaticWagerAllowed"] is False
    assert all(
        "rawReliabilityProbability" in row
        and row["reliabilityCalibrationVersion"] == calibration.VERSION
        for row in result["thresholdSelectionRows"]
    )


def test_single_validation_slate_fails_closed() -> None:
    rows = [row for row in _rows() if row["slateDateEt"] == "2026-09-01"]
    with pytest.raises(ValueError, match="at least two validation slate dates"):
        calibration.prepare_validation_for_threshold_selection(rows)


def test_nonbinary_label_and_invalid_probability_fail_closed() -> None:
    rows = _rows()
    rows[0]["pickCorrect"] = None
    with pytest.raises(ValueError, match="binary final-settlement label"):
        calibration.prepare_validation_for_threshold_selection(rows)

    rows = _rows()
    rows[0]["reliabilityProbability"] = float("nan")
    with pytest.raises(ValueError, match="must be finite"):
        calibration.prepare_validation_for_threshold_selection(rows)
