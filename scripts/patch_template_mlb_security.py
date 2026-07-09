from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()


def insert_once(current: str, marker: str, block: str, contains: str) -> str:
    if contains in current:
        return current
    if marker not in current:
        raise RuntimeError(f"Template marker not found: {marker.strip()}")
    return current.replace(marker, block + marker, 1)


admin_env = "        INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken\n"
if admin_env not in text:
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in text:
        raise RuntimeError("ODDS_API_KEY global env marker not found; cannot inject INQSI_ADMIN_API_TOKEN")
    text = text.replace(marker, marker + admin_env, 1)

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
    "Handler: mlb_manual_pull_protected.lambda_handler",
    "Handler: mlb_daily_pick_lock_protected.lambda_handler",
    "INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken",
    "MLBProductionVerifierFunction:",
    "MLBProductionVerifierEvery5Min:",
    "MLBProductionIngestVerifyDaily435Et:",
    "MLBProductionLockVerifyDaily556Et:",
]
missing = [token for token in required if token not in text]
if missing:
    raise RuntimeError("MLB security/schedule patch failed; missing: " + ", ".join(missing))

TEMPLATE.write_text(text)
print("Patched template.yaml to protect HTTP MLB writes and schedule AWS MLB production verification checks: every 5 minutes, daily ingest check, and daily lock check.")
