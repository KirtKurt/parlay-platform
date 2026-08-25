from __future__ import annotations

from pathlib import Path


def patch_authority() -> None:
    path = Path("mlb_auto_llm/ml_authority.py")
    text = path.read_text(encoding="utf-8")
    if "import boto3\n" not in text:
        text = text.replace(
            "import urllib.request\nfrom datetime import datetime, timezone\n",
            "import urllib.request\n\nimport boto3\nfrom datetime import datetime, timezone\n",
            1,
        )
    marker = 'READ_FUNCTION_NAME = os.environ.get("MLB_AUTO_ML_READ_FUNCTION", "").strip()'
    if marker not in text:
        text = text.replace(
            'API_BASE_URL = os.environ.get("MLB_AUTO_ML_API_BASE_URL", "").rstrip("/")\n',
            'API_BASE_URL = os.environ.get("MLB_AUTO_ML_API_BASE_URL", "").rstrip("/")\n'
            'READ_FUNCTION_NAME = os.environ.get("MLB_AUTO_ML_READ_FUNCTION", "").strip()\n',
            1,
        )

    old = '''def _http_json(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not API_BASE_URL:
        raise RuntimeError("MLB_AUTO_ML_API_BASE_URL_MISSING")
    query = urllib.parse.urlencode(
        {key: value for key, value in (params or {}).items() if value is not None}
    )
    url = API_BASE_URL + "/" + path.lstrip("/")
    if query:
        url += "?" + query
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-mlb-auto-ml-authority/1.0",
        },
    )
    timeout_seconds = float(os.environ.get("MLB_AUTO_ML_HTTP_TIMEOUT_SECONDS", "60"))
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("MLB_ML_API_RESPONSE_NOT_OBJECT")
    return payload
'''
    new = '''def _direct_lambda_json(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not READ_FUNCTION_NAME:
        raise RuntimeError("MLB_AUTO_ML_READ_FUNCTION_MISSING")
    query = {
        str(key): str(value)
        for key, value in (params or {}).items()
        if value is not None
    }
    event = {
        "rawPath": path,
        "path": path,
        "httpMethod": "GET",
        "requestContext": {"http": {"method": "GET", "path": path}},
        "queryStringParameters": query,
    }
    response = boto3.client("lambda").invoke(
        FunctionName=READ_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode("utf-8"),
    )
    raw = response["Payload"].read()
    envelope = json.loads(raw.decode("utf-8")) if raw else {}
    if response.get("FunctionError"):
        raise RuntimeError(
            "MLB_ML_DIRECT_READ_FUNCTION_ERROR:"
            + json.dumps(envelope, sort_keys=True, default=str)[:2000]
        )
    if not isinstance(envelope, dict):
        raise RuntimeError("MLB_ML_DIRECT_READ_ENVELOPE_NOT_OBJECT")
    status = int(envelope.get("statusCode") or 200)
    body = envelope.get("body", envelope)
    payload = json.loads(body) if isinstance(body, str) else body
    if status >= 400:
        raise RuntimeError(
            f"MLB_ML_DIRECT_READ_HTTP_{status}:"
            + json.dumps(payload, sort_keys=True, default=str)[:2000]
        )
    if not isinstance(payload, dict):
        raise RuntimeError("MLB_ML_API_RESPONSE_NOT_OBJECT")
    return payload


def _http_json(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # API Gateway has a hard integration timeout shorter than the protected MLB
    # read Lambda's worst-case cold path. In AWS, call that exact read Lambda
    # directly so the same persisted, read-only authority is used without a
    # gateway 504. HTTP remains a local/test fallback only when no function is
    # configured. This does not recalculate or rewrite any prediction.
    if READ_FUNCTION_NAME:
        return _direct_lambda_json(path, params)
    if not API_BASE_URL:
        raise RuntimeError("MLB_AUTO_ML_API_BASE_URL_MISSING")
    query = urllib.parse.urlencode(
        {key: value for key, value in (params or {}).items() if value is not None}
    )
    url = API_BASE_URL + "/" + path.lstrip("/")
    if query:
        url += "?" + query
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-mlb-auto-ml-authority/1.0",
        },
    )
    timeout_seconds = float(os.environ.get("MLB_AUTO_ML_HTTP_TIMEOUT_SECONDS", "60"))
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("MLB_ML_API_RESPONSE_NOT_OBJECT")
    return payload
'''
    if old not in text and "def _direct_lambda_json" not in text:
        raise RuntimeError("ml_authority HTTP block drifted; refusing unsafe patch")
    if old in text:
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def patch_template() -> None:
    path = Path("mlb-auto-llm-template.yaml")
    text = path.read_text(encoding="utf-8")
    if "  MlbMlReadFunctionName:\n" not in text:
        text = text.replace(
            "  MlbMlApiBaseUrl:\n    Type: String\n",
            "  MlbMlApiBaseUrl:\n    Type: String\n  MlbMlReadFunctionName:\n    Type: String\n",
            1,
        )
    env_line = "          MLB_AUTO_ML_API_BASE_URL: !Ref MlbMlApiBaseUrl\n"
    new_env_line = env_line + "          MLB_AUTO_ML_READ_FUNCTION: !Ref MlbMlReadFunctionName\n"
    if "MLB_AUTO_ML_READ_FUNCTION" not in text:
        text = text.replace(env_line, new_env_line)
    else:
        # Ensure both isolated Lambda functions have the direct-read environment.
        while text.count("MLB_AUTO_ML_READ_FUNCTION") < 2 and env_line in text:
            idx = text.find(env_line, text.find("MLB_AUTO_ML_READ_FUNCTION") + 1)
            if idx < 0:
                break
            text = text[:idx] + new_env_line + text[idx + len(env_line):]

    policy = (
        "            - Effect: Allow\n"
        "              Action:\n"
        "                - lambda:InvokeFunction\n"
        "              Resource: !Sub 'arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:${MlbMlReadFunctionName}'\n"
    )
    market = (
        "            - Effect: Allow\n"
        "              Action:\n"
        "                - aws-marketplace:ViewSubscriptions\n"
        "              Resource: '*'\n"
    )
    need = 2 - text.count("lambda:InvokeFunction")
    cursor = 0
    for _ in range(max(need, 0)):
        idx = text.find(market, cursor)
        if idx < 0:
            raise RuntimeError("template policy marker missing")
        text = text[:idx] + policy + text[idx:]
        cursor = idx + len(policy) + len(market)
    path.write_text(text, encoding="utf-8")


def patch_deploy_workflow() -> None:
    path = Path(".github/workflows/deploy-mlb-auto-llm.yml")
    text = path.read_text(encoding="utf-8")
    if "MLB_ML_READ_FUNCTION=$(aws cloudformation describe-stack-resource" not in text:
        anchor = (
            '          test -n "$MLB_ML_API_BASE_URL"\n'
            '          test "$MLB_ML_API_BASE_URL" != "None"\n'
            '          sam deploy \\\n'
        )
        replacement = (
            '          test -n "$MLB_ML_API_BASE_URL"\n'
            '          test "$MLB_ML_API_BASE_URL" != "None"\n'
            '          MLB_ML_READ_FUNCTION=$(aws cloudformation describe-stack-resource \\\n'
            '            --stack-name parlay-platform-dev --region "$AWS_REGION" \\\n'
            '            --logical-resource-id MLBV3ReadFunction \\\n'
            "            --query 'StackResourceDetail.PhysicalResourceId' \\\n"
            '            --output text)\n'
            '          test -n "$MLB_ML_READ_FUNCTION"\n'
            '          test "$MLB_ML_READ_FUNCTION" != "None"\n'
            '          sam deploy \\\n'
        )
        if anchor not in text:
            raise RuntimeError("deploy workflow anchor missing")
        text = text.replace(anchor, replacement, 1)
    if 'MlbMlReadFunctionName="${MLB_ML_READ_FUNCTION}"' not in text:
        text = text.replace(
            '              MlbMlApiBaseUrl="${MLB_ML_API_BASE_URL}" \\\n',
            '              MlbMlApiBaseUrl="${MLB_ML_API_BASE_URL}" \\\n'
            '              MlbMlReadFunctionName="${MLB_ML_READ_FUNCTION}" \\\n',
            1,
        )
    if "grep -q 'MLB_AUTO_ML_READ_FUNCTION' mlb_auto_llm/ml_authority.py" not in text:
        text = text.replace(
            "          grep -q 'AWS_ML_RANKED_ENSEMBLE' mlb_auto_llm/ml_authority.py\n",
            "          grep -q 'AWS_ML_RANKED_ENSEMBLE' mlb_auto_llm/ml_authority.py\n"
            "          grep -q 'MLB_AUTO_ML_READ_FUNCTION' mlb_auto_llm/ml_authority.py\n"
            "          grep -q 'MlbMlReadFunctionName' mlb-auto-llm-template.yaml\n",
            1,
        )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_authority()
    patch_template()
    patch_deploy_workflow()


if __name__ == "__main__":
    main()
