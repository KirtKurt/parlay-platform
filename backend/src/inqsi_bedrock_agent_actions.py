"""InQsi Bedrock agent action Lambda.

This Lambda is designed to be used as an Amazon Bedrock Agent action group.
It gives the agent controlled tools to inspect the AWS deployment and trigger
GitHub deployment workflows without exposing broad production mutation powers.

Default posture:
- AWS inspection is read-only.
- GitHub workflow dispatch requires a GitHub token stored in Secrets Manager.
- Dangerous direct AWS mutation is intentionally not implemented here.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3

DEFAULT_STACK_NAME = os.environ.get("INQSI_STACK_NAME", "parlay-platform-dev")
DEFAULT_REPO = os.environ.get("GITHUB_REPOSITORY", "KirtKurt/parlay-platform")
DEFAULT_WORKFLOW = os.environ.get("GITHUB_DEPLOY_WORKFLOW", "deploy.yml")
DEFAULT_BRANCH = os.environ.get("GITHUB_DEPLOY_BRANCH", "main")
GITHUB_TOKEN_SECRET_NAME = os.environ.get("GITHUB_TOKEN_SECRET_NAME", "")

cf = boto3.client("cloudformation")
lambda_client = boto3.client("lambda")
ddb = boto3.client("dynamodb")
apigw = boto3.client("apigateway")
sts = boto3.client("sts")
secrets = boto3.client("secretsmanager")


def bedrock_response(event: Dict[str, Any], status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", "InqsiEngineeringActions"),
            "apiPath": event.get("apiPath", "/unknown"),
            "httpMethod": event.get("httpMethod", "GET"),
            "httpStatusCode": status_code,
            "responseBody": {
                "application/json": {
                    "body": json.dumps(body, default=str),
                }
            },
        },
    }


def parameter_value(event: Dict[str, Any], name: str, default: Optional[str] = None) -> Optional[str]:
    for item in event.get("parameters") or []:
        if item.get("name") == name:
            return item.get("value")
    request_body = event.get("requestBody", {}) or {}
    content = request_body.get("content", {}) or {}
    app_json = content.get("application/json", {}) or {}
    properties = app_json.get("properties", []) or []
    for item in properties:
        if item.get("name") == name:
            return item.get("value")
    return default


def github_token() -> str:
    if not GITHUB_TOKEN_SECRET_NAME:
        raise RuntimeError("GITHUB_TOKEN_SECRET_NAME is not configured")
    secret = secrets.get_secret_value(SecretId=GITHUB_TOKEN_SECRET_NAME)
    raw = secret.get("SecretString")
    if not raw and secret.get("SecretBinary"):
        raw = base64.b64decode(secret["SecretBinary"]).decode("utf-8")
    if not raw:
        raise RuntimeError("GitHub token secret is empty")
    try:
        parsed = json.loads(raw)
        return parsed.get("GITHUB_TOKEN") or parsed.get("token") or raw
    except json.JSONDecodeError:
        return raw


def github_request(method: str, url: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "inqsi-bedrock-agent-actions",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
            return {"status": response.status, "body": json.loads(payload) if payload else {}}
    except HTTPError as exc:
        details = exc.read().decode("utf-8") if exc.fp else ""
        return {"status": exc.code, "error": details}
    except URLError as exc:
        return {"status": 503, "error": str(exc)}


def stack_status(stack_name: str) -> Dict[str, Any]:
    stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    resources = cf.describe_stack_resources(StackName=stack_name)["StackResources"]
    inqsi_resources = [
        {
            "logicalId": item.get("LogicalResourceId"),
            "physicalId": item.get("PhysicalResourceId"),
            "type": item.get("ResourceType"),
            "status": item.get("ResourceStatus"),
        }
        for item in resources
        if str(item.get("LogicalResourceId", "")).lower().startswith("inqsi")
    ]
    return {
        "stackName": stack_name,
        "status": stack.get("StackStatus"),
        "updatedTime": stack.get("LastUpdatedTime"),
        "outputs": stack.get("Outputs", []),
        "inqsiResources": inqsi_resources,
    }


def list_inqsi_tables() -> Dict[str, Any]:
    paginator = ddb.get_paginator("list_tables")
    names: List[str] = []
    for page in paginator.paginate():
        names.extend(page.get("TableNames", []))
    inqsi = [name for name in names if name.startswith("inqsi_")]
    details = []
    for name in inqsi:
        table = ddb.describe_table(TableName=name)["Table"]
        details.append({
            "tableName": name,
            "status": table.get("TableStatus"),
            "itemCount": table.get("ItemCount"),
            "billingMode": (table.get("BillingModeSummary") or {}).get("BillingMode"),
            "gsis": [idx.get("IndexName") for idx in table.get("GlobalSecondaryIndexes", [])],
        })
    return {"tables": details}


def list_inqsi_lambdas() -> Dict[str, Any]:
    paginator = lambda_client.get_paginator("list_functions")
    functions = []
    for page in paginator.paginate():
        for fn in page.get("Functions", []):
            name = fn.get("FunctionName", "")
            if "Inqsi" in name or "inqsi" in name:
                functions.append({
                    "functionName": name,
                    "runtime": fn.get("Runtime"),
                    "handler": fn.get("Handler"),
                    "lastModified": fn.get("LastModified"),
                    "state": fn.get("State"),
                })
    return {"functions": functions}


def backend_health_target(stack_name: str) -> Dict[str, Any]:
    status = stack_status(stack_name)
    api_url = None
    for output in status.get("outputs", []):
        if output.get("OutputKey") == "ApiUrl":
            api_url = output.get("OutputValue")
            break
    return {
        "stackName": stack_name,
        "apiUrl": api_url,
        "healthUrl": f"{api_url}v1/health" if api_url else None,
        "stackStatus": status.get("status"),
    }


def trigger_github_deploy(repo: str, workflow: str, branch: str) -> Dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    result = github_request("POST", url, {"ref": branch})
    if result.get("status") in [200, 201, 202, 204]:
        return {"triggered": True, "repo": repo, "workflow": workflow, "branch": branch, "status": result.get("status")}
    return {"triggered": False, "repo": repo, "workflow": workflow, "branch": branch, "githubResponse": result}


def latest_github_runs(repo: str, workflow: str) -> Dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?per_page=5"
    result = github_request("GET", url)
    if result.get("status") != 200:
        return {"runs": [], "githubResponse": result}
    runs = []
    for run in result.get("body", {}).get("workflow_runs", []):
        runs.append({
            "id": run.get("id"),
            "name": run.get("name"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "headSha": run.get("head_sha"),
            "createdAt": run.get("created_at"),
            "updatedAt": run.get("updated_at"),
            "htmlUrl": run.get("html_url"),
        })
    return {"runs": runs}


def dispatch(event: Dict[str, Any]) -> Dict[str, Any]:
    path = event.get("apiPath") or "/"
    method = event.get("httpMethod") or "GET"
    stack_name = parameter_value(event, "stackName", DEFAULT_STACK_NAME) or DEFAULT_STACK_NAME
    repo = parameter_value(event, "repo", DEFAULT_REPO) or DEFAULT_REPO
    workflow = parameter_value(event, "workflow", DEFAULT_WORKFLOW) or DEFAULT_WORKFLOW
    branch = parameter_value(event, "branch", DEFAULT_BRANCH) or DEFAULT_BRANCH

    if method == "GET" and path == "/aws/identity":
        return {"identity": sts.get_caller_identity()}
    if method == "GET" and path == "/aws/stack-status":
        return stack_status(stack_name)
    if method == "GET" and path == "/aws/inqsi-tables":
        return list_inqsi_tables()
    if method == "GET" and path == "/aws/inqsi-lambdas":
        return list_inqsi_lambdas()
    if method == "GET" and path == "/aws/backend-health-target":
        return backend_health_target(stack_name)
    if method == "POST" and path == "/github/trigger-deploy":
        return trigger_github_deploy(repo, workflow, branch)
    if method == "GET" and path == "/github/latest-runs":
        return latest_github_runs(repo, workflow)
    return {"error": "unknown_action", "path": path, "method": method}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        result = dispatch(event)
        status = 400 if result.get("error") else 200
        return bedrock_response(event, status, result)
    except Exception as exc:
        return bedrock_response(event, 500, {"error": "agent_action_failed", "message": str(exc)})
