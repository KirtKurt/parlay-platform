from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()

START_DATE_UTC = "2026-07-03T05:00:00Z"  # 1:00 AM America/New_York on July 3, 2026
SCHEDULE_POLICY = "MLB_AUDITED_ODDS_SNAPSHOT_EVERY_15_MIN_START_2026_07_03_1AM_ET"


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
                if is_next_event or is_next_resource:
                    break
                i += 1
            continue
        output.append(line)
        i += 1
    return "".join(output)


# Replace older SAM/EventBridge Rule schedules with one EventBridge Scheduler
# resource that has an exact start boundary and America/New_York timezone policy.
for legacy_event in [
    "MLBHotEvery15Min",
    "MLBHotKickoff1amET",
    "MLBBasePull",
    "MLBT2",
    "MLBT3",
    "MLBT4",
]:
    text = remove_indented_event_block(text, legacy_event)

marker = "  MLBSignalApiFunction:\n"
if marker not in text:
    marker = "  MLBResultsSchedulerFunction:\n"
if marker not in text:
    marker = "Outputs:\n"

if "MLBAuditedOddsSnapshotEvery15From1amETSchedule:" not in text:
    scheduler_input = (
        '{"sport":"mlb","t":"HOT","run":"aws_scheduler_mlb_audited_odds_snapshot_every_15_min_from_2026_07_03_1am_et",'
        '"days_ahead":1,"policy":"' + SCHEDULE_POLICY + '","source":"the_odds_api"}'
    )
    block = f"""
  MLBAuditedOddsSnapshotEvery15From1amETInvokeRole:
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
        - PolicyName: InvokeMLBAuditedPull
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - lambda:InvokeFunction
                Resource: !GetAtt MLBAuditedPullFunction.Arn

  MLBAuditedOddsSnapshotEvery15From1amETSchedule:
    Type: AWS::Scheduler::Schedule
    Properties:
      Description: "MLB audited Odds API snapshot capture every 15 minutes starting 1:00 AM ET on 2026-07-03."
      State: ENABLED
      FlexibleTimeWindow:
        Mode: "OFF"
      ScheduleExpression: "cron(0/15 * * * ? *)"
      ScheduleExpressionTimezone: America/New_York
      StartDate: "{START_DATE_UTC}"
      Target:
        Arn: !GetAtt MLBAuditedPullFunction.Arn
        RoleArn: !GetAtt MLBAuditedOddsSnapshotEvery15From1amETInvokeRole.Arn
        Input: '{scheduler_input}'

"""
    text = insert_once(text, marker, block, "MLBAuditedOddsSnapshotEvery15From1amETSchedule:")

TEMPLATE.write_text(text)
print(
    "Patched template.yaml with dedicated MLB audited Odds API snapshot Scheduler: "
    "every 15 minutes beginning 2026-07-03 1:00 AM ET."
)
