from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()


def remove_child_event(s: str, name: str) -> str:
    lines = s.splitlines(keepends=True)
    out = []
    i = 0
    needle = f"        {name}:"
    while i < len(lines):
        if lines[i].startswith(needle):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if (nxt.startswith("        ") and not nxt.startswith("          ")) or (nxt.startswith("  ") and not nxt.startswith("    ")) or nxt.startswith("Outputs:"):
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def remove_resource(s: str, name: str) -> str:
    lines = s.splitlines(keepends=True)
    out = []
    i = 0
    needle = f"  {name}:"
    while i < len(lines):
        if lines[i].startswith(needle):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if (nxt.startswith("  ") and not nxt.startswith("    ")) or nxt.startswith("Outputs:"):
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def insert_once(s: str, marker: str, block: str, token: str) -> str:
    if token in s:
        return s
    if marker not in s:
        raise RuntimeError(f"Template marker not found: {marker.strip()}")
    return s.replace(marker, block + marker, 1)


def add_global_env(s: str, key: str, value: str) -> str:
    if f"        {key}:" in s:
        return s
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in s:
        raise RuntimeError("ODDS_API_KEY marker missing")
    return s.replace(marker, marker + f"        {key}: {value}\n", 1)


def patch_mlb_hot_block(s: str) -> str:
    lines = s.splitlines(keepends=True)
    out = []
    in_block = False
    seen = False
    for line in lines:
        if line.startswith("        MLBHotEvery15Min:"):
            in_block = True
            seen = True
            out.append(line)
            continue
        if in_block:
            if (line.startswith("        ") and not line.startswith("          ")) or (line.startswith("  ") and not line.startswith("    ")) or line.startswith("Outputs:"):
                in_block = False
                out.append(line)
                continue
            if line.lstrip().startswith("Schedule:"):
                out.append(line[: len(line) - len(line.lstrip())] + "Schedule: cron(0/15 * * * ? *)\n")
                continue
            if line.lstrip().startswith("Input:") and '"sport":"mlb"' in line:
                out.append(line[: len(line) - len(line.lstrip())] + "Input: '{\"sport\":\"mlb\",\"t\":\"HOT\",\"run\":\"hot_pull_audited\",\"days_ahead\":0}'\n")
                continue
        out.append(line)
    if not seen:
        raise RuntimeError("MLBHotEvery15Min block missing")
    return "".join(out)


def block_for(s: str, name: str) -> str:
    lines = s.splitlines(keepends=True)
    out = []
    in_block = False
    for line in lines:
        if line.startswith(f"        {name}:"):
            in_block = True
            out.append(line)
            continue
        if in_block:
            if (line.startswith("        ") and not line.startswith("          ")) or (line.startswith("  ") and not line.startswith("    ")) or line.startswith("Outputs:"):
                break
            out.append(line)
    return "".join(out)


# Remove old/dedicated MLB route functions that caused API Gateway/Lambda 502 on smoke tests.
# The stable ApiFunction proxy handles /v1/mlb/* through usercustomize.py.
text = remove_resource(text, "InqsiMLBV1CoreFunction")

for legacy in ["MLBBasePull", "MLBT2", "MLBT3", "MLBT4", "MLBHotKickoff1amET"]:
    text = remove_child_event(text, legacy)
text = remove_resource(text, "MLBHotPullRecoveryFunction")
text = patch_mlb_hot_block(text)
text = text.replace('"days_ahead":1', '"days_ahead":0').replace('"days_ahead": 1', '"days_ahead": 0')

for key, value in [
    ("MLB_PULL_START_AT_ET", "'2026-07-02T01:00:00-04:00'"),
    ("MLB_SCHED_INTERVAL_MINUTES", "'15'"),
    ("ODDS_PRIMARY_BOOK", "'fanduel'"),
    ("MLB_PROMOTION_EDGE_THRESHOLD", "'0.0015'"),
    ("MLB_PROMOTION_FALLBACK_EDGE_THRESHOLD", "'0.0005'"),
    ("MLB_MIN_EV_FOR_PROMOTION", "'0.0'"),
]:
    text = add_global_env(text, key, value)

text = insert_once(text, "  MLBResultsSchedulerFunction:\n", """
  MLBDailyPickLockFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_daily_pick_lock.lambda_handler
      # A full-slate lock invocation performs fail-closed, strongly consistent
      # readback of immutable pull manifests before any write.  A large MLB
      # pull history can legitimately exceed the 60-second global default.
      Timeout: 300
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

if "MLBDailyPickLockFunction:" in text:
    for line in ["          MLB_MIN_PULLS_PER_GAME_FOR_LOCK: '4'\n", "          MLB_MAX_LATEST_PULL_AGE_MINUTES_FOR_LOCK: '20'\n"]:
        if line.strip() not in text:
            text = text.replace("          MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'\n", "          MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'\n" + line, 1)

text = text.replace('"sports":"mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"', '"sports":"wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"')
text = text.replace('"includeFullMlbSnapshots":true', '"includeFullMlbSnapshots":false')

hot = block_for(text, "MLBHotEvery15Min")
violations = []
for required in ["MLBDailyPickLockFunction:", "Path: /v1/mlb/locks/status"]:
    if required not in text:
        violations.append(f"missing {required}")
if "Schedule: cron(0/15 * * * ? *)" not in hot:
    violations.append("MLBHotEvery15Min is not quarter-hour cron")
if '"days_ahead":1' in text or '"days_ahead": 1' in text:
    violations.append("days_ahead:1 still present")
for legacy in ["MLBBasePull", "MLBT2", "MLBT3", "MLBT4", "MLBHotKickoff1amET", "MLBHotPullRecoveryFunction", "InqsiMLBV1CoreFunction"]:
    if f"        {legacy}:" in text or f"  {legacy}:" in text:
        violations.append(f"{legacy} still present")
if violations:
    raise RuntimeError("Unsafe MLB SAM template after patch: " + "; ".join(violations))

TEMPLATE.write_text(text)
print("Patched template.yaml: MLB uses stable proxy routes, quarter-hour MLB HOT schedule, and daily lock.")
