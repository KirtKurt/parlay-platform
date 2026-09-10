"""Existing workflow's 01:00 Eastern KS1 grading; capture mode is read-only."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from ks1.daily import PREFIX
from ks1.features import utc
from ks1.inventory import Reader, RESEARCH, encode
from ks1.sim_lifecycle import update
from ks1.simulation import IDENTITY, RECIPE, STATE_KEY
from ks1.sources import aws_clients


def optional(s3, bucket, key):
    try:
        result = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(result['Body'].read()), result['ETag']
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') not in ('NoSuchKey', '404'):
            raise
        return None, None


def locked_rows(s3, bucket, as_of):
    """Prove original storage before the existing T-10 boundary using versions.

    Never admit a historical reconstruction because its `as_of` looks old.
    Verify the surviving frozen row against its newest pre-cutoff S3 version.
    Missing evidence is reported, never upgraded into an official lock.
    """
    versions, deleted = defaultdict(list), set()
    for page in s3.get_paginator('list_object_versions').paginate(Bucket=bucket, Prefix=PREFIX):
        deleted.update(v['Key'] for v in page.get('DeleteMarkers', []) if v.get('IsLatest'))
        for v in page.get('Versions', []):
            if v['Key'].endswith('/predictions.parquet'):
                versions[v['Key']].append(v)
    admitted, excluded, sources = [], [], []
    for key, entries in sorted(versions.items()):
        if key in deleted:
            excluded.append({'key': key, 'reason': 'current_object_deleted'}); continue
        candidates, current = {}, None
        for v in sorted(entries, key=lambda v: v['LastModified']):
            result = s3.get_object(Bucket=bucket, Key=key, VersionId=v['VersionId'])
            body = result['Body'].read()
            source = {'key': key, 'bucket': bucket, 'version_id': v['VersionId'],
                      'stored_at': v['LastModified'].isoformat(), 'sha256': hashlib.sha256(body).hexdigest()}
            sources.append(source)
            rows = pq.ParquetFile(io.BytesIO(body)).read().to_pylist()
            if len({r['game_id'] for r in rows}) != len(rows):
                raise ValueError('duplicate persisted prediction IDs')
            if v.get('IsLatest'):
                current = {r['game_id']: r for r in rows}
            for row in rows:
                cutoff = utc(row['commence_time'])-timedelta(minutes=10)
                if (v['VersionId'] != 'null' and row['date'] == key.split('date=')[1].split('/')[0]
                        and utc(row['as_of']) <= utc(source['stored_at']) <= cutoff < utc(as_of)):
                    candidates[row['game_id']] = {'row': row, 'evidence': source}
        for pk, entry in candidates.items():
            # Nulls added by schema evolution do not alter original stored fields.
            now = (current or {}).get(pk, {})
            if not now or any(now.get(k) != v for k, v in entry['row'].items()):
                excluded.append({'game_id': pk, 'reason': 'frozen_row_changed_or_missing'}); continue
            admitted.append(entry)
        for pk, row in (current or {}).items():
            if pk not in candidates and utc(row['commence_time'])-timedelta(minutes=10) < utc(as_of):
                excluded.append({'game_id': pk, 'reason': 'no_original_pre_cutoff_version'})
    if len({e['row']['game_id'] for e in admitted}) != len(admitted):
        raise ValueError('same official game appears in multiple date partitions')
    return admitted, {'prediction_keys': len(versions), 'versions_read': len(sources),
                      'locked_rows': len(admitted), 'excluded': excluded, 'sources': sources}


def capture(s3, bucket, as_of):
    locked, inventory = locked_rows(s3, bucket, as_of)
    reader = Reader(s3, bucket)
    # Existing full boxes have final teamStats.batting.runs; compact summaries
    # do not. No provider request or archive download is needed here.
    prior = reader.pointer(reader.read(RESEARCH+'prior-games.json')['artifact'])
    finals = {}
    for game in prior.get('games', []):
        if not game.get('completedAtUtc'):
            continue
        try:
            scores = {s: game['teams'][s]['teamStats']['batting']['runs'] for s in ('home', 'away')}
            teams = {s: game['teams'][s]['team']['id'] for s in ('home', 'away')}
        except KeyError:
            continue
        finals[str(game['officialGamePk'])] = {
            'completed_at': game['completedAtUtc'],
            **{s+'_score': scores[s] for s in scores}, **{s+'_id': str(teams[s]) for s in teams}}
    state, etag = optional(s3, bucket, STATE_KEY)
    return {'system': 'KS1', 'as_of': as_of, 'bucket': bucket, 'locked': locked,
            'inventory': inventory, 'finals': finals, 'final_sources': reader.receipts,
            'state': state, 'state_etag': etag, 'aws_writes': 0, 'provider_calls': 0}


def allowed_pipeline():
    return (os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_REPOSITORY') == 'KirtKurt/parlay-platform'
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_EVENT_NAME') in ('schedule', 'workflow_dispatch')
            and '/.github/workflows/mlb-research-ingestion.yml@' in os.environ.get('GITHUB_WORKFLOW_REF', ''))


def publish_state(s3, capture, state):
    if not allowed_pipeline():
        raise ValueError('state writes require existing main research workflow')
    body = encode(state)
    condition = {'IfMatch': capture['state_etag']} if capture['state_etag'] else {'IfNoneMatch': '*'}
    result = s3.put_object(Bucket=capture['bucket'], Key=STATE_KEY, Body=body,
                           ContentType='application/json', **condition)
    args = {'VersionId': result['VersionId']} if result.get('VersionId') else {}
    stored = s3.get_object(Bucket=capture['bucket'], Key=STATE_KEY, **args)['Body'].read()
    if stored != body:
        raise ValueError('simulation state readback mismatch')


def at_nightly_hour(now):
    return now.astimezone(ZoneInfo('America/New_York')).hour == 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--capture-only', action='store_true')
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--nightly-gate', action='store_true')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.nightly_gate and not at_nightly_hour(now):
        print('KS1 nightly skipped: outside 01:00 Eastern hour'); return
    if args.publish and (args.inputs or args.capture_only):
        raise ValueError('offline/capture-only inputs cannot publish')
    args.output.mkdir(parents=True, exist_ok=True)
    if args.inputs:
        captured = json.loads(args.inputs.read_bytes())
    else:
        _, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
        captured = capture(s3, bucket, now.isoformat())
    (args.output/'capture.json').write_bytes(encode(captured))
    state = update(captured['state'], captured['locked'], captured['finals'], captured['as_of'])
    (args.output/'state.json').write_bytes(encode(state))
    report = {'as_of': captured['as_of'], 'inventory': captured['inventory'],
              'comparison': state['comparison'], 'graded_games': state['locked_graded_games'],
              'calibration_attempts': state['calibration_attempts'], 'refit_requests': state['refit_requests'],
              'published': False, 'comparison_status': 'verified_locked' if state['comparison']['game_ids'] else 'no_complete_verified_locked_cohort'}
    if args.publish:
        publish_state(s3, captured, state); report['published'] = True
    (args.output/'report.json').write_bytes(encode(report))
    print(json.dumps({k: v for k, v in report.items() if k != 'inventory'}, indent=2))


if __name__ == '__main__':
    main()
