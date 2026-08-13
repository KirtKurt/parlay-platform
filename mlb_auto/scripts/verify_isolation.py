from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src' / 'mlb_auto'
TEMPLATE = ROOT / 'template.yaml'

forbidden_runtime_tokens = (
    'hello_world.',
    'import tennis',
    'from tennis',
    'parlay_platform_snapshots',
    'parlay_platform_predictions',
    'parlay_platform_outcomes',
    'parlay-platform-tennis-ml-prod',
)

violations = []
for path in SRC.glob('*.py'):
    text = path.read_text()
    lower = text.lower()
    for token in forbidden_runtime_tokens:
        if token.lower() in lower:
            violations.append(f'{path.name}: forbidden token {token}')

text = TEMPLATE.read_text()
required = (
    'MLB_AUTO_STATE_TABLE', 'MLB_AUTO_SNAPSHOTS_TABLE', 'MLB_AUTO_PREDICTIONS_TABLE',
    'MLB_AUTO_LOCKS_TABLE', 'MLB_AUTO_OUTCOMES_TABLE', 'MLB_AUTO_MODELS_TABLE',
    'mlb_auto.autonomous_handler.handler',
)
for token in required:
    if token not in text:
        violations.append(f'template missing {token}')

if re.search(r'\bSNAPSHOTS_TABLE\b', text) or re.search(r'\bPREDICTIONS_TABLE\b', text):
    violations.append('template contains generic shared table environment names')

if violations:
    raise SystemExit('MLB_AUTO_ISOLATION_FAILED\n' + '\n'.join(violations))
print('PASS: MLB Auto runtime and infrastructure are isolated from Tennis and existing MLB resources')
