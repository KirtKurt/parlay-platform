from copy import deepcopy
from pathlib import Path
import json

import pytest
import yaml

from soccer_auto.kss1_picks import recorded_picks
from soccer_auto.kss1_readiness import verify_rollout
from tests.soccer_auto.test_kss1_picks import Store
from tests.soccer_auto.test_kss1_readiness import proof_fixture


def context():
    return {"model_digest": "model", "context_as_of": "2026-09-14T18:00:00Z",
            "artifact_uri": "s3://soccer/context"}


def test_default_day_card_keeps_old_picks_but_rollout_excludes_previous_model():
    store = Store()
    assert recorded_picks(store, "2026-09-14")["count"] == 1
    scope = {**context(), "model_digest": "new_model"}
    result = recorded_picks(store, "2026-09-14", context=scope)
    assert result["count"] == 0
    assert result["context"] == scope
    assert result["missing"][0]["reason"] == "NO_RECORDED_T60_TRAINED_PICK"


def test_same_model_from_previous_context_cannot_prove_current_rollout():
    store = Store()
    store.row["goals_context_as_of"] = "2026-09-14T17:00:00Z"
    assert recorded_picks(store, "2026-09-14")["count"] == 1
    assert recorded_picks(store, "2026-09-14", context=context())["count"] == 0


def test_current_context_uses_exact_immutable_key_and_keeps_t60_gate():
    from soccer_auto.canonical import digest
    class ScopedStore(Store):
        def query(self, **kwargs):
            sort_key = kwargs["KeyConditionExpression"].get_expression()["values"][1].get_expression()
            assert sort_key["operator"] == "="
            assert sort_key["values"][1] == "PRED#KSS1#REV#1#TARGET#kss1_book#MODEL#model#CONTEXT#" + digest(context()["context_as_of"])
            return super().query(**kwargs)
    store = ScopedStore()
    store.row["goals_context_as_of"] = context()["context_as_of"]
    assert recorded_picks(store, "2026-09-14", context=context())["count"] == 1
    store.row["created_at"] = "2026-09-14T19:00:01Z"
    assert recorded_picks(store, "2026-09-14", context=context())["count"] == 0


@pytest.mark.parametrize("scope", [{}, {"model_digest": "model"}, {**context(), "context_as_of": "invalid"}])
def test_unavailable_context_never_falls_back_to_historical_picks(scope):
    with pytest.raises(ValueError):
        recorded_picks(Store(), "2026-09-14", context=scope)


def test_rollout_rejects_pointer_movement_between_status_and_picks():
    training, status, picks = proof_fixture()
    picks["context"] = {k: status["training_context"][k] for k in ("model_digest", "context_as_of", "artifact_uri")}
    picks["context"]["artifact_uri"] = "s3://soccer/newer-context"
    with pytest.raises(ValueError, match="GOALS_PICKS_CONTEXT_READBACK_MISMATCH"):
        verify_rollout(training, status, picks)


def test_current_rollout_rejects_mixed_context_row_even_with_same_model():
    training, status, picks = proof_fixture()
    picks["context"] = {k: status["training_context"][k] for k in ("model_digest", "context_as_of", "artifact_uri")}
    picks["picks"][0]["goals_context_as_of"] = "2026-09-14T09:00:00Z"
    with pytest.raises(ValueError, match="GOALS_PICK_CONTEXT_MISMATCH"):
        verify_rollout(training, status, picks)
    picks["picks"][0]["goals_context_as_of"] = status["training_context"]["context_as_of"]
    assert verify_rollout(training, status, picks)["verified"] is True


def test_current_context_api_is_explicit_and_missing_pointer_fails(monkeypatch):
    from soccer_auto import api, kss1_training_runtime
    store = Store()
    store.row["goals_context_as_of"] = context()["context_as_of"]
    monkeypatch.setattr(api, "SoccerStore", lambda: store)
    monkeypatch.setattr(kss1_training_runtime, "goals_status", lambda _: {"training_context": context()})
    request = {"path": "/v1/soccer-auto/kss1/picks", "queryStringParameters": {"date": "2026-09-14", "context": "current"}}
    reply = api.api_handler(request, None)
    assert reply["statusCode"] == 200
    assert json.loads(reply["body"])["context"] == context()
    monkeypatch.setattr(kss1_training_runtime, "goals_status", lambda _: {"training_context": None})
    assert api.api_handler(request, None)["statusCode"] == 400


def test_training_workflows_share_non_cancelling_group_and_scope_readback():
    root = Path(__file__).resolve().parents[2]
    workflows = [yaml.safe_load((root / ".github/workflows" / name).read_text()) for name in
                 ("deploy-soccer-auto.yml", "soccer-kss1-reuse-and-freeze.yml")]
    assert workflows[0]["concurrency"] == workflows[1]["concurrency"]
    assert workflows[0]["concurrency"]["cancel-in-progress"] is False
    for workflow in workflows:
        steps = next(iter(workflow["jobs"].values()))["steps"]
        script = next(s["run"] for s in steps if s.get("name", "").startswith("Train"))
        assert '"context":"current"' in script
        assert "verify_rollout(" in script
