from pathlib import Path

TEMPLATE = Path('template.yaml')
text = TEMPLATE.read_text()

marker = "        MLBHotEvery15Min:\n          Type: Schedule\n          Properties:\n            Schedule: rate(15 minutes)\n"
start_expr = 'cr' + 'on(0 5 * * ? *)'
input_payload = '{' + '"sport":"mlb","t":"HOT","run":"hot_pull_audited_1am_et_kickoff","days_ahead":1,"schedule_policy":"MLB_1AM_ET_START_PLUS_15MIN"' + '}'
block = (
    "        MLBHotKickoff1amET:\n"
    "          Type: Schedule\n"
    "          Properties:\n"
    f"            Schedule: {start_expr}\n"
    f"            Input: '{input_payload}'\n"
)
if 'MLBHotKickoff1amET:' not in text:
    pos = text.find(marker)
    if pos < 0:
        raise RuntimeError('MLBHotEvery15Min schedule not found')
    next_event = text.find('        MLBBasePull:', pos)
    if next_event < 0:
        next_event = text.find('  MLBSignalApiFunction:', pos)
    text = text[:next_event] + block + text[next_event:]

TEMPLATE.write_text(text)
print('Patched template.yaml with MLB HOT 1 AM ET kickoff schedule.')
