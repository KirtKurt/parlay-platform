#!/usr/bin/env bash
set -euo pipefail
: "${AWS_REGION:=us-east-1}"
: "${TENNIS_STACK_NAME:=inqsi-tennis-v1}"
: "${RELEASE_SHA256:=dbc456f51a89d3b5fa2c16668577f6e90a7f125e5357cb93be92523a14391112}"
BASE=/tmp/tennis-release
SRC="$BASE/source/tennis_predictive_platform_v1"
HELPER="$GITHUB_WORKSPACE/tennis_release/release_checks.py"
mkdir -p "$BASE"
cat "$GITHUB_WORKSPACE"/tennis_release/TENNIS_PREDICTIVE_PLATFORM_V1_1_ML_REPO_OVERLAY.zip.b64.part* | base64 --decode > "$BASE/overlay.zip"
actual=$(sha256sum "$BASE/overlay.zip" | awk '{print $1}')
[[ "$actual" == "$RELEASE_SHA256" ]] || { echo "::error::release digest mismatch: $actual"; exit 1; }
unzip -q "$BASE/overlay.zip" -d "$BASE/source"
[[ -f "$SRC/template.yaml" && -f "$SRC/src/tennis_training_handler.py" ]]
cd "$SRC"
python -m pip install -r requirements-dev.txt
./scripts/run_all_checks.sh
aws sts get-caller-identity > /tmp/aws-identity.json
[[ -n "${TENNIS_ODDS_API_KEY:-}" ]] || { echo "::error::Missing dedicated TENNIS_ODDS_API_KEY"; exit 1; }

aws cloudformation get-template --stack-name parlay-platform-dev --template-stage Processed --region "$AWS_REGION" --output json > /tmp/mlb-template-before.json
aws cloudformation list-stack-resources --stack-name parlay-platform-dev --region "$AWS_REGION" --output json > /tmp/mlb-resources-before.json
python "$HELPER" fingerprint /tmp/mlb-template-before.json /tmp/mlb-resources-before.json /tmp/mlb-before.sha256

python "$HELPER" restrict template.yaml template.deploy.yaml
python scripts/validate_template.py
sam validate --lint --template-file template.deploy.yaml
sam build --no-cached --template-file template.deploy.yaml
sam deploy --stack-name "$TENNIS_STACK_NAME" --region "$AWS_REGION" --resolve-s3 --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides TennisOddsApiKey="$TENNIS_ODDS_API_KEY" StageName=prod ScheduleState=ENABLED

aws cloudformation describe-stacks --stack-name "$TENNIS_STACK_NAME" --region "$AWS_REGION" --output json > /tmp/tennis-stack.json
python "$HELPER" outputs /tmp/tennis-stack.json /tmp/tennis-outputs.json /tmp/tennis-api-url
for rule in \
  "$TENNIS_STACK_NAME-tennis-ingest-every-15-minutes" \
  "$TENNIS_STACK_NAME-tennis-lock-every-minute" \
  "$TENNIS_STACK_NAME-tennis-outcomes-every-6-hours" \
  "$TENNIS_STACK_NAME-tennis-training-daily"; do
  state=$(aws events describe-rule --name "$rule" --region "$AWS_REGION" --query State --output text)
  [[ "$state" == ENABLED ]] || { echo "::error::$rule is $state"; exit 1; }
done

API_URL=$(cat /tmp/tennis-api-url)
curl --fail --silent --show-error "$API_URL/v1/tennis/model/version" | tee /tmp/tennis-model.json
python "$HELPER" payload /tmp/tennis-model.json model
curl --fail --silent --show-error "$API_URL/v1/tennis/ml/status" | tee /tmp/tennis-ml-status.json
python "$HELPER" payload /tmp/tennis-ml-status.json status
sigv4=(--aws-sigv4 "aws:amz:${AWS_REGION}:execute-api" --user "${AWS_ACCESS_KEY_ID}:${AWS_SECRET_ACCESS_KEY}")
[[ -z "${AWS_SESSION_TOKEN:-}" ]] || sigv4+=(-H "x-amz-security-token: ${AWS_SESSION_TOKEN}")
curl --fail --silent --show-error "${sigv4[@]}" -H 'content-type: application/json' -X POST "$API_URL/v1/pull/tennis" -d '{"run":"deploy_discovery_smoke"}' | tee /tmp/tennis-discovery.json
python "$HELPER" payload /tmp/tennis-discovery.json discovery

aws cloudformation get-template --stack-name parlay-platform-dev --template-stage Processed --region "$AWS_REGION" --output json > /tmp/mlb-template-after.json
aws cloudformation list-stack-resources --stack-name parlay-platform-dev --region "$AWS_REGION" --output json > /tmp/mlb-resources-after.json
python "$HELPER" fingerprint /tmp/mlb-template-after.json /tmp/mlb-resources-after.json /tmp/mlb-after.sha256
cmp -s /tmp/mlb-before.sha256 /tmp/mlb-after.sha256 || { echo "::error::MLB stack changed during Tennis deployment"; exit 1; }
echo "Tennis AWS deployment verified; MLB stack unchanged."
