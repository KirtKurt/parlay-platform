from pathlib import Path

TEMPLATE = Path('template.yaml')
text = TEMPLATE.read_text()

violations = []

if '"days_ahead":1' in text or '"days_ahead": 1' in text:
    violations.append('unsafe days_ahead=1 exists in template')

for event_name in ['MLBBasePull', 'MLBT2', 'MLBT3', 'MLBT4', 'MLBHotKickoff1amET']:
    if f'        {event_name}:' in text:
        violations.append(f'legacy MLB schedule still exists: {event_name}')

if 'MLBHotEvery15Min:' not in text:
    violations.append('MLBHotEvery15Min schedule missing')

if 'cron(0/15 * * * ? *)' not in text:
    violations.append('MLBHotEvery15Min is not quarter-hour cron')

if '"days_ahead":0' not in text and '"days_ahead": 0' not in text:
    violations.append('same-day days_ahead=0 input missing')

if violations:
    raise SystemExit('MLB schedule invariant failure: ' + '; '.join(violations))

print('MLB schedule invariants PASS: same-day only, quarter-hour cron, no legacy MLB T schedules.')
