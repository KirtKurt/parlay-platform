from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()

if "MLBHotPullRecoveryFunction:" not in text:
    marker = "  InqsiAutopsySchedulerFunction:\n"
    if marker not in text:
        marker = "Outputs:\n"
    block = """
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
      Events:
        MLBHotPullRecoveryEvery15Min:
          Type: Schedule
          Properties:
            Schedule: rate(15 minutes)
            Input: '{"sport":"mlb","run":"aws_dedicated_mlb_hot_pull_recovery_every_15_min","policy":"permanent_mlb_hot_pull_recovery"}'
        MLBHotPullRecoveryKickoff1amEtDst:
          Type: Schedule
          Properties:
            Schedule: cron(0 5 * * ? *)
            Input: '{"sport":"mlb","run":"aws_dedicated_mlb_hot_pull_recovery_1am_et_dst","policy":"permanent_mlb_hot_pull_recovery"}'
        MLBHotPullRecoveryKickoff1amEtStandard:
          Type: Schedule
          Properties:
            Schedule: cron(0 6 * * ? *)
            Input: '{"sport":"mlb","run":"aws_dedicated_mlb_hot_pull_recovery_1am_et_standard","policy":"permanent_mlb_hot_pull_recovery"}'

"""
    text = text.replace(marker, block + marker, 1)

TEMPLATE.write_text(text)
print("Patched template.yaml with permanent dedicated MLB HOT pull recovery Lambda and EventBridge schedules.")
