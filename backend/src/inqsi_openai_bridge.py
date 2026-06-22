"""InQsi OpenAI bridge Lambda.

Controlled AWS-side bridge to OpenAI. API Gateway routes are protected by an
admin token. Direct internal jobs are protected by AWS IAM because they require
permission to invoke this Lambda.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

import boto3

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "content-type,authorization,x-inqsi-admin-token",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}

SECRETS = boto3.client("secretsmanager")

DIRECT_TASKS: Dict[str, Dict[str, str]] = {
    "code_review": {
        "model": "gpt-5-mini",
        "reasoning": "medium",
        "system": "Review InQsi code for bugs, security risks, broken deploy paths, and missing tests. Return exact fixes.",
    },
    "deployment_diagnosis": {
        "model": "gpt-5-mini",
        "reasoning": "medium",
        "system": "Diagnose failed GitHub or AWS deployments. Identify root cause, exact file/line if possible, and next fix.",
    },
    "failed_log_summary": {
        "model": "gpt-5-mini",
        "reasoning": "medium",
        "system": "Summarize failed logs into plain English. Separate cause, impact, and next action.",
    },
    "admin_tool_plan": {
        "model": "gpt-5-mini",
        "reasoning": "medium",
        "system": "Design internal admin AI tools for InQsi. Keep controls private and avoid exposing secrets in the browser.",
    },
    "sports_api_algorithm_lab": {
        "model": "gpt-5-pro",
        "reasoning": "high",
        "system": "Analyze sports API/market data for stronger InQsi algorithm design. Do not invent data, do not claim guaranteed wins, and recommend testable scoring improvements only.",
    },
}


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status_code, "headers": CORS_HEADERS, "body": json.dumps(body)}


def method_and_path(event: Dict[str, Any]) -> Tuple[str, str]:
    request_context = event.get("requestContext", {})
    method = (
        request_context.get("http", {}).get("method")
        or request_context.get("httpMethod")
        or event.get("httpMethod")
        or "GET"
    )
    path = event.get("rawPath") or event.get("path") or "/"
    return method.upper(), path.rstrip("/") or "/"


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw_body = event.get("body")
    if not raw_body:
        return {}
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return {}


def normalized_headers(event: Dict[str, Any]) -> Dict[str, str]:
    headers = event.get("headers") or {}
    return {str(key).lower(): str(value) for key, value in headers.items() if value is not None}


def require_admin(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    expected = os.environ.get("INQSI_BRIDGE_ADMIN_TOKEN", "").strip()
    if not expected or expected == "DISABLED_UNTIL_OWNER_TOKEN_IS_SET":
        return response(503, {
            "error": "bridge_admin_token_not_configured",
            "message": "Set INQSI_BRIDGE_ADMIN_TOKEN before enabling OpenAI bridge POST routes.",
        })
    provided = normalized_headers(event).get("x-inqsi-admin-token", "")
    if provided != expected:
        return response(403, {"error": "valid_x_inqsi_admin_token_required"})
    return None


def load_openai_key() -> str:
    secret_name = os.environ.get("OPENAI_SECRET_NAME", "inqsi/openai/api-key")
    result = SECRETS.get_secret_value(SecretId=secret_name)
    secret_string = result.get("SecretString") or ""
    if not secret_string:
        raise RuntimeError("OpenAI secret has no SecretString")
    try:
        parsed = json.loads(secret_string)
        key = parsed.get("OPENAI_API_KEY") or parsed.get("openai_api_key")
    except json.JSONDecodeError:
        key = secret_string
    if not key or not str(key).startswith("sk-"):
        raise RuntimeError("OPENAI_API_KEY is missing or malformed in Secrets Manager")
    return str(key)


def extract_text(openai_payload: Dict[str, Any]) -> str:
    if isinstance(openai_payload.get("output_text"), str):
        return openai_payload["output_text"]
    parts = []
    for item in openai_payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def call_openai(
    task: str,
    prompt: str,
    context: str = "",
    system_override: str = "",
    model_override: str = "",
    reasoning_override: str = "",
    max_tokens_override: Optional[int] = None,
) -> Dict[str, Any]:
    api_key = load_openai_key()
    model = model_override or os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    reasoning_effort = reasoning_override or os.environ.get("OPENAI_REASONING_EFFORT", "medium")
    reasoning_summary = os.environ.get("OPENAI_REASONING_SUMMARY", "auto")
    max_output_tokens = max_tokens_override or int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "1200"))

    system_message = (
        "You are the InQsi internal AI bridge. Be direct, operational, and careful. "
        "No fake data, no default zeros, no pretending a step is complete. "
        "Prefer GitHub/CloudFormation controlled changes over direct production mutation. "
        "Never expose secrets or credentials. "
        + system_override
    ).strip()

    user_message = f"Task: {task}\n\nPrompt:\n{prompt[:30000]}"
    if context:
        user_message += f"\n\nContext:\n{context[:30000]}"

    payload = {
        "model": model,
        "reasoning": {"effort": reasoning_effort, "summary": reasoning_summary},
        "input": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "max_output_tokens": max_output_tokens,
    }

    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=250) as result:
            parsed = json.loads(result.read().decode("utf-8"))
            return {
                "ok": True,
                "task": task,
                "model": model,
                "reasoningEffort": reasoning_effort,
                "text": extract_text(parsed),
                "openaiResponseId": parsed.get("id"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "task": task,
            "error": "openai_http_error",
            "status": exc.code,
            "detail": exc.read().decode("utf-8")[:2000],
        }


def handle_health() -> Dict[str, Any]:
    secret_name = os.environ.get("OPENAI_SECRET_NAME", "inqsi/openai/api-key")
    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", "medium")
    try:
        SECRETS.describe_secret(SecretId=secret_name)
        secret_status = "found"
    except Exception as exc:
        secret_status = f"not_found_or_no_access: {type(exc).__name__}"
    return response(200, {
        "ok": True,
        "service": "inqsi-openai-bridge",
        "secretName": secret_name,
        "secretStatus": secret_status,
        "model": model,
        "reasoningEffort": reasoning_effort,
        "apiGatewayDefault": "fast-medium",
        "directTasks": sorted(DIRECT_TASKS.keys()),
        "adminProtected": True,
    })


def handle_direct_task(event: Dict[str, Any]) -> Dict[str, Any]:
    task = str(event.get("task") or "direct_smoke_test").strip().replace("-", "_")
    if event.get("directSmokeTest") is True:
        task = "deployment_diagnosis"
    if task not in DIRECT_TASKS:
        return response(400, {"ok": False, "error": "unknown_direct_task", "allowedTasks": sorted(DIRECT_TASKS.keys())})
    config = DIRECT_TASKS[task]
    prompt = str(event.get("prompt") or "Confirm the InQsi OpenAI bridge is working. Keep the answer under 20 words.")
    context = str(event.get("context") or "")
    result = call_openai(
        task=task,
        prompt=prompt,
        context=context,
        system_override=config["system"],
        model_override=str(event.get("model") or config["model"]),
        reasoning_override=str(event.get("reasoningEffort") or config["reasoning"]),
        max_tokens_override=int(event.get("maxOutputTokens") or (3200 if config["model"] == "gpt-5-pro" else 1200)),
    )
    return response(200 if result.get("ok") else 502, result)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if (event.get("directTask") is True or event.get("directSmokeTest") is True) and "requestContext" not in event:
        return handle_direct_task(event)

    method, path = method_and_path(event)
    if method == "OPTIONS":
        return response(200, {"ok": True})
    if method == "GET" and path == "/v1/ai/health":
        return handle_health()
    if method != "POST" or not path.startswith("/v1/ai/"):
        return response(404, {"error": "route_not_found", "path": path})

    auth_error = require_admin(event)
    if auth_error:
        return auth_error

    body = parse_body(event)
    prompt = (body.get("prompt") or body.get("message") or "").strip()
    if not prompt:
        return response(400, {"error": "prompt_required"})
    task = path.split("/v1/ai/", 1)[-1].replace("-", "_")
    result = call_openai(task=task, prompt=prompt, context=body.get("context") or "")
    return response(200 if result.get("ok") else 502, result)
