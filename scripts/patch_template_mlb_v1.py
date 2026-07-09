from __future__ import annotations

import re
from pathlib import Path

ROOT = Path.cwd()
TEMPLATE = ROOT / "template.yaml"


def read(path: str) -> str:
    return (ROOT / path).read_text()


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text)
    print(f"patched {path}")


def remove_indented_event_block(current: str, event_name: str) -> str:
    lines = current.splitlines(keepends=True)
    out = []
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
        out.append(line)
        i += 1
    return "".join(out)


def remove_resource_block(current: str, resource_name: str) -> str:
    lines = current.splitlines(keepends=True)
    out = []
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
        out.append(lines[i])
        i += 1
    return "".join(out)


def insert_once(current: str, marker: str, block: str, contains: str) -> str:
    if contains in current:
        return current
    if marker not in current:
        raise RuntimeError(f"Template marker not found: {marker.strip()}")
    return current.replace(marker, block + marker, 1)


def patch_template() -> None:
    text = TEMPLATE.read_text()
    for event_name in ["MLBBasePull", "MLBT2", "MLBT3", "MLBT4", "MLBHotKickoff1amET"]:
        text = remove_indented_event_block(text, event_name)
    text = remove_resource_block(text, "MLBHotPullRecoveryFunction")
    text = re.sub(
        r'(        MLBHotEvery15Min:\n          Type: Schedule\n          Properties:\n)            Schedule: [^\n]+\n            Input: .*?\n',
        r'\1            Schedule: cron(0/15 * * * ? *)\n            Input: \'{"sport":"mlb","t":"HOT","run":"hot_pull_audited","days_ahead":0}\'\n',
        text,
        count=1,
        flags=re.S,
    )
    text = text.replace('"days_ahead":1', '"days_ahead":0').replace('"days_ahead": 1', '"days_ahead": 0')
    if "MLB_PULL_START_AT_ET:" not in text:
        marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
        if marker in text:
            text = text.replace(marker, marker + "        MLB_PULL_START_AT_ET: '2026-07-02T01:00:00-04:00'\n        MLB_SCHED_INTERVAL_MINUTES: '15'\n", 1)
    if "RawArchiveBucket:" not in text:
        text = insert_once(text, "  InqsiMembersTable:\n", """
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

""", "RawArchiveBucket:")
    if "RAW_ARCHIVE_BUCKET: !Ref RawArchiveBucket" not in text:
        text = text.replace("        OUTCOMES_TABLE: !Ref OutcomesTable\n", "        OUTCOMES_TABLE: !Ref OutcomesTable\n        RAW_ARCHIVE_BUCKET: !Ref RawArchiveBucket\n", 1)
    if "MLBDailyPickLockFunction:" not in text:
        text = insert_once(text, "  MLBResultsSchedulerFunction:\n", """
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
          MLB_MIN_PULLS_PER_GAME_FOR_LOCK: '4'
          MLB_MAX_LATEST_PULL_AGE_MINUTES_FOR_LOCK: '20'
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

""", "MLBDailyPickLockFunction:")
    else:
        for env_line in ["          MLB_MIN_PULLS_PER_GAME_FOR_LOCK: '4'\n", "          MLB_MAX_LATEST_PULL_AGE_MINUTES_FOR_LOCK: '20'\n"]:
            if env_line.strip() not in text:
                text = text.replace("          MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'\n", "          MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'\n" + env_line, 1)
    text = text.replace('"sports":"mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"', '"sports":"wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"')
    text = text.replace('"includeFullMlbSnapshots":true', '"includeFullMlbSnapshots":false')
    violations = []
    if '"days_ahead":1' in text or '"days_ahead": 1' in text:
        violations.append("days_ahead:1 still present")
    if "Schedule: rate(15 minutes)" in text and "MLBHotEvery15Min" in text:
        violations.append("MLBHotEvery15Min is not quarter-hour cron")
    for legacy_event in ["MLBBasePull", "MLBT2", "MLBT3", "MLBT4", "MLBHotKickoff1amET", "MLBHotPullRecoveryFunction"]:
        if legacy_event in text:
            violations.append(f"{legacy_event} still present")
    if violations:
        raise RuntimeError("Unsafe MLB SAM template after patch: " + "; ".join(violations))
    TEMPLATE.write_text(text)
    print("patched template.yaml for single MLB HOT ingest path plus T-minus-45 daily lock")


def patch_manual_pull() -> None:
    path = "hello_world/mlb_manual_pull.py"
    text = read(path)
    old = '''        game_key = f"mlb|{game_date}|{away.lower()}|{home.lower()}"
        games_out.append({
            "id": raw_game.get("id") or game_key,
            "game_id": raw_game.get("id") or game_key,
            "game_key": game_key,
            "internal_key": game_key,
'''
    new = '''        provider_event_id = str(raw_game.get("id") or "").strip()
        commence_key = str(raw_game.get("commence_time") or "unknown").replace("|", "_")
        if provider_event_id:
            game_key = f"mlb|{game_date}|event|{provider_event_id}"
        else:
            game_key = f"mlb|{game_date}|{commence_key}|{away.lower()}|{home.lower()}"
        games_out.append({
            "id": provider_event_id or game_key,
            "game_id": provider_event_id or game_key,
            "game_key": game_key,
            "game_identity": provider_event_id or game_key,
            "internal_key": game_key,
'''
    if old in text:
        text = text.replace(old, new, 1)
    text = text.replace('"game_key_pattern": "mlb|YYYY-MM-DD|away|home"', '"game_key_pattern": "mlb|YYYY-MM-DD|event|ODDS_API_EVENT_ID"')
    write(path, text)


def patch_signal_api() -> None:
    path = "hello_world/mlb_signal_api.py"
    text = read(path)
    marker = '''def _build_three_leg_parlay(rows: List[Dict[str, Any]], latest_games: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:'''
    start = text.find(marker)
    end = text.find("\n\ndef hot_sides", start)
    if start >= 0 and end > start:
        text = text[:start] + '''def _build_three_leg_parlay(rows: List[Dict[str, Any]], latest_games: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {"ok": False, "deprecated": True, "reason": "MLB production is individual game moneyline picks only."}
''' + text[end:]
    text = text.replace('"message": "MLB game-winner attempts and the 3-leg parlay attempt are returned for pre-start display; No Clean Edge rows remain marked as No Clean Edge.",', '"message": "MLB individual game-winner attempts are returned for pre-start display; parlay output is deprecated for MLB production.",')
    text = text.replace('"7_three_leg_parlay_attempt": "CONNECTED",', '"7_three_leg_parlay_attempt": "DEPRECATED_MLB_SINGLE_GAME_ONLY",')
    write(path, text)


def patch_date_signal_api() -> None:
    path = "hello_world/mlb_date_signal_api.py"
    text = read(path)
    text = text.replace('parlay = _build_three_leg_parlay(rows, latest_games)', 'parlay = {"ok": False, "deprecated": True, "reason": "MLB production is individual game moneyline picks only."}')
    text = text.replace('"message": "MLB game-winner attempts and 3-leg parlay are built only from date-isolated 15-minute HOT pull history. Advanced context is scored into eligibility and blocks ADVANCED_ELIGIBLE when required feeds are missing.",', '"message": "MLB individual game-winner attempts are built only from date-isolated 15-minute HOT pull history. Parlay output is deprecated for MLB production.",')
    write(path, text)


def patch_core_api() -> None:
    path = "hello_world/inqsi_mlb_v1_core.py"
    text = read(path)
    text = text.replace('"message": "INQSI MLB v1.0 core is available. Individual game-winner prediction is now the first priority; parlays are secondary assembly."', '"message": "INQSI MLB core is single-game moneyline picks only. Parlays are deprecated on the MLB production path."')
    text = text.replace(', "parlay_analysis": parlay_analysis(market_rows, data.get("three_leg_parlay") or {})', ', "parlay_analysis": {"deprecated": True, "reason": "MLB production is individual game moneyline picks only."}')
    write(path, text)


def main() -> None:
    patch_template()
    patch_manual_pull()
    patch_signal_api()
    patch_date_signal_api()
    patch_core_api()
    print("MLB AWS production patch complete: single-game ML engine, no MLB parlays, same-day quarter-hour HOT pulls, T-minus-45 lock.")


if __name__ == "__main__":
    main()
