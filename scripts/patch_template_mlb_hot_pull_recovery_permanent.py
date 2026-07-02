from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()

START_DATE_UTC = "2026-07-03T05:00:00Z"  # 1:00 AM America/New_York on July 3, 2026
SCHEDULE_POLICY = "ODDS_API_LINE_MOVEMENT_TO_MLB_WINNERS_EVERY_15_MIN_START_2026_07_03_1AM_ET"


def insert_once(current: str, marker: str, block: str, contains: str) -> str:
    if contains in current:
        return current
    if marker not in current:
        raise RuntimeError(f"Template marker not found: {marker.strip()}")
    return current.replace(marker, block + marker, 1)


def remove_indented_event_block(current: str, event_name: str) -> str:
    """Remove an old SAM Function Events child block by event name.

    The dedicated MLB recovery path now uses EventBridge Scheduler with an exact
    StartDate and America/New_York schedule timezone. Old EventBridge Rule
    schedules remain safe if deployed, but they can double-fire the same 15-minute
    window. Remove them from freshly patched templates.
    """
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


for legacy_event in [
    "MLBHotPullRecoveryEvery15Min",
    "MLBHotPullRecoveryKickoff1amEtDst",
    "MLBHotPullRecoveryKickoff1amEtStandard",
]:
    text = remove_indented_event_block(text, legacy_event)

marker = "  InqsiAutopsySchedulerFunction:\n"
if marker not in text:
    marker = "Outputs:\n"

if "MLBHotPullRecoveryFunction:" not in text:
    function_block = """
  MLBHotPullRecoveryFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_hot_pull_recovery_lambda.lambda_handler
      Timeout: 300
      MemorySize: 1024
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref SnapshotsTable

"""
    text = insert_once(text, marker, function_block, "MLBHotPullRecoveryFunction:")

if "MLBWinnerPredictionEvery15From1amETSchedule:" not in text:
    scheduler_input = (
        '{"sport":"mlb","run":"aws_scheduler_mlb_line_movement_every_15_min_from_2026_07_03_1am_et",'
        '"policy":"' + SCHEDULE_POLICY + '","source":"the_odds_api","store_predictions":true}'
    )
    scheduler_block = f"""
  MLBWinnerPredictionEvery15From1amETInvokeRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: scheduler.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: InvokeMLBHotPullRecovery
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - lambda:InvokeFunction
                Resource: !GetAtt MLBHotPullRecoveryFunction.Arn

  MLBWinnerPredictionEvery15From1amETSchedule:
    Type: AWS::Scheduler::Schedule
    Properties:
      Description: "MLB Odds API line-movement pull, storage, and winner prediction every 15 minutes starting 1:00 AM ET on 2026-07-03."
      State: ENABLED
      FlexibleTimeWindow:
        Mode: "OFF"
      ScheduleExpression: "cron(0/15 * * * ? *)"
      ScheduleExpressionTimezone: America/New_York
      StartDate: "{START_DATE_UTC}"
      Target:
        Arn: !GetAtt MLBHotPullRecoveryFunction.Arn
        RoleArn: !GetAtt MLBWinnerPredictionEvery15From1amETInvokeRole.Arn
        Input: '{scheduler_input}'

"""
    text = insert_once(text, marker, scheduler_block, "MLBWinnerPredictionEvery15From1amETSchedule:")

TEMPLATE.write_text(text)
print(
    "Patched template.yaml with dedicated MLB Odds API line-movement recovery Lambda "
    "and EventBridge Scheduler cadence: every 15 minutes beginning 2026-07-03 1:00 AM ET."
)
