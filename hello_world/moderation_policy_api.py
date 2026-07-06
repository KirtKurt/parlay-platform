def lambda_handler(event, context):
    return {
        "statusCode": 200,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,OPTIONS",
            "access-control-allow-headers": "content-type,authorization"
        },
        "body": "{\"ok\":true,\"service\":\"inqsi-moderation-policy\",\"route\":\"/v1/moderation/policy\",\"deploymentSmoke\":\"read_only\",\"secretExposed\":false}"
    }
