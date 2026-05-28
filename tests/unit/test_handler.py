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


def test_rank_mlb_returns_eight_combos():
    event = {
        "httpMethod": "POST",
        "path": "/v1/rank/mlb",
        "queryStringParameters": None,
        "body": json.dumps(
            {
                "games": [
                    {"game_id": "G1", "home": "Yankees", "away": "Red Sox", "ml": {"home": -145, "away": 125}},
                    {"game_id": "G2", "home": "Mets", "away": "Braves", "ml": {"home": 110, "away": -130}},
                    {"game_id": "G3", "home": "Dodgers", "away": "Padres", "ml": {"home": -175, "away": 150}},
                ]
            }
        ),
    }

    response = api.lambda_handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["ok"] is True
    assert body["sport"] == "mlb"
    assert body["model"] == "MLB-B1.0A.3"
    assert len(body["ranked"]) == 8
    assert all("underdogs" in row for row in body["ranked"])
    assert all("parlay_decimal" in row for row in body["ranked"])
