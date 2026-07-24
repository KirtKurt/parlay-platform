from __future__ import annotations

import hashlib
from pathlib import Path

import yaml


SOURCE = Path(".github/workflows/mlb-historical-optimizer.yml")
TARGET = Path("runtime_reports/mlb_v15_11_1_rollback_recovery_workflow.staged.yml")
ANCHOR = "      - name: Deploy fail-closed historical optimizer\n"

RECOVERY = r'''      - name: Recover historical optimizer stack from failed create
        env:
          HISTORICAL_STACK_NAME: parlay-platform-mlb-historical-optimizer
          AWS_REGION_VALUE: ${{ secrets.AWS_REGION }}
        run: |
          set -euo pipefail

          read_stack_status() {
            local output
            if output=$(aws cloudformation describe-stacks \
              --stack-name "$HISTORICAL_STACK_NAME" \
              --region "$AWS_REGION_VALUE" \
              --query 'Stacks[0].StackStatus' \
              --output text 2>/tmp/mlb-historical-stack-status.err); then
              printf '%s\n' "$output"
            elif grep -qiE 'does not exist|not exist' /tmp/mlb-historical-stack-status.err; then
              printf 'STACK_NOT_FOUND\n'
            else
              cat /tmp/mlb-historical-stack-status.err >&2
              return 1
            fi
          }

          for attempt in $(seq 1 120); do
            status="$(read_stack_status)"
            echo "Historical optimizer stack status: $status"
            case "$status" in
              STACK_NOT_FOUND)
                break
                ;;
              ROLLBACK_COMPLETE)
                echo 'Deleting failed-create optimizer stack before clean recreation.'
                aws cloudformation delete-stack \
                  --stack-name "$HISTORICAL_STACK_NAME" \
                  --region "$AWS_REGION_VALUE"
                aws cloudformation wait stack-delete-complete \
                  --stack-name "$HISTORICAL_STACK_NAME" \
                  --region "$AWS_REGION_VALUE"
                status="$(read_stack_status)"
                if [ "$status" != 'STACK_NOT_FOUND' ]; then
                  echo "::error::Historical optimizer stack still exists after delete: $status"
                  exit 1
                fi
                break
                ;;
              CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE|IMPORT_COMPLETE|IMPORT_ROLLBACK_COMPLETE)
                echo 'Historical optimizer stack is updateable.'
                break
                ;;
              *_IN_PROGRESS)
                if [ "$attempt" -eq 120 ]; then
                  echo "::error::Timed out waiting for historical optimizer stack: $status"
                  exit 1
                fi
                sleep 15
                ;;
              *)
                echo "::error::Unexpected non-updateable historical optimizer stack status: $status"
                exit 1
                ;;
            esac
          done
'''


def main() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    if text.count(ANCHOR) != 1:
        raise SystemExit(f"deploy anchor count={text.count(ANCHOR)}")
    if "Recover historical optimizer stack from failed create" in text:
        raise SystemExit("rollback recovery step is already present")

    patched = text.replace(ANCHOR, RECOVERY + ANCHOR, 1)
    recovery_position = patched.index(
        "- name: Recover historical optimizer stack from failed create"
    )
    deploy_position = patched.index("- name: Deploy fail-closed historical optimizer")
    if recovery_position >= deploy_position:
        raise SystemExit("recovery step was not inserted before deployment")

    required = (
        "ROLLBACK_COMPLETE",
        "aws cloudformation delete-stack",
        "aws cloudformation wait stack-delete-complete",
        "Unexpected non-updateable historical optimizer stack status",
        "STACK_NOT_FOUND",
    )
    for value in required:
        if value not in patched[recovery_position:deploy_position]:
            raise SystemExit(f"missing recovery contract: {value}")

    yaml.safe_load(patched)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(patched, encoding="utf-8")
    digest = hashlib.sha256(patched.encode("utf-8")).hexdigest()
    print(f"STAGED_WORKFLOW={TARGET}")
    print(f"STAGED_WORKFLOW_SHA256={digest}")


if __name__ == "__main__":
    main()
