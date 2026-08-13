from __future__ import annotations


def finish(context, evaluation, base) -> dict:
    store = context['store']
    artifact = evaluation['artifact']
    model_id = evaluation['model_id']
    gate = evaluation['gate']
    store.put_model(f'CHALLENGER#{model_id}', artifact)
    promoted = bool(gate.get('promote'))
    if promoted:
        store.put_model('CHAMPION', artifact)
    trained_at = base._iso()
    store.put_state('controller', {
        'last_training_at': trained_at,
        'last_training_count': context['count'],
        'last_training_attempt_at': trained_at,
        'last_training_attempt_count': context['count'],
        'last_training_attempt_git_sha': context['deploy_sha'],
        'last_training_attempt_result': 'TRAINED',
        'last_training_new_examples': context['new_examples'],
        'champion_model_id': model_id if promoted else evaluation['incumbent_item'].get('model_id'),
        'last_training_gate': gate,
        'last_search_manifest': evaluation['manifest'],
    })
    return {
        'ok': True,
        'trained': True,
        'model_id': model_id,
        'promoted': promoted,
        'gate': gate,
        'examples': context['count'],
        'search_manifest': evaluation['manifest'],
    }
