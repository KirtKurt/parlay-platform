from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()


def insert_once(current: str, marker: str, block: str, contains: str) -> str:
    if contains in current:
        return current
    if marker not in current:
        raise RuntimeError(f"Template marker not found: {marker.strip()}")
    return current.replace(marker, block + marker, 1)


def remove_indented_event_block(current: str, event_name: str) -> str:
    lines = current.splitlines(keepends=True)
    output = []
    i = 0
    needle = f"        {event_name}:"
    while i < len(lines):
        line = lines[i]
        if line.startswith(needle):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                is_next_event = nxt.startswith("        ") and not nxt.startswith("          ")
                is_next_resource = nxt.startswith("  ") and not nxt.startswith("    ")
                is_outputs = nxt.startswith("Outputs:")
                if is_next_event or is_next_resource or is_outputs:
                    break
                i += 1
            continue
        output.append(line)
        i += 1
    return "".join(output)


def remove_resource_block(current: str, resource_name: str) -> str:
    lines = current.splitlines(keepends=True)
    output = []
    i = 0
    needle = f"  {resource_name}:"
    while i < len(lines):
        if lines[i].startswith(needle):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                is_next_resource = nxt.startswith("  ") and not nxt.startswith("    ")
                is_outputs = nxt.startswith("Outputs:")
                if is_next_resource or is_outputs:
                    break
                i += 1
            continue
        output.append(lines[i])
        i += 1
    return "".join(output)


# Global MLB runtime knobs. These are safe defaults for the SAM/Lambda platform.
if "MLB_PULL_START_AT_ET:" not in text:
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in text:
        raise RuntimeError("ODDS_API_KEY environment marker not found in template.yaml")
    text = text.replace(
        marker,
        marker
        + "        MLB_PULL_START_AT_ET: '2026-07-02T01:00:00-04:00'\n"
        + "        MLB_SCHED_INTERVAL_MINUTES: '15'\n"
        + "        MLB_PROMOTION_THRESHOLD: '0.0015'\n"
        + "        MLB_MIN_PROMOTION_EV: '0.001'\n"
        + "        MLB_MIN_DOG_MODEL_PROB: '0.34'\n"
        + "        MLB_MAX_PROMOTED_DOG_PRICE: '160'\n",
        1,
    )

# MLB V2 has one automatic odds path: HOT 15-minute same-day Odds API pull.
for legacy_event in ["MLBHotKickoff1amET", "MLBBasePull", "MLBT2", "MLBT3", "MLBT4"]:
    text = remove_indented_event_block(text, legacy_event)

text = text.replace(
    "        MLBHotEvery15Min:\n          Type: Schedule\n          Properties:\n            Schedule: rate(15 minutes)\n",
    "        MLBHotEvery15Min:\n          Type: Schedule\n          Properties:\n            Schedule: cron(0/15 * * * ? *)\n",
    1,
)
text = text.replace('"days_ahead":1', '"days_ahead":0')
text = text.replace('"days_ahead": 1', '"days_ahead": 0')

# Remove obsolete duplicate recovery/resource paths so there is one production pull source.
text = remove_resource_block(text, "MLBHotPullRecoveryFunction")

if "InqsiMLBV1CoreFunction:" not in text:
    text = insert_once(
        text,
        "  MLBResultsSchedulerFunction:\n",
        """
  InqsiMLBV1CoreFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: inqsi_mlb_v1_core.lambda_handler
      Timeout: 60
      MemorySize: 1024
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref SnapshotsTable
        - DynamoDBCrudPolicy:
            TableName: !Ref SignalLedgerTable
        - DynamoDBCrudPolicy:
            TableName: !Ref PredictionsTable
        - DynamoDBCrudPolicy:
            TableName: !Ref OutcomesTable
      Events:
        InqsiMLBV1Today:
          Type: Api
          Properties:
            Path: /v1/mlb/today
            Method: GET
        InqsiMLBV1Games:
          Type: Api
          Properties:
            Path: /v1/mlb/games
            Method: GET
        InqsiMLBV1Predictions:
          Type: Api
          Properties:
            Path: /v1/mlb/predictions
            Method: GET
        InqsiMLBV1GameWinners:
          Type: Api
          Properties:
            Path: /v1/mlb/game-winners
            Method: GET
        InqsiMLBV1Audit:
          Type: Api
          Properties:
            Path: /v1/mlb/audit
            Method: GET
        InqsiMLBV1ModelVersion:
          Type: Api
          Properties:
            Path: /v1/mlb/model/version
            Method: GET

""",
        "InqsiMLBV1CoreFunction:",
    )
elif "Path: /v1/mlb/game-winners" not in text and "Path: /v1/mlb/predictions" in text:
    text = text.replace(
        "        InqsiMLBV1Audit:\n          Type: Api\n",
        "        InqsiMLBV1GameWinners:\n          Type: Api\n          Properties:\n            Path: /v1/mlb/game-winners\n            Method: GET\n        InqsiMLBV1Audit:\n          Type: Api\n",
        1,
    )

if "MLBDailyPickLockFunction:" not in text:
    text = insert_once(
        text,
        "  MLBResultsSchedulerFunction:\n",
        """
  MLBDailyPickLockFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_daily_pick_lock.lambda_handler
      Timeout: 60
      MemorySize: 1024
      Environment:
        Variables:
          INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken
          MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME: '45'
          MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'
          MLB_MIN_PULLS_FOR_LOCK: '4'
          MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES: '20'
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref SnapshotsTable
      Events:
        MLBDailyPickLockEveryMinute:
          Type: Schedule
          Properties:
            Schedule: rate(1 minute)
            Input: '{"sport":"mlb","run":"daily_lock_check","auto_ingest":false}'
        MLBDailyPickLockRun:
          Type: Api
          Properties:
            Path: /v1/mlb/locks/run
            Method: POST
        MLBDailyPickLockStatus:
          Type: Api
          Properties:
            Path: /v1/mlb/locks/status
            Method: GET
        MLBDailyPickLockToday:
          Type: Api
          Properties:
            Path: /v1/mlb/locks/today
            Method: GET

""",
        "MLBDailyPickLockFunction:",
    )
else:
    replacements = {
        "          MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'\n": "          MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'\n          MLB_MIN_PULLS_FOR_LOCK: '4'\n          MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES: '20'\n",
        "          MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME: '45'\n": "          INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken\n          MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME: '45'\n",
    }
    for old, new in replacements.items():
        if old in text and new not in text:
            text = text.replace(old, new, 1)

# MLB must not also be pulled by the generic all-sports scheduler.
text = text.replace('"sports":"mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"', '"sports":"wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"')
text = text.replace('"includeFullMlbSnapshots":true', '"includeFullMlbSnapshots":false')

violations = []
for token in ['"days_ahead":1', '"days_ahead": 1']:
    if token in text:
        violations.append(f"unsafe scheduled MLB {token} still present")
for legacy_event in ["MLBBasePull", "MLBT2", "MLBT3", "MLBT4", "MLBHotKickoff1amET", "MLBHotPullRecoveryFunction"]:
    if legacy_event in text:
        violations.append(f"obsolete MLB schedule/resource still present: {legacy_event}")
required = [
    "Schedule: cron(0/15 * * * ? *)",
    "MLBDailyPickLockEveryMinute:",
    "MLB_MIN_PULLS_FOR_LOCK: '4'",
    "MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES: '20'",
    "INQSI_ADMIN_API_TOKEN: !Ref InqsiAdminApiToken",
    "Path: /v1/mlb/game-winners",
]
for token in required:
    if token not in text:
        violations.append(f"required MLB V2 template token missing: {token}")
if violations:
    raise RuntimeError("Unsafe MLB V2 SAM template after patch: " + "; ".join(violations))

TEMPLATE.write_text(text)
print("Patched template.yaml for MLB V2: single-game EV picks, 15-minute same-day Odds API pulls, hardened T-minus-45 lock, no duplicate MLB schedules, and no odds-only deploy split.")
