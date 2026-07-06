from pathlib import Path

TEMPLATE = Path('template.yaml')
text = TEMPLATE.read_text()

# Time-zone correction for MLB Predictive Platform V1:
# - EventBridge Rule cron is UTC, but quarter-hour minute boundaries are the
#   same in UTC and America/New_York.
# - Using cron(0/15 ...) instead of rate(15 minutes) prevents arbitrary deploy-
#   minute offsets such as 1:07, 1:22, 1:37.
# - Production scheduled pulls must be same-day only. They must not populate
#   tomorrow's MLB partition before the daily slate begins.
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

# Scheduled EventBridge MLB pulls are same-day only. This prevents late-night
# pulls from being written into the next ET slate and inflating the count.
text = text.replace('"days_ahead":1', '"days_ahead":0')
text = text.replace('"days_ahead": 1', '"days_ahead": 0')

# Keep the historical one-time start override safe, but do not use it to permit
# tomorrow-slate pulls. The schedule input above controls the production scope.
if "MLB_PULL_START_AT_ET:" not in text:
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in text:
        raise RuntimeError("ODDS_API_KEY environment marker not found in template.yaml")
    replacement = (
        marker
        + "        MLB_PULL_START_AT_ET: '2026-07-02T01:00:00-04:00'\n"
        + "        MLB_SCHED_INTERVAL_MINUTES: '15'\n"
    )
    text = text.replace(marker, replacement, 1)


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
print('Patched template.yaml with MLB quarter-hour UTC cron and same-day-only scheduled pulls.')
