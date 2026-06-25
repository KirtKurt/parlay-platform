from pathlib import Path

TEMPLATE = Path("template.yaml")
text = TEMPLATE.read_text()

marker = "        MLBHotEvery15Min:\n          Type: Schedule\n          Properties:\n            Schedule: rate(15 minutes)\n            Input: '{\"sport\":\"mlb\",\"t\":\"HOT\",\"run\":\"hot_pull_audited\",\"days_ahead\":1}'\n"

kickoff = """        MLBHotKickoff1amET:
          Type: Schedule
          Properties:
            Schedule: cron(0 5 * * ? *)
            Input: '{"sport":"mlb","t":"HOT","run":"hot_pull_audited_1am_et_kickoff","days_ahead":1,"schedule_policy":"MLB_1AM_ET_START_PLUS_15MIN"}'
"""

if "MLBHotKickoff1amET:" not in text:
    if marker not in text:
        raise RuntimeError("MLBHotEvery15Min schedule marker not found; cannot add 1 AM ET kickoff")
    text = text.replace(marker, marker + kickoff)

TEMPLATE.write_text(text)
print("Patched template.yaml with MLB 1 AM ET HOT kickoff schedule.")
