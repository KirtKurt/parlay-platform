from pathlib import Path

TEMPLATE = Path('template.yaml')
text = TEMPLATE.read_text()


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


# MLB production pull policy:
# - AWS EventBridge is the only automatic MLB pull source.
# - Scheduled MLB pulls are same-day only.
# - No late-night tomorrow-slate writes.
# - No legacy T1/T2/T3/T4 schedules.
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

# Force scheduled EventBridge MLB inputs to same-day only.
text = text.replace('"days_ahead":1', '"days_ahead":0')
text = text.replace('"days_ahead": 1', '"days_ahead": 0')

# Remove legacy MLB schedules that can create duplicate or non-HOT pulls.
for event_name in ['MLBHotKickoff1amET', 'MLBBasePull', 'MLBT2', 'MLBT3', 'MLBT4']:
    text = remove_event_block(text, event_name)

# Keep the handler start-gate environment safe.
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

# Hard invariant check. If this fails, do not deploy a broken schedule.
violations = []
if '"days_ahead":1' in text or '"days_ahead": 1' in text:
    violations.append('days_ahead:1 still present')
for legacy_event in ['MLBBasePull', 'MLBT2', 'MLBT3', 'MLBT4', 'MLBHotKickoff1amET']:
    if f"        {legacy_event}:" in text:
        violations.append(f'{legacy_event} still present')
if violations:
    raise RuntimeError('Unsafe MLB pull schedule after patch: ' + '; '.join(violations))

TEMPLATE.write_text(text)
print('Patched template.yaml with MLB quarter-hour UTC cron, same-day-only scheduled pulls, and hard safety invariants.')
