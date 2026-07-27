from __future__ import annotations

import mlb_historical_v7_selected_pick_bands_v1 as subject
import mlb_historical_v7_selective_search_v2 as search_module
from test_mlb_historical_v7_selective_search_v2 import Config, Optimizer, records


def test_attach_publishes_incremental_and_cumulative_bands_for_rejected_candidate():
    optimizer = Optimizer()
    result = search_module.search(optimizer, records(), Config())
    result["status"] = "SELECTIVE_CANDIDATE_REJECTED"

    output = subject.attach(search_module, optimizer, records(), result)
    diagnostics = output["selectedPickBandDiagnostics"]

    assert diagnostics["status"] == "AVAILABLE"
    assert diagnostics["reportingOnly"] is True
    assert diagnostics["changesPromotionDecision"] is False
    assert output["status"] == "SELECTIVE_CANDIDATE_REJECTED"

    for partition_name in ("walkForward", "untouchedHoldout"):
        partition = diagnostics[partition_name]
        assert len(partition["incrementalBands"]) == len(subject.BAND_EDGES)
        assert len(partition["cumulativeThresholds"]) == len(subject.BAND_EDGES)
        assert partition["incrementalBands"][0]["label"] == "0.600-0.625"
        assert partition["incrementalBands"][-1]["label"] == ">=0.800"
        assert partition["cumulativeThresholds"][0]["label"] == ">=0.600"
        assert partition["cumulativeThresholds"][-1]["label"] == ">=0.800"
        assert all("accuracy" in row for row in partition["incrementalBands"])
        assert all("coverage" in row for row in partition["cumulativeThresholds"])


def test_install_wraps_independent_v7_search_without_changing_authority():
    optimizer = Optimizer()
    search_module.install(optimizer)
    subject.install(search_module, optimizer)

    result = optimizer.v7_selective_search(records(), Config())

    assert result["promotionAuthority"] is False
    assert result["selectedPickBandDiagnostics"]["status"] == "AVAILABLE"
    assert optimizer.V7_SELECTED_PICK_BANDS_VERSION == subject.VERSION


def test_insufficient_result_gets_explicit_diagnostic_status():
    output = subject.attach(search_module, Optimizer(), records(), {"ok": True})
    assert output["selectedPickBandDiagnostics"]["status"] == "INSUFFICIENT_FROZEN_SELECTIVE_RESULT"
