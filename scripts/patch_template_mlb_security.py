from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()


def insert_once(current: str, marker: str, block: str, contains: str) -> str:
    if contains in current:
        return current
    if marker not in current:
        raise RuntimeError(f"Template marker not found: {marker.strip()}")
    return current.replace(marker, block + marker, 1)


def remove_child_event(current: str, name: str) -> str:
    """Remove one named SAM event block at eight-space indentation."""
    lines = current.splitlines(keepends=True)
    out = []
    i = 0
    needle = f"        {name}:"
    while i < len(lines):
        if lines[i].startswith(needle):
            i += 1
            while i < len(lines):
                line = lines[i]
                if (
                    (line.startswith("        ") and not line.startswith("          "))
                    or (line.startswith("  ") and not line.startswith("    "))
                    or line.startswith("Outputs:")
                ):
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


admin_env = "        INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken\n"
if admin_env not in text:
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in text:
        raise RuntimeError("ODDS_API_KEY global env marker not found; cannot inject INQSI_ADMIN_API_TOKEN")
    text = text.replace(marker, marker + admin_env, 1)

# Keep the backend wrapper for member/admin/health routes, but do not attach MLB
# read routes to it. The backend package does not contain the live hello_world
# MLB v3 runtime and previously returned a hard-coded v2.1 smoke response.
text = text.replace("Handler: inqsi_backend_api.lambda_handler", "Handler: inqsi_backend_api_wrapper.lambda_handler", 1)

legacy_backend_mlb_events = [
    "InqsiMlbModelVersion",
    "InqsiMlbToday",
    "InqsiMlbGameWinners",
    "InqsiMlbPredictions",
    "InqsiMlbGames",
]
for event_name in legacy_backend_mlb_events:
    text = remove_child_event(text, event_name)

# MLB reads are intentionally handled by ApiFunction's /{proxy+} event. That
# Lambda packages hello_world and runs usercustomize.py, including the v3 runtime
# installer and exact frozen-vector pipeline.
if "  ApiFunction:\n" not in text or "            Path: /{proxy+}\n" not in text:
    raise RuntimeError("ApiFunction catch-all route missing; cannot route MLB reads to live v3 runtime")

text = text.replace("Handler: mlb_manual_pull.lambda_handler", "Handler: mlb_manual_pull_protected.lambda_handler", 1)
text = text.replace("Handler: mlb_daily_pick_lock.lambda_handler", "Handler: mlb_daily_pick_lock_protected.lambda_handler", 1)

text = insert_once(
    text,
    "  MLBResultsSchedulerFunction:\n",
    """
  MLBProductionVerifierFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_production_verifier.lambda_handler
      Timeout: 60
      MemorySize: 1024
      Environment:
        Variables:
          MLB_VERIFY_MAX_PULL_AGE_MINUTES: '20'
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref SnapshotsTable
      Events:
        MLBProductionVerifierEvery5Min:
          Type: Schedule
          Properties:
            Schedule: rate(5 minutes)
            Input: '{"sport":"mlb","mode":"continuous","run":"aws_production_verifier_5m"}'
        MLBProductionIngestVerifyDaily435Et:
          Type: Schedule
          Properties:
            Schedule: cron(35 20 * * ? *)
            Input: '{"sport":"mlb","mode":"ingest","run":"daily_ingest_verify_1635_et"}'
        MLBProductionLockVerifyDaily556Et:
          Type: Schedule
          Properties:
            Schedule: cron(56 21 * * ? *)
            Input: '{"sport":"mlb","mode":"lock","run":"daily_lock_verify_1756_et"}'

""",
    "MLBProductionVerifierFunction:",
)

required = [
    "Handler: inqsi_backend_api_wrapper.lambda_handler",
    "  ApiFunction:",
    "Path: /{proxy+}",
    "Handler: mlb_manual_pull_protected.lambda_handler",
    "Handler: mlb_daily_pick_lock_protected.lambda_handler",
    "INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken",
    "MLBProductionVerifierFunction:",
    "MLBProductionVerifierEvery5Min:",
    "MLBProductionIngestVerifyDaily435Et:",
    "MLBProductionLockVerifyDaily556Et:",
]
missing = [token for token in required if token not in text]
remaining_legacy = [name for name in legacy_backend_mlb_events if f"        {name}:" in text]
if missing or remaining_legacy:
    details = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if remaining_legacy:
        details.append("legacy backend MLB events remain: " + ", ".join(remaining_legacy))
    raise RuntimeError("MLB security/schedule patch failed; " + "; ".join(details))

TEMPLATE.write_text(text)
print("Patched template.yaml to protect MLB writes, route MLB reads through the live v3 ApiFunction, and schedule AWS production verification checks.")
