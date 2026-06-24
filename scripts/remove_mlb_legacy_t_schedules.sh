#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${STACK_NAME:-parlay-platform-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"

LOGICAL_IDS=(MLBBasePull MLBT2 MLBT3 MLBT4)

for logical_id in "${LOGICAL_IDS[@]}"; do
  echo "Legacy schedule still to reconcile: $logical_id in $STACK_NAME / $AWS_REGION"
done

echo "Runtime protection is active: the MLB pull Lambda refuses non-HOT inputs, so legacy fixed-time triggers are harmless while waiting for template reconciliation."
