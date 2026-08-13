from __future__ import annotations

from .training_guard import begin, record
from .training_persist import finish
from .training_search import evaluate


def run(*, Store, base, discover_challenger, promote_challenger) -> dict:
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
