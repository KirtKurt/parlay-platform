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
    """Remove a SAM Events child block indented under a Function Events map."""
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
                if is_next_event or is_next_resource:
                    break
                i += 1
            continue
        output.append(line)
        i += 1
    return "".join(output)


def ensure_global_env(current: str, key: str, value_line: str) -> str:
    if f"        {key}:" in current:
        return current
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in current:
        raise RuntimeError("ODDS_API_KEY environment marker not found in template.yaml")
    return current.replace(marker, marker + value_line, 1)


for key, value in [
    ("MLB_PULL_START_AT_ET", "        MLB_PULL_START_AT_ET: '2026-07-02T01:00:00-04:00'\n"),
    ("MLB_SCHED_INTERVAL_MINUTES", "        MLB_SCHED_INTERVAL_MINUTES: '15'\n"),
    ("MLB_PRIMARY_BOOK", "        MLB_PRIMARY_BOOK: 'fanduel'\n"),
    ("MLB_PROMOTION_EDGE_THRESHOLD", "        MLB_PROMOTION_EDGE_THRESHOLD: '0.0015'\n"),
    ("MLB_MIN_PROMOTION_EV", "        MLB_MIN_PROMOTION_EV: '0.0'\n"),
    ("MLB_MAX_PROMOTED_DOG_PRICE", "        MLB_MAX_PROMOTED_DOG_PRICE: '170'\n"),
    ("MLB_MIN_PULLS_FOR_LOCK", "        MLB_MIN_PULLS_FOR_LOCK: '4'\n"),
    ("MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES", "        MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES: '20'\n"),
]:
    text = ensure_global_env(text, key, value)

if "RAW_ARCHIVE_BUCKET:" not in text:
    text = text.replace(
        "        OUTCOMES_TABLE: !Ref OutcomesTable\n",
        "        OUTCOMES_TABLE: !Ref OutcomesTable\n        RAW_ARCHIVE_BUCKET: !Ref RawArchiveBucket\n",
        1,
    )

if "RawArchiveBucket:" not in text:
    text = insert_once(
        text,
        "  InqsiMembersTable:\n",
        """
  RawArchiveBucket:
    Type: AWS::S3::Bucket
    DeletionPolicy: Retain
    Properties:
      BucketName: !Sub "${AWS::StackName}-raw-archive-${AWS::AccountId}-${AWS::Region}"
      VersioningConfiguration:
        Status: Enabled
      BucketEncryption:
        ServerSideEncryptionConfiguration:
          - ServerSideEncryptionByDefault:
              SSEAlgorithm: AES256
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true

""",
        "RawArchiveBucket:",
    )

for legacy_event in ["MLBBasePull", "MLBT2", "MLBT3", "MLBT4", "MLBHotKickoff1amET"]:
    text = remove_indented_event_block(text, legacy_event)

text = text.replace(
    "Schedule: rate(15 minutes)\n            Input: '{\"sport\":\"mlb\",\"t\":\"HOT\",\"run\":\"hot_pull_audited\",\"days_ahead\":1}'",
    "Schedule: cron(0/15 * * * ? *)\n            Input: '{\"sport\":\"mlb\",\"t\":\"HOT\",\"run\":\"hot_pull_audited\",\"days_ahead\":0}'",
)
text = text.replace('"days_ahead":1', '"days_ahead":0')
text = text.replace('"days_ahead": 1', '"days_ahead": 0')

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

  MLBRawArchiveFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_raw_s3_archive.lambda_handler
      Policies:
        - Statement:
            - Effect: Allow
              Action:
                - s3:PutObject
              Resource: !Join ["", [!GetAtt RawArchiveBucket.Arn, "/*"]]
      Events:
        MLBRawArchiveEvery15Min:
          Type: Schedule
          Properties:
            Schedule: cron(0/15 * * * ? *)
            Input: '{"sport":"mlb","run":"hot_raw_archive"}'

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
          MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME: '45'
          MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'
          MLB_MIN_PULLS_FOR_LOCK: '4'
          MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES: '20'
          MLB_MIN_PROMOTED_PICKS_FOR_CLEAN_LOCK: '0'
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
    env_marker = "          MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'\n"
    additions = (
        "          MLB_MIN_PULLS_FOR_LOCK: '4'\n"
        "          MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES: '20'\n"
        "          MLB_MIN_PROMOTED_PICKS_FOR_CLEAN_LOCK: '0'\n"
    )
    if env_marker in text and "          MLB_MIN_PULLS_FOR_LOCK:" not in text:
        text = text.replace(env_marker, env_marker + additions, 1)

if "MLBResultSignalsFunction:" not in text:
    text = insert_once(
        text,
        "  MLBResultsSchedulerFunction:\n",
        """
  MLBResultSignalsFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_result_signals.lambda_handler
      Timeout: 300
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
        MLBResultSignalsGet:
          Type: Api
          Properties:
            Path: /v1/mlb/result-signals
            Method: GET
        MLBResultSignalsBuild:
          Type: Api
          Properties:
            Path: /v1/mlb/result-signals
            Method: POST

""",
        "MLBResultSignalsFunction:",
    )

if "AllSportsLiveSchedulerFunction:" not in text:
    text = insert_once(
        text,
        "  InqsiAutopsySchedulerFunction:\n",
        """
  AllSportsLiveSchedulerFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: all_sports_live_scheduler.lambda_handler
      Timeout: 300
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
        AllSportsHotEvery15Min:
          Type: Schedule
          Properties:
            Schedule: rate(15 minutes)
            Input: '{"sports":"wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis","run":"all_sports_hot_every_15_min","policy":"aws_eventbridge_primary_1am_et_start_plus_15min","includeFullMlbSnapshots":false}'
        AllSportsHotKickoff1amEtDst:
          Type: Schedule
          Properties:
            Schedule: cron(0 5 * * ? *)
            Input: '{"sports":"wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis","run":"all_sports_hot_1am_et_kickoff_dst","policy":"aws_eventbridge_primary_1am_et_start_plus_15min","includeFullMlbSnapshots":false}'
        AllSportsHotKickoff1amEtStandard:
          Type: Schedule
          Properties:
            Schedule: cron(0 6 * * ? *)
            Input: '{"sports":"wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis","run":"all_sports_hot_1am_et_kickoff_standard","policy":"aws_eventbridge_primary_1am_et_start_plus_15min","includeFullMlbSnapshots":false}'

""",
        "AllSportsLiveSchedulerFunction:",
    )

text = text.replace('"sports":"mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"', '"sports":"wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"')
text = text.replace('"includeFullMlbSnapshots":true', '"includeFullMlbSnapshots":false')

TEMPLATE.write_text(text)
exec(Path("scripts/patch_template_mlb_hot_start_v2.py").read_text())
exec(Path("scripts/patch_template_mlb_hot_pull_recovery_permanent.py").read_text())
exec(Path("scripts/verify_mlb_schedule_invariants.py").read_text())
print(
    "Patched template.yaml for INQSI MLB v1.1 single-game routes, EV promotion model env, "
    "fresh T-minus-45 daily lock guardrails, quarter-hour Odds API polling, same-day-only pulls, "
    "no legacy MLB T schedules, no duplicate MLB all-sports polling, and verified schedule invariants."
)
