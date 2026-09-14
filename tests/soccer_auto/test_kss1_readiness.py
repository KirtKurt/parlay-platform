from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from soccer_auto.kss1_features import HistoryIndex, build_training_table
from soccer_auto.kss1_goals_model import train_and_validate
from soccer_auto.kss1_readiness import verify_rollout
from soccer_auto.kss1_training_runtime import source_history
from soccer_auto.settlement import build_settlement
from tests.soccer_auto.test_kss1_goals_training import history
from tests.soccer_auto.test_kss1_training_runtime import Store


def proof_fixture():
    readiness = {"split_counts": {"train": 500, "validation": 100, "holdout": 100}}
    context = {"trained": True, "model_digest": "a" * 64, "candidate_model_digest": "a" * 64,
               "artifact_uri": "s3://soccer/artifacts/kss1/context.json",
               "candidate_passed_retrospective": True, "readiness": readiness,
               "candidate_holdout": {"count": 100, "event_manifest": "cohort", "brier": .5, "log_loss": .9},
               "candidate_baseline": {"count": 100, "event_manifest": "cohort", "brier": .6, "log_loss": 1.0},
               "context_as_of": "2026-09-14T10:00:00Z", "automatic_prediction_allowed": False}
    training = {"trained": True, "published_context": True, "model_digest": "a" * 64,
                "artifact_uri": context["artifact_uri"], "readiness": deepcopy(readiness),
                "automatic_prediction_allowed": False}
    status = {"training_context": context, "automatic_prediction_allowed": False}
    picks = {"trained_only": True, "selection": "12", "truncated": False, "count": 1,
             "automatic_prediction_allowed": False, "picks": [
                 {"model_digest": "a" * 64, "model_state": "FITTED_SHADOW", "event_key": "fixture",
                  "created_at": "2026-09-14T12:00:00Z", "commence_time": "2026-09-14T13:00:00Z",
                  "markets": {"double_chance_published": "12"},
                  "input_coverage": {"team_strength_complete": True, "xg_complete": False}}]}
    return training, status, picks


def test_verified_score_only_readback_can_pass_without_xg():
    assert verify_rollout(*proof_fixture())["recorded_12_picks"] == 1


@pytest.mark.parametrize("defect,reason", [
    ("empty", "NO_RECORDED_TRAINED_12_PICKS"),
    ("untrained", "GOALS_MODEL_NOT_TRAINED"),
    ("rejected", "GOALS_CANDIDATE_NOT_QUALIFIED"),
    ("wrong_model", "GOALS_PICK_MODEL_MISMATCH"),
    ("wrong_context", "GOALS_CONTEXT_READBACK_MISMATCH"),
    ("late", "GOALS_PICK_AFTER_T60"),
    ("insufficient_history", "GOALS_PICK_NOT_READY"),
    ("wrong_selection", "GOALS_PICK_NOT_READY"),
    ("wrong_cohort", "GOALS_HOLDOUT_COHORT_MISMATCH"),
    ("worse_loss", "GOALS_HOLDOUT_GATE_FAILED"),
    ("nan", "GOALS_HOLDOUT_GATE_FAILED"),
    ("small_split", "GOALS_CHRONOLOGICAL_SPLITS_NOT_READY"),
    ("duplicate", "GOALS_DUPLICATE_OR_MISSING_PICK_IDENTITY"),
    ("authority", "GOALS_SHADOW_AUTHORITY_VIOLATION"),
])
def test_rollout_proof_rejects_unready_or_mismatched_evidence(defect, reason):
    training, status, picks = proof_fixture()
    context = status["training_context"]
    if defect == "empty": picks.update(count=0, picks=[])
    elif defect == "untrained": training["trained"] = False
    elif defect == "rejected": context["candidate_passed_retrospective"] = False
    elif defect == "wrong_model": picks["picks"][0]["model_digest"] = "b" * 64
    elif defect == "wrong_context": training["artifact_uri"] = "old"
    elif defect == "late": picks["picks"][0]["created_at"] = "2026-09-14T12:00:01Z"
    elif defect == "insufficient_history": picks["picks"][0]["input_coverage"]["team_strength_complete"] = False
    elif defect == "wrong_selection": picks["picks"][0]["markets"]["double_chance_published"] = "1X"
    elif defect == "wrong_cohort": context["candidate_baseline"]["event_manifest"] = "different"
    elif defect == "worse_loss": context["candidate_holdout"]["log_loss"] = 1.1
    elif defect == "nan": context["candidate_holdout"]["brier"] = float("nan")
    elif defect == "small_split":
        context["readiness"]["split_counts"]["train"] = 499
        training["readiness"] = deepcopy(context["readiness"])
    elif defect == "duplicate": picks["picks"] *= 2; picks["count"] = 2
    elif defect == "authority": status["automatic_prediction_allowed"] = True
    with pytest.raises(ValueError, match=reason):
        verify_rollout(training, status, picks)


def test_readiness_counts_only_rows_with_prior_team_history():
    rows = history(56)
    for row in rows:
        row["home_xg"] = row["away_xg"] = None
    table = build_training_table(HistoryIndex(rows))
    report = train_and_validate(table, min_train=500, min_test=100)
    assert report["input_rows"] == 56
    assert report["eligible_rows"] < 56
    assert report["excluded_insufficient_team_history"] > 0
    assert report["additional_eligible_rows_lower_bound"] == 700 - report["eligible_rows"]
    assert report["required_split_counts"] == {"train": 500, "validation": 100, "holdout": 100}
    assert report["xg_required"] is False
    assert report["trained"] is False


def test_source_audit_separates_invalid_regulation_and_competition_exclusions():
    def score(event_id, sport, ambiguous=False):
        return build_settlement({"id": event_id, "sport_key": sport, "schedule_revision": 1,
                                 "home_team": "Home", "away_team": "Away",
                                 "commence_time": "2026-09-01T12:00:00Z", "completed": True,
                                 "scores": [{"name": "Home", "score": "2"}, {"name": "Away", "score": "1"}]},
                                observed_at="2026-09-01T14:00:00Z", regulation_ambiguous=ambiguous)
    good = score("good", "soccer_epl")
    bad = score("bad", "soccer_epl")
    bad["home_score"] = 8
    audit = {}
    rows = source_history(Store([good, bad, score("ambiguous", "soccer_epl", True),
                                 score("unsupported", "soccer_unknown_league")]), audit=audit)
    assert len(rows) == 1
    assert audit["final_score_rows"] == 4
    assert audit["accepted_scores"] == audit["invalid_score_evidence"] == 1
    assert audit["regulation_ineligible"] == audit["competition_ineligible"] == 1
    assert audit["accepted_by_competition"] == {"soccer_epl": 1}


def test_final_training_proof_runs_after_integrity_and_settlement_reconciliation():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/deploy-soccer-auto.yml").read_text())
    steps = workflow["jobs"]["verify-and-deploy"]["steps"]
    assert steps[-1]["name"] == "Train KSS1 goals and verify recorded-picks readback"
    assert "verify_rollout(training['goals_training']" in steps[-1]["run"]
    assert any("assert health['healthy'] is True" in step.get("run", "") for step in steps[:-1])
