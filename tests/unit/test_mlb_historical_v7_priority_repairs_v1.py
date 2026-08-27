import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
REPAIRS = ROOT / "hello_world" / "mlb_historical_v7_priority_repairs_v1.py"
SCRIPT = ROOT / "scripts" / "run_mlb_historical_supervised_v9_shadow.py"
WORKFLOW = ROOT / ".github" / "workflows" / "mlb-historical-supervised-v9-shadow.yml"
TEMPLATE = ROOT / "mlb_historical_optimizer" / "template.yaml"


def _module():
    spec = importlib.util.spec_from_file_location("v7_repairs_under_test", REPAIRS)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeLearner:
    FEATURES = ("marketLogit",)
    FEATURE_VERSION = "old"

    @staticmethod
    def pair_features(home, away, policy):
        return {"marketLogit": float(home.get("marketLogit", 0.0))}

    @staticmethod
    def _fundamental(signal, names):
        for name in names:
            value = signal.get(name)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def _v8(signal, name):
        return signal.get(name)

    @staticmethod
    def _temporal(signal, horizon, name):
        return float(signal.get("coverageRatio", 0.0))


def test_install_is_idempotent_and_missingness_is_explicit():
    repairs = _module()
    learner = FakeLearner()
    repairs.install_feature_repairs(learner)
    first_features = learner.FEATURES
    repairs.install_feature_repairs(learner)
    assert learner.FEATURES == first_features
    values = learner.pair_features(
        {"marketLogit": 0.2, "starterQuality": 3, "coverageRatio": 1},
        {"marketLogit": -0.1, "coverageRatio": 1},
        {},
    )
    assert values["starterAvailable"] == 0.0
    assert values["starterDiff"] == 3.0
    assert values["fullHistoryAvailable"] == 1.0


def test_strict_binary_labels_never_coerce_missing_to_away_win():
    repairs = _module()
    assert repairs.strict_binary_label(True) == 1
    assert repairs.strict_binary_label(False) == 0
    assert repairs.strict_binary_label("1") == 1
    assert repairs.strict_binary_label("0") == 0
    for invalid in (None, "", "false", "true", 2, -1, float("nan")):
        assert repairs.strict_binary_label(invalid) is None


def test_dataset_fingerprint_is_order_independent_and_label_sensitive():
    repairs = _module()
    rows = [
        {"slateDateEt": "2026-07-01", "gameId": "b", "homeWon": 0, "fingerprint": "fb"},
        {"slateDateEt": "2026-07-01", "gameId": "a", "homeWon": 1, "fingerprint": "fa"},
    ]
    assert repairs.dataset_fingerprint(rows) == repairs.dataset_fingerprint(list(reversed(rows)))
    changed = [dict(row) for row in rows]
    changed[0]["homeWon"] = 1
    assert repairs.dataset_fingerprint(rows) != repairs.dataset_fingerprint(changed)


def test_population_report_excludes_invalid_outcomes():
    repairs = _module()
    learner = FakeLearner()
    records = [
        {"slateDateEt": "2026-07-01", "homeWon": 1, "homeSignal": {"marketLogit": 1}},
        {"slateDateEt": "2026-07-01", "homeWon": None, "homeSignal": {"marketLogit": 100}},
        {"slateDateEt": "2026-07-01", "homeWon": 0, "homeSignal": {"marketLogit": -1}},
    ]
    report = repairs.feature_population_report(records, learner, {})
    assert report["recordCount"] == 3
    assert report["eligibleRecordCount"] == 2
    assert report["invalidLabelCount"] == 1
    assert report["features"]["marketLogit"]["count"] == 2
    assert report["features"]["marketLogit"]["mean"] == 0.0


def test_selective_report_fails_closed_for_missing_labels_and_markets():
    repairs = _module()
    records = [
        {"slateDateEt": "2026-07-01", "homeWon": 1,
         "homeSignal": {"marketConsensusProbability": 0.7},
         "awaySignal": {"marketConsensusProbability": 0.3}},
        {"slateDateEt": "2026-07-01", "homeWon": None,
         "homeSignal": {"marketConsensusProbability": 0.8},
         "awaySignal": {"marketConsensusProbability": 0.2}},
        {"slateDateEt": "2026-07-01", "homeWon": 0,
         "homeSignal": {}, "awaySignal": {}},
    ]
    report = repairs.selective_accuracy_report(records)
    assert report["fullSlateGameCount"] == 1
    assert report["invalidLabelCount"] == 1
    assert report["invalidMarketCount"] == 1
    assert report["fullSlateAccuracy"] == 1.0
    assert report["selectiveThresholds"]["0.70"]["accuracy"] == 1.0
    assert report["selectiveThresholds"]["0.80"]["accuracy"] is None


def test_candidate_handoff_is_fail_closed_until_evidence_is_complete():
    repairs = _module()
    blocked = repairs.candidate_handoff({"policy": {"x": 1}}, "abc")
    assert blocked["promotionAuthority"] is False
    assert blocked["eligibleForCanonicalSeed"] is False
    assert set(blocked["eligibilityBlockers"]) == {"MISSING_SEARCH_VERSION", "SEARCH_NOT_COMPLETED"}
    ready = repairs.candidate_handoff(
        {"policy": {"x": 1}, "searchVersion": "v7-search", "status": "SUCCESS"}, "abc"
    )
    assert ready["eligibleForCanonicalSeed"] is True
    assert ready["requiresFresh200GameUntouchedAudit"] is True
    assert ready["requiredUntouchedAuditGames"] == 200
    assert ready["requiresEverySlateAtLeast80Pct"] is True


def test_shadow_workflow_is_retained_for_operator_only_evaluation():
    source = WORKFLOW.read_text(encoding="utf-8")
    document = yaml.load(source, Loader=yaml.BaseLoader)

    assert set(document["on"]) == {"workflow_dispatch"}
    assert "github.event.pull_request.head.sha || github.sha" in source


def test_shadow_refit_is_gated_and_canonical_audit_remains_200():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "WAITING_FOR_50_NEW_ELIGIBLE_GAMES" in source
    assert "datasetFingerprint" in source
    assert "canonicalFreshAuditIncrementGames" in source
    assert "candidate_handoff" in source


def test_range_and_round_ceiling_are_extended_without_reducing_audit():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "Default: '2026-12-31'" in source
    assert "Default: 24" in source
    assert "MaxValue: 36" in source
    assert "MinValue: 200" in source
