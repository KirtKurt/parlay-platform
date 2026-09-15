"""Read-only evidence for the separately deployed legacy MLB AutoFunction.

This collector never invokes Lambda, reads provider secrets, or authorizes a
writer. Identity and IAM evidence must be reviewed before changing root scope.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from zipfile import ZipFile

try:
    from scripts.mlb_lambda_artifact_identity import (
        MAX_COMPRESSED_ARTIFACT_BYTES, lambda_code_sha256, zip_content_manifest,
    )
except ModuleNotFoundError:
    from mlb_lambda_artifact_identity import (
        MAX_COMPRESSED_ARTIFACT_BYTES, lambda_code_sha256, zip_content_manifest,
    )

STACK = "parlay-platform-mlb-auto-prod"
HANDLER = "mlb_auto.autonomous_handler.handler"
EXPECTED_ACCOUNT = "735707987003"
EXPECTED_READER_ARN = f"arn:aws:sts::{EXPECTED_ACCOUNT}:assumed-role/ks1-autofunction-identity-reader/ks1-autofunction-readonly"
READ_OPERATIONS = {
    "sts": {"get_caller_identity"},
    "cloudformation": {"describe_stacks", "list_stack_resources"},
    "lambda": {"get_function", "get_function_configuration"},
    "iam": {"get_role", "list_role_policies", "get_role_policy",
            "list_attached_role_policies", "get_policy", "get_policy_version"},
    "events": {"list_rules", "list_targets_by_rule"},
}


def call(clients, service, operation, **kwargs):
    if operation not in READ_OPERATIONS.get(service, set()):
        raise ValueError("Operation is outside the read-only evidence contract")
    try:
        return getattr(clients[service], operation)(**kwargs)
    except Exception as exc:
        # Record only an operation name and an AWS error code, never its message.
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(code)):
            code = "UnclassifiedError"
        raise RuntimeError(f"{service}.{operation}:{code}") from None


def pages(clients, service, operation, field, request_token="NextToken",
          response_token="NextToken", **kwargs):
    result, seen = [], set()
    while True:
        page = call(clients, service, operation, **kwargs)
        result.extend(page.get(field) or [])
        token = page.get(response_token)
        if not token:
            if page.get("IsTruncated"):
                raise ValueError("Truncated AWS evidence without a pagination token")
            return result
        if token in seen:
            raise ValueError("Repeated AWS pagination token")
        seen.add(token)
        kwargs[request_token] = token


def resource_environment(environment, resources):
    """Only expose exact matches to independently collected stack resources."""
    known = {r.get("PhysicalResourceId") for r in resources
             if r.get("ResourceType") in {"AWS::DynamoDB::Table", "AWS::S3::Bucket"}}
    result = {}
    for key, value in environment.items():
        if (re.fullmatch(r"[A-Z0-9_]*(?:TABLE|TABLE_NAME|BUCKET|BUCKET_NAME|PREFIX)", key)
                and not any(word in key for word in ("SECRET", "TOKEN", "PASSWORD", "KEY"))):
            result[key] = ({"stackResourceId": value} if value in known
                           else {"redacted": True, "sha256": hashlib.sha256(str(value).encode()).hexdigest()})
    return result


def source_summary(artifact):
    result = []
    with ZipFile(BytesIO(artifact)) as archive:
        for name in sorted(archive.namelist()):
            if not name.startswith("mlb_auto/") or not name.endswith(".py"):
                continue
            content = archive.read(name)
            tree = ast.parse(content.decode("utf-8"))
            imports, env_keys, methods = set(), set(), set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.add(node.module or "")
                elif isinstance(node, ast.Call):
                    fn = node.func
                    if isinstance(fn, ast.Attribute):
                        methods.add(fn.attr)
                    if (isinstance(fn, ast.Attribute) and fn.attr in {"getenv", "get"}
                            and node.args and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, str)
                            and re.fullmatch(r"[A-Z][A-Z0-9_]+", node.args[0].value)):
                        env_keys.add(node.args[0].value)
            result.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(),
                           "imports": sorted(imports), "possibleEnvironmentKeys": sorted(env_keys),
                           "calledMethods": sorted(methods)})
    return result


def download(location):
    url = urlparse(location)
    if url.scheme != "https" or not (url.hostname or "").endswith(".amazonaws.com"):
        raise ValueError("Unexpected Lambda artifact download host")
    with urlopen(location, timeout=60) as response:
        artifact = response.read(MAX_COMPRESSED_ARTIFACT_BYTES + 1)
    if len(artifact) > MAX_COMPRESSED_ARTIFACT_BYTES:
        raise ValueError("Lambda artifact exceeds size limit")
    return artifact


def collect(clients, download_artifact=download):
    caller = call(clients, "sts", "get_caller_identity")
    if caller.get("Account") != EXPECTED_ACCOUNT or caller.get("Arn") != EXPECTED_READER_ARN:
        raise ValueError("Unexpected production account or read-only session identity")
    stack = call(clients, "cloudformation", "describe_stacks", StackName=STACK)["Stacks"][0]
    resources = pages(clients, "cloudformation", "list_stack_resources", "StackResourceSummaries", StackName=STACK)
    auto = [r for r in resources if r.get("LogicalResourceId") == "AutoFunction"
            and r.get("ResourceType") == "AWS::Lambda::Function"]
    if len(auto) != 1:
        raise ValueError("Expected exactly one stack-owned AutoFunction")
    function = call(clients, "lambda", "get_function", FunctionName=auto[0]["PhysicalResourceId"])
    config = function["Configuration"]
    artifact = download_artifact(function["Code"]["Location"])
    manifest = zip_content_manifest(artifact)
    actual_hash = lambda_code_sha256(artifact)
    arn = config["FunctionArn"]
    role_arn = config["Role"]
    role_name = role_arn.rsplit("/", 1)[-1]
    role = call(clients, "iam", "get_role", RoleName=role_name)["Role"]
    inline_names = pages(clients, "iam", "list_role_policies", "PolicyNames", "Marker", "Marker", RoleName=role_name)
    policies = []
    for name in sorted(inline_names):
        policy = call(clients, "iam", "get_role_policy", RoleName=role_name, PolicyName=name)
        policies.append({"kind": "inline", "name": name, "document": policy["PolicyDocument"]})
    attached = pages(clients, "iam", "list_attached_role_policies", "AttachedPolicies", "Marker", "Marker", RoleName=role_name)
    for policy in sorted(attached, key=lambda p: p["PolicyArn"]):
        metadata = call(clients, "iam", "get_policy", PolicyArn=policy["PolicyArn"])["Policy"]
        version = call(clients, "iam", "get_policy_version", PolicyArn=policy["PolicyArn"], VersionId=metadata["DefaultVersionId"])["PolicyVersion"]
        policies.append({"kind": "attached", "arn": policy["PolicyArn"], "version": metadata["DefaultVersionId"], "document": version["Document"]})
    rules = []
    inventory = pages(clients, "events", "list_rules", "Rules")
    for rule in sorted(inventory, key=lambda r: r["Name"]):
        name = rule["Name"]
        targets = pages(clients, "events", "list_targets_by_rule", "Targets", Rule=name)
        if not any(t.get("Arn") == arn or str(t.get("Arn", "")).startswith(arn + ":") for t in targets):
            continue
        # Inputs may contain secrets; retain binding and a digest, not raw input.
        rules.append({"name": name, "arn": rule["Arn"], "state": rule["State"],
                      "schedule": rule.get("ScheduleExpression"),
                      "targets": [{"id": t["Id"], "arn": t["Arn"],
                                   "inputSha256": hashlib.sha256(json.dumps({k: t[k] for k in ("Input", "InputPath", "InputTransformer") if k in t}, sort_keys=True).encode()).hexdigest()}
                                  for t in targets]})
    after = call(clients, "lambda", "get_function_configuration", FunctionName=arn)
    environment = (config.get("Environment") or {}).get("Variables") or {}
    bindings = resource_environment(environment, resources)
    stack_account = stack["StackId"].split(":")[4]
    checks = {
        "stackName": stack.get("StackName") == STACK,
        "accountBinding": stack_account == caller["Account"] == arn.split(":")[4] == role_arn.split(":")[4] == EXPECTED_ACCOUNT,
        "functionNameBinding": config.get("FunctionName") == auto[0]["PhysicalResourceId"]
            and arn.rsplit(":", 1)[-1] == config.get("FunctionName"),
        "expectedHandler": config.get("Handler") == HANDLER,
        "downloadHash": actual_hash == config["CodeSha256"],
        "stableRevision": bool(config.get("RevisionId")) and all(after.get(k) == config.get(k) for k in ("RevisionId", "CodeSha256", "Role", "Handler")),
        "handlerModulePresent": any(x["path"] == "mlb_auto/autonomous_handler.py" for x in source_summary(artifact)),
        "roleArnBinding": role.get("Arn") == role_arn,
        "roleStackOwnership": any(r.get("ResourceType") == "AWS::IAM::Role" and r.get("PhysicalResourceId") == role_name for r in resources),
    }
    return {
        "schema": "KS1-SEPARATE-AUTOFUNCTION-READONLY-IDENTITY-v1",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "identityVerified": all(checks.values()), "checks": checks,
        "isolationAuthorized": False, "isolationDecision": "requires_review_of_permissions_and_storage_scope",
        "readerPrincipalArn": caller["Arn"],
        "ruleInventoryComplete": True, "ruleInventoryScope": "default_event_bus_in_requested_region",
        "stack": {k: stack.get(k) for k in ("StackName", "StackId", "StackStatus")},
        "resources": [{k: r.get(k) for k in ("LogicalResourceId", "PhysicalResourceId", "ResourceType", "ResourceStatus")} for r in resources],
        "function": {k: config.get(k) for k in ("FunctionName", "FunctionArn", "Handler", "Runtime", "Role", "CodeSha256", "RevisionId", "LastModified", "State", "LastUpdateStatus")},
        "environmentKeys": sorted(environment), "resourceBindings": bindings,
        "artifact": {**manifest, "downloadCodeSha256": actual_hash},
        "sourceSummary": source_summary(artifact),
        "role": {k: role.get(k) for k in ("Arn", "RoleId", "PermissionsBoundary", "AssumeRolePolicyDocument")},
        "policies": policies, "rules": rules,
        "lambdaInvocations": 0, "awsWrites": 0, "providerCalls": 0,
        "productionAuthorityChanged": False,
    }


def main():
    import boto3
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        clients = {service: boto3.client(service, region_name=args.region) for service in READ_OPERATIONS}
        report = collect(clients)
    except Exception as exc:
        # AWS exception strings and URLs can expose environment or signed data.
        report = {"identityVerified": False, "isolationAuthorized": False,
                  "errorType": type(exc).__name__, "awsWrites": 0, "lambdaInvocations": 0}
        if isinstance(exc, RuntimeError) and re.fullmatch(r"[a-z]+\.[a-z_]+:[A-Za-z0-9_.-]+", str(exc)):
            report["failedRead"] = str(exc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: report.get(k) for k in ("identityVerified", "isolationAuthorized", "checks", "errorType")}))
    return 0 if report.get("identityVerified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
