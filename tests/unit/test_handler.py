import json
import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from hello_world import api


def test_health_check():
    event = {
        "httpMethod": "GET",
        "path": "/v1/health",
        "queryStringParameters": None,
        "body": None,
    }

    response = api.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["ok"] is True
    assert body["status"] == "healthy"
    assert body["service"] == "parlay-platform"


def test_rank_nba_requires_three_games():
    event = {
        "httpMethod": "POST",
        "path": "/v1/rank/nba",
        "queryStringParameters": None,
        "body": json.dumps({"games": []}),
    }

    response = api.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 400
    assert body["ok"] is False
    assert "exactly 3 games" in body["error"]
