from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json

import pytest
import yaml

from soccer_auto.canonical import digest
from soccer_auto.kss1_bbd import BbdClient, BbdError
from soccer_auto.kss1_training_runtime import CONTEXT_KEY, enrich_xg, load_context, source_history, train_goals_shadow
from soccer_auto.settlement import build_settlement
from tests.soccer_auto.test_isolation_contract import CloudFormationLoader
from tests.soccer_auto.test_kss1_goals_training import history


class Table:
    def __init__(self):
        self.item = None
        self.writes = []
    def get_item(self, **kwargs):
        from soccer_auto.kss1_score_archive import ARCHIVE_KEY
        if kwargs["Key"] == ARCHIVE_KEY:
            return {}
        assert kwargs["Key"] == CONTEXT_KEY
        return {"Item": deepcopy(self.item)} if self.item else {}
    def put_item(self, **kwargs):
        self.writes.append(kwargs)
        self.item = deepcopy(kwargs["Item"])


class Store:
    artifact_bucket = "soccer-test-bucket"
    def __init__(self, rows=None):
        self.models = Table()
        self.artifacts = {}
        self.settlements = rows or []
        self.ops = []
    def scan_all(self, table, **kwargs):
        yield from table
    def read_json(self, uri):
        return deepcopy(self.artifacts[uri])
    def write_artifact(self, category, payload, artifact_digest):
        assert category.startswith("kss1/")
        assert digest(payload) == artifact_digest
        uri = f"s3://{self.artifact_bucket}/artifacts/{category}/{artifact_digest}.json"
        self.artifacts[uri] = deepcopy(payload)
        return uri


def signed_score():
    return build_settlement({"id": "one", "sport_key": "soccer_epl", "schedule_revision": 1,
                             "home_team": "Arsenal", "away_team": "Chelsea",
                             "commence_time": "2025-01-01T16:00:00Z", "completed": True,
                             "scores": [{"name": "Arsenal", "score": "2"}, {"name": "Chelsea", "score": "1"}],
                             "last_update": "2025-01-01T18:00:00Z"}, observed_at="2025-01-02T00:00:00Z", regulation_ambiguous=False)


def test_only_valid_signed_scores_enter_history(monkeypatch):
    good = signed_score()
    rows = source_history(Store([good]))
    assert len(rows) == 1
    assert rows[0]["available_at"] == "2025-01-02T00:00:00Z"
    assert rows[0]["source_receipt"] == digest(good | {"effective_training_eligible_1x2": True, "training_admissibility_source": "FINAL_SCORE_ROW"})
    corrupt = deepcopy(good)
    corrupt["home_score"] = 9
    assert source_history(Store([corrupt])) == []
    monkeypatch.setattr("soccer_auto.trainer._settlement_conflict_events", lambda store: {good["event_key"]})
    assert source_history(Store([good])) == []


def test_small_history_persists_receipts_without_claiming_training(monkeypatch):
    store = Store()
    monkeypatch.setattr("soccer_auto.kss1_training_runtime.source_history", lambda store, **kwargs: history(40))
    result = train_goals_shadow(store)
    assert result["trained"] is False
    assert result["bbd"]["reason"] == "BBD_CREDENTIAL_NOT_CONFIGURED"
    assert len(store.models.writes) == 1
    assert store.models.writes[0]["Item"]["PK"] == "MODEL#kss1_goals#global"
    assert "context_as_of < :cutoff" in store.models.writes[0]["ConditionExpression"]
    context = load_context(store)
    assert len(context["history"]) == 40
    assert context["model"] is None
    assert context["automatic_prediction_allowed"] is False
    context["history"][0]["home_score"] = 12
    store.artifacts[result["artifact_uri"]] = context
    with pytest.raises(ValueError, match="digest"):
        load_context(store)


def test_context_cannot_read_an_unrelated_bucket():
    store = Store()
    store.models.item = {"artifact_uri": "s3://other-bucket/artifacts/kss1/context.json"}
    with pytest.raises(ValueError, match="isolated"):
        load_context(store)


def test_bbd_failures_are_bounded_per_competition(monkeypatch):
    calls = []
    class Client:
        def __init__(self, token): pass
        def list_matches(self, *args, **kwargs):
            calls.append(args)
            raise BbdError("provider unavailable")
    monkeypatch.setattr("soccer_auto.kss1_training_runtime.BbdClient", Client)
    rows = history(100)
    for row in rows:
        row["home_xg"] = row["away_xg"] = None
    _, status = enrich_xg(Store(), rows, None, "test-token")
    assert len(calls) == 1
    assert status["errors"] == 1


def test_bbd_xg_receipt_is_dated_at_collection_not_backdated(monkeypatch):
    rows = history(1)
    rows[0]["home_xg"] = rows[0]["away_xg"] = None
    class Client:
        def __init__(self, token): pass
        def list_matches(self, *args, **kwargs):
            return [{"id": "11111111-1111-1111-1111-111111111111", "home": "Team 0", "away": "Team 1", "kickoff_utc": rows[0]["commence_time"]}]
        def match_stats(self, match_id):
            return {"home_xg": 1.2, "away_xg": .9}
    monkeypatch.setattr("soccer_auto.kss1_training_runtime.BbdClient", Client)
    now = datetime(2025, 2, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("soccer_auto.kss1_training_runtime.now_utc", lambda: now)
    store = Store()
    result, status = enrich_xg(store, rows, None, "test-token")
    assert status["fetched"] == 1
    assert result[0]["xg_available_at"] == "2025-02-01T00:00:00Z"
    assert result[0]["home_xg"] == 1.2
    assert len(store.artifacts) == 1


def test_goals_schedule_is_independent_and_uses_existing_soccer_role():
    root = Path(__file__).resolve().parents[2]
    template = yaml.load((root / "soccer-auto-template.yaml").read_text(), Loader=CloudFormationLoader)
    resources = template["Resources"]
    market = resources["SoccerTrainerFunction"]["Properties"]
    goals = resources["SoccerGoalsTrainerFunction"]["Properties"]
    assert market["Handler"] == "soccer_auto.trainer.trainer_handler"
    assert goals["Handler"] == "soccer_auto.kss1_training_runtime.trainer_handler"
    assert goals["Role"] == market["Role"]
    assert goals["Events"]["TrainGoalsEverySixHours"]["Properties"]["Schedule"] == "cron(43 1/6 * * ? *)"
    assert "SOCCER_AUTO_BBD_API_KEY" not in template["Globals"]["Function"]["Environment"]["Variables"]
    assert "SOCCER_AUTO_BBD_API_KEY" not in goals["Environment"]["Variables"]
    assert goals["Environment"]["Variables"]["SOCCER_AUTO_BBD_SECRET_ARN"] == ["BbdConfigured", "SoccerBbdApiSecret", ""]
    assert resources["SoccerBbdApiSecret"]["Condition"] == "BbdConfigured"
    policies = resources["SoccerAutoRuntimeRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    secret_permissions = [p for p in policies if p.get("Action") == "secretsmanager:GetSecretValue"]
    assert secret_permissions[0]["Resource"] == ["SoccerOddsApiSecret", ["BbdConfigured", "SoccerBbdApiSecret", "AWS::NoValue"]]


@pytest.mark.parametrize("payload", [{"error": "bad token"}, {"data": {"error": "not available", "home_xg": 2, "away_xg": 1}}])
def test_bbd_error_envelopes_cannot_become_training_inputs(payload):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(payload).encode()
    client = BbdClient("test-token", opener=lambda *args, **kwargs: Response())
    with pytest.raises(BbdError, match="error envelope"):
        client.match_stats("11111111-1111-1111-1111-111111111111")


def test_goals_status_reports_missing_training_without_promoting():
    from soccer_auto.kss1_training_runtime import goals_status
    result = goals_status(Store())
    assert result["reason"] == "GOALS_TRAINER_HAS_NOT_COMPLETED"
    assert result["authority"] == "SHADOW_LEARNING"
    assert result["automatic_prediction_allowed"] is False


def test_large_prediction_evidence_is_retained_outside_ddb(monkeypatch):
    from soccer_auto.kss1_runtime import write_kss1_shadow
    from tests.soccer_auto.test_kss1_goals_training import fixture
    evidence = {"source_receipts": ["x" * 310_000], "feature_digest": "feature-digest"}
    item = {"PK": "future", "SK": "shadow", "goals_features": evidence}
    monkeypatch.setattr("soccer_auto.kss1_runtime.build_kss1_shadow_item", lambda *args, **kwargs: deepcopy(item))
    store = Store()
    written = []
    store.put_prediction = lambda row: written.append(row) or True
    assert write_kss1_shadow(store, fixture(), "2025-10-01T15:00:00Z")["written"]
    assert "goals_features" not in written[0]
    assert store.read_json(written[0]["goals_features_uri"]) == evidence
    assert written[0]["goals_feature_digest"] == "feature-digest"


@pytest.mark.parametrize("failure", ["missing", "error"])
def test_failed_stats_rotate_so_older_available_xg_is_eventually_collected(monkeypatch, failure):
    rows = history(21)
    for row in rows:
        row["home_xg"] = row["away_xg"] = None
    matches = [{"id": f"11111111-1111-1111-1111-{i:012d}", "home": r["home_team"], "away": r["away_team"], "kickoff_utc": r["commence_time"]} for i, r in enumerate(rows)]
    calls = []
    class Client:
        def __init__(self, token): pass
        def list_matches(self, *args, **kwargs): return matches
        def match_stats(self, match_id):
            calls.append(match_id)
            if match_id == matches[0]["id"]:
                return {"home_xg": 1.2, "away_xg": .8}
            if failure == "error":
                raise BbdError("unavailable")
            return {}
    monkeypatch.setattr("soccer_auto.kss1_training_runtime.BbdClient", Client)
    first, _ = enrich_xg(Store(), deepcopy(rows), None, "test-token")
    assert len(calls) == 20
    assert matches[0]["id"] not in calls
    second, _ = enrich_xg(Store(), deepcopy(rows), {"history": first}, "test-token")
    assert calls[20] == matches[0]["id"]
    assert second[0]["home_xg"] == 1.2
    assert all(r.get("xg_last_attempt_at") for r in second)


def test_bbd_token_reads_only_configured_secret(monkeypatch):
    from soccer_auto.kss1_training_runtime import _bbd_token
    calls = []
    class Secrets:
        def get_secret_value(self, **kwargs):
            calls.append(kwargs)
            return {"SecretString": "test-token"}
    monkeypatch.setattr("soccer_auto.kss1_training_runtime.boto3.client", lambda service: Secrets() if service == "secretsmanager" else None)
    monkeypatch.delenv("SOCCER_AUTO_BBD_SECRET_ARN", raising=False)
    monkeypatch.setenv("SOCCER_AUTO_BBD_API_KEY", "ignored-direct-key")
    assert _bbd_token() == ""
    assert calls == []
    monkeypatch.setenv("SOCCER_AUTO_BBD_SECRET_ARN", "test-secret-arn")
    assert _bbd_token() == "test-token"
    assert calls == [{"SecretId": "test-secret-arn"}]
