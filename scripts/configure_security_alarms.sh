#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${1:-parlay-platform-dev}"
AWS_REGION="${2:-}"
MONTHLY_BUDGET_LIMIT="${3:-100}"
FAILED=0

if [ -z "$AWS_REGION" ]; then
  echo "Usage: configure_security_alarms.sh <stack_name> <aws_region> [monthly_budget_limit]"
  exit 1
fi

put_alarm() {
  local name="$1"
  shift
  local settings
  if ! settings=$(aws cloudwatch describe-alarms --region "$AWS_REGION" --alarm-names "$name" --query 'MetricAlarms[0]' --output json); then
    echo "Cannot read existing alarm actions for $name; leaving it unchanged."
    FAILED=1
    return
  fi
  settings=$(printf '%s' "$settings" | python -c 'import json,sys; old=json.load(sys.stdin) or {}; print(json.dumps({k:old[k] for k in ("ActionsEnabled","AlarmActions","OKActions","InsufficientDataActions") if k in old}))')
  if aws cloudwatch put-metric-alarm --region "$AWS_REGION" --alarm-name "$name" --cli-input-json "$settings" "$@"; then
    echo "Configured alarm: $name"
  else
    echo "Failed alarm: $name"
    FAILED=1
  fi
}

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
API_ID=$(aws cloudformation describe-stack-resource \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --logical-resource-id ServerlessRestApi \
  --query "StackResourceDetail.PhysicalResourceId" \
  --output text 2>/dev/null || true)
LAMBDA_NAME=$(aws cloudformation describe-stack-resource \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --logical-resource-id ApiFunction \
  --query "StackResourceDetail.PhysicalResourceId" \
  --output text 2>/dev/null || true)

if [ -n "$API_ID" ] && [ "$API_ID" != "None" ]; then
  API_NAME=$(aws apigateway get-rest-api --rest-api-id "$API_ID" --region "$AWS_REGION" --query name --output text)
  if [ -z "$API_NAME" ] || [ "$API_NAME" = "None" ]; then
    echo "Could not resolve REST API metric name."
    exit 1
  fi
  put_alarm "inqsi-api-5xx-spike" \
    --metric-name 5XXError \
    --namespace AWS/ApiGateway \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 10 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --dimensions "Name=ApiName,Value=$API_NAME" Name=Stage,Value=Prod \
    --treat-missing-data notBreaching

  put_alarm "inqsi-api-4xx-spike" \
    --metric-name 4XXError \
    --namespace AWS/ApiGateway \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 50 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --dimensions "Name=ApiName,Value=$API_NAME" Name=Stage,Value=Prod \
    --treat-missing-data notBreaching

  put_alarm "inqsi-api-request-volume-spike" \
    --metric-name Count \
    --namespace AWS/ApiGateway \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 3000 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --dimensions "Name=ApiName,Value=$API_NAME" Name=Stage,Value=Prod \
    --treat-missing-data notBreaching
else
  echo "API Gateway resource not found; skipping API alarms."
  FAILED=1
fi

if [ -n "$LAMBDA_NAME" ] && [ "$LAMBDA_NAME" != "None" ]; then
  put_alarm "inqsi-lambda-errors" \
    --metric-name Errors \
    --namespace AWS/Lambda \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 5 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --dimensions Name=FunctionName,Value="$LAMBDA_NAME" \
    --treat-missing-data notBreaching

  put_alarm "inqsi-lambda-throttles" \
    --metric-name Throttles \
    --namespace AWS/Lambda \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --dimensions Name=FunctionName,Value="$LAMBDA_NAME" \
    --treat-missing-data notBreaching
else
  echo "Lambda ApiFunction not found; skipping Lambda alarms."
  FAILED=1
fi

for TABLE in parlay_platform_snapshots parlay_platform_signals parlay_platform_predictions parlay_platform_outcomes; do
  put_alarm "inqsi-ddb-throttles-${TABLE}" \
    --metric-name ThrottledRequests \
    --namespace AWS/DynamoDB \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --dimensions Name=TableName,Value="$TABLE" \
    --treat-missing-data notBreaching
 done

cat > /tmp/inqsi-budget.json <<JSON
{
  "BudgetName": "inqsi-monthly-cost-guardrail",
  "BudgetLimit": {"Amount": "${MONTHLY_BUDGET_LIMIT}", "Unit": "USD"},
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST"
}
JSON

if aws budgets create-budget --account-id "$ACCOUNT_ID" --budget file:///tmp/inqsi-budget.json >/tmp/inqsi-budget-create.log 2>/tmp/inqsi-budget-error.log; then
  echo "Configured AWS budget guardrail: ${MONTHLY_BUDGET_LIMIT} USD/month"
else
  if grep -qi "DuplicateRecordException" /tmp/inqsi-budget-error.log; then
    echo "Budget guardrail already exists."
  else
    echo "Budget setup failed; an authorized administrator must resolve the reported permission or configuration error."
    echo "The legacy AWS Budgets write permission is budgets:ModifyBudget (not budgets:CreateBudget)."
    cat /tmp/inqsi-budget-error.log || true
    FAILED=1
  fi
fi

if [ "$FAILED" -ne 0 ]; then
  echo "Security setup is incomplete; see the failed operations above."
fi
exit "$FAILED"
