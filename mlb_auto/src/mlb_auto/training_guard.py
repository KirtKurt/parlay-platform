from __future__ import annotations

import os
from datetime import datetime, timezone


def _dt(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _authority_minutes(example) -> int | None:
    try:
        if example.get('lock_minutes') is not None:
            return int(example['lock_minutes'])
        start = _dt(example.get('commence_time'))
        cutoff = _dt(example.get('lock_cutoff_at'))
        return int(round((start - cutoff).total_seconds() / 60.0))
    except Exception:
        return None


def record(store, base, count: int, deploy_sha: str, result: str, **extra) -> None:
    store.put_state('controller', {
        'last_training_attempt_at': base._iso(),
        'last_training_attempt_count': int(count),
        'last_training_attempt_git_sha': str(deploy_sha),
        'last_training_attempt_result': str(result),
        **extra,
    })


def begin(Store, base):
    store = Store()
    all_examples = store.query_training_examples(limit=5000)
    examples = [row for row in all_examples if _authority_minutes(row) == base.LOCK_MINUTES]
    examples.sort(key=lambda x: (str(x.get('commence_time') or ''), str(x.get('SK') or '')))
    count = len(examples)
    incompatible_count = len(all_examples) - count
    deploy_sha = os.getenv('MLB_AUTO_DEPLOY_GIT_SHA', 'unknown')
    if count < base.MIN_TRAIN:
        store.put_state('controller', {
            'last_training_check_at': base._iso(),
            'last_training_check_count': count,
            'last_training_incompatible_lock_horizon_count': incompatible_count,
            'training_lock_minutes': base.LOCK_MINUTES,
            'last_training_check_result': 'INSUFFICIENT_EXAMPLES',
        })
        return None, {
            'ok': True, 'trained': False, 'reason': 'INSUFFICIENT_EXAMPLES',
            'count': count, 'minimum': base.MIN_TRAIN,
            'incompatible_lock_horizon_count': incompatible_count,
            'training_lock_minutes': base.LOCK_MINUTES,
        }

    state = store.get_state('controller')
    previous_count = max(
        int(state.get('last_training_attempt_count') or 0),
        int(state.get('last_training_count') or 0),
    )
    previous_sha = str(state.get('last_training_attempt_git_sha') or '')
    new_examples = max(0, count - previous_count)
    if previous_count >= base.MIN_TRAIN and previous_sha == deploy_sha and new_examples < base.MIN_NEW:
        store.put_state('controller', {
            'last_training_check_at': base._iso(),
            'last_training_check_count': count,
            'last_training_check_result': 'NO_NEW_EVIDENCE',
            'last_training_new_examples': new_examples,
        })
        return None, {
            'ok': True, 'trained': False, 'reason': 'NO_NEW_EVIDENCE',
            'count': count, 'new_examples': new_examples,
            'minimum_new_examples': base.MIN_NEW,
        }

    rows = [dict(x.get('features') or {}) for x in examples]
    labels = [int(x.get('label_home_win')) for x in examples]
    if len(set(labels)) < 2:
        record(store, base, count, deploy_sha, 'INSUFFICIENT_LABEL_DIVERSITY')
        return None, {
            'ok': True, 'trained': False,
            'reason': 'INSUFFICIENT_LABEL_DIVERSITY', 'count': count,
        }
    return {
        'store': store, 'rows': rows, 'labels': labels, 'count': count,
        'deploy_sha': deploy_sha, 'new_examples': new_examples,
        'incompatible_lock_horizon_count': incompatible_count,
    }, None
