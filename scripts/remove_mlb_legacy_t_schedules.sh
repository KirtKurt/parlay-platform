#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${STACK_NAME:-parlay-platform-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# These are the legacy MLB fixed-time EventBridge schedules that should no longer exist.
# Preferred removal path is CloudFormation/SAM deploy after removing them from template.yaml.
# This script is a direct cleanup fallback for an operator with AWS CLI credentials.
LOGICAL_IDS=(MLBBasePull MLBT2 MLBT3 MLBT4)

for logical_id in "${LOGICAL_IDS[@]}"; do
  echo "Looking up $logical_id in stack $STACK_NAME..."
  physical_id=$(aws cloudformation describe-stack-resource \
    --stack-name "$STACK_NAME" \
    --logical-resource-id "$logical_id" \
    --region "$AWS_REGION" \
    --query 'StackResourceDetail.PhysicalResourceId' \
    --output text 2>/dev/null || true)

  if [[ -z "$physical_id" || "$physical_id" == "None" ]]; then
    echo "No physical rule found for $logical_id; skipping."
    continue
  fi

  echo "Removing EventBridge targets for $physical_id..."
  target_ids=$(aws events list-targets-by-rule \
    --rule "$physical_id" \
    --region "$AWS_REGION" \
    --query 'Targets[].Id' \
    --output text 2>/dev/null || true)

  if [[ -n "$target_ids" && "$target_ids" != "None" ]]; then
    aws events remove-targets --rule "$physical_id" --ids $target_ids --region "$AWS_REGION" || true
  fi

  echo "Deleting EventBridge rule $physical_id..."
  aws events delete-rule --name "$physical_id" --region "$AWS_REGION" || true
  echo "Deleted/attempted: $logical_id -> $physical_id"
done

echo "Done. Next SAM deploy should also remove these rules from CloudFormation if template.yaml no longer defines them."
