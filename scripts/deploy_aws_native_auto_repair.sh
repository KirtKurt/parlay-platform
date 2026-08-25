#!/usr/bin/env bash
set -euo pipefail

SPORT=${1:?sport required}
TARGET_STACK=${2:?target stack required}
REPAIR_STACK=${3:?repair stack required}
REGION=${4:?AWS region required}
TEMPLATE=${5:-aws-auto-repair-template.yaml}
BUILD_DIR=".aws-sam/auto-repair-${SPORT}"
PROOF="/tmp/aws-native-auto-repair-${SPORT}.json"

case "$SPORT" in mlb-auto|tennis|soccer|nfl) ;; *) echo "unsupported sport: $SPORT" >&2; exit 2 ;; esac
case "$TARGET_STACK" in *"$SPORT"*) ;; *) echo "target stack/sport mismatch: $SPORT $TARGET_STACK" >&2; exit 2 ;; esac

stable_target() {
  local status
  status=$(aws cloudformation describe-stacks \
    --stack-name "$TARGET_STACK" --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text)
  case "$status" in
    CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE|IMPORT_COMPLETE) printf '%s' "$status" ;;
    *) echo "target stack is not stable: $TARGET_STACK=$status" >&2; return 1 ;;
  esac
}

invoke_cycle() {
  local mode=$1
  local output=$2
  local payload
  if [ "$mode" = dry ]; then
    payload="{\"action\":\"cycle\",\"sport\":\"$SPORT\",\"dry_run\":true}"
  else
    payload="{\"action\":\"cycle\",\"sport\":\"$SPORT\"}"
  fi
  for attempt in $(seq 1 12); do
    aws lambda invoke \
      --function-name "$REPAIR_FUNCTION" \
      --region "$REGION" \
      --cli-binary-format raw-in-base64-out \
      --cli-read-timeout 900 \
      --payload "$payload" \
      "$output" >"${output}.meta"
    state=$(python - "$output" "${output}.meta" <<'PY'
import json,sys
body=json.load(open(sys.argv[1])); meta=json.load(open(sys.argv[2]))
if meta.get('FunctionError'):
    print('FUNCTION_ERROR')
else:
    print(body.get('status') or 'UNKNOWN')
PY
)
    if [ "$state" != REPAIR_LEASE_HELD ]; then return 0; fi
    sleep 10
  done
  echo "repair lease remained held after bounded retries" >&2
  return 1
}

TARGET_BEFORE=$(stable_target)
aws sts get-caller-identity >/tmp/auto-repair-identity.json

existing=$(aws cloudformation describe-stacks \
  --stack-name "$REPAIR_STACK" --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo STACK_MISSING)
case "$existing" in
  ROLLBACK_COMPLETE|ROLLBACK_FAILED)
    aws cloudformation delete-stack --stack-name "$REPAIR_STACK" --region "$REGION"
    aws cloudformation wait stack-delete-complete --stack-name "$REPAIR_STACK" --region "$REGION"
    ;;
  *_IN_PROGRESS)
    for attempt in $(seq 1 80); do
      sleep 15
      existing=$(aws cloudformation describe-stacks \
        --stack-name "$REPAIR_STACK" --region "$REGION" \
        --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo STACK_MISSING)
      case "$existing" in
        CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE|STACK_MISSING) break ;;
        *_IN_PROGRESS) ;;
        *) echo "repair stack is not updateable: $REPAIR_STACK=$existing" >&2; exit 1 ;;
      esac
      test "$attempt" != 80
    done
    ;;
  STACK_MISSING|CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE) ;;
  *) echo "repair stack is not updateable: $REPAIR_STACK=$existing" >&2; exit 1 ;;
esac

rm -rf "$BUILD_DIR"
sam build --no-cached --template-file "$TEMPLATE" --build-dir "$BUILD_DIR"
sam deploy \
  --template-file "$BUILD_DIR/template.yaml" \
  --stack-name "$REPAIR_STACK" \
  --region "$REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    SportName="$SPORT" \
    TargetStackName="$TARGET_STACK" \
    FunctionNamePrefix="$TARGET_STACK" \
    RuleNamePrefix="$TARGET_STACK" \
    RepairLeaseSeconds=960 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

REPAIR_FUNCTION=$(aws cloudformation describe-stacks \
  --stack-name "$REPAIR_STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AutoRepairFunctionName'].OutputValue" \
  --output text)
STATE_TABLE=$(aws cloudformation describe-stacks \
  --stack-name "$REPAIR_STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AutoRepairStateTableName'].OutputValue" \
  --output text)
test -n "$REPAIR_FUNCTION" && test "$REPAIR_FUNCTION" != None
test -n "$STATE_TABLE" && test "$STATE_TABLE" != None

invoke_cycle dry /tmp/auto-repair-dry.json
invoke_cycle live /tmp/auto-repair-live.json

SPORT="$SPORT" TARGET_STACK="$TARGET_STACK" python - <<'PY'
import json,os
for stem, allowed in (
    ('dry', {'DRY_RUN','TARGET_STACK_BUSY_OR_UNSAFE'}),
    ('live', {'REPAIRED_OR_HEALTHY','SAFE_REPAIR_COMPLETED_WITH_BLOCKERS','TARGET_STACK_BUSY_OR_UNSAFE'}),
):
    body=json.load(open(f'/tmp/auto-repair-{stem}.json'))
    meta=json.load(open(f'/tmp/auto-repair-{stem}.json.meta'))
    assert not meta.get('FunctionError'), (stem,meta,body)
    assert body.get('ok') is True, (stem,body)
    assert body.get('sport') == os.environ['SPORT'], (stem,body)
    assert body.get('target_stack') == os.environ['TARGET_STACK'], (stem,body)
    assert body.get('status') in allowed, (stem,body)
    for key in (
        'immutable_prediction_history_rewritten',
        'promotion_gate_changed',
        'winner_authority_changed',
        'other_sport_changed',
    ):
        assert body.get(key) is False, (stem,key,body)
    assert body.get('forbidden_operations_available', False) is False, (stem,body)
    if stem == 'live':
        metrics=body.get('metrics') or {}
        assert int(metrics.get('RepairFailures') or 0) == 0, body
PY

REPAIR_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$REPAIR_STACK" --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text)
case "$REPAIR_STATUS" in CREATE_COMPLETE|UPDATE_COMPLETE) ;; *) exit 1 ;; esac

RULE_NAME=$(aws cloudformation list-stack-resources \
  --stack-name "$REPAIR_STACK" --region "$REGION" \
  --query "StackResourceSummaries[?ResourceType=='AWS::Events::Rule'].PhysicalResourceId | [0]" \
  --output text)
test -n "$RULE_NAME" && test "$RULE_NAME" != None
RULE_STATE=$(aws events describe-rule --name "$RULE_NAME" --region "$REGION" --query State --output text)
test "$RULE_STATE" = ENABLED

ALARM_COUNT=$(aws cloudformation list-stack-resources \
  --stack-name "$REPAIR_STACK" --region "$REGION" \
  --query "length(StackResourceSummaries[?ResourceType=='AWS::CloudWatch::Alarm'])" \
  --output text)
test "$ALARM_COUNT" -ge 6

aws dynamodb get-item \
  --table-name "$STATE_TABLE" \
  --region "$REGION" \
  --consistent-read \
  --key "{\"PK\":{\"S\":\"STATUS#$SPORT\"},\"SK\":{\"S\":\"LATEST\"}}" \
  --output json >/tmp/auto-repair-state.json
python - <<'PY'
import json
state=json.load(open('/tmp/auto-repair-state.json'))
assert state.get('Item'), state
PY

TARGET_AFTER=$(stable_target)
SPORT="$SPORT" TARGET_STACK="$TARGET_STACK" REPAIR_STACK="$REPAIR_STACK" \
REPAIR_FUNCTION="$REPAIR_FUNCTION" STATE_TABLE="$STATE_TABLE" \
TARGET_BEFORE="$TARGET_BEFORE" TARGET_AFTER="$TARGET_AFTER" \
REPAIR_STATUS="$REPAIR_STATUS" RULE_NAME="$RULE_NAME" RULE_STATE="$RULE_STATE" \
ALARM_COUNT="$ALARM_COUNT" GITHUB_SHA_VALUE="${GITHUB_SHA:-UNKNOWN}" PROOF="$PROOF" \
python - <<'PY'
import json,os
from pathlib import Path
proof={
  'sport':os.environ['SPORT'],
  'target_stack':os.environ['TARGET_STACK'],
  'target_stack_status_before':os.environ['TARGET_BEFORE'],
  'target_stack_status_after':os.environ['TARGET_AFTER'],
  'repair_stack':os.environ['REPAIR_STACK'],
  'repair_stack_status':os.environ['REPAIR_STATUS'],
  'repair_function':os.environ['REPAIR_FUNCTION'],
  'repair_state_table':os.environ['STATE_TABLE'],
  'repair_rule_name':os.environ['RULE_NAME'],
  'repair_rule_state':os.environ['RULE_STATE'],
  'repair_alarm_count':int(os.environ['ALARM_COUNT']),
  'source_commit':os.environ['GITHUB_SHA_VALUE'],
  'dry_run':json.load(open('/tmp/auto-repair-dry.json')),
  'live_run':json.load(open('/tmp/auto-repair-live.json')),
  'durable_state':json.load(open('/tmp/auto-repair-state.json')),
  'production_authority_changed':False,
  'immutable_prediction_history_rewritten':False,
  'promotion_gate_changed':False,
  'human_or_chatgpt_winner_selection':False,
  'other_sport_changed':False,
}
Path(os.environ['PROOF']).write_text(json.dumps(proof,indent=2,sort_keys=True)+'\n')
print(json.dumps(proof,indent=2,sort_keys=True))
PY
