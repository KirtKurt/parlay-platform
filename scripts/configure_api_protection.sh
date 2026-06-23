#!/usr/bin/env bash
set -euo pipefail

API_URL="${1:-}"
AWS_REGION="${2:-}"

if [ -z "$API_URL" ] || [ -z "$AWS_REGION" ]; then
  echo "Usage: configure_api_protection.sh <api_url> <aws_region>"
  exit 1
fi

API_ID=$(API_URL="$API_URL" python - <<'PY'
import os, urllib.parse
url = os.environ['API_URL']
host = urllib.parse.urlparse(url).netloc
print(host.split('.')[0])
PY
)

if [ -z "$API_ID" ]; then
  echo "Could not parse API ID from API URL."
  exit 1
fi

WEB_ACL_NAME="inqsi-api-protection"

cat > waf-rules.json <<'JSON'
[
  {
    "Name": "AWS-AmazonIpReputationList",
    "Priority": 0,
    "OverrideAction": {"None": {}},
    "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": "AWSManagedRulesAmazonIpReputationList"}},
    "VisibilityConfig": {"SampledRequestsEnabled": true, "CloudWatchMetricsEnabled": true, "MetricName": "InqsiAmazonIpReputation"}
  },
  {
    "Name": "AWS-CommonRuleSet",
    "Priority": 1,
    "OverrideAction": {"None": {}},
    "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": "AWSManagedRulesCommonRuleSet"}},
    "VisibilityConfig": {"SampledRequestsEnabled": true, "CloudWatchMetricsEnabled": true, "MetricName": "InqsiCommonRules"}
  },
  {
    "Name": "AWS-KnownBadInputs",
    "Priority": 2,
    "OverrideAction": {"None": {}},
    "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": "AWSManagedRulesKnownBadInputsRuleSet"}},
    "VisibilityConfig": {"SampledRequestsEnabled": true, "CloudWatchMetricsEnabled": true, "MetricName": "InqsiKnownBadInputs"}
  },
  {
    "Name": "InqsiIpRateLimit",
    "Priority": 3,
    "Action": {"Block": {}},
    "Statement": {"RateBasedStatement": {"Limit": 2000, "AggregateKeyType": "IP"}},
    "VisibilityConfig": {"SampledRequestsEnabled": true, "CloudWatchMetricsEnabled": true, "MetricName": "InqsiIpRateLimit"}
  }
]
JSON

EXISTING_ARN=$(aws wafv2 list-web-acls \
  --scope REGIONAL \
  --region "$AWS_REGION" \
  --query "WebACLs[?Name=='$WEB_ACL_NAME'].ARN | [0]" \
  --output text)

if [ "$EXISTING_ARN" = "None" ] || [ -z "$EXISTING_ARN" ]; then
  aws wafv2 create-web-acl \
    --name "$WEB_ACL_NAME" \
    --scope REGIONAL \
    --region "$AWS_REGION" \
    --default-action Allow={} \
    --description "Inqis API protection WebACL" \
    --visibility-config SampledRequestsEnabled=true,CloudWatchMetricsEnabled=true,MetricName=InqsiApiProtection \
    --rules file://waf-rules.json >/tmp/inqsi-waf-create.json
  WEB_ACL_ARN=$(python - <<'PY'
import json
with open('/tmp/inqsi-waf-create.json') as f:
    print(json.load(f)['Summary']['ARN'])
PY
)
else
  WEB_ACL_ARN="$EXISTING_ARN"
fi

if [ -z "$WEB_ACL_ARN" ]; then
  echo "Could not resolve WAF WebACL ARN."
  exit 1
fi

API_STAGE_ARN="arn:aws:apigateway:${AWS_REGION}::/restapis/${API_ID}/stages/Prod"

if aws wafv2 get-web-acl-for-resource --resource-arn "$API_STAGE_ARN" --region "$AWS_REGION" >/tmp/inqsi-current-waf.json 2>/dev/null; then
  echo "WAF already associated with API stage."
else
  aws wafv2 associate-web-acl \
    --web-acl-arn "$WEB_ACL_ARN" \
    --resource-arn "$API_STAGE_ARN" \
    --region "$AWS_REGION"
fi

aws apigateway update-stage \
  --rest-api-id "$API_ID" \
  --stage-name Prod \
  --region "$AWS_REGION" \
  --patch-operations \
    op=replace,path='/*/*/throttling/rateLimit',value='100' \
    op=replace,path='/*/*/throttling/burstLimit',value='200'

echo "Inqis API protection configured: WAF associated and stage throttling updated."
