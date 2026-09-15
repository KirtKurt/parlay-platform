"""Provision only the dedicated metadata reader through the deployment pipeline.

This mutating infrastructure operation is deliberately separate from the audit
workflow, which receives only restricted OIDC credentials. CloudFormation owns
the role and inline policy as one resource, so partial creation is rolled back.
Existing shared OIDC provider configuration is never updated.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ACCOUNT = "735707987003"
STACK = "parlay-platform-ks1-autofunction-reader"
PROVIDER = f"arn:aws:iam::{ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
PARAMETER = "ExistingGitHubOidcProviderArn"
TEMPLATE = Path(__file__).resolve().parents[1] / "infra/ks1-autofunction-reader.json"


def missing_stack(exc):
    error = getattr(exc, "response", {}).get("Error", {})
    return error.get("Code") == "ValidationError" and "does not exist" in error.get("Message", "")


def provision(sts, cf, iam):
    caller = sts.get_caller_identity()
    if caller.get("Account") != ACCOUNT:
        raise ValueError("Unexpected production account")
    try:
        existing = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    except Exception as exc:
        if not missing_stack(exc):
            raise
        existing = None
    if existing:
        if existing.get("StackStatus") not in {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}:
            raise ValueError("Reader infrastructure stack requires recovery before update")
        deployed_template = cf.get_template(StackName=STACK, TemplateStage="Original")["TemplateBody"]
        if isinstance(deployed_template, str):
            deployed_template = json.loads(deployed_template)
        if deployed_template != json.loads(TEMPLATE.read_text()):
            raise ValueError("Existing reader stack template requires separately reviewed migration")
        # Preserve provider ownership on reruns; discovery alone would switch an
        # owned provider to external and change the CloudFormation resource set.
        parameters = {p["ParameterKey"]: p.get("ParameterValue", "") for p in existing.get("Parameters", [])}
        if PARAMETER not in parameters or parameters[PARAMETER] not in {"", PROVIDER}:
            raise ValueError("Existing reader stack has an unrecognized provider contract")
        provider = parameters[PARAMETER]
    else:
        providers = iam.list_open_id_connect_providers()["OpenIDConnectProviderList"]
        provider = PROVIDER if any(p["Arn"] == PROVIDER for p in providers) else ""
    if provider:
        config = iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider)
        if config.get("Url", "").removeprefix("https://") != "token.actions.githubusercontent.com" or "sts.amazonaws.com" not in config.get("ClientIDList", []):
            raise ValueError("Existing GitHub OIDC provider does not support the required audience")
    request = dict(StackName=STACK, TemplateBody=TEMPLATE.read_text(),
                   Parameters=[{"ParameterKey": PARAMETER, "ParameterValue": provider}],
                   Capabilities=["CAPABILITY_NAMED_IAM"])
    action = "update" if existing else "create"
    if existing:
        try:
            cf.update_stack(**request)
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            if error.get("Code") != "ValidationError" or error.get("Message") != "No updates are to be performed.":
                raise
            action = "unchanged"
    else:
        cf.create_stack(**request, OnFailure="ROLLBACK")
    if action != "unchanged":
        cf.get_waiter(f"stack_{action}_complete").wait(StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 45})
    final = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in final.get("Outputs", [])}
    expected = f"arn:aws:iam::{ACCOUNT}:role/ks1-autofunction-identity-reader"
    if final.get("StackStatus") not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"} or outputs.get("ReaderRoleArn") != expected:
        raise ValueError("Reader infrastructure completion/readback mismatch")
    return {"ok": True, "operation": action, "stackId": final["StackId"], "readerRoleArn": expected,
            "existingProviderArn": provider or None, "algorithmResourcesChanged": False,
            "identityVerified": False, "isolationAuthorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "identityVerified": False, "isolationAuthorized": False}
    try:
        import boto3
        session = boto3.Session(region_name="us-east-1")
        report = provision(*(session.client(s) for s in ("sts", "cloudformation", "iam")))
    except Exception as exc:
        report.update(errorType=type(exc).__name__, errorCode=getattr(exc, "response", {}).get("Error", {}).get("Code"))
    report["observedAt"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
