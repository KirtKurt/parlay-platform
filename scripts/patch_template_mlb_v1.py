from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()

if "RAW_ARCHIVE_BUCKET:" not in text:
    text = text.replace("        OUTCOMES_TABLE: !Ref OutcomesTable\n", "        OUTCOMES_TABLE: !Ref OutcomesTable\n        RAW_ARCHIVE_BUCKET: !Ref RawArchiveBucket\n")

if "RawArchiveBucket:" not in text:
    marker = "  InqsiMembersTable:\n"
    bucket = """
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

"""
    text = text.replace(marker, bucket + marker)

legacy_blocks = ["MLBBasePull", "MLBT2", "MLBT3", "MLBT4"]
for name in legacy_blocks:
    while f"        {name}:\n" in text:
        start = text.index(f"        {name}:\n")
        next_start = text.find("        ", start + 1)
        if next_start == -1:
            text = text[:start]
        else:
            text = text[:start] + text[next_start:]

if "InqsiMLBV1CoreFunction:" not in text:
    marker = "  MLBResultsSchedulerFunction:\n"
    resource = """
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
            Schedule: rate(15 minutes)
            Input: '{"sport":"mlb","run":"hot_raw_archive"}'

"""
    text = text.replace(marker, resource + marker)

if "MLBResultSignalsFunction:" not in text:
    marker = "  MLBResultsSchedulerFunction:\n"
    resource = """
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

"""
    text = text.replace(marker, resource + marker)

TEMPLATE.write_text(text)
exec(Path("scripts/patch_template_mlb_hot_start_v2.py").read_text())
print("Patched template.yaml for INQSI MLB v1 routes, result-signal learning, raw S3 archive, 1 AM ET HOT kickoff, and HOT-only scheduled MLB pulls.")
