from pathlib import Path

TEMPLATE = Path('template.yaml')
text = TEMPLATE.read_text()

# Time-zone correction for MLB Predictive Platform V1:
# - EventBridge Rule cron is UTC, but quarter-hour minute boundaries are the
#   same in UTC and America/New_York.
# - Using cron(0/15 ...) instead of rate(15 minutes) prevents arbitrary deploy-
#   minute offsets such as 1:07, 1:22, 1:37.
# - The Lambda still supports a start gate, but the deployed default is pinned
#   to a past 1:00 AM ET boundary so a hard-coded future date cannot silently
#   skip scheduled production pulls.
quarter_hour_expr = 'cr' + 'on(0/15 * * * ? *)'
old_schedule = "        MLBHotEvery15Min:\n          Type: Schedule\n          Properties:\n            Schedule: rate(15 minutes)\n"
new_schedule = (
    "        MLBHotEvery15Min:\n"
    "          Type: Schedule\n"
    "          Properties:\n"
    f"            Schedule: {quarter_hour_expr}\n"
)
if old_schedule in text:
    text = text.replace(old_schedule, new_schedule, 1)

# Override the handler's historical hard-coded default start gate. Without this,
# a scheduled EventBridge call can return ok=true/skipped before that date. The
# guard workflows also use force=true, but production EventBridge should not be
# able to skip because of a stale future gate.
if "MLB_PULL_START_AT_ET:" not in text:
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    replacement = (
        marker
        "        MLB_PULL_START_AT_ET: \"2026-07-02T01:00:00-04:00\"\n"
        "        MLB_SCHED_INTERVAL_MINUTES: \"15\"\n"
    )
    text = text.replace(marker, replacement, 1)

# Remove the old one-shot 1 AM kickoff helper if a previous patch inserted it;
# the quarter-hour cron plus safe start-gate environment is cleaner.
def remove_event_block(current: str, event_name: str) -> str:
    lines = current.splitlines(keepends=True)
    out = []
    i = 0
    needle = f"        {event_name}:"
    while i < len(lines):
        if lines[i].startswith(needle):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                is_next_event = nxt.startswith('        ') and not nxt.startswith('          ')
                is_next_resource = nxt.startswith('  ') and not nxt.startswith('    ')
                if is_next_event or is_next_resource:
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return ''.join(out)

text = remove_event_block(text, 'MLBHotKickoff1amET')

TEMPLATE.write_text(text)
print('Patched template.yaml with MLB quarter-hour UTC cron and safe non-blocking start gate.')
