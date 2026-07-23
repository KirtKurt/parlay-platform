from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(".github/workflows/deploy-tennis.yml")
START = "          sigv4=(\n"
END = "            | tee /tmp/tennis-discovery-smoke.json\n"

REPLACEMENT = r'''          TENNIS_INGEST_FUNCTION=$(aws cloudformation describe-stack-resource \
            --stack-name "$TENNIS_STACK_NAME" \
            --logical-resource-id TennisIngestFunction \
            --query 'StackResourceDetail.PhysicalResourceId' \
            --output text)
          test -n "$TENNIS_INGEST_FUNCTION"
          test "$TENNIS_INGEST_FUNCTION" != None
          printf '%s\n' \
            '{"httpMethod":"POST","path":"/v1/pull/tennis","body":"{\"run\":\"deploy_force_live_pull\",\"force\":true}"}' \
            > /tmp/tennis-ingest-invoke-event.json
          aws lambda invoke \
            --function-name "$TENNIS_INGEST_FUNCTION" \
            --region "$AWS_REGION" \
            --cli-connect-timeout 10 \
            --cli-read-timeout 300 \
            --cli-binary-format raw-in-base64-out \
            --payload file:///tmp/tennis-ingest-invoke-event.json \
            /tmp/tennis-ingest-invoke-envelope.json \
            | tee /tmp/tennis-ingest-invoke-metadata.json
          python - <<'PY'
          import json
          from pathlib import Path

          invocation = json.loads(
              Path("/tmp/tennis-ingest-invoke-metadata.json").read_text()
          )
          if invocation.get("StatusCode") != 200 or invocation.get("FunctionError"):
              raise SystemExit(f"Tennis ingest Lambda invocation failed: {invocation}")
          envelope = json.loads(
              Path("/tmp/tennis-ingest-invoke-envelope.json").read_text()
          )
          if int(envelope.get("statusCode") or 0) != 200:
              raise SystemExit(f"Tennis ingest returned non-200: {envelope}")
          payload = json.loads(str(envelope.get("body") or "{}"))
          Path("/tmp/tennis-discovery-smoke.json").write_text(
              json.dumps(payload, indent=2, sort_keys=True)
          )
          print(json.dumps(payload, indent=2, sort_keys=True))
          PY
'''


def main() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    if "aws lambda invoke" in text and "TennisIngestFunction" in text:
        if "--aws-sigv4" in text:
            raise SystemExit("mixed API/Lambda smoke transport detected")
        print("Direct Lambda smoke is already installed")
        return

    start = text.find(START)
    if start < 0:
        raise SystemExit("signed API smoke start marker not found")
    end = text.find(END, start)
    if end < 0:
        raise SystemExit("signed API smoke end marker not found")
    end += len(END)

    updated = text[:start] + REPLACEMENT + text[end:]
    required = (
        "aws lambda invoke",
        "TennisIngestFunction",
        "payload.get('live_pull_ok') is True",
        "payload.get('fallback_used') is False",
        "ScheduleState=DISABLED",
        "ScheduleState=ENABLED",
        "tennis/predictions?date=${PREDICTION_SLATE_DATE}",
        "MLB stack changed during Tennis deployment",
    )
    missing = [token for token in required if token not in updated]
    if missing:
        raise SystemExit(f"patched workflow lost required contracts: {missing}")
    forbidden = [token for token in ("--aws-sigv4", "sigv4=(") if token in updated]
    if forbidden:
        raise SystemExit(f"signed API smoke remains after patch: {forbidden}")

    WORKFLOW.write_text(updated, encoding="utf-8")
    print("Installed direct Tennis ingest Lambda live smoke")


if __name__ == "__main__":
    main()
