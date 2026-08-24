from __future__ import annotations
import importlib.util
import json
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

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

def manifest(bound=True):
    return {
        'game_date_et':'2026-08-24','gameCount':1,'version':'v','fingerprint':'a'*64,
        'pk':'p','sk':'s','immutable':True,'fullProviderSchedule':True,
        'boundToCanonicalPull':bound,'ok':bound,
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

def payload(*, bound=True, winner_row=None):
    return {
        'ok':True,'count':1,'providerScheduleManifestComplete':bound,
        'provider_schedule_manifests':[manifest(bound)],
        'game_winner_predictions':[winner_row or winner()],
    }

@contextmanager
def loaded(responses):
    responses = list(responses)
    manual = ModuleType('mlb_manual_pull')
    calls=[]
    def call(event, context):
        calls.append(dict(event))
        value = responses.pop(0)
        return {'statusCode':200,'body':json.dumps(value)}
    manual.lambda_handler=call
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

def test_manifest_binding_failure_retries_once_and_requires_valid_retry():
    with loaded([payload(bound=False),payload(bound=True,winner_row=winner(ok=True))]) as (handler,calls):
        response=handler.lambda_handler({'sport':'mlb'},None)
    assert response['statusCode']==200
    assert len(calls)==2
    assert calls[1].get('manifest_binding_retry') is True
    assert calls[1].get('force') is True
