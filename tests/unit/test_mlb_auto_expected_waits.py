import json
from io import BytesIO

import pytest
import orchestrator_v3 as subject


def test_exact_no_champion_response_is_typed_but_malformed_response_still_errors(monkeypatch):
    authority = subject.ml_authority
    payload = {"status": "NO_QUALIFIED_CHAMPION", "publicationClosed": True,
               "productionSelectionAllowed": False, "qualifiedChampionPresent": False}
    class Client:
        def invoke(self, **kwargs):
            return {"Payload": BytesIO(json.dumps({"statusCode": 503, "body": json.dumps(payload)}).encode())}
    monkeypatch.setattr(authority, "_resolve_read_function_name", lambda: "read-only-test")
    monkeypatch.setattr(authority.boto3, "client", lambda *a, **k: Client())
    with pytest.raises(authority.QualifiedChampionUnavailable):
        authority._direct_lambda_json("/v1/mlb/game-winners")
    payload["winner_predictions"] = [{"predictedWinner": "unexpected"}]
    with pytest.raises(RuntimeError, match="MLB_ML_DIRECT_READ_HTTP_503"):
        authority._direct_lambda_json("/v1/mlb/game-winners")


@pytest.mark.parametrize("error,status", [
    (subject.ml_authority.QualifiedChampionUnavailable("NO_QUALIFIED_CHAMPION"), "AUTHORITY_READINESS_GATED"),
    (subject.strict_bedrock.PublicationDeadlineMissed("AUTHORITATIVE_CARD_DEADLINE_MISSED:fixture"), "AUTHORITATIVE_CARD_DEADLINE_MISSED"),
])
def test_scheduled_waits_stop_retries_without_publishing_or_claiming_health(monkeypatch, error, status):
    def fail(*args):
        raise error
    monkeypatch.setattr(subject.strict_bedrock, "lambda_handler", fail)
    result = subject.lambda_handler({"source": "aws.events"}, None)
    assert result["status"] == status
    assert result["ok"] is False
    assert result["publicationClosed"] is True
    assert result["productionSelectionAllowed"] is False
    assert result["cardPublished"] is False
    with pytest.raises(type(error)):
        subject.lambda_handler({"rawPath": "/status"}, None)


def test_unexpected_runtime_failure_is_not_suppressed(monkeypatch):
    def fail(*args):
        raise RuntimeError("UNEXPECTED_FAILURE")
    monkeypatch.setattr(subject.strict_bedrock, "lambda_handler", fail)
    with pytest.raises(RuntimeError, match="UNEXPECTED_FAILURE"):
        subject.lambda_handler({}, None)
