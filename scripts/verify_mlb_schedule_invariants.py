from pathlib import Path
import subprocess
import sys

TEMPLATE = Path('template.yaml')
ENGINE = Path('hello_world/mlb_game_winner_engine.py')
COVERAGE_VERIFY = Path('scripts/verify_mlb_complete_slate_coverage.py')
ML_OPTIMIZATION_VERIFY = Path('scripts/verify_mlb_ml_optimization_v3.py')
ML_PROMOTION_VERIFY = Path('scripts/verify_mlb_ml_promotion_safety.py')
ML_FEATURE_INTEGRITY_VERIFY = Path('scripts/verify_mlb_ml_feature_integrity.py')
ML_INSTALLATION_1_5_VERIFY = Path('scripts/verify_mlb_ml_installation_1_5.py')
text = TEMPLATE.read_text()
engine = ENGINE.read_text() if ENGINE.exists() else ''
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

required_template_strings = {
    'MLBDailyPickLockFunction': 'daily lock function missing',
    'MLBDailyPickLockEveryMinute': 'daily lock one-minute scheduler missing',
    'MLBProductionVerifierFunction': 'AWS production verifier function missing',
    'MLBProductionVerifierEvery5Min': '5-minute AWS production verifier schedule missing',
    'MLBProductionIngestVerifyDaily435Et': 'daily ingest verification schedule missing',
    'MLBProductionLockVerifyDaily556Et': 'daily lock verification schedule missing',
    'MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME': 'lock T-minus env missing',
    '/v1/mlb/locks/run': 'manual lock route missing',
    '/v1/mlb/locks/status': 'lock status route missing',
    '/v1/mlb/locks/today': 'lock today route missing',
}
for needle, message in required_template_strings.items():
    if needle not in text:
        violations.append(message)
if not ('MLB_MIN_PULLS_FOR_LOCK' in text or 'MLB_MIN_PULLS_PER_GAME_FOR_LOCK' in text):
    violations.append('minimum pull-depth lock guardrail missing')
if not ('MLB_MAX_LOCK_SNAPSHOT_AGE_MINUTES' in text or 'MLB_MAX_LATEST_PULL_AGE_MINUTES_FOR_LOCK' in text):
    violations.append('stale snapshot lock guardrail missing')
if not ('MLB_PRIMARY_BOOK' in text or 'ODDS_PRIMARY_BOOK' in text or 'PRIMARY_BOOK' in engine):
    violations.append('real-book pricing setting missing')
if not ('MLB_PROMOTION_EDGE_THRESHOLD' in text or 'PROMOTION_THRESHOLD' in engine):
    violations.append('promotion threshold setting missing')
if '"sports":"mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis"' in text:
    violations.append('all-sports scheduler still includes MLB duplicate pulls')

for required_path in [
    Path('hello_world/mlb_slate_coverage_patch.py'),
    Path('hello_world/mlb_doubleheader_safe_audit_patch.py'),
    Path('hello_world/mlb_all_games_coverage_patch.py'),
    Path('hello_world/mlb_ml_frozen_features.py'),
    Path('hello_world/mlb_ml_exact_lock_vector_patch.py'),
    Path('hello_world/mlb_official_freeze_bridge.py'),
    Path('hello_world/mlb_ml_audit_feature_bridge_v1.py'),
    Path('hello_world/mlb_ml_clean_cohort_v1.py'),
    Path('hello_world/mlb_ml_clean_cohort_hardening_v1.py'),
    Path('hello_world/mlb_ml_dual_model_v1.py'),
    Path('hello_world/mlb_ml_walk_forward_v1.py'),
    Path('hello_world/mlb_fundamentals_snapshot_v1.py'),
    Path('hello_world/mlb_ml_champion_challenger_v1.py'),
    Path('hello_world/mlb_ml_manual_promotion_only_v1.py'),
    Path('hello_world/mlb_ml_champion_runtime_v1.py'),
    Path('hello_world/mlb_ml_runtime_safety_patch.py'),
    Path('hello_world/mlb_ml_runtime_install_v3.py'),
    Path('hello_world/mlb_ml_optimization_v3.py'),
    Path('scripts/promote_mlb_ml_champion.py'),
    Path('.github/workflows/mlb-ml-promote-champion.yml'),
    COVERAGE_VERIFY,
    ML_OPTIMIZATION_VERIFY,
    ML_PROMOTION_VERIFY,
    ML_FEATURE_INTEGRITY_VERIFY,
    ML_INSTALLATION_1_5_VERIFY,
]:
    if not required_path.exists():
        violations.append(f'production component missing: {required_path}')

if violations:
    raise SystemExit('MLB production invariant failure: ' + '; '.join(violations))

subprocess.run([sys.executable, str(COVERAGE_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_OPTIMIZATION_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_PROMOTION_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_FEATURE_INTEGRITY_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_INSTALLATION_1_5_VERIFY)], check=True)

print('MLB production invariants PASS: complete-slate coverage, exact clean-cohort lock vectors, final-label joins, separate outcome/reliability models, untouched validation with priced ROI, source-honest fundamentals, independent promotion gates, manual-only DDB champion promotion, and single runtime authority are installed.')
