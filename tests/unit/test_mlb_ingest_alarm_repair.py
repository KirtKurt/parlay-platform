from __future__ import annotations
import copy
import importlib.util
import json
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / 'hello_world'
HANDLER = HELLO / 'mlb_manual_pull_protected.py'
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

REQUIRED = {
    'accuracyTargetsSeparated','legacyReliabilityOverlaySafety','sourceHonestFundamentals',
    'sourceHonestFundamentalsV2','legacyV1ChampionRuntimeInstalledForShadowDiagnostics',
    'legacyV1AuthorityDisabled','v2ShadowManualFirst','officialSemanticsFinalized',
    'exactCleanCohortVectorPatch','officialFreezeBridge','immutableFeatureFreeze',
    'immutableLockedStorageAuthority','canonicalLockedStorageFinalizer',
    'lastPrelockPromotionAuthority','canonicalProbabilityAndPersistedPrelockAuthority',
    'providerNeutralCalibrationAndActionability','legacyFinalGateDisabled',
}
DEFECT_SCOPE_VERSION = 'MLB-WINNER-LIFECYCLE-DEFECT-SCOPE-v1-release-separated'
CONVERGENCE_VERSION = 'MLB-WINNER-LIFECYCLE-CONVERGENCE-v1-exact-cutoff-read-only'
GAME_DATE = '2026-08-24'
CUTOFF = '2026-08-25T01:00:00+00:00'
COMMENCE = '2026-08-25T01:45:00+00:00'
OPEN_CUTOFF = '2026-08-25T01:30:00+00:00'
OPEN_COMMENCE = '2026-08-25T02:15:00+00:00'
ASOF = '2026-08-25T01:00:26+00:00'
PULL_ID = 'mlb_v1_2026-08-24_2026_08_25T01_00_26_00_00'
PROVIDER_MANIFEST_VERSION = 'INQSI-PROVIDER-SCHEDULE-MANIFEST-v1'
OFFICIAL_AUTHORITY_VERSION = (
    'MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date'
)
STORAGE_VERSION = (
    'MLB-LOCKED-PREDICTION-STORAGE-FINALIZER-v6-effective-schedule-lifecycle'
)
STORAGE_AUTHORITY = 'consistent-read verified immutable T-minus-45 stage'
PROVIDER_PK = f'PROVIDER_MANIFEST#mlb#{GAME_DATE}'
PROVIDER_SK = f'OBSERVED#{ASOF}#PULL#{PULL_ID}'
SCHEDULED_EVENT = {
    'sport': 'mlb',
    't': 'HOT',
    'run': 'hot_pull_audited',
    'days_ahead': 0,
}

def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)

def manifest(bound=True, game_count=1):
    return {
        'game_date_et':'2026-08-24','gameCount':game_count,
        'version':PROVIDER_MANIFEST_VERSION,'fingerprint':'a'*64,
        'pk':PROVIDER_PK,'sk':PROVIDER_SK,
        'immutable':True,'fullProviderSchedule':True,
        'boundToCanonicalPull':bound,
        'officialScheduleBacked':True,
        'officialScheduleAuthorityBound':True,
        'officialScheduleAuthorityVersion':OFFICIAL_AUTHORITY_VERSION,
        'officialScheduleAuthorityFingerprint':'b'*64,
        'officialScheduleGameCount':game_count,
        'ok':bound,
    }

def winner(ok=False, operational=False):
    return {
        'game_date_et':'2026-08-24','ok':ok,'operationalDefect':operational,
        'gameCount':1,'allGamesPredicted':True,'displayStatusCoverageComplete':True,
        'lifecycleCoverageComplete':True,'preLockStorageLifecycleAware':True,
        'preLockStorageCandidateCount':1,'preLockStoredCount':1,
        'preLockStorageComplete':True,'preLockStorageDispositionCount':1,
        'preLockStorageDispositionComplete':True,
    }

def payload(*, bound=True, winner_row=None, asof=ASOF, slot=CUTOFF):
    winner_result = winner_row or winner()
    game_count = winner_result.get('gameCount', 1)
    out = {
        'ok':True,'count':game_count,'providerScheduleManifestComplete':bound,
        'sport':'mlb','live_pull_ok':True,'fallback_used':False,
        't':'HOT','run':'hot_pull_audited',
        'provider_schedule_manifests':[manifest(bound, game_count)],
        'game_winner_predictions':[winner_result],
        'asof': asof,
        'days_ahead': 0,
        'canonical_pull_history': [{
            'game_date_et': GAME_DATE,
            'ok': True,
            'error': None,
            'providerManifestBound': True,
            'providerManifestImmutable': True,
            'providerManifestFullSchedule': True,
            'providerManifestValidationErrors': [],
            'providerManifestVersion': PROVIDER_MANIFEST_VERSION,
            'providerManifestPk': PROVIDER_PK,
            'providerManifestSk': PROVIDER_SK,
            'providerManifestFingerprint': 'a' * 64,
            'providerManifestGameCount': game_count,
            'canonicalManifestValidatedAgainstPersistedPull': True,
            'officialScheduleAuthorityBound': True,
            'officialScheduleBacked': True,
            'officialScheduleAuthorityVersion': OFFICIAL_AUTHORITY_VERSION,
            'officialScheduleAuthorityFingerprint': 'b' * 64,
            'officialScheduleGameCount': game_count,
            'games': game_count,
            'canonicalPullPk': f'PULLS#mlb#{GAME_DATE}',
            'pk': f'PULLS#mlb#{GAME_DATE}',
            'canonicalPullSk': f'PULL#SLOT#{slot}',
            'canonicalPulledAtUtc': asof,
            'canonicalSlotStartUtc': slot,
            'pull_id': PULL_ID,
            'canonicalPullId': PULL_ID,
            'retryReturnedExistingCanonicalPull': False,
            'sameSlotRetryAuthorityRebound': False,
        }],
    }
    return out

def lifecycle_row(
    status='LOCK_DUE_CANONICAL_MISSING',
    *,
    game_id='game-1',
    cutoff=CUTOFF,
    commence=COMMENCE,
):
    raw_id = game_id.removeprefix('provider:')
    locked = status == 'LOCKED_CANONICAL'
    if locked:
        official_status = 'OFFICIAL_LOCKED_PREDICTION'
        per_game_status = 'OFFICIAL_LOCKED_PREDICTION'
        recommendation_status = 'PICK'
        tags = [
            'CANONICAL_PER_GAME_LOCK',
            'FINAL_LOCKED',
            'OFFICIAL_LOCKED_PREDICTION',
            'OFFICIAL_PREDICTION',
        ]
    elif status == 'OPEN_PRE_LOCK':
        official_status = 'PRE_LOCK_PLATFORM_PREDICTION'
        per_game_status = 'OPEN_PRE_LOCK'
        recommendation_status = 'PRE_LOCK_PREDICTION'
        tags = [
            'PER_GAME_CANONICAL_LOCK_PENDING',
            'PRE_LOCK_PREDICTION',
        ]
    else:
        official_status = status
        per_game_status = status
        recommendation_status = status
        tags = [status, 'PER_GAME_CANONICAL_LOCK_MISSING']
    return {
        'gameId': raw_id,
        'gameIdentity': raw_id,
        'providerEventId': raw_id,
        'commenceTime': commence,
        'scheduledLockAtUtc': cutoff,
        'lockStatus': status,
        'officialPredictionStatus': official_status,
        'recommendationStatus': recommendation_status,
        'locked': locked,
        'lockedPrediction': locked,
        'lockOutcomeRecorded': locked,
        'canonical': locked,
        'officialPrediction': locked,
        'officialPick': locked,
        'isOfficialDisplayPick': locked,
        'displayPrediction': True,
        'perGameCanonicalLock': {
            'status': per_game_status,
            'lockAtUtc': cutoff,
            'canonical': locked,
        },
        'tags': tags,
    }

def lifecycle_card(*args, **kwargs):
    row = lifecycle_row(*args, **kwargs)
    row.pop('isOfficialDisplayPick')
    row.pop('displayPrediction')
    return row

def due_winner():
    row = winner(ok=True, operational=True)
    row.update({
        'slate_date': GAME_DATE,
        'canonicalPredictionComplete': False,
        'readAuthority': 'persisted_prelock_and_canonical_locked_only',
        'operationalDefectScopeVersion': DEFECT_SCOPE_VERSION,
        'winnerLifecycleOperationalDefect': True,
        'releasePlayabilityOperationalDefect': False,
        'operationalDefectScopes': ['WINNER_LIFECYCLE'],
        'preLockStorageCandidateCount': 0,
        'preLockStoredCount': 0,
        'stored': False,
        'storedCount': 0,
        'preLockStorageLifecycleSkippedCount': 1,
        'preLockStorageLifecycleSkippedStatuses': [
            'LOCK_DUE_CANONICAL_MISSING',
        ],
        'preLockStorageErrors': [],
        'preLockStorageRowCount': 1,
        'canonicalLockedStorageCandidateCount': 0,
        'canonicalLockedStoredCount': 0,
        'canonicalLockedStorageComplete': False,
        'canonicalLockedStorageErrors': {},
        'canonicalLockedStorageVersion': STORAGE_VERSION,
        'canonicalLockedStorageSuppressedUnauthorizedCount': 0,
        'canonicalLockedStorageAuthority': STORAGE_AUTHORITY,
        'canonicalLockedStorageSuppressedEarlyWrites': True,
        'invalidTerminalLifecycleRows': {},
        'invalidPlayabilityReleaseRows': {},
        'predictions': [lifecycle_row()],
        'perGameStatus': [lifecycle_card()],
        'slatePredictionLock': {
            'manifestGameIdentities': ['provider:game-1'],
            'providerManifestVersion': PROVIDER_MANIFEST_VERSION,
            'providerManifestPk': PROVIDER_PK,
            'providerManifestSk': PROVIDER_SK,
            'providerManifestFingerprint': 'a' * 64,
            'providerManifestObservedAtUtc': ASOF,
            'providerManifestPullId': PULL_ID,
            'providerManifestValidated': True,
            'providerManifestImmutable': True,
            'providerManifestFullProviderSchedule': True,
            'officialScheduleAuthorityFingerprint': 'b' * 64,
            'officialScheduleAuthorityVersion': OFFICIAL_AUTHORITY_VERSION,
            'officialScheduleBacked': True,
            'officialScheduleAuthoritativeStartTimes': True,
            'durableRosterImmutableReadbackVerified': True,
            'manifestGameCount': 1,
            'verifiedFullSlateGameCount': 1,
            'officialScheduleGameCount': 1,
            'lockStatus': 'LOCK_DUE_CANONICAL_MISSING',
            'lockMinutesBeforeEachGame': 45,
            'lockDueCanonicalMissingCount': 1,
            'missedLockCount': 0,
            'canonicalLockedGameCount': 0,
            'pendingCanonicalGameCount': 1,
            'canonicalCoverageComplete': False,
            'canonicalPredictionComplete': False,
            'canonicalReadOperational': True,
            'canonicalReadError': None,
            'pendingCanonicalStatuses': {
                'provider:game-1': 'LOCK_DUE_CANONICAL_MISSING',
            },
            'invalidCanonicalRows': {},
            'invalidLifecycleStatusRows': {},
            'invalidTerminalLifecycleRows': {},
            'invalidPlayabilityReleaseRows': {},
        },
        'slateCoverage': {
            'invalidCanonicalRows': {},
            'invalidLifecycleStatusRows': {},
            'invalidPersistedPrelockRows': {},
            'ambiguousCurrentPredictionIdentities': [],
            'extraCurrentPredictionIdentities': [],
            'missingGameIdentities': [],
            'missingWinnerPredictionGameIdentities': [],
            'missingLifecycleDisplayGameIdentities': [],
            'canonicalReadAuthorityWriteCount': 0,
            'prelockPredictionAuthority': (
                'validated_immutable_pregame_snapshot'
            ),
            'publicPrelockRecomputed': False,
            'storeRequested': False,
            'canonicalReadOperational': True,
            'canonicalReadError': None,
            'lockDueCanonicalMissingCount': 1,
            'missedLockCount': 0,
            'canonicalLockedGameCount': 0,
            'pendingCanonicalGameCount': 1,
            'canonicalCoverageComplete': False,
            'canonicalPredictionComplete': False,
            'pendingCanonicalStatuses': {
                'provider:game-1': 'LOCK_DUE_CANONICAL_MISSING',
            },
        },
    })
    return row

def converged_winner():
    row = copy.deepcopy(due_winner())
    row.update({
        'operationalDefect': False,
        'winnerLifecycleOperationalDefect': False,
        'releasePlayabilityOperationalDefect': False,
        'operationalDefectScopes': [],
        'canonicalPredictionComplete': True,
    })
    row['slatePredictionLock'].update({
        'lockStatus': 'COMPLETE_MANIFEST_ALL_CANONICAL',
        'lockDueCanonicalMissingCount': 0,
        'missedLockCount': 0,
        'pendingCanonicalStatuses': {},
        'canonicalLockedGameCount': 1,
        'pendingCanonicalGameCount': 0,
        'canonicalCoverageComplete': True,
        'canonicalPredictionComplete': True,
    })
    row['slateCoverage'].update({
        'lockDueCanonicalMissingCount': 0,
        'missedLockCount': 0,
        'pendingCanonicalStatuses': {},
        'canonicalLockedGameCount': 1,
        'pendingCanonicalGameCount': 0,
        'canonicalCoverageComplete': True,
        'canonicalPredictionComplete': True,
    })
    row['predictions'] = [lifecycle_row('LOCKED_CANONICAL')]
    row['perGameStatus'] = [lifecycle_card('LOCKED_CANONICAL')]
    return row

def staggered_due_winner():
    row = copy.deepcopy(due_winner())
    due = lifecycle_row()
    open_row = lifecycle_row(
        'OPEN_PRE_LOCK',
        game_id='game-2',
        cutoff=OPEN_CUTOFF,
        commence=OPEN_COMMENCE,
    )
    pending = {
        'provider:game-1': 'LOCK_DUE_CANONICAL_MISSING',
        'provider:game-2': 'OPEN_PRE_LOCK',
    }
    open_card = lifecycle_card(
        'OPEN_PRE_LOCK',
        game_id='game-2',
        cutoff=OPEN_CUTOFF,
        commence=OPEN_COMMENCE,
    )
    row.update({
        'gameCount': 2,
        'stored': True,
        'storedCount': 1,
        'preLockStorageCandidateCount': 1,
        'preLockStoredCount': 1,
        'preLockStorageDispositionCount': 2,
        'preLockStorageRowCount': 2,
        'predictions': [due, open_row],
        'perGameStatus': [lifecycle_card(), open_card],
    })
    row['slatePredictionLock'].update({
        'manifestGameIdentities': [
            'provider:game-1',
            'provider:game-2',
        ],
        'pendingCanonicalStatuses': pending,
        'pendingCanonicalGameCount': 2,
        'manifestGameCount': 2,
        'verifiedFullSlateGameCount': 2,
        'officialScheduleGameCount': 2,
    })
    row['slateCoverage'].update({
        'pendingCanonicalStatuses': copy.deepcopy(pending),
        'pendingCanonicalGameCount': 2,
    })
    return row

def staggered_converged_winner():
    row = copy.deepcopy(staggered_due_winner())
    canonical = lifecycle_row('LOCKED_CANONICAL')
    open_row = lifecycle_row(
        'OPEN_PRE_LOCK',
        game_id='game-2',
        cutoff=OPEN_CUTOFF,
        commence=OPEN_COMMENCE,
    )
    canonical_card = lifecycle_card('LOCKED_CANONICAL')
    open_card = lifecycle_card(
        'OPEN_PRE_LOCK',
        game_id='game-2',
        cutoff=OPEN_CUTOFF,
        commence=OPEN_COMMENCE,
    )
    row.update({
        'operationalDefect': False,
        'winnerLifecycleOperationalDefect': False,
        'releasePlayabilityOperationalDefect': False,
        'operationalDefectScopes': [],
        'canonicalPredictionComplete': False,
        'predictions': [canonical, open_row],
        'perGameStatus': [canonical_card, open_card],
    })
    row['slatePredictionLock'].update({
        'lockStatus': 'PARTIAL_PER_GAME_CANONICAL',
        'lockDueCanonicalMissingCount': 0,
        'missedLockCount': 0,
        'pendingCanonicalStatuses': {
            'provider:game-2': 'OPEN_PRE_LOCK',
        },
        'canonicalLockedGameCount': 1,
        'pendingCanonicalGameCount': 1,
        'canonicalCoverageComplete': False,
        'canonicalPredictionComplete': False,
    })
    row['slateCoverage'].update({
        'lockDueCanonicalMissingCount': 0,
        'missedLockCount': 0,
        'pendingCanonicalStatuses': {
            'provider:game-2': 'OPEN_PRE_LOCK',
        },
        'canonicalLockedGameCount': 1,
        'pendingCanonicalGameCount': 1,
        'canonicalCoverageComplete': False,
        'canonicalPredictionComplete': False,
    })
    return row

class CallLog(list):
    def __init__(self):
        super().__init__()
        self.winner_reads = []

@contextmanager
def loaded(responses, *, winner_reads=None):
    responses = list(responses)
    winner_reads = list(winner_reads or [])
    manual = ModuleType('mlb_manual_pull')
    calls=CallLog()
    def call(event, context):
        calls.append(dict(event))
        value = responses.pop(0)
        return {'statusCode':200,'body':json.dumps(value)}
    manual.lambda_handler=call
    if winner_reads:
        def read_persisted_predictions(game_date, *, store, limit):
            calls.winner_reads.append({
                'game_date': game_date,
                'store': store,
                'limit': limit,
            })
            value = winner_reads.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        manual.mlb_game_winner_engine = SimpleNamespace(
            read_persisted_predictions=read_persisted_predictions,
            _INQSI_MLB_PERSISTED_PRELOCK_PUBLIC_AUTHORITY_ENABLED=True,
            MLB_PUBLIC_PER_GAME_AUTHORITY_VERSION=(
                'MLB-LAST-PRELOCK-PROMOTION-AUTHORITY-v1-canonical-read-overlay'
            ),
        )
    runtime=ModuleType('mlb_ml_runtime_install_v3')
    runtime.VERSION='test-v'
    def install():
        sys.modules['mlb_manual_pull']=manual
        return {'applied':True,'ok':True,'version':'test-v','steps':{k:True for k in REQUIRED},'errors':[]}
    runtime.install=install
    old_r=sys.modules.get('mlb_ml_runtime_install_v3'); old_m=sys.modules.get('mlb_manual_pull')
    name='_alarm_repair_'+uuid.uuid4().hex
    try:
        sys.modules['mlb_ml_runtime_install_v3']=runtime
        sys.modules.pop('mlb_manual_pull',None)
        spec=importlib.util.spec_from_file_location(name,HANDLER)
        mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod)
        yield mod,calls
    finally:
        sys.modules.pop(name,None)
        if old_r is None: sys.modules.pop('mlb_ml_runtime_install_v3',None)
        else: sys.modules['mlb_ml_runtime_install_v3']=old_r
        if old_m is None: sys.modules.pop('mlb_manual_pull',None)
        else: sys.modules['mlb_manual_pull']=old_m

def test_negative_ev_nonactionable_result_does_not_raise_when_storage_complete():
    with loaded([payload(winner_row=winner(ok=False, operational=False))]) as (handler,calls):
        response=handler.lambda_handler({'sport':'mlb'},None)
    assert response['statusCode']==200
    assert len(calls)==1

def test_operational_winner_failure_still_raises():
    with loaded([payload(winner_row=winner(ok=False, operational=True))]) as (handler,calls):
        try: handler.lambda_handler({'sport':'mlb'},None)
        except RuntimeError as exc: message=str(exc)
        else: raise AssertionError('expected operational failure')
    assert 'winner_prediction_failed:2026-08-24' in message
    assert len(calls)==1

def test_release_only_playability_gap_remains_visible_without_failing_winner_ingest():
    release_only = winner(ok=True, operational=True)
    release_only.update({
        'operationalDefectScopeVersion': DEFECT_SCOPE_VERSION,
        'winnerLifecycleOperationalDefect': False,
        'releasePlayabilityOperationalDefect': True,
        'operationalDefectScopes': ['RELEASE_PLAYABILITY'],
        'invalidPlayabilityReleaseRows': {
            'provider:game-1': ['T_MINUS_15:required_assessment_missing'],
        },
    })
    with loaded([payload(winner_row=release_only)]) as (handler,calls):
        response=handler.lambda_handler({'sport':'mlb'},None)

    body=json.loads(response['body'])
    returned=body['game_winner_predictions'][0]
    health=body['winnerLifecycleHealth']
    assert response['statusCode']==200
    assert returned['operationalDefect'] is True
    assert returned['winnerLifecycleOperationalDefect'] is False
    assert returned['releasePlayabilityOperationalDefect'] is True
    assert returned['invalidPlayabilityReleaseRows'] == {
        'provider:game-1': ['T_MINUS_15:required_assessment_missing'],
    }
    assert health == {
        'version': DEFECT_SCOPE_VERSION,
        'resultCount': 1,
        'scopedResultCount': 1,
        'scopeComplete': True,
        'winnerLifecycleHealthy': True,
        'winnerLifecycleOperationalDefectDates': [],
        'releasePlayabilityHealthy': False,
        'releasePlayabilityOperationalDefectDates': ['2026-08-24'],
        'releasePlayabilityFailClosed': True,
    }
    assert len(calls)==1

def test_release_only_scope_cannot_suppress_incomplete_canonical_locked_storage():
    combined_defect = winner(ok=False, operational=True)
    combined_defect.update({
        'operationalDefectScopeVersion': DEFECT_SCOPE_VERSION,
        # Reproduce the stale release-only scope emitted before the storage
        # finalizer discovered that its canonical write failed.
        'winnerLifecycleOperationalDefect': False,
        'releasePlayabilityOperationalDefect': True,
        'operationalDefectScopes': ['RELEASE_PLAYABILITY'],
        'canonicalLockedStorageCandidateCount': 1,
        'canonicalLockedStoredCount': 0,
        'canonicalLockedStorageComplete': False,
        'canonicalLockedStorageErrors': {
            'provider:game-1': ['injected canonical write failure'],
        },
    })
    with loaded([payload(winner_row=combined_defect)]) as (handler,calls):
        try: handler.lambda_handler({'sport':'mlb'},None)
        except RuntimeError as exc: message=str(exc)
        else: raise AssertionError('expected canonical locked storage failure')

    assert 'winner_prediction_failed:2026-08-24' in message
    assert 'canonical_locked_storage_incomplete:2026-08-24' in message
    assert 'canonical_locked_storage_count_mismatch:2026-08-24' in message
    assert 'canonical_locked_storage_errors:2026-08-24' in message
    assert len(calls)==1

def test_canonical_locked_storage_errors_fail_even_without_candidates():
    orphan_error = winner(ok=True, operational=False)
    orphan_error.update({
        'canonicalLockedStorageCandidateCount': 0,
        'canonicalLockedStoredCount': 0,
        'canonicalLockedStorageComplete': False,
        'canonicalLockedStorageErrors': {
            'provider:orphan': ['injected disposition error'],
        },
    })
    with loaded([payload(winner_row=orphan_error)]) as (handler,calls):
        try: handler.lambda_handler({'sport':'mlb'},None)
        except RuntimeError as exc: message=str(exc)
        else: raise AssertionError('expected canonical locked storage error')

    assert 'winner_prediction_failed:2026-08-24' in message
    assert 'canonical_locked_storage_errors:2026-08-24' in message
    assert 'canonical_locked_storage_incomplete:2026-08-24' not in message
    assert len(calls)==1

def test_incomplete_defect_scope_cannot_suppress_an_operational_winner_failure():
    malformed_scope = winner(ok=True, operational=True)
    malformed_scope.update({
        'operationalDefectScopeVersion': DEFECT_SCOPE_VERSION,
        'winnerLifecycleOperationalDefect': False,
        # The required release boolean and exact scopes are intentionally absent.
    })
    with loaded([payload(winner_row=malformed_scope)]) as (handler,calls):
        try: handler.lambda_handler({'sport':'mlb'},None)
        except RuntimeError as exc: message=str(exc)
        else: raise AssertionError('expected malformed defect scope to fail closed')

    assert 'winner_prediction_failed:2026-08-24' in message
    assert len(calls)==1

def test_scoped_terminal_lifecycle_defect_still_fails_winner_ingest():
    terminal_defect = winner(ok=True, operational=True)
    terminal_defect.update({
        'operationalDefectScopeVersion': DEFECT_SCOPE_VERSION,
        'winnerLifecycleOperationalDefect': True,
        'releasePlayabilityOperationalDefect': False,
        'operationalDefectScopes': ['WINNER_LIFECYCLE'],
        'invalidTerminalLifecycleRows': {
            'provider:game-1': ['terminal_outcome_fingerprint_mismatch'],
        },
    })
    with loaded([payload(winner_row=terminal_defect)]) as (handler,calls):
        try: handler.lambda_handler({'sport':'mlb'},None)
        except RuntimeError as exc: message=str(exc)
        else: raise AssertionError('expected scoped terminal lifecycle failure')

    assert 'winner_prediction_failed:2026-08-24' in message
    assert len(calls)==1

def test_scoped_storage_defect_still_fails_winner_ingest():
    storage_defect = winner(ok=False, operational=True)
    storage_defect.update({
        'operationalDefectScopeVersion': DEFECT_SCOPE_VERSION,
        'winnerLifecycleOperationalDefect': True,
        'releasePlayabilityOperationalDefect': False,
        'operationalDefectScopes': ['WINNER_LIFECYCLE'],
        'preLockStoredCount': 0,
        'preLockStorageComplete': False,
    })
    with loaded([payload(winner_row=storage_defect)]) as (handler,calls):
        try: handler.lambda_handler({'sport':'mlb'},None)
        except RuntimeError as exc: message=str(exc)
        else: raise AssertionError('expected scoped storage lifecycle failure')

    assert 'winner_prediction_failed:2026-08-24' in message
    assert 'prelock_storage_incomplete:2026-08-24' in message
    assert len(calls)==1

def invoke_at(handler, now, body):
    handler._utc_now = lambda: utc(now)
    return handler.lambda_handler(dict(SCHEDULED_EVENT), body)

def scheduled_failure(handler, now='2026-08-25T01:01:00+00:00'):
    try:
        invoke_at(handler, now, None)
    except RuntimeError as exc:
        return str(exc)
    raise AssertionError('expected scheduled pull failure')

def test_row_contract_accepts_live_coverage_overlay_shapes():
    import mlb_slate_coverage_patch as coverage

    due = coverage._prelock_row({
        'gameId': 'game-1',
        'gameIdentity': 'game-1',
        'providerEventId': 'game-1',
        'commenceTime': COMMENCE,
        'tags': [],
    }, {}, CUTOFF, 'LOCK_DUE_CANONICAL_MISSING')
    open_row = coverage._prelock_row({
        'gameId': 'game-2',
        'gameIdentity': 'game-2',
        'providerEventId': 'game-2',
        'commenceTime': OPEN_COMMENCE,
        'tags': [],
    }, {}, OPEN_CUTOFF)
    canonical = coverage._official_row({
        'gameId': 'game-1',
        'gameIdentity': 'game-1',
        'providerEventId': 'game-1',
        'commenceTime': COMMENCE,
        'lockedAtUtc': CUTOFF,
        'lastPrelockSelectionFingerprint': 'selection-fingerprint',
        'tags': [],
    }, {})
    result = staggered_due_winner()
    now = utc('2026-08-25T01:01:00+00:00')
    with loaded([payload(winner_row=result)]) as (handler,calls):
        assert handler._due_row(result, due) is True
        assert handler._due_row(
            result,
            coverage._display_card(due),
            display_card=True,
        ) is True
        assert handler._open_row(result, open_row, now) is True
        assert handler._open_row(
            result,
            coverage._display_card(open_row),
            now,
            display_card=True,
        ) is True
        assert handler._canonical_row(result, canonical) is True
        assert handler._canonical_row(
            result,
            coverage._display_card(canonical),
            display_card=True,
        ) is True
    assert calls == []

def test_exact_current_cutoff_due_is_explicitly_pending_and_preserves_defect():
    with loaded(
        [payload(winner_row=due_winner())],
        winner_reads=[due_winner()],
    ) as (handler,calls):
        response = invoke_at(handler, '2026-08-25T01:01:00+00:00', None)

    body = json.loads(response['body'])
    returned = body['game_winner_predictions'][0]
    evidence = returned['winnerLifecycleConvergence']
    assert response['statusCode'] == 200
    assert len(calls) == 1
    assert calls.winner_reads == [{
        'game_date': GAME_DATE,
        'store': False,
        'limit': 500,
    }]
    assert returned['winnerLifecycleOperationalDefect'] is True
    assert returned['operationalDefect'] is True
    assert body['winnerLifecycleHealth']['winnerLifecycleHealthy'] is False
    assert evidence['version'] == CONVERGENCE_VERSION
    assert evidence['evidenceScope'] == 'SUPPLEMENTAL_PERSISTED_READ'
    assert evidence['status'] == 'CONVERGENCE_PENDING_CURRENT_CUTOFF'
    assert evidence['converged'] is False
    assert evidence['convergencePending'] is True
    assert evidence['nonfatalForCurrentPull'] is True
    assert evidence['operationalDefectPreserved'] is True
    assert evidence['dueGameIdentities'] == ['provider:game-1']
    assert evidence['winnerWriterInvoked'] is False
    assert evidence['candidateWritten'] is False
    assert evidence['initialWinnerLifecycleExecuted'] is True

def test_immediate_healthy_persisted_read_merges_convergence_without_writing():
    initial = due_winner()
    with loaded(
        [payload(winner_row=initial)],
        winner_reads=[converged_winner()],
    ) as (handler,calls):
        response = invoke_at(handler, '2026-08-25T01:01:00+00:00', None)

    body = json.loads(response['body'])
    returned = body['game_winner_predictions'][0]
    evidence = returned['winnerLifecycleConvergence']
    assert returned['winnerLifecycleOperationalDefect'] is False
    assert returned['operationalDefect'] is False
    assert returned['preLockStorageDispositionComplete'] is True
    assert returned['stored'] is False
    assert returned['canonicalLockedStorageErrors'] == {}
    assert evidence['status'] == 'CONVERGED_BY_IMMEDIATE_PERSISTED_READ'
    assert evidence['converged'] is True
    assert evidence['convergencePending'] is False
    assert evidence['winnerWriterInvoked'] is False
    assert body['winnerLifecycleHealth']['winnerLifecycleHealthy'] is True
    assert len(calls) == 1
    assert len(calls.winner_reads) == 1

def test_grace_includes_exact_five_minute_boundary():
    with loaded(
        [payload(winner_row=due_winner())],
        winner_reads=[due_winner()],
    ) as (handler,calls):
        response = invoke_at(handler, '2026-08-25T01:05:00+00:00', None)
    body = json.loads(response['body'])
    assert response['statusCode'] == 200
    assert body['winnerLifecycleConvergence']['convergencePending'] is True
    assert len(calls.winner_reads) == 1

def test_read_crossing_five_minute_boundary_restores_original_hard_failure():
    with loaded(
        [payload(winner_row=due_winner())],
        winner_reads=[due_winner()],
    ) as (handler,calls):
        moments = iter((
            utc('2026-08-25T01:04:59+00:00'),
            utc('2026-08-25T01:05:00.000001+00:00'),
        ))
        handler._utc_now = lambda: next(moments)
        try:
            handler.lambda_handler(dict(SCHEDULED_EVENT), None)
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError('expected boundary-crossing pull failure')
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert len(calls.winner_reads) == 1

def test_regressing_wall_clock_after_read_fails_closed():
    with loaded(
        [payload(winner_row=due_winner())],
        winner_reads=[due_winner()],
    ) as (handler,calls):
        moments = iter((
            utc('2026-08-25T01:01:00+00:00'),
            utc('2026-08-25T01:00:30+00:00'),
        ))
        handler._utc_now = lambda: next(moments)
        try:
            handler.lambda_handler(dict(SCHEDULED_EVENT), None)
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError('expected regressing-clock pull failure')
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert len(calls.winner_reads) == 1

def test_repeated_due_after_five_minutes_hard_fails_without_read():
    for now in (
        '2026-08-25T01:05:00.000001+00:00',
        '2026-08-25T01:15:00+00:00',
    ):
        with loaded(
            [payload(winner_row=due_winner())],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler, now)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_same_slot_retry_never_receives_a_second_convergence_grace():
    same_slot_retry = payload(winner_row=due_winner())
    same_slot_retry['canonical_pull_history'][0].update({
        'retryReturnedExistingCanonicalPull': True,
        'sameSlotRetryAuthorityRebound': True,
    })
    with loaded(
        [same_slot_retry],
        winner_reads=[due_winner()],
    ) as (handler,calls):
        message = scheduled_failure(handler)
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert calls.winner_reads == []

def test_only_exact_natural_eventbridge_input_can_receive_grace():
    variants = (
        {'sport': 'mlb'},
        {**SCHEDULED_EVENT, 'force': True},
        {**SCHEDULED_EVENT, 'repair': True},
        {**SCHEDULED_EVENT, 'days_ahead': False},
    )
    for event in variants:
        with loaded(
            [payload(winner_row=due_winner())],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            handler._utc_now = lambda: utc('2026-08-25T01:01:00+00:00')
            try:
                handler.lambda_handler(event, None)
            except RuntimeError as exc:
                message = str(exc)
            else:
                raise AssertionError('expected non-natural pull failure')
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_unattested_persisted_reader_cannot_enter_convergence_path():
    with loaded(
        [payload(winner_row=due_winner())],
        winner_reads=[due_winner()],
    ) as (handler,calls):
        engine = handler.mlb_manual_pull.mlb_game_winner_engine
        engine._INQSI_MLB_PERSISTED_PRELOCK_PUBLIC_AUTHORITY_ENABLED = False
        message = scheduled_failure(handler)
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert calls.winner_reads == []

    write_claim = due_winner()
    write_claim['slateCoverage']['canonicalReadAuthorityWriteCount'] = 1
    with loaded(
        [payload(winner_row=due_winner())],
        winner_reads=[write_claim],
    ) as (handler,calls):
        message = scheduled_failure(handler)
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert len(calls.winner_reads) == 1

def test_independent_delegate_failure_cannot_be_masked_by_convergence():
    top_level_failures = []
    for field, value in (
        ('ok', False),
        ('live_pull_ok', False),
        ('fallback_used', True),
    ):
        failed = payload(winner_row=due_winner())
        failed[field] = value
        top_level_failures.append(failed)
    for failed in top_level_failures:
        with loaded(
            [failed],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

    failed = payload(winner_row=due_winner())
    with loaded(
        [failed],
        winner_reads=[due_winner()],
    ) as (handler,calls):
        def failed_delegate(event, context):
            calls.append(dict(event))
            return {'statusCode': 500, 'body': json.dumps(failed)}
        handler.mlb_manual_pull.lambda_handler = failed_delegate
        message = scheduled_failure(handler)
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert calls.winner_reads == []

def test_future_cutoff_and_stale_or_future_asof_hard_fail():
    cases = (
        ('2026-08-25T00:59:59+00:00', ASOF),
        ('2026-08-25T01:01:00+00:00', '2026-08-25T00:59:59+00:00'),
        ('2026-08-25T01:01:00+00:00', '2026-08-25T01:01:00.000001+00:00'),
    )
    for now, asof in cases:
        with loaded(
            [payload(winner_row=due_winner(), asof=asof)],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler, now)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_naive_timestamp_or_cutoff_mismatch_hard_fails():
    for field, value in (
        ('scheduledLockAtUtc', '2026-08-25T01:00:01+00:00'),
        ('scheduledLockAtUtc', '2026-08-25T01:00:00'),
        ('commenceTime', '2026-08-25T01:45:01+00:00'),
    ):
        defect = due_winner()
        for row_field in ('predictions', 'perGameStatus'):
            defect[row_field][0][field] = value
        with loaded(
            [payload(winner_row=defect)],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_raw_provider_alias_binds_to_prefixed_manifest_identity():
    provider_only = due_winner()
    for field in ('predictions', 'perGameStatus'):
        provider_only[field][0].pop('gameIdentity')
        provider_only[field][0].pop('gameId')
        assert provider_only[field][0]['providerEventId'] == 'game-1'
    source_alias = due_winner()
    for field in ('predictions', 'perGameStatus'):
        source_alias[field][0]['providerEventId'] = 'source-provider-alias'
    for aliased in (provider_only, source_alias):
        with loaded(
            [payload(winner_row=aliased)],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            response = invoke_at(handler, '2026-08-25T01:01:00+00:00', None)
        assert response['statusCode'] == 200
        assert len(calls.winner_reads) == 1

def test_manifest_member_provider_alias_collision_fails_closed():
    collision = staggered_due_winner()
    for field in ('predictions', 'perGameStatus'):
        collision[field][0]['providerEventId'] = 'game-2'
        assert collision[field][1]['providerEventId'] == 'game-2'
    with loaded(
        [payload(winner_row=collision)],
        winner_reads=[staggered_due_winner()],
    ) as (handler,calls):
        message = scheduled_failure(handler)
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert calls.winner_reads == []

def test_boolean_or_numeric_row_identities_fail_closed():
    for field, value in (
        ('gameId', True),
        ('providerEventId', 1),
    ):
        malformed = due_winner()
        for row_field in ('predictions', 'perGameStatus'):
            malformed[row_field][0][field] = value
        with loaded(
            [payload(winner_row=malformed)],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_missing_duplicate_or_conflicting_row_identity_hard_fails():
    defects = []
    missing = due_winner()
    missing['predictions'] = []
    defects.append(missing)
    duplicate = due_winner()
    duplicate['predictions'] = duplicate['predictions'] * 2
    duplicate['gameCount'] = 2
    duplicate['preLockStorageDispositionCount'] = 2
    duplicate['preLockStorageRowCount'] = 2
    defects.append(duplicate)
    conflicting = due_winner()
    conflicting['predictions'][0]['gameId'] = 'other-game'
    defects.append(conflicting)
    for defect in defects:
        with loaded(
            [payload(winner_row=defect)],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_pending_identity_without_exact_rows_hard_fails():
    defect = due_winner()
    defect['slatePredictionLock']['pendingCanonicalStatuses'] = {
        'provider:no-row': 'LOCK_DUE_CANONICAL_MISSING',
    }
    defect['slateCoverage']['pendingCanonicalStatuses'] = {
        'provider:no-row': 'LOCK_DUE_CANONICAL_MISSING',
    }
    with loaded(
        [payload(winner_row=defect)],
        winner_reads=[due_winner()],
    ) as (handler,calls):
        message = scheduled_failure(handler)
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert calls.winner_reads == []

def test_manifest_authority_token_mismatch_fails_closed_before_or_after_read():
    bad_history = payload(winner_row=due_winner())
    bad_history['canonical_pull_history'][0][
        'officialScheduleAuthorityFingerprint'
    ] = 'c' * 64
    bad_manifest = payload(winner_row=due_winner())
    bad_manifest['provider_schedule_manifests'][0]['sk'] = 'other-sk'
    extra_history = payload(winner_row=due_winner())
    extra_history['canonical_pull_history'].append({
        'game_date_et': '2026-08-23',
        'ok': True,
    })
    for bad_initial in (bad_history, bad_manifest, extra_history):
        with loaded(
            [bad_initial],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

    bad_read = converged_winner()
    bad_read['slatePredictionLock']['providerManifestSk'] = 'other-sk'
    with loaded(
        [payload(winner_row=due_winner())],
        winner_reads=[bad_read],
    ) as (handler,calls):
        message = scheduled_failure(handler)
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert len(calls.winner_reads) == 1

def test_boolean_authority_tokens_and_wrong_storage_authority_fail_closed():
    boolean_authority = due_winner()
    token_fields = (
        'providerManifestPk',
        'providerManifestSk',
        'providerManifestFingerprint',
        'officialScheduleAuthorityFingerprint',
    )
    for field in token_fields:
        boolean_authority['slatePredictionLock'][field] = True
    boolean_payload = payload(winner_row=boolean_authority)
    for field in ('pk', 'sk', 'fingerprint',
                  'officialScheduleAuthorityFingerprint'):
        boolean_payload['provider_schedule_manifests'][0][field] = True
    for field in token_fields:
        boolean_payload['canonical_pull_history'][0][field] = True

    wrong_storage = due_winner()
    wrong_storage['canonicalLockedStorageAuthority'] = 'plausible but wrong'
    for failed in (boolean_payload, payload(winner_row=wrong_storage)):
        with loaded(
            [failed],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

    persisted = due_winner()
    for field in token_fields:
        persisted['slatePredictionLock'][field] = True
    with loaded(
        [payload(winner_row=due_winner())],
        winner_reads=[persisted],
    ) as (handler,calls):
        message = scheduled_failure(handler)
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert len(calls.winner_reads) == 1

def test_tags_must_be_unique_string_lists_without_mixed_failure_states():
    invalid_initials = []
    dict_tags = due_winner()
    for field in ('predictions', 'perGameStatus'):
        dict_tags[field][0]['tags'] = {
            'LOCK_DUE_CANONICAL_MISSING': True,
            'PER_GAME_CANONICAL_LOCK_MISSING': True,
        }
    invalid_initials.append(dict_tags)

    duplicate_tags = due_winner()
    for field in ('predictions', 'perGameStatus'):
        duplicate_tags[field][0]['tags'].append(
            'LOCK_DUE_CANONICAL_MISSING'
        )
    invalid_initials.append(duplicate_tags)

    mixed_open = staggered_due_winner()
    for field in ('predictions', 'perGameStatus'):
        mixed_open[field][1]['tags'].append('MISSED_LOCK')
    invalid_initials.append(mixed_open)

    mixed_due = due_winner()
    for field in ('predictions', 'perGameStatus'):
        mixed_due[field][0]['tags'].append('MISSED_LOCK')
    invalid_initials.append(mixed_due)

    for failed in invalid_initials:
        with loaded(
            [payload(winner_row=failed)],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_non_integer_contract_counts_cannot_enter_convergence_grace():
    for field, value in (
        ('gameCount', '1'),
        ('preLockStorageDispositionCount', 1.0),
    ):
        malformed = due_winner()
        malformed[field] = value
        with loaded(
            [payload(winner_row=malformed)],
            winner_reads=[due_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_staggered_slate_due_plus_future_open_is_explicitly_pending():
    initial = staggered_due_winner()
    with loaded(
        [payload(winner_row=initial)],
        winner_reads=[staggered_due_winner()],
    ) as (handler,calls):
        response = invoke_at(handler, '2026-08-25T01:01:00+00:00', None)

    body = json.loads(response['body'])
    returned = body['game_winner_predictions'][0]
    evidence = returned['winnerLifecycleConvergence']
    assert response['statusCode'] == 200
    assert evidence['convergencePending'] is True
    assert evidence['dueGameIdentities'] == ['provider:game-1']
    assert returned['stored'] is True
    assert returned['storedCount'] == 1
    assert returned['slatePredictionLock']['pendingCanonicalStatuses'] == {
        'provider:game-1': 'LOCK_DUE_CANONICAL_MISSING',
        'provider:game-2': 'OPEN_PRE_LOCK',
    }
    assert len(calls.winner_reads) == 1

def test_staggered_slate_due_converges_while_future_game_remains_open():
    with loaded(
        [payload(winner_row=staggered_due_winner())],
        winner_reads=[staggered_converged_winner()],
    ) as (handler,calls):
        response = invoke_at(handler, '2026-08-25T01:01:00+00:00', None)

    body = json.loads(response['body'])
    returned = body['game_winner_predictions'][0]
    evidence = returned['winnerLifecycleConvergence']
    assert response['statusCode'] == 200
    assert evidence['converged'] is True
    assert evidence['convergencePending'] is False
    assert returned['winnerLifecycleOperationalDefect'] is False
    assert returned['canonicalPredictionComplete'] is False
    assert returned['stored'] is True
    assert returned['storedCount'] == 1
    assert returned['slatePredictionLock']['lockStatus'] == (
        'PARTIAL_PER_GAME_CANONICAL'
    )
    assert returned['slatePredictionLock']['pendingCanonicalStatuses'] == {
        'provider:game-2': 'OPEN_PRE_LOCK',
    }
    assert [row['lockStatus'] for row in returned['predictions']] == [
        'LOCKED_CANONICAL',
        'OPEN_PRE_LOCK',
    ]
    assert body['winnerLifecycleHealth']['winnerLifecycleHealthy'] is True
    assert len(calls.winner_reads) == 1

def test_non_due_staggered_row_becoming_due_or_missed_cannot_false_green():
    for status in ('LOCK_DUE_CANONICAL_MISSING', 'MISSED_LOCK'):
        persisted = staggered_converged_winner()
        replacement = lifecycle_row(
            status,
            game_id='game-2',
            cutoff=OPEN_CUTOFF,
            commence=OPEN_COMMENCE,
        )
        replacement_card = lifecycle_card(
            status,
            game_id='game-2',
            cutoff=OPEN_CUTOFF,
            commence=OPEN_COMMENCE,
        )
        persisted.update({
            'operationalDefect': True,
            'winnerLifecycleOperationalDefect': True,
            'operationalDefectScopes': ['WINNER_LIFECYCLE'],
        })
        persisted['predictions'][1] = replacement
        persisted['perGameStatus'][1] = replacement_card
        pending = {'provider:game-2': status}
        due_count = int(status == 'LOCK_DUE_CANONICAL_MISSING')
        missed_count = int(status == 'MISSED_LOCK')
        persisted['slatePredictionLock'].update({
            'lockStatus': status,
            'lockDueCanonicalMissingCount': due_count,
            'missedLockCount': missed_count,
            'pendingCanonicalStatuses': pending,
        })
        persisted['slateCoverage'].update({
            'lockDueCanonicalMissingCount': due_count,
            'missedLockCount': missed_count,
            'pendingCanonicalStatuses': copy.deepcopy(pending),
        })
        with loaded(
            [payload(winner_row=staggered_due_winner())],
            winner_reads=[persisted],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert len(calls.winner_reads) == 1

def test_staggered_open_timing_cannot_shift_across_persisted_read():
    for persisted in (staggered_due_winner(), staggered_converged_winner()):
        for field in ('predictions', 'perGameStatus'):
            row = persisted[field][1]
            row['scheduledLockAtUtc'] = '2026-08-25T01:45:00+00:00'
            row['commenceTime'] = '2026-08-25T02:30:00+00:00'
            row['perGameCanonicalLock']['lockAtUtc'] = (
                '2026-08-25T01:45:00+00:00'
            )
        with loaded(
            [payload(winner_row=staggered_due_winner())],
            winner_reads=[persisted],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert len(calls.winner_reads) == 1

def test_due_plus_storage_failure_never_attempts_convergence_read():
    mixed = due_winner()
    mixed.update({
        'canonicalLockedStorageCandidateCount': 1,
        'canonicalLockedStoredCount': 0,
        'canonicalLockedStorageComplete': False,
        'canonicalLockedStorageErrors': {
            'provider:game-1': ['injected canonical write failure'],
        },
    })
    with loaded(
        [payload(winner_row=mixed)],
        winner_reads=[converged_winner()],
    ) as (handler,calls):
        message = scheduled_failure(handler)
    assert f'canonical_locked_storage_incomplete:{GAME_DATE}' in message
    assert f'canonical_locked_storage_count_mismatch:{GAME_DATE}' in message
    assert f'canonical_locked_storage_errors:{GAME_DATE}' in message
    assert calls.winner_reads == []

def test_missed_lock_never_attempts_convergence_read():
    missed = due_winner()
    missed['slatePredictionLock'].update({
        'lockStatus': 'MISSED_LOCK',
        'lockDueCanonicalMissingCount': 0,
        'missedLockCount': 1,
        'pendingCanonicalStatuses': {'provider:game-1': 'MISSED_LOCK'},
    })
    missed['slateCoverage'].update({
        'lockDueCanonicalMissingCount': 0,
        'missedLockCount': 1,
        'pendingCanonicalStatuses': {'provider:game-1': 'MISSED_LOCK'},
    })
    mixed = staggered_due_winner()
    missed_row = lifecycle_row(
        'MISSED_LOCK',
        game_id='game-2',
        cutoff='2026-08-25T00:00:00+00:00',
        commence='2026-08-25T00:45:00+00:00',
    )
    missed_card = lifecycle_card(
        'MISSED_LOCK',
        game_id='game-2',
        cutoff='2026-08-25T00:00:00+00:00',
        commence='2026-08-25T00:45:00+00:00',
    )
    mixed.update({
        'stored': False,
        'storedCount': 0,
        'preLockStorageCandidateCount': 0,
        'preLockStoredCount': 0,
        'preLockStorageLifecycleSkippedCount': 2,
        'preLockStorageLifecycleSkippedStatuses': [
            'LOCK_DUE_CANONICAL_MISSING',
            'MISSED_LOCK',
        ],
    })
    mixed['predictions'][1] = missed_row
    mixed['perGameStatus'][1] = missed_card
    mixed_pending = {
        'provider:game-1': 'LOCK_DUE_CANONICAL_MISSING',
        'provider:game-2': 'MISSED_LOCK',
    }
    mixed['slatePredictionLock'].update({
        'lockStatus': 'MISSED_LOCK',
        'missedLockCount': 1,
        'pendingCanonicalStatuses': mixed_pending,
    })
    mixed['slateCoverage'].update({
        'missedLockCount': 1,
        'pendingCanonicalStatuses': copy.deepcopy(mixed_pending),
    })
    for result in (missed, mixed):
        with loaded(
            [payload(winner_row=result)],
            winner_reads=[converged_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_malformed_scope_terminal_or_query_defect_hard_fails():
    malformed = due_winner()
    malformed.pop('releasePlayabilityOperationalDefect')
    terminal = due_winner()
    terminal['invalidTerminalLifecycleRows'] = {
        'provider:game-1': ['terminal_outcome_fingerprint_mismatch'],
    }
    query = due_winner()
    query['slatePredictionLock']['canonicalReadOperational'] = False
    query['slatePredictionLock']['canonicalReadError'] = 'read failed'
    missing_evidence = due_winner()
    missing_evidence['slateCoverage'].pop('extraCurrentPredictionIdentities')
    for defect in (malformed, terminal, query, missing_evidence):
        with loaded(
            [payload(winner_row=defect)],
            winner_reads=[converged_winner()],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert calls.winner_reads == []

def test_malformed_or_failed_persisted_read_cannot_create_false_green():
    malformed = converged_winner()
    malformed['perGameStatus'] = []
    for read in (malformed, None, [], RuntimeError('read failed')):
        with loaded(
            [payload(winner_row=due_winner())],
            winner_reads=[read],
        ) as (handler,calls):
            message = scheduled_failure(handler)
        assert f'winner_prediction_failed:{GAME_DATE}' in message
        assert len(calls.winner_reads) == 1

def test_healthy_read_for_wrong_due_cutoff_cannot_create_false_green():
    wrong_cutoff = converged_winner()
    for field in ('predictions', 'perGameStatus'):
        row = wrong_cutoff[field][0]
        row['scheduledLockAtUtc'] = '2026-08-25T01:15:00+00:00'
        row['commenceTime'] = '2026-08-25T02:00:00+00:00'
        row['perGameCanonicalLock']['lockAtUtc'] = '2026-08-25T01:15:00+00:00'
    with loaded(
        [payload(winner_row=due_winner())],
        winner_reads=[wrong_cutoff],
    ) as (handler,calls):
        message = scheduled_failure(handler)
    assert f'winner_prediction_failed:{GAME_DATE}' in message
    assert len(calls.winner_reads) == 1

def test_manifest_binding_failure_retries_once_and_requires_valid_retry():
    with loaded([payload(bound=False),payload(bound=True,winner_row=winner(ok=True))]) as (handler,calls):
        response=handler.lambda_handler({'sport':'mlb'},None)
    assert response['statusCode']==200
    assert len(calls)==2
    assert calls[1].get('manifest_binding_retry') is True
    assert calls[1].get('force') is True

def test_canonical_manifest_defect_still_fails_after_bounded_retry():
    with loaded([payload(bound=False),payload(bound=False)]) as (handler,calls):
        try: handler.lambda_handler({'sport':'mlb'},None)
        except RuntimeError as exc: message=str(exc)
        else: raise AssertionError('expected canonical manifest binding failure')

    assert 'provider_schedule_manifest_authority_invalid:2026-08-24' in message
    assert 'provider_schedule_manifest_incomplete' in message
    assert len(calls)==2
