from sports.nba.algorithm import rank_nba_b11c1 
import json

# import requests

    # Rank NBA combos: POST /v1/rank/nba
    if path == "/v1/rank/nba" and method == "POST":
        payload = _parse_json(event.get("body"))
        games = payload.get("games")

        if not isinstance(games, list) or len(games) != 3:
            return _resp(400, {
                "ok": False,
                "error": "Provide exactly 3 games in body.games",
                "example": {
                    "games": [
                        {"game_id": "G1", "home": "BOS", "away": "MIA", "ml": {"home": -180, "away": 155}},
                        {"game_id": "G2", "home": "NYK", "away": "GSW", "ml": {"home": -130, "away": 110}},
                        {"game_id": "G3", "home": "LAL", "away": "DEN", "ml": {"home": 120, "away": -140}}
                    ]
                }
            })

        try:
            result = rank_nba_b11c1(games)
            return _resp(200, result)
        except Exception as e:
            return _resp(500, {"ok": False, "error": str(e)})
def lambda_handler(event, context):
    """Sample pure Lambda function

    Parameters
    ----------
    event: dict, required
        API Gateway Lambda Proxy Input Format

        Event doc: https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html#api-gateway-simple-proxy-for-lambda-input-format

    context: object, required
        Lambda Context runtime methods and attributes

        Context doc: https://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html

    Returns
    ------
    API Gateway Lambda Proxy Output Format: dict

        Return doc: https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html
    """

    # try:
    #     ip = requests.get("http://checkip.amazonaws.com/")
    # except requests.RequestException as e:
    #     # Send some context about this error to Lambda Logs
    #     print(e)

    #     raise e

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "hello world",
            # "location": ip.text.replace("\n", "")
        }),
    }
