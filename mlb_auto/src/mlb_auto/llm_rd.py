from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import replace
from datetime import datetime, timezone

import boto3

from .mantle_client import invoke_anthropic

STATE_KEY = 'llm_rd'
ACCOUNT_MODEL_POLICY = 'ACCOUNT_SAFE_AMAZON_GEO_FALLBACK_V1'
DEFAULT_MODELS = (
    'us.amazon.nova-premier-v1:0',
    'us.amazon.nova-pro-v1:0',
    'us.amazon.nova-2-lite-v1:0',
    'us.amazon.nova-lite-v1:0',
    'us.amazon.nova-micro-v1:0',
)
_PROFILE_ALIASES = {
    'amazon.nova-premier-v1:0': 'us.amazon.nova-premier-v1:0',
    'amazon.nova-pro-v1:0': 'us.amazon.nova-pro-v1:0',
    'amazon.nova-2-lite-v1:0': 'us.amazon.nova-2-lite-v1:0',
    'amazon.nova-lite-v1:0': 'us.amazon.nova-lite-v1:0',
    'amazon.nova-micro-v1:0': 'us.amazon.nova-micro-v1:0',
}
_ALLOW_MARKETPLACE_MODELS = str(
    os.getenv('MLB_AUTO_LLM_ALLOW_MARKETPLACE_MODELS', 'false')
).strip().lower() in {'1', 'true', 'yes', 'on'}
RAW_MODEL_IDS = tuple(
    x.strip() for x in os.getenv(
        'MLB_AUTO_LLM_MODEL_IDS', ','.join(DEFAULT_MODELS),
    ).split(',') if x.strip()
)


def _account_safe_models(values):
    selected = []
    excluded = []
    for raw in values:
        model_id = str(raw).strip()
        if not model_id:
            continue
        if 'anthropic.claude-' in model_id and not _ALLOW_MARKETPLACE_MODELS:
            excluded.append(model_id)
            continue
        model_id = _PROFILE_ALIASES.get(model_id, model_id)
        if model_id not in selected:
            selected.append(model_id)
    if not selected:
        selected.extend(DEFAULT_MODELS)
    return tuple(selected), tuple(excluded)


MODEL_IDS, ACCOUNT_EXCLUDED_MODEL_IDS = _account_safe_models(RAW_MODEL_IDS)
MIN_EXAMPLES = int(os.getenv('MLB_AUTO_LLM_MIN_EXAMPLES', '75'))
INTERVAL_SECONDS = int(os.getenv('MLB_AUTO_LLM_INTERVAL_SECONDS', '14400'))
MAX_FEATURES = int(os.getenv('MLB_AUTO_LLM_MAX_FEATURES', '8'))
MAX_OUTPUT_TOKENS = int(os.getenv('MLB_AUTO_LLM_MAX_OUTPUT_TOKENS', '900'))
OPS = {'difference','sum','product','ratio','abs_difference','log1p_abs','sqrt_product','tanh_product'}
FORBIDDEN = ('label','winner','result','score','settled','completed','postgame','final_','outcome','actual_')
SYSTEM_TEXT = (
    'MLB Auto R&D only. Return compact JSON only. Use only supplied point-in-time '
    'pregame feature names and allowed numeric operations. Never use outcomes, scores, '
    'postgame data, external actions, executable code, or changes to validation rules.'
)


class ModelProviderUnavailableError(RuntimeError):
    def __init__(self, errors):
        self.errors = tuple(str(error) for error in errors)
        super().__init__('MODEL_PROVIDER_UNAVAILABLE|' + '|'.join(self.errors))


def _iso():
    return datetime.now(timezone.utc).isoformat()


def _num(value):
    try:
        x = float(value)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def _safe_names(rows):
    names = set()
    for row in rows:
        for key, value in row.items():
            name = str(key)
            if any(token in name.lower() for token in FORBIDDEN):
                continue
            if _num(value) is not None:
                names.add(name)
    return sorted(names)


def _transform(op, a, b):
    a = float(a); b = float(b)
    if op == 'difference': return a - b
    if op == 'sum': return a + b
    if op == 'product': return a * b
    if op == 'ratio': return a / b if abs(b) > 1e-9 else 0.0
    if op == 'abs_difference': return abs(a - b)
    if op == 'log1p_abs': return math.log1p(abs(a - b))
    if op == 'sqrt_product': return math.sqrt(abs(a * b))
    if op == 'tanh_product': return math.tanh(a * b)
    raise ValueError('UNKNOWN_RD_OP')


def validate_program(program, allowed_names):
    features = list((program or {}).get('features') or [])
    if not features or len(features) > MAX_FEATURES:
        raise ValueError('INVALID_RD_FEATURE_COUNT')
    allowed = set(allowed_names); clean = []; seen = set()
    for item in features:
        name = str((item or {}).get('name') or '')
        op = str((item or {}).get('op') or '')
        left = str((item or {}).get('left') or '')
        right = str((item or {}).get('right') or '')
        if not name.startswith('rd_') or not name.replace('_','').isalnum() or name in seen:
            raise ValueError('INVALID_RD_FEATURE_NAME')
        if op not in OPS or left not in allowed or right not in allowed:
            raise ValueError('INVALID_RD_FEATURE_SPEC')
        clean.append({
            'name': name[:64], 'op': op, 'left': left, 'right': right,
            'rationale': str((item or {}).get('rationale') or '')[:500],
        })
        seen.add(name)
    return {
        'hypothesis': str((program or {}).get('hypothesis') or '')[:1500],
        'features': clean,
        'architecture_notes': str((program or {}).get('architecture_notes') or '')[:1500],
        'safety_contract': 'FIXED_NUMERIC_TRANSFORM_LIBRARY_V1',
    }


def apply_program(row, program):
    out = dict(row)
    for item in (program or {}).get('features') or []:
        a = _num(out.get(item.get('left'))) or 0.0
        b = _num(out.get(item.get('right'))) or 0.0
        try:
            value = _transform(str(item.get('op')), a, b)
        except Exception:
            value = 0.0
        out[str(item.get('name'))] = max(-1e6, min(1e6, float(value))) if math.isfinite(float(value)) else 0.0
    return out


def _extract(text):
    raw = str(text or '').strip()
    if raw.startswith('```'):
        raw = raw.strip('`')
        if raw.startswith('json'):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find('{')
        end = raw.rfind('}')
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def _retryable_provider_unavailable(exc):
    text = f'{type(exc).__name__}:{exc}'.lower()
    quota_or_capacity = any(marker in text for marker in (
        'throttlingexception',
        'too many tokens per day',
        'quota exceeded',
        'rate exceeded',
        'too many requests',
        'http_429',
        'serviceunavailableexception',
        'modelnotreadyexception',
        'temporarily unavailable',
        'http_503',
    ))
    account_access = (
        'accessdeniedexception' in text
        and any(marker in text for marker in (
            'model access is denied',
            'aws marketplace',
            'aws-marketplace',
            'subscription',
            'first time use',
        ))
    )
    return quota_or_capacity or account_access


def _invoke(prompt, client=None):
    bedrock = client or boto3.client('bedrock-runtime')
    errors = []
    retryable_unavailable = []
    system = [{'text': SYSTEM_TEXT}]
    messages = [{'role':'user','content':[{'text':prompt}]}]
    for model_id in MODEL_IDS:
        try:
            if 'anthropic.claude-' in str(model_id):
                proposal, usage = invoke_anthropic(
                    str(model_id),
                    prompt,
                    max_tokens=max(512, min(8000, MAX_OUTPUT_TOKENS)),
                    system_prompt=SYSTEM_TEXT,
                )
                return proposal, usage, model_id, errors
            response = bedrock.converse(
                modelId=model_id,
                system=system,
                messages=messages,
                inferenceConfig={
                    'maxTokens': max(300, min(1500, MAX_OUTPUT_TOKENS)),
                },
            )
            parts = (((response.get('output') or {}).get('message') or {}).get('content') or [])
            text = ''.join(str(x.get('text') or '') for x in parts if isinstance(x, dict))
            usage = dict(response.get('usage') or {})
            usage['endpoint_family'] = 'bedrock-runtime-converse'
            usage['runtime_model_id'] = model_id
            usage['account_model_policy'] = ACCOUNT_MODEL_POLICY
            return _extract(text), usage, model_id, errors
        except Exception as exc:
            errors.append(f'{model_id}:{type(exc).__name__}:{str(exc)[:800]}')
            retryable_unavailable.append(_retryable_provider_unavailable(exc))
    if errors and all(retryable_unavailable):
        raise ModelProviderUnavailableError(errors)
    raise RuntimeError('ALL_BEDROCK_ENDPOINTS_FAILED|' + '|'.join(errors))


def _state(store):
    return dict(store.get_state(STATE_KEY) or {})


def active_program(store):
    return dict(_state(store).get('active_program') or {}) or None


def _due(current):
    try:
        last = datetime.fromisoformat(str(current.get('last_run_at')).replace('Z','+00:00'))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() >= INTERVAL_SECONDS
    except Exception:
        return True


def run_research(*, Store, force=False, bedrock_client=None):
    store = Store(); current = _state(store)
    if not force and not _due(current):
        return {
            'ok':True,'action':'LLM_RD_NOT_DUE','generated':False,
            'reason':'NOT_DUE','degraded':bool(current.get('degraded')),
        }
    examples = store.query_training_examples(limit=5000)
    examples.sort(key=lambda x:(str(x.get('commence_time') or ''),str(x.get('SK') or '')))
    count = len(examples); now = _iso()
    common = {
        'training_example_count':count,
        'configured_model_ids':list(MODEL_IDS),
        'raw_configured_model_ids':list(RAW_MODEL_IDS),
        'account_excluded_model_ids':list(ACCOUNT_EXCLUDED_MODEL_IDS),
        'account_model_policy':ACCOUNT_MODEL_POLICY,
        'max_output_tokens':MAX_OUTPUT_TOKENS,
    }
    if count < MIN_EXAMPLES:
        store.put_state(STATE_KEY, {
            **common,
            'last_run_at':now, 'last_run_ok':True,
            'last_invocation_ok':None, 'provider_available':None,
            'degraded':False, 'retryable':False,
            'last_result':'INSUFFICIENT_EXAMPLES', 'last_error':'',
            'model_fallback_errors':[],
        })
        return {
            'ok':True,'action':'LLM_RD','generated':False,
            'reason':'INSUFFICIENT_EXAMPLES','count':count,'minimum':MIN_EXAMPLES,
            'degraded':False,
        }
    reserve = max(50, int(os.getenv('MLB_AUTO_MIN_VALIDATION_EXAMPLES','50')))
    development = examples[:-reserve]
    rows = [dict(x.get('features') or {}) for x in development]
    names = _safe_names(rows)
    prompt = json.dumps({
        'objective':'Invent useful nonlinear MLB pregame interactions for game-winner prediction.',
        'allowed_feature_names':names,
        'allowed_ops':sorted(OPS),
        'development_rows':len(development),
        'withheld_audit_rows':reserve,
        'return':{
            'hypothesis':'short string',
            'features':[{'name':'rd_name','op':'allowed op','left':'allowed name','right':'allowed name','rationale':'short string'}],
            'architecture_notes':'short string',
        },
        'rules':['1-8 features','names start rd_','MLB Auto only','JSON only'],
    }, separators=(',', ':'), sort_keys=True)
    try:
        proposal, usage, model_id, prior_errors = _invoke(prompt, bedrock_client)
        clean = validate_program(proposal, names)
        candidate_id = 'LLM_RD_' + hashlib.sha256(json.dumps(clean,sort_keys=True).encode()).hexdigest()[:16]
        clean.update({'candidate_id':candidate_id,'created_at':now})
        store.put_state(STATE_KEY, {
            **common,
            'last_run_at':now, 'last_run_ok':True,
            'last_invocation_ok':True, 'provider_available':True,
            'degraded':False, 'retryable':False,
            'last_result':'CANDIDATE_GENERATED',
            'last_error':'', 'llm_model_id':model_id, 'candidate_id':candidate_id,
            'candidate_status':'DEVELOPMENT_CANDIDATE', 'candidate_program':clean,
            'research_development_count':len(development),
            'untouched_audit_reserve_count':reserve, 'llm_usage':usage,
            'model_fallback_errors':prior_errors,
        })
        store.archive_json(f'mlb_auto/llm-rd/{candidate_id}.json', {
            'candidate':clean, 'development_rows':len(development),
            'untouched_audit_reserve_count':reserve, 'model_id':model_id,
            'usage':usage, 'fallback_errors':prior_errors,
            'account_model_policy':ACCOUNT_MODEL_POLICY,
        })
        return {
            'ok':True,'action':'LLM_RD','generated':True,'candidate_id':candidate_id,
            'feature_count':len(clean['features']),'llm_model_id':model_id,
            'endpoint_family':usage.get('endpoint_family'),
            'development_rows':len(development),'untouched_audit_reserve_count':reserve,
            'fallbacks_attempted':len(prior_errors),
            'provider_available':True,'provider_invocation_ok':True,'degraded':False,
        }
    except ModelProviderUnavailableError as exc:
        error = f'{type(exc).__name__}:{str(exc)[:1800]}'
        store.put_state(STATE_KEY, {
            **common,
            'last_run_at':now, 'last_run_ok':True,
            'last_invocation_ok':False, 'provider_available':False,
            'degraded':True, 'retryable':True,
            'last_result':'MODEL_PROVIDER_UNAVAILABLE',
            'last_error':error, 'model_fallback_errors':list(exc.errors),
        })
        return {
            'ok':True,'action':'LLM_RD','generated':False,
            'reason':'MODEL_PROVIDER_UNAVAILABLE','degraded':True,
            'provider_available':False,'provider_invocation_ok':False,
            'retryable':True,'fallbacks_attempted':len(exc.errors),
            'error':error,
        }
    except Exception as exc:
        error = f'{type(exc).__name__}:{str(exc)[:1800]}'
        store.put_state(STATE_KEY, {
            **common,
            'last_run_at':now,'last_run_ok':False,
            'last_invocation_ok':False,'provider_available':None,
            'degraded':False,'retryable':False,
            'last_result':'LLM_RD_FAILED',
            'last_error':error,
        })
        return {
            'ok':False,'action':'LLM_RD','generated':False,
            'reason':'LLM_RD_FAILED','degraded':False,'error':error,
        }


def research_discoverer(*, Store, discover_challenger):
    def discover(rows, labels, **kwargs):
        current = _state(Store())
        active = dict(current.get('active_program') or {}) or None
        candidate = dict(current.get('candidate_program') or {}) or None
        transformed = [apply_program(apply_program(row, active), candidate) for row in rows]
        result = discover_challenger(transformed, labels, **kwargs)
        if candidate:
            metadata = dict(result.model.metadata or {})
            metadata.update({
                'llm_rd_candidate_id':candidate.get('candidate_id'),
                'llm_rd_feature_names':[x.get('name') for x in candidate.get('features') or []],
            })
            result = replace(result, model=replace(result.model, metadata=metadata))
        return result
    return discover


def record_training_result(*, Store, result):
    store = Store(); current = _state(store)
    candidate = dict(current.get('candidate_program') or {}) or None
    if not candidate or not result.get('trained'):
        return
    changes = {
        'last_candidate_gate':dict(result.get('gate') or {}),
        'last_candidate_model_id':result.get('model_id'),
        'candidate_status':'PROMOTED_WITH_CHAMPION' if result.get('promoted') else 'AUDIT_NOT_PROMOTED',
    }
    if result.get('promoted'):
        changes.update({
            'active_program':candidate,'active_program_id':candidate.get('candidate_id'),
            'active_program_promoted_at':_iso(),'candidate_program':{},
        })
    store.put_state(STATE_KEY, changes)


def status_payload(*, Store):
    current = _state(Store())
    usage = dict(current.get('llm_usage') or {})
    return {
        'enabled':True,'mode':'BEDROCK_LLM_AUTONOMOUS_RD',
        'model_id':current.get('llm_model_id'),
        'endpoint_family':usage.get('endpoint_family'),
        'configured_model_ids':current.get('configured_model_ids') or list(MODEL_IDS),
        'raw_configured_model_ids':current.get('raw_configured_model_ids') or list(RAW_MODEL_IDS),
        'account_excluded_model_ids':current.get('account_excluded_model_ids') or list(ACCOUNT_EXCLUDED_MODEL_IDS),
        'account_model_policy':current.get('account_model_policy') or ACCOUNT_MODEL_POLICY,
        'last_run_at':current.get('last_run_at'),'last_run_ok':current.get('last_run_ok'),
        'last_invocation_ok':current.get('last_invocation_ok'),
        'provider_available':current.get('provider_available'),
        'degraded':bool(current.get('degraded')),
        'retryable':bool(current.get('retryable')),
        'last_result':current.get('last_result'),'last_error':current.get('last_error'),
        'candidate_id':current.get('candidate_id'),'candidate_status':current.get('candidate_status'),
        'active_program_id':current.get('active_program_id'),
        'candidate_feature_count':len((current.get('candidate_program') or {}).get('features') or []),
        'active_feature_count':len((current.get('active_program') or {}).get('features') or []),
        'untouched_audit_reserve_count':current.get('untouched_audit_reserve_count'),
        'model_fallback_errors':current.get('model_fallback_errors') or [],
        'max_output_tokens':current.get('max_output_tokens') or MAX_OUTPUT_TOKENS,
        'generated_executable_code':False,
        'promotion_requires_existing_model_audit_gate':True,
        'scope':'mlb_auto_only',
    }
