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
    if 'READ_STACK_NAME = os.environ.get("MLB_AUTO_ML_READ_STACK_NAME", "parlay-platform-dev").strip()' not in text:
        text = text.replace(
            'API_BASE_URL = os.environ.get("MLB_AUTO_ML_API_BASE_URL", "").rstrip("/")\n',
            'API_BASE_URL = os.environ.get("MLB_AUTO_ML_API_BASE_URL", "").rstrip("/")\n'
            'READ_STACK_NAME = os.environ.get("MLB_AUTO_ML_READ_STACK_NAME", "parlay-platform-dev").strip()\n'
            'READ_LOGICAL_ID = os.environ.get("MLB_AUTO_ML_READ_LOGICAL_ID", "MLBV3ReadFunction").strip()\n'
            '_READ_FUNCTION_NAME: Optional[str] = None\n',
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
    new = '''def _resolve_read_function_name() -> str:
    global _READ_FUNCTION_NAME
    if _READ_FUNCTION_NAME:
        return _READ_FUNCTION_NAME
    if not READ_STACK_NAME or not READ_LOGICAL_ID:
        raise RuntimeError("MLB_AUTO_ML_READ_STACK_CONFIGURATION_MISSING")
    detail = boto3.client("cloudformation").describe_stack_resource(
        StackName=READ_STACK_NAME,
        LogicalResourceId=READ_LOGICAL_ID,
    ).get("StackResourceDetail") or {}
    name = str(detail.get("PhysicalResourceId") or "").strip()
    if not name:
        raise RuntimeError("MLB_AUTO_ML_READ_FUNCTION_UNRESOLVED")
    _READ_FUNCTION_NAME = name
    return name


def _direct_lambda_json(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    function_name = _resolve_read_function_name()
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
        FunctionName=function_name,
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
    # The public API path is protected by API Gateway's integration timeout.
    # Inside AWS use the exact existing read Lambda directly so the same
    # persisted, read-only authority is retained without a gateway 504.
    # No prediction is recalculated or rewritten here.
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
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
    if old not in text and "def _resolve_read_function_name" not in text:
        raise RuntimeError("ml_authority HTTP block drifted; refusing unsafe patch")
    if old in text:
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def patch_template() -> None:
    path = Path("mlb-auto-llm-template.yaml")
    text = path.read_text(encoding="utf-8")
    env_line = "          MLB_AUTO_ML_API_BASE_URL: !Ref MlbMlApiBaseUrl\n"
    new_env_line = (
        env_line
        + "          MLB_AUTO_ML_READ_STACK_NAME: parlay-platform-dev\n"
        + "          MLB_AUTO_ML_READ_LOGICAL_ID: MLBV3ReadFunction\n"
    )
    if "MLB_AUTO_ML_READ_STACK_NAME" not in text:
        text = text.replace(env_line, new_env_line)
    while text.count("MLB_AUTO_ML_READ_STACK_NAME") < 2:
        first = text.find("MLB_AUTO_ML_READ_STACK_NAME")
        idx = text.find(env_line, first + 1)
        if idx < 0:
            break
        text = text[:idx] + new_env_line + text[idx + len(env_line):]

    invoke_policy = (
        "            - Effect: Allow\n"
        "              Action:\n"
        "                - lambda:InvokeFunction\n"
        "              Resource: !Sub 'arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:parlay-platform-dev-MLBV3ReadFunction-*'\n"
        "            - Effect: Allow\n"
        "              Action:\n"
        "                - cloudformation:DescribeStackResource\n"
        "              Resource: !Sub 'arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/parlay-platform-dev/*'\n"
    )
    market = (
        "            - Effect: Allow\n"
        "              Action:\n"
        "                - aws-marketplace:ViewSubscriptions\n"
        "              Resource: '*'\n"
    )
    existing = text.count("cloudformation:DescribeStackResource")
    cursor = 0
    for _ in range(max(2 - existing, 0)):
        idx = text.find(market, cursor)
        if idx < 0:
            raise RuntimeError("template policy marker missing")
        text = text[:idx] + invoke_policy + text[idx:]
        cursor = idx + len(invoke_policy) + len(market)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_authority()
    patch_template()


if __name__ == "__main__":
    main()
