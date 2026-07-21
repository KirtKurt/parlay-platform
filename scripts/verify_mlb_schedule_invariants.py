from pathlib import Path
import ast
import os
import re
import subprocess
import sys

# Validation imports Lambda modules that create boto3 resources at import time.
# The audit job intentionally configures production AWS credentials only after
# this offline invariant suite passes, so give those imports a deterministic,
# network-free region instead of letting botocore consult instance metadata.
if not os.environ.get('AWS_DEFAULT_REGION'):
    os.environ['AWS_DEFAULT_REGION'] = os.environ.get('AWS_REGION') or 'us-east-1'
if not os.environ.get('AWS_REGION'):
    os.environ['AWS_REGION'] = os.environ['AWS_DEFAULT_REGION']
os.environ.setdefault('AWS_EC2_METADATA_DISABLED', 'true')

TEMPLATE = Path('template.yaml')
ENGINE = Path('hello_world/mlb_game_winner_engine.py')
LOCK_HANDLER = Path('hello_world/mlb_daily_pick_lock_protected.py')
MANUAL_PULL_HANDLER = Path('hello_world/mlb_manual_pull_protected.py')
COVERAGE_VERIFY = Path('scripts/verify_mlb_complete_slate_coverage.py')
IMMUTABLE_LOCKED_STORAGE_VERIFY = Path('scripts/verify_mlb_immutable_locked_storage.py')
DAILY_LOCK_VECTOR_VERIFY = Path('scripts/verify_mlb_daily_lock_ml_vector_preservation.py')
ACCURACY_TARGET_SEPARATION_VERIFY = Path('scripts/verify_mlb_accuracy_target_separation.py')
ML_OPTIMIZATION_VERIFY = Path('scripts/verify_mlb_ml_optimization_v3.py')
ML_PROMOTION_VERIFY = Path('scripts/verify_mlb_ml_promotion_safety.py')
ML_FEATURE_INTEGRITY_VERIFY = Path('scripts/verify_mlb_ml_feature_integrity.py')
ML_TEMPORAL_MISSINGNESS_VERIFY = Path('scripts/verify_mlb_temporal_missingness_features.py')
PER_GAME_LOCK_VERIFY = Path('scripts/verify_mlb_per_game_lock.py')
LOCKED_STORAGE_FINALIZER_VERIFY = Path('scripts/verify_mlb_locked_storage_finalizer.py')
ML_INSTALLATION_1_5_VERIFY = Path('scripts/verify_mlb_ml_installation_1_5.py')
MLB_API_FUNCTION_IMPORT_VERIFY = Path('scripts/verify_api_function_mlb_v3_import.py')
ML_TRAINING_READINESS_VERIFY = Path('scripts/verify_mlb_ml_training_readiness.py')
YESTERDAY_AUDIT_IMMUTABLE_SOURCE_VERIFY = Path('scripts/verify_mlb_yesterday_audit_immutable_source.py')
text = TEMPLATE.read_text()
engine = ENGINE.read_text() if ENGINE.exists() else ''
violations = []


def _verify_explicit_lock_runtime_install() -> None:
    if not LOCK_HANDLER.exists():
        return
    try:
        tree = ast.parse(LOCK_HANDLER.read_text())
    except SyntaxError as exc:
        violations.append(f'protected MLB lock handler is not valid Python: {exc}')
        return

    install_lines = []
    lock_import_lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == 'install'
                and isinstance(func.value, ast.Name)
                and func.value.id == 'mlb_ml_runtime_install_v3'
            ):
                install_lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name == 'mlb_daily_pick_lock' for alias in node.names):
                lock_import_lines.append(node.lineno)

    if not install_lines:
        violations.append('protected MLB lock handler does not explicitly install the ML runtime')
    if not lock_import_lines:
        violations.append('protected MLB lock handler does not import the daily lock implementation')
    if install_lines and lock_import_lines and min(install_lines) >= min(lock_import_lines):
        violations.append('protected MLB lock handler installs the ML runtime after importing the daily lock implementation')

    source = LOCK_HANDLER.read_text()
    if 'MLB_ML_LOCK_RUNTIME_NOT_READY' not in source:
        violations.append('protected MLB lock handler does not fail closed when ML runtime installation is incomplete')
    if 'mlRuntimeInstallation' not in source:
        violations.append('protected MLB lock handler does not expose ML runtime installation status')
    if 'MLB-LOCK-RUNTIME-FIX-v5-official-schedule-lifecycle-vector-separation' not in source:
        violations.append('protected MLB lock handler is missing the official-schedule lifecycle/vector-separation fix marker')
    if 'MLB_PER_GAME_LOCK_ATTEMPT_DIAGNOSTICS_VERSION' not in source:
        violations.append('protected MLB lock handler does not require durable lock-attempt diagnostics')
    if 'lastPrelockAtCutoffBecomesFinal' not in source or 'modelOrSignalRecomputedAtLock' not in source:
        violations.append('protected MLB lock handler does not attest no-rescore last-prelock promotion')
    if 'candidatePayloadFingerprintVersion' not in source or 'candidatePayloadFingerprintDdbReadCanonical' not in source:
        violations.append('protected MLB lock handler does not attest DynamoDB-canonical candidate fingerprints')
    for token in (
        'readinessCheckpointsAtTMinus60AndTMinus50',
        'lockOutcomeStatusSeparateFromPrediction',
        'latePlayabilityAssessmentCannotRewriteSelection',
        'doubleheaderGame2EventDrivenPlayabilityRecheck',
        'officialScheduleAuthorityRequired',
        'selectionLockIndependentOfTrainingVector',
    ):
        if token not in source:
            violations.append(f'protected MLB lock handler missing lifecycle attestation: {token}')


def _verify_explicit_pull_runtime_install() -> None:
    if not MANUAL_PULL_HANDLER.exists():
        return
    try:
        tree = ast.parse(MANUAL_PULL_HANDLER.read_text())
    except SyntaxError as exc:
        violations.append(f'protected MLB pull handler is not valid Python: {exc}')
        return

    install_lines = []
    pull_import_lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == 'install'
                and isinstance(func.value, ast.Name)
                and func.value.id == 'mlb_ml_runtime_install_v3'
            ):
                install_lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name == 'mlb_manual_pull' for alias in node.names):
                pull_import_lines.append(node.lineno)

    if not install_lines:
        violations.append('protected MLB pull handler does not explicitly install the ML runtime')
    if not pull_import_lines:
        violations.append('protected MLB pull handler does not import the HOT candidate writer')
    if install_lines and pull_import_lines and min(install_lines) >= min(pull_import_lines):
        violations.append('protected MLB pull handler imports the candidate writer before installing the ML runtime')

    source = MANUAL_PULL_HANDLER.read_text()
    if 'MLB_ML_PULL_RUNTIME_NOT_READY' not in source:
        violations.append('protected MLB pull handler does not fail closed when ML runtime installation is incomplete')
    if 'lastPrelockPromotionAuthority' not in source:
        violations.append('protected MLB pull handler does not require last-prelock promotion authority')
    if 'immutableLockedStorageAuthority' not in source:
        violations.append('protected MLB pull handler does not require verified immutable stage storage authority')
    if 'MLB_SCHEDULED_PULL_FAILED' not in source:
        violations.append('protected MLB pull handler hides delegated scheduled pull failures')


def _ensure_validation_dependencies() -> None:
    """Install the AWS SDK only when the runner does not already provide it.

    The production-invariant suite imports Lambda modules that initialize DynamoDB
    clients at import time. GitHub's setup-sam action keeps its boto3 dependency in
    an isolated virtual environment, so the workflow Python interpreter may still
    lack boto3. Installing it here makes the mandatory pre-deploy check deterministic
    without weakening or skipping the coverage tests.
    """
    try:
        import boto3  # noqa: F401
        import botocore  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    subprocess.run(
        [
            sys.executable,
            '-m',
            'pip',
            'install',
            '--disable-pip-version-check',
            '--quiet',
            'boto3>=1.34,<2',
        ],
        check=True,
    )


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
if "MLB_PULL_START_AT_ET: '01:00'" not in text:
    violations.append('recurring daily 1 AM ET pull gate missing')
if "Schedule: rate(15 minutes)" not in text or "results_pull_15m" not in text:
    violations.append('MLB result settlement is not scheduled every 15 minutes')
for obsolete in ['MLBProductionIngestVerifyDaily435Et', 'MLBProductionLockVerifyDaily556Et']:
    if obsolete in text:
        violations.append(f'obsolete fixed-UTC verifier schedule exists: {obsolete}')
for required in [
    'DeployGitSha:',
    'DeployTemplateSha256:',
    'INQSI_DEPLOY_GIT_SHA: !Ref DeployGitSha',
    'INQSI_DEPLOY_TEMPLATE_SHA256: !Ref DeployTemplateSha256',
]:
    if required not in text:
        violations.append(f'deploy identity contract missing: {required}')

required_template_strings = {
    'MLBDailyPickLockFunction': 'daily lock function missing',
    'MLBDailyPickLockEveryMinute': 'daily lock one-minute scheduler missing',
    'MLBProductionVerifierFunction': 'AWS production verifier function missing',
    'MLBProductionVerifierEvery5Min': '5-minute AWS production verifier schedule missing',
    'MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME': 'lock T-minus env missing',
    '/v1/mlb/locks/run': 'manual lock route missing',
    '/v1/mlb/locks/status': 'lock status route missing',
    '/v1/mlb/locks/today': 'lock today route missing',
    'MLBMLArtifactsBucket': 'versioned MLB ML artifact bucket missing',
    'MLBMLTrainingFunction': 'AWS-native MLB ML trainer missing',
    'Handler: mlb_ml_aws_training_v1.lambda_handler': 'AWS-native MLB ML trainer handler missing',
    'MLBMLTrainingEvery6Hours': 'AWS-native MLB ML full training schedule missing',
    'Schedule: rate(6 hours)': 'AWS-native MLB ML full training is not scheduled every 6 hours',
    'MLBMLSelectionCaptureEvery15Minutes': 'AWS-native MLB ML selection capture schedule missing',
    'Input: \'{"sport":"mlb","mode":"scheduled","run":"aws_native_fixed_prospective_shadow_training"}\'': 'AWS-native MLB ML full training event input is stale',
    'Input: \'{"sport":"mlb","mode":"selection_capture","run":"aws_native_prospective_selection_capture"}\'': 'AWS-native MLB ML selection capture event input is stale',
    'ReservedConcurrentExecutions: 1': 'AWS-native MLB ML trainer must not overlap',
    's3:GetObjectVersion': 'AWS-native MLB ML trainer cannot read an exact frozen challenger version',
    "MLB_ML_EXPERIMENT_ID: 'mlb-v2-2026-07-21-future-prospective-r2'": 'AWS-native MLB ML trainer experiment identity is stale',
    "MLB_ML_RELEASE_CONTRACT_ID: 'mlb-v2-2026-07-21-future-prospective-r2'": 'AWS-native MLB ML trainer release contract is stale',
    "MLB_ML_RELEASE_CUTOFF_UTC: '2026-07-22T04:00:00+00:00'": 'AWS-native MLB ML trainer release cutoff is stale',
    "INQSI_MLB_ML_AUTO_PROMOTE: 'false'": 'automatic MLB ML promotion must be disabled',
    "INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED: 'false'": 'legacy MLB V1 runtime authority must be disabled',
    'MLBMLTrainingDeadLetterQueue': 'AWS-native MLB ML trainer dead-letter queue missing',
    'MaximumRetryAttempts: 2': 'AWS-native MLB ML trainer retry policy missing',
    'Arn: !GetAtt MLBMLTrainingDeadLetterQueue.Arn': 'AWS-native MLB ML trainer DLQ binding missing',
    'Service: events.amazonaws.com': 'AWS-native MLB ML trainer DLQ send policy missing',
}
for needle, message in required_template_strings.items():
    if needle not in text:
        violations.append(message)
if 'MLBMLTrainingEvery15Minutes' in text:
    violations.append('obsolete full MLB training every-15-minutes schedule exists')

lock_resource_marker = '\n  MLBDailyPickLockFunction:\n'
lock_resource_start = text.find(lock_resource_marker)
if lock_resource_start >= 0:
    resource_tail_start = lock_resource_start + len(lock_resource_marker)
    next_resource_match = re.search(r'(?m)^  [A-Za-z][A-Za-z0-9]*:\s*$', text[resource_tail_start:])
    lock_resource_end = (
        resource_tail_start + next_resource_match.start()
        if next_resource_match is not None
        else len(text)
    )
    lock_resource = text[lock_resource_start:lock_resource_end]
    if '\n      Timeout: 300\n' not in lock_resource:
        violations.append('daily lock function timeout must allow full immutable-manifest verification')
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

_verify_explicit_lock_runtime_install()
_verify_explicit_pull_runtime_install()

for required_path in [
    Path('hello_world/mlb_slate_coverage_patch.py'),
    Path('hello_world/mlb_doubleheader_safe_audit_patch.py'),
    Path('hello_world/mlb_all_games_coverage_patch.py'),
    Path('hello_world/mlb_immutable_locked_storage_patch.py'),
    Path('hello_world/mlb_daily_lock_ml_vector_preservation_patch.py'),
    Path('hello_world/mlb_daily_per_game_lock_patch.py'),
    Path('hello_world/mlb_official_schedule_authority.py'),
    LOCK_HANDLER,
    MANUAL_PULL_HANDLER,
    Path('hello_world/mlb_accuracy_target_policy_v1.py'),
    Path('hello_world/mlb_ml_frozen_features.py'),
    Path('hello_world/mlb_ml_exact_lock_vector_patch.py'),
    Path('hello_world/mlb_official_freeze_bridge.py'),
    Path('hello_world/mlb_ml_audit_feature_bridge_v1.py'),
    Path('hello_world/mlb_ml_clean_cohort_v1.py'),
    Path('hello_world/mlb_ml_clean_cohort_hardening_v1.py'),
    Path('hello_world/mlb_ml_dual_model_v1.py'),
    Path('hello_world/mlb_temporal_features_v1.py'),
    Path('hello_world/mlb_ml_feature_missingness_v1.py'),
    Path('hello_world/mlb_ml_walk_forward_v1.py'),
    Path('hello_world/mlb_fundamentals_snapshot_v1.py'),
    Path('hello_world/mlb_fundamentals_snapshot_v2.py'),
    Path('hello_world/mlb_canonical_final_labels_v1.py'),
    Path('hello_world/mlb_prediction_probability_contract_v1.py'),
    Path('hello_world/mlb_ml_current_lock_authority_v1.py'),
    Path('hello_world/mlb_ml_experiment_v2.py'),
    Path('hello_world/mlb_ml_walk_forward_v2.py'),
    Path('hello_world/mlb_ml_dual_model_v2.py'),
    Path('hello_world/mlb_ml_promotion_policy_v2.py'),
    Path('hello_world/mlb_ml_aws_training_v1.py'),
    Path('hello_world/mlb_ml_champion_challenger_v1.py'),
    Path('hello_world/mlb_ml_champion_runtime_v1.py'),
    Path('hello_world/mlb_ml_runtime_safety_patch.py'),
    Path('hello_world/mlb_ml_runtime_install_v3.py'),
    Path('hello_world/mlb_locked_prediction_storage_finalizer_v1.py'),
    Path('hello_world/mlb_ml_optimization_v3.py'),
    Path('scripts/promote_mlb_ml_champion.py'),
    COVERAGE_VERIFY,
    IMMUTABLE_LOCKED_STORAGE_VERIFY,
    DAILY_LOCK_VECTOR_VERIFY,
    ACCURACY_TARGET_SEPARATION_VERIFY,
    ML_OPTIMIZATION_VERIFY,
    ML_PROMOTION_VERIFY,
    ML_FEATURE_INTEGRITY_VERIFY,
    ML_TEMPORAL_MISSINGNESS_VERIFY,
    PER_GAME_LOCK_VERIFY,
    LOCKED_STORAGE_FINALIZER_VERIFY,
    ML_INSTALLATION_1_5_VERIFY,
    MLB_API_FUNCTION_IMPORT_VERIFY,
    ML_TRAINING_READINESS_VERIFY,
    YESTERDAY_AUDIT_IMMUTABLE_SOURCE_VERIFY,
]:
    if not required_path.exists():
        violations.append(f'production component missing: {required_path}')

if violations:
    raise SystemExit('MLB production invariant failure: ' + '; '.join(violations))

_ensure_validation_dependencies()
subprocess.run([sys.executable, str(COVERAGE_VERIFY)], check=True)
subprocess.run([sys.executable, str(IMMUTABLE_LOCKED_STORAGE_VERIFY)], check=True)
subprocess.run([sys.executable, str(DAILY_LOCK_VECTOR_VERIFY)], check=True)
subprocess.run([sys.executable, str(ACCURACY_TARGET_SEPARATION_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_OPTIMIZATION_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_PROMOTION_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_FEATURE_INTEGRITY_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_TEMPORAL_MISSINGNESS_VERIFY)], check=True)
subprocess.run([sys.executable, str(PER_GAME_LOCK_VERIFY)], check=True)
subprocess.run([sys.executable, str(LOCKED_STORAGE_FINALIZER_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_INSTALLATION_1_5_VERIFY)], check=True)
subprocess.run([sys.executable, str(ML_TRAINING_READINESS_VERIFY)], check=True)
subprocess.run([sys.executable, str(YESTERDAY_AUDIT_IMMUTABLE_SOURCE_VERIFY)], check=True)
subprocess.run([sys.executable, str(MLB_API_FUNCTION_IMPORT_VERIFY)], check=True)

print('MLB production invariants PASS: canonical 15-minute evidence, persisted probability authority, immutable locks and official labels, source-honest Fundamentals V2, fixed 300/100/100 whole-slate experiments, AWS-native versioned shadow training, legacy V1 authority disabled, dashboard-only 90% aspiration, and manual-first V2 promotion are installed.')
