from __future__ import annotations


def ensure_opus_access(*, Store, **_kwargs):
    """Legacy compatibility shim; model selection is configuration-driven."""
    del Store
    return {
        'ok': False,
        'action': 'MODEL_CONFIGURATION_ONLY',
        'scope': 'mlb_auto_only',
        'reason': 'MODEL_ENABLEMENT_NOT_MANAGED_BY_RUNTIME',
        'account_mutation_attempted': False,
    }
