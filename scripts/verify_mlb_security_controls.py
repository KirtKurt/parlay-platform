"""Read back the existing API protection, alarm and budget configuration."""
import json
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config


def main():
    options = {"region_name": "us-east-1", "config": Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 1})}
    report = {"observedAtUtc": datetime.now(timezone.utc).isoformat(), "readOnly": True, "checks": {}, "blockers": []}
    def check(name, operation):
        try:
            evidence = operation()
            report["checks"][name] = evidence
            if evidence.get("ok") is not True:
                report["blockers"].append(name + ":CONFIGURATION_NOT_VERIFIED")
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            report["checks"][name] = {"ok": False, "errorCode": error.get("Code") or type(exc).__name__,
                                     "error": (error.get("Message") or str(exc))[:1000]}
            report["blockers"].append(name + ":" + report["checks"][name]["errorCode"])

    cf = boto3.client("cloudformation", **options)
    gateway = boto3.client("apigateway", **options)
    def resource(logical):
        return cf.describe_stack_resource(StackName="parlay-platform-dev", LogicalResourceId=logical)["StackResourceDetail"]["PhysicalResourceId"]
    def alarms():
        api = gateway.get_rest_api(restApiId=resource("ServerlessRestApi"))
        desired = {"inqsi-api-5xx-spike": ("5XXError", 10), "inqsi-api-4xx-spike": ("4XXError", 50),
                   "inqsi-api-request-volume-spike": ("Count", 3000)}
        observed = boto3.client("cloudwatch", **options).describe_alarms(AlarmNames=list(desired))["MetricAlarms"]
        valid = len(observed) == len(desired)
        rows = []
        for alarm in observed:
            metric, threshold = desired[alarm["AlarmName"]]
            ok = (alarm.get("Namespace") == "AWS/ApiGateway" and alarm.get("MetricName") == metric
                  and alarm.get("Threshold") == threshold and alarm.get("Statistic") == "Sum"
                  and alarm.get("Period") == 300 and alarm.get("EvaluationPeriods") == 1
                  and alarm.get("ComparisonOperator") == "GreaterThanOrEqualToThreshold"
                  and {d["Name"]: d["Value"] for d in alarm.get("Dimensions", [])} == {"ApiName": api["name"], "Stage": "Prod"})
            valid = valid and ok
            rows.append({"name": alarm["AlarmName"], "ok": ok, "dimensions": alarm.get("Dimensions"), "state": alarm.get("StateValue")})
        return {"ok": valid, "alarms": rows}
    def waf():
        arn = "arn:aws:apigateway:us-east-1::/restapis/" + resource("ServerlessRestApi") + "/stages/Prod"
        acl = boto3.client("wafv2", **options).get_web_acl_for_resource(ResourceArn=arn).get("WebACL") or {}
        groups = {r.get("Statement", {}).get("ManagedRuleGroupStatement", {}).get("Name") for r in acl.get("Rules", [])}
        required = {"AWSManagedRulesAmazonIpReputationList", "AWSManagedRulesCommonRuleSet", "AWSManagedRulesKnownBadInputsRuleSet"}
        rules = acl.get("Rules", [])
        managed_blocking = all(r.get("OverrideAction") == {"None": {}} for r in rules if "ManagedRuleGroupStatement" in r.get("Statement", {}))
        rate_blocking = any(r.get("Action") == {"Block": {}} and r.get("Statement", {}).get("RateBasedStatement", {}).get("Limit") == 2000
                            and r["Statement"]["RateBasedStatement"].get("AggregateKeyType") == "IP" for r in rules)
        return {"ok": acl.get("Name") == "inqsi-api-protection" and required <= groups and len(rules) == 4
                and acl.get("DefaultAction") == {"Allow": {}} and managed_blocking and rate_blocking,
                "resourceArn": arn, "webAclArn": acl.get("ARN"), "managedRuleGroups": sorted(g for g in groups if g)}
    def budget():
        account = boto3.client("sts", **options).get_caller_identity()["Account"]
        value = boto3.client("budgets", **options).describe_budget(AccountId=account, BudgetName="inqsi-monthly-cost-guardrail")["Budget"]
        limit = value.get("BudgetLimit") or {}
        return {"ok": Decimal(str(limit.get("Amount"))) == Decimal("100") and limit.get("Unit") == "USD"
                and value.get("TimeUnit") == "MONTHLY" and value.get("BudgetType") == "COST",
                "limit": limit, "timeUnit": value.get("TimeUnit"), "spendingCapEnforced": False}
    def throttling():
        settings = gateway.get_stage(restApiId=resource("ServerlessRestApi"), stageName="Prod").get("methodSettings", {}).get("*/*", {})
        return {"ok": settings.get("throttlingRateLimit") == 100 and settings.get("throttlingBurstLimit") == 200,
                "rateLimit": settings.get("throttlingRateLimit"), "burstLimit": settings.get("throttlingBurstLimit")}
    check("restApiAlarms", alarms)
    check("apiThrottling", throttling)
    check("webApplicationFirewall", waf)
    check("monthlyBudget", budget)
    report["ok"] = not report["blockers"]
    output = Path("runtime_reports/mlb_security_controls_latest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
