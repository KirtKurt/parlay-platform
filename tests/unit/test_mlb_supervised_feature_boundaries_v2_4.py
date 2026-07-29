from types import SimpleNamespace

from hello_world import mlb_supervised_feature_boundaries_v2_4 as boundaries


def test_prior_game_snapshot_is_not_target_game_fundamentals():
    module = SimpleNamespace(
        VERSION="base",
        _fundamentals_payload=lambda record: record.get("payload") or {},
    )
    boundaries.install_features(module)
    boundaries.install_features(module)

    prior = {
        "snapshotRole": boundaries.BBD_PRIOR_GAME_SNAPSHOT_ROLE,
        "home": {"bbsWinRate5": 0.8},
    }
    target = {
        "snapshotRole": "TARGET_GAME_FUNDAMENTALS_AT_T_MINUS_45",
        "home": {"starterQuality": 0.8},
    }
    assert module._fundamentals_payload({"payload": prior}) == {}
    assert module._fundamentals_payload({"payload": target}) == target
    assert module.VERSION.count(boundaries.VERSION) == 1


def test_report_exposes_bbs_full_corpus_and_supported_cohort_coverage():
    records = [
        {"slateDateEt": "2025-07-01"},
        {"slateDateEt": "2026-03-01"},
        {"slateDateEt": "2026-07-01"},
    ]

    class Model:
        @staticmethod
        def train_and_evaluate(records, **kwargs):
            return {
                "featureCoverage": {"exampleCount": len(records)},
                "historicalBbsFundamentals": {"appliedGameCount": 1},
                "architecture": {},
                "resultDigest": "old",
            }

        @staticmethod
        def _sha(value):
            return "new-digest"

    boundaries.install_model(Model)
    boundaries.install_model(Model)
    result = Model.train_and_evaluate(records)

    assert result["featureCoverage"]["bbsPriorSupported"] == 0.66666667
    assert result["featureCoverage"]["bbsPriorAvailable"] == 0.33333333
    assert result["featureCoverage"]["bbsPriorWithinSupported"] == 0.5
    assert result["architecture"][
        "targetGameFundamentalsExcludeBbsPriorGameSnapshots"
    ] is True
    assert result["resultDigest"] == "new-digest"
