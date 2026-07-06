from __future__ import annotations

import json
from typing import Any, Dict


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return {
        "statusCode": 200,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,OPTIONS",
            "access-control-allow-headers": "content-type,authorization",
        },
        "body": json.dumps({
            "ok": True,
            "service": "inqsi-moderation-policy",
            "route": "/v1/moderation/policy",
            "policy": {
                "imageModeration": "enabled_when_rekognition_policy_is_attached",
                "textModeration": "enabled_when_rekognition_policy_is_attached",
                "deploymentSmoke": "read_only",
                "secretExposed": False,
            },
        }),
    }
