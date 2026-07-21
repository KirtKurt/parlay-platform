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
    lines = current.splitlines(keepends=True)
    out = []
    i = 0
    needle = f"        {name}:"
    while i < len(lines):
        if lines[i].startswith(needle):
            i += 1
            while i < len(lines):
                line = lines[i]
                if ((line.startswith("        ") and not line.startswith("          ")) or (line.startswith("  ") and not line.startswith("    ")) or line.startswith("Outputs:")):
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def ensure_function_env(current: str, resource_name: str, key: str, value: str) -> str:
    """Add one function-specific environment variable without disturbing other resources."""
    lines = current.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.startswith(f"  {resource_name}:")), None)
    if start is None:
        raise RuntimeError(f"Function resource missing: {resource_name}")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("  ") and not lines[i].startswith("    "):
            end = i
            break
    block = lines[start:end]
    if any(line.startswith(f"          {key}:") for line in block):
        return current

    variables_index = next((i for i, line in enumerate(block) if line.startswith("        Variables:")), None)
    if variables_index is not None:
        block.insert(variables_index + 1, f"          {key}: {value}\n")
    else:
        properties_index = next((i for i, line in enumerate(block) if line.startswith("    Properties:")), None)
        if properties_index is None:
            raise RuntimeError(f"Properties block missing: {resource_name}")
        block[properties_index + 1:properties_index + 1] = [
            "      Environment:\n",
            "        Variables:\n",
            f"          {key}: {value}\n",
        ]
    lines[start:end] = block
    return "".join(lines)


def resource_block(current: str, resource_name: str) -> str:
    lines = current.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"  {resource_name}:")), None)
    if start is None:
        return ""
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("  ") and not lines[i].startswith("    "):
            end = i
            break
    return "\n".join(lines[start:end])


for obsolete_verifier_event in [
    "MLBProductionIngestVerifyDaily435Et",
    "MLBProductionLockVerifyDaily556Et",
]:
    text = remove_child_event(text, obsolete_verifier_event)

admin_env = "        INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken\n"
if admin_env not in text:
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in text:
        raise RuntimeError("ODDS_API_KEY global env marker missing")
    text = text.replace(marker, marker + admin_env, 1)

text = text.replace("Handler: inqsi_backend_api.lambda_handler", "Handler: inqsi_backend_api_wrapper.lambda_handler", 1)
for name in [
    "InqsiMlbModelVersion", "InqsiMlbToday", "InqsiMlbGameWinners", "InqsiMlbPredictions", "InqsiMlbGames",
    "ApiMlbV3ModelVersion", "ApiMlbV3Today", "ApiMlbV3GameWinners", "ApiMlbV3Predictions", "ApiMlbV3Games",
]:
    text = remove_child_event(text, name)

text = text.replace("Handler: mlb_manual_pull.lambda_handler", "Handler: mlb_manual_pull_protected.lambda_handler", 1)
text = text.replace("Handler: mlb_daily_pick_lock.lambda_handler", "Handler: mlb_daily_pick_lock_protected.lambda_handler", 1)
text = ensure_function_env(text, "MLBAuditedPullFunction", "INQSI_ADMIN_API_TOKEN", "!Ref InqsiAdminApiToken")
text = ensure_function_env(text, "MLBDailyPickLockFunction", "INQSI_ADMIN_API_TOKEN", "!Ref InqsiAdminApiToken")

# Resource entries must be indented exactly two spaces beneath Resources:.
dedicated = """
  MLBV3ReadFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_v3_read_api.lambda_handler
      Timeout: 60
      MemorySize: 1024
      Policies:
        - DynamoDBReadPolicy:
            TableName: !Ref SnapshotsTable
      Events:
        MLBV3ModelVersion:
          Type: Api
          Properties:
            Path: /v1/mlb/model/version
            Method: GET
        MLBV3Today:
          Type: Api
          Properties:
            Path: /v1/mlb/today
            Method: GET
        MLBV3GameWinners:
          Type: Api
          Properties:
            Path: /v1/mlb/game-winners
            Method: GET
        MLBV3Predictions:
          Type: Api
          Properties:
            Path: /v1/mlb/predictions
            Method: GET
        MLBV3Games:
          Type: Api
          Properties:
            Path: /v1/mlb/games
            Method: GET

"""
text = insert_once(text, "  ApiFunction:\n", dedicated, "  MLBV3ReadFunction:\n")
mlb_v3_read_block = resource_block(text, "MLBV3ReadFunction")
if "DynamoDBCrudPolicy:" in mlb_v3_read_block:
    text = text.replace(
        mlb_v3_read_block,
        mlb_v3_read_block.replace("DynamoDBCrudPolicy:", "DynamoDBReadPolicy:"),
        1,
    )
    mlb_v3_read_block = resource_block(text, "MLBV3ReadFunction")

verifier = """
  MLBProductionVerifierFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_production_verifier.lambda_handler
      Timeout: 30
      MemorySize: 1024
      EventInvokeConfig:
        MaximumEventAgeInSeconds: 300
        MaximumRetryAttempts: 0
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
            Schedule: cron(2/5 * * * ? *)
            Enabled: false
            Input: '{"sport":"mlb","mode":"continuous","run":"aws_production_verifier_5m"}'
            RetryPolicy:
              MaximumEventAgeInSeconds: 300
              MaximumRetryAttempts: 0

"""
text = insert_once(text, "  MLBResultsSchedulerFunction:\n", verifier, "  MLBProductionVerifierFunction:\n")

required = [
    "Handler: inqsi_backend_api_wrapper.lambda_handler",
    "  MLBV3ReadFunction:", "Handler: mlb_v3_read_api.lambda_handler",
    "Path: /v1/mlb/model/version", "Path: /v1/mlb/today", "Path: /v1/mlb/game-winners", "Path: /v1/mlb/predictions", "Path: /v1/mlb/games",
    "Handler: mlb_manual_pull_protected.lambda_handler", "Handler: mlb_daily_pick_lock_protected.lambda_handler",
    "INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken",
    "  MLBProductionVerifierFunction:", "MLBProductionVerifierEvery5Min:",
]
missing = [value for value in required if value not in text]
invalid_top_level = [name for name in ("MLBV3ReadFunction", "MLBProductionVerifierFunction") if f"\n{name}:\n" in text]
missing_function_tokens = [
    resource_name
    for resource_name in ("MLBAuditedPullFunction", "MLBDailyPickLockFunction")
    if "          INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken" not in resource_block(text, resource_name)
]
invalid_read_policy = (
    "DynamoDBReadPolicy:" not in mlb_v3_read_block
    or "DynamoDBCrudPolicy:" in mlb_v3_read_block
)
obsolete_verifiers = [
    name for name in ("MLBProductionIngestVerifyDaily435Et", "MLBProductionLockVerifyDaily556Et")
    if name in text
]
if missing or invalid_top_level or missing_function_tokens or invalid_read_policy or obsolete_verifiers:
    details = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if invalid_top_level:
        details.append("resource indentation invalid: " + ", ".join(invalid_top_level))
    if missing_function_tokens:
        details.append("function-specific admin token missing: " + ", ".join(missing_function_tokens))
    if invalid_read_policy:
        details.append("MLBV3ReadFunction must have DynamoDBReadPolicy and no DynamoDBCrudPolicy")
    if obsolete_verifiers:
        details.append("obsolete fixed-UTC verifier events remain: " + ", ".join(obsolete_verifiers))
    raise RuntimeError("MLB dedicated v3 route patch failed; " + "; ".join(details))
TEMPLATE.write_text(text)
print("Patched template.yaml with dedicated MLB v3 reads, protected writes, and one continuous production verifier.")
