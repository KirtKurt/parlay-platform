#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${1:-parlay-platform-dev}"
AWS_REGION="${2:-}"
MONTHLY_BUDGET_LIMIT="${3:-100}"

if [ -z "$AWS_REGION" ]; then
  echo "Usage: configure_security_alarms.sh <stack_name> <aws_region> [monthly_budget_limit]"
  exit 1
fi

put_alarm() {
  local name="$1"
  shift
  if aws cloudwatch put-metric-alarm --region "$AWS_REGION" --alarm-name "$name" "$@"; then
    echo "Configured alarm: $name"
  else
    echo "Skipped alarm: $name"
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
  put_alarm "inqsi-api-5xx-spike" \
    --metric-name 5XXError \
    --namespace AWS/ApiGateway \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 10 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --dimensions Name=ApiId,Value="$API_ID" Name=Stage,Value=Prod \
    --treat-missing-data notBreaching

  put_alarm "inqsi-api-4xx-spike" \
    --metric-name 4XXError \
    --namespace AWS/ApiGateway \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 50 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --dimensions Name=ApiId,Value="$API_ID" Name=Stage,Value=Prod \
    --treat-missing-data notBreaching

  put_alarm "inqsi-api-request-volume-spike" \
    --metric-name Count \
    --namespace AWS/ApiGateway \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 3000 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --dimensions Name=ApiId,Value="$API_ID" Name=Stage,Value=Prod \
    --treat-missing-data notBreaching
else
  echo "API Gateway resource not found; skipping API alarms."
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
    echo "Budget guardrail skipped: deploy IAM user likely needs budgets:CreateBudget."
    cat /tmp/inqsi-budget-error.log || true
  fi
fi

echo "Security alarms configuration attempted. Missing permissions are non-fatal."
