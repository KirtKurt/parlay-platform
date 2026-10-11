from __future__ import annotations

from .runtime_hardening import TRAINING_LEASE_SECONDS, acquire_named_lease, release_named_lease
from .training_guard import begin, record
from .training_persist import finish
from .training_search import evaluate


def run(*, Store, base, discover_challenger, promote_challenger) -> dict:
    lease_store = Store()
    token = None
    state_table = getattr(lease_store, 'state', None)
    lease_capable = (
        callable(getattr(state_table, 'update_item', None))
        and callable(getattr(state_table, 'delete_item', None))
    )
    if lease_capable:
        token = acquire_named_lease(lease_store, 'training', TRAINING_LEASE_SECONDS)
        if token is None:
            lease_store.put_state('controller', {
                'last_training_check_at': base._iso(),
                'last_training_check_result': 'TRAINING_ALREADY_RUNNING',
            })
            return {
                'ok': True,
                'trained': False,
                'reason': 'TRAINING_ALREADY_RUNNING',
            }
    try:
        context, response = begin(Store, base)
        if response is not None:
            return response
        evaluation, response = evaluate(context, base, discover_challenger, promote_challenger)
        if response is not None:
            record(
                context['store'], base, context['count'], context['deploy_sha'],
                response['reason'], search_count=response.get('search_count'),
            )
            return response
        return finish(context, evaluation, base)
    finally:
        if token is not None:
            release_named_lease(lease_store, 'training', token)
