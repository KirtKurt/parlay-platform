#!/usr/bin/env bash
set -Eeuo pipefail
stage=bootstrap
report_failure() {
  local rc="$1" line="$2" command="$3"
  printf 'Tennis deployment failed\nexit=%s\nstage=%s\nline=%s\ncommand=%s\n' \
    "$rc" "$stage" "$line" "$command" | tee /tmp/tennis-failure.txt >&2
}
trap 'rc=$?; report_failure "$rc" "$LINENO" "$BASH_COMMAND"' ERR
trap 'rc=$?; if (( rc != 0 )) && [[ ! -s /tmp/tennis-failure.txt ]]; then report_failure "$rc" "$LINENO" "stage:${stage}"; fi' EXIT
: "${AWS_REGION:=us-east-1}"
: "${TENNIS_STACK_NAME:=inqsi-tennis-v1}"
: "${RELEASE_SHA256:=dbc456f51a89d3b5fa2c16668577f6e90a7f125e5357cb93be92523a14391112}"
: "${RELEASE_TREE_SHA256:=bb1abd8de476e9f935408d18eeaf5678a1d5d9dfed17d3b1259e3e411ac0c449}"
BASE=/tmp/tennis-release
SRC="$BASE/source/tennis_predictive_platform_v1"
HELPER="$GITHUB_WORKSPACE/tennis_release/release_checks.py"

stage=reconstruct_release
mkdir -p "$BASE"
cat "$GITHUB_WORKSPACE"/tennis_release/TENNIS_PREDICTIVE_PLATFORM_V1_1_ML_REPO_OVERLAY.zip.b64.part* | base64 --decode > "$BASE/overlay.zip"
actual=$(sha256sum "$BASE/overlay.zip" | awk '{print $1}')
printf '%s\n' "$actual" > /tmp/tennis-release-transport.sha256
unzip -tq "$BASE/overlay.zip"
unzip -q "$BASE/overlay.zip" -d "$BASE/source"
[[ -f "$SRC/template.yaml" && -f "$SRC/src/tennis_training_handler.py" ]]
tree_actual=$(python - "$SRC" <<'PY'
from pathlib import Path
import hashlib
import sys
root = Path(sys.argv[1])
files = sorted((p for p in root.rglob('*') if p.is_file()), key=lambda p: p.relative_to(root).as_posix())
h = hashlib.sha256()
for path in files:
    rel = path.relative_to(root).as_posix().encode()
    data = path.read_bytes()
    h.update(len(rel).to_bytes(8, 'big')); h.update(rel)
    h.update(len(data).to_bytes(8, 'big')); h.update(data)
print(h.hexdigest())
PY
)
printf '%s\n' "$tree_actual" > /tmp/tennis-release-tree.sha256
if [[ "$tree_actual" != "$RELEASE_TREE_SHA256" ]]; then
  report_failure 1 "$LINENO" "source_tree_hash_mismatch:expected=${RELEASE_TREE_SHA256}:actual=${tree_actual}:transport=${actual}"
  exit 1
fi
echo "Verified Tennis source tree $tree_actual (transport ZIP $actual; original ZIP $RELEASE_SHA256)."

stage=validate_release
cd "$SRC"
python -m pip install --disable-pip-version-check -r requirements-dev.txt
./scripts/run_all_checks.sh

stage=aws_identity
aws sts get-caller-identity > /tmp/aws-identity.json
[[ -n "${TENNIS_ODDS_API_KEY:-}" ]] || { echo "::error::Missing Tennis odds provider credential"; exit 1; }

stage=fingerprint_mlb_before
aws cloudformation get-template --stack-name parlay-platform-dev --template-stage Processed --region "$AWS_REGION" --output json > /tmp/mlb-template-before.json
aws cloudformation list-stack-resources --stack-name parlay-platform-dev --region "$AWS_REGION" --output json > /tmp/mlb-resources-before.json
python "$HELPER" fingerprint /tmp/mlb-template-before.json /tmp/mlb-resources-before.json /tmp/mlb-before.sha256

stage=prepare_tennis_template
python "$HELPER" restrict template.yaml template.deploy.yaml
python scripts/validate_template.py
sam validate --lint --template-file template.deploy.yaml
sam build --no-cached --template-file template.deploy.yaml

stage=deploy_tennis_stack
sam deploy --stack-name "$TENNIS_STACK_NAME" --region "$AWS_REGION" --resolve-s3 --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides TennisOddsApiKey="$TENNIS_ODDS_API_KEY" StageName=prod ScheduleState=ENABLED

stage=verify_tennis_stack
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

stage=verify_tennis_api
API_URL=$(cat /tmp/tennis-api-url)
curl --fail --silent --show-error "$API_URL/v1/tennis/model/version" | tee /tmp/tennis-model.json
python "$HELPER" payload /tmp/tennis-model.json model
curl --fail --silent --show-error "$API_URL/v1/tennis/ml/status" | tee /tmp/tennis-ml-status.json
python "$HELPER" payload /tmp/tennis-ml-status.json status
sigv4=(--aws-sigv4 "aws:amz:${AWS_REGION}:execute-api" --user "${AWS_ACCESS_KEY_ID}:${AWS_SECRET_ACCESS_KEY}")
[[ -z "${AWS_SESSION_TOKEN:-}" ]] || sigv4+=(-H "x-amz-security-token: ${AWS_SESSION_TOKEN}")
curl --fail --silent --show-error "${sigv4[@]}" -H 'content-type: application/json' -X POST "$API_URL/v1/pull/tennis" -d '{"run":"deploy_discovery_smoke"}' | tee /tmp/tennis-discovery.json
python "$HELPER" payload /tmp/tennis-discovery.json discovery

stage=fingerprint_mlb_after
aws cloudformation get-template --stack-name parlay-platform-dev --template-stage Processed --region "$AWS_REGION" --output json > /tmp/mlb-template-after.json
aws cloudformation list-stack-resources --stack-name parlay-platform-dev --region "$AWS_REGION" --output json > /tmp/mlb-resources-after.json
python "$HELPER" fingerprint /tmp/mlb-template-after.json /tmp/mlb-resources-after.json /tmp/mlb-after.sha256
cmp -s /tmp/mlb-before.sha256 /tmp/mlb-after.sha256 || { echo "::error::MLB stack changed during Tennis deployment"; exit 1; }
stage=complete
rm -f /tmp/tennis-failure.txt
echo "Tennis AWS deployment verified; MLB stack unchanged."
