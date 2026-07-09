from pathlib import Path

TEMPLATE = Path('template.yaml')
text = TEMPLATE.read_text()

violations = []

if '"days_ahead":1' in text or '"days_ahead": 1' in text:
    violations.append('unsafe days_ahead=1 exists in template')

for event_name in ['MLBBasePull', 'MLBT2', 'MLBT3', 'MLBT4', 'MLBHotKickoff1amET', 'MLBHotPullRecoveryFunction']:
    if f'        {event_name}:' in text or f'  {event_name}:' in text:
        violations.append(f'legacy/duplicate MLB schedule still exists: {event_name}')

if 'MLBHotEvery15Min:' not in text:
    violations.append('MLBHotEvery15Min schedule missing')

if 'cron(0/15 * * * ? *)' not in text:
    violations.append('MLBHotEvery15Min is not quarter-hour cron')

if '"days_ahead":0' not in text and '"days_ahead": 0' not in text:
    violations.append('same-day days_ahead=0 input missing')

required_strings = {
    'MLBDailyPickLockFunction': 'daily lock function missing',
    'MLBDailyPickLockEveryMinute': 'daily lock one-minute scheduler missing',
    'MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME': 'lock T-minus env missing',
    'MLB_MIN_PULLS_FOR_LOCK': 'minimum pull-depth lock guardrail missing',
    'MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES': 'stale snapshot lock guardrail missing',
    'MLB_PRIMARY_BOOK': 'real-book pricing env missing',
    'MLB_PROMOTION_EDGE_THRESHOLD': 'promotion threshold env missing',
    '/v1/mlb/locks/run': 'manual lock route missing',
    '/v1/mlb/locks/status': 'lock status route missing',
    '/v1/mlb/locks/today': 'lock today route missing',
}
for needle, message in required_strings.items():
    if needle not in text:
        violations.append(message)

if '"sports":"mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"' in text:
    violations.append('all-sports scheduler still includes MLB duplicate pulls')

if violations:
    raise SystemExit('MLB production invariant failure: ' + '; '.join(violations))

print('MLB production invariants PASS: same-day quarter-hour Odds API pulls, no legacy MLB schedules, daily T-minus lock, fresh-snapshot guardrails, and real-book promotion env are present.')
