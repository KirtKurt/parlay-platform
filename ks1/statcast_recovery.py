"""Bounded trusted-main recovery of invalid retained daily Statcast objects.

Never edit raw observations or relax admission. New provider responses occupy
content-addressed objects and are revalidated against the current official boxes.
"""
from datetime import date, datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
import time

from ks1.inventory import RESEARCH, Reader, encode
from ks1.statcast_history import (official_physical_pitch_counts,
                                  official_pitch_counts,
                                  physical_validation_reason,
                                  validation_reason)

PREFIX = 'sources/statcast-recovery-v1/'
MAX_DATES = 64


def recovery_pointer_key(value):
    return RESEARCH + PREFIX + value + '/latest.json'


def read_recovery(s3, bucket, value):
    reader = Reader(s3, bucket)
    state = reader.read(recovery_pointer_key(value))
    pointer = state.get('verified_artifact')
    if not pointer:
        return None, []
    expected_prefix = PREFIX + value + '/objects/'
    if (set(pointer) != {'name', 'versionId', 'sha256'}
            or pointer['name'] != expected_prefix + pointer['sha256'] + '.json'):
        raise ValueError('recovery pointer not bound to date and content')
    payload = reader.pointer(pointer)
    return payload, reader.receipts


def recover(bundle, s3, bucket, initial_report, *, fetch=None, max_dates=MAX_DATES,
            seconds=1200):
    from ks1.train import artifact_write_authorized
    if (not artifact_write_authorized()
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_EVENT_NAME') not in ('push', 'schedule', 'workflow_dispatch')):
        raise ValueError('Statcast recovery requires the trusted main training workflow')
    if not 0 <= max_dates <= MAX_DATES or not 0 < seconds <= 1200:
        raise ValueError('invalid recovery budget')
    # These are the existing provider/store adapters, not a new credential path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mlb_research'))
    from mlb_research_store_v1 import Store
    from mlb_research_sources_v1 import statcast
    store = Store(bucket, s3)
    fetch = fetch or statcast
    deadline = time.monotonic() + seconds
    today = datetime.now(timezone.utc).date().isoformat()
    expected, invalid = official_pitch_counts(bundle['full'])
    physical_expected, physical_batters, physical_invalid = official_physical_pitch_counts(
        bundle['full'])
    scheduled = {}
    from ks1.features import day
    for game in bundle['schedule']:
        if (game.get('gameType') in ('R', 'F', 'D', 'L', 'W')
                and game.get('status', {}).get('abstractGameState') == 'Final'
                and game.get('status', {}).get('detailedState') not in ('Postponed', 'Cancelled')
                and not game.get('resumedFrom')):
            scheduled.setdefault(day(game['gameDate']).isoformat(), set()).add(int(game['gamePk']))
    report = {'provider_requests': 0, 'recovered_dates': [], 'attempts': [],
              'deferred_dates': [], 'prediction_writes': 0, 'max_dates': max_dates}
    candidates = []
    for error in sorted(initial_report['errors'], key=lambda item: item['date'], reverse=True):
        value = error['date']
        if error['reason'] == 'unfinished_scheduled_game':
            continue
        games = scheduled.get(value, set())
        if not games or {str(pk) for pk in games} & invalid:
            report['deferred_dates'].append({'date': value, 'reason': 'official_evidence_incomplete'})
            continue
        name = PREFIX + value + '/latest.json'
        # Storage denial/corruption is a hard error, not permission to replace it.
        state = store.get(name) or {}
        game_set_hash = hashlib.sha256(encode(sorted(games))).hexdigest()
        if state.get('attempt_date') == today and state.get('game_set_sha256') == game_set_hash:
            report['deferred_dates'].append({'date': value, 'reason': 'already_attempted_today'})
            continue
        previous = state.get('verified_artifact') if state.get('game_set_sha256') == game_set_hash else None
        candidates.append((state.get('attempt_date', ''), -date.fromisoformat(value).toordinal(),
                           value, games, name, game_set_hash, previous))
    # Never-attempted dates first, recent first within that group. This advances
    # through older gaps across days as well as multiple runs on the same day.
    for _, _, value, games, name, game_set_hash, previous in sorted(candidates):
        if report['provider_requests'] >= max_dates or time.monotonic() >= deadline:
            report['deferred_dates'].append({'date': value, 'reason': 'recovery_budget'})
            continue
        report['provider_requests'] += 1
        # A transient object read failure is not evidence that a retained
        # version disappeared. Keep it reachable after a failed provider retry;
        # load_training_statcast still revalidates it before any admission.
        pointer = previous
        stop_provider = False
        try:
            payload = fetch(value)
            # The provider also returns spring/exhibition games. Select only the
            # independent official game set, exactly as the existing collector.
            payload = {**payload, 'rows': [row for row in payload['rows']
                       if str(row.get('game_pk')) in {str(pk) for pk in games}]}
            reason = physical_validation_reason(
                payload, value, games, physical_expected, physical_batters,
                physical_invalid)
            if reason is None:
                reason = validation_reason(payload, value, games, expected, invalid)
        except Exception as exc:
            reason = 'provider_error:' + type(exc).__name__
            status = getattr(exc, 'code', None)
            if status in (401, 403, 429):
                reason += ':' + str(status)
                stop_provider = True
        if reason is None:
            content_hash = hashlib.sha256(encode(payload)).hexdigest()
            object_name = PREFIX + value + '/objects/' + content_hash + '.json'
            # once() is conditional and Store.put verifies the exact version.
            store.once(object_name, payload)
            reader = Reader(s3, bucket)
            retained = reader.read(RESEARCH + object_name, sha=content_hash)
            receipt = reader.receipts[-1]
            if (receipt.get('versionId') in (None, '', 'null')
                    or physical_validation_reason(
                        retained, value, games, physical_expected, physical_batters,
                        physical_invalid) is not None
                    or validation_reason(retained, value, games, expected, invalid) is not None):
                raise ValueError('recovered source readback failed')
            pointer = {'name': object_name, 'versionId': receipt['versionId'], 'sha256': content_hash}
            report['recovered_dates'].append(value)
        store.latest(name, {'attempt_date': today, 'game_set_sha256': game_set_hash,
                            'reason': reason, 'verified_artifact': pointer})
        report['attempts'].append({'date': value, 'reason': reason, 'verified_artifact': pointer})
        if stop_provider:
            report['stopped_reason'] = reason
            break
    return report
