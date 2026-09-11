"""Read-only calibration inputs from existing frozen predictions and finals.

No lock, grade ledger, audit record, provider endpoint, or AWS state is written.
"""
from collections import defaultdict
from datetime import timedelta
import hashlib
import io
import json
import re

import numpy as np
import pyarrow.parquet as pq

from ks1.features import utc
from ks1.inventory import encode
from ks1.platt import identity, raw_model_version, temperature_identity

PREFIX = 'mlb/ks1/predictions-v1/'


def read_locked_predictions(s3, bucket, as_of):
    versions, deleted = defaultdict(list), set()
    for page in s3.get_paginator('list_object_versions').paginate(Bucket=bucket, Prefix=PREFIX):
        deleted.update(v['Key'] for v in page.get('DeleteMarkers', []) if v.get('IsLatest'))
        for v in page.get('Versions', []):
            if re.fullmatch(re.escape(PREFIX)+r'date=\d{4}-\d{2}-\d{2}/predictions.parquet', v['Key']):
                versions[v['Key']].append(v)
    admitted, excluded, sources = [], [], []
    for key, entries in sorted(versions.items()):
        if key in deleted:
            excluded.append({'key': key, 'reason': 'current_object_deleted'}); continue
        candidates, current = {}, None
        for v in sorted(entries, key=lambda v: v['LastModified']):
            body = s3.get_object(Bucket=bucket, Key=key, VersionId=v['VersionId'])['Body'].read()
            source = {'key': key, 'bucket': bucket, 'version_id': v['VersionId'],
                      'stored_at': v['LastModified'].isoformat(), 'sha256': hashlib.sha256(body).hexdigest()}
            sources.append(source)
            rows = pq.ParquetFile(io.BytesIO(body)).read().to_pylist()
            if len({r['game_id'] for r in rows}) != len(rows):
                raise ValueError('duplicate stored prediction IDs')
            if v.get('IsLatest'):
                current = {r['game_id']: r for r in rows}
            for row in rows:
                cutoff = utc(row['commence_time'])-timedelta(minutes=10)
                if (v['VersionId'] != 'null' and row['date'] == key.split('date=')[1].split('/')[0]
                        and utc(row['as_of']) <= utc(source['stored_at']) <= cutoff < utc(as_of)):
                    candidates[row['game_id']] = {'row': row, 'evidence': source}
        for pk, entry in candidates.items():
            now = (current or {}).get(pk)
            if now is None:
                entry = dict(entry)
                entry['evidence'] = dict(entry['evidence'], recovered_missing_current=True)
                admitted.append(entry)
                continue
            if any(now.get(k) != v for k, v in entry['row'].items()):
                excluded.append({'game_id': pk, 'reason': 'changed_or_missing_frozen_row'}); continue
            admitted.append(entry)
        for pk, row in (current or {}).items():
            if pk not in candidates and utc(row['commence_time'])-timedelta(minutes=10) < utc(as_of):
                excluded.append({'game_id': pk, 'reason': 'no_original_pre_cutoff_version'})
    return admitted, {'prediction_keys': len(versions), 'locked_rows': len(admitted),
                      'versions_read': len(sources), 'sources': sources, 'excluded': excluded}


def capture(s3, bucket, as_of, prior, final_sources):
    locked, inventory = read_locked_predictions(s3, bucket, as_of)
    finals = {}
    for game in prior.get('games', []):
        if not game.get('completedAtUtc'):
            continue
        try:
            finals[str(game['officialGamePk'])] = {
                'completed_at': game['completedAtUtc'],
                'observed_at': game.get('receipt', {}).get('retrievedAtUtc') or as_of,
                **{s+'_score': game['teams'][s]['teamStats']['batting']['runs'] for s in ('home', 'away')},
                **{s+'_id': str(game['teams'][s]['team']['id']) for s in ('home', 'away')}}
        except KeyError:
            continue
    keys = [o['Key'] for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=PREFIX)
            for o in page.get('Contents', []) if re.fullmatch(
                re.escape(PREFIX)+r'date=\d{4}-\d{2}-\d{2}/(?:platt|temperature).json', o['Key'])]
    models = {'platt': identity(), 'temperature': temperature_identity()}
    found = set()
    # Existing date artifacts retain parameters between disposable job runners.
    for key in sorted(keys, reverse=True):
        kind = key.rsplit('/', 1)[1].removesuffix('.json')
        if kind in found:
            continue
        model = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
        if model.get('fitted_at') is None or utc(model['fitted_at']) <= utc(as_of):
            models[kind] = model; found.add(kind)
    from ks1.calibration_store import latest_checkpoint
    checkpoint = latest_checkpoint(s3, bucket, as_of)
    if checkpoint:
        # The nightly commit is authoritative even when today's slate is empty
        # or a later hourly prediction file still contains an older model copy.
        for kind in models:
            models[kind] = checkpoint['state'][kind+'_model']
    return {'system': 'KS1', 'as_of': as_of, 'locked': locked, 'finals': finals,
            'inventory': inventory, 'final_sources': final_sources, 'platt_model': models['platt'], 'temperature_model': models['temperature'],
            'committed_ledger': checkpoint['ledger'] if checkpoint else None,
            'aws_writes': 0, 'provider_calls': 0}


def dataset(capture):
    if capture.get('system') != 'KS1' or capture.get('errors'):
        raise ValueError('invalid KS1 calibration capture')
    as_of = capture['as_of']
    admitted, excluded = [], []
    first_seen = (capture.get('platt_model') or {}).get('label_first_seen', {})
    for entry in capture['locked']:
        row, proof = entry['row'], entry['evidence']
        pk = str(row['game_id'])
        if row['model_version'] != raw_model_version() or row.get('official_probability_field', 'p_home') != 'p_home':
            excluded.append({'game_id': pk, 'reason': 'different_raw_model_or_official_engine'}); continue
        final = capture['finals'].get(pk)
        if not final or not capture.get('final_sources'):
            excluded.append({'game_id': pk, 'reason': 'no_bound_final'}); continue
        cutoff = utc(row['commence_time'])-timedelta(minutes=10)
        if (not proof.get('version_id') or proof['version_id'] == 'null'
                or not re.fullmatch('[0-9a-f]{64}', proof.get('sha256', ''))
                or not (utc(row['as_of']) <= utc(proof['stored_at']) <= cutoff < utc(as_of))):
            raise ValueError('no prospective stored lock evidence')
        if any(str(final[s+'_id']) != str(row[s+'_id']) for s in ('home', 'away')):
            raise ValueError('final/locked team identity mismatch')
        scores = np.asarray([final['home_score'], final['away_score']], float)
        if (not np.isfinite(scores).all() or (scores < 0).any() or (scores != np.floor(scores)).any()
                or scores[0] == scores[1] or utc(final['completed_at']) <= utc(row['commence_time'])
                or utc(final['completed_at']) > utc(as_of)):
            excluded.append({'game_id': pk, 'reason': 'not_a_decisive_final'}); continue
        if (row.get('platt_version') or row.get('calibration_version')) and row.get('p_raw', row.get('p_home_raw')) is None:
            raise ValueError('calibrated lock has no original raw probability')
        raw = row.get('p_raw', row.get('p_home_raw'))
        raw = raw if raw is not None else row['p_home']
        record = {'game_id': pk, 'as_of': row['as_of'], 'locked_at': cutoff.isoformat(),
                  'p_raw': raw, 'home_win': int(scores[0] > scores[1]), 'raw_model_version': row['model_version']}
        signature = hashlib.sha256(encode(record)).hexdigest()
        known = first_seen.get(pk)
        if known and known['signature'] != signature:
            raise ValueError('previous calibration observation changed; review source correction')
        # Unknown historic label availability is conservatively this capture,
        # never backdated to final inning time for a walk-forward comparison.
        observed = max(utc(final['completed_at']), utc(final.get('observed_at') or as_of))
        grade_at = utc(known['graded_at']) if known else observed
        if grade_at > utc(as_of):
            excluded.append({'game_id': pk, 'reason': 'future_final_receipt'}); continue
        record.update(graded_at=grade_at.isoformat(), signature=signature)
        admitted.append(record)
    if len({r['game_id'] for r in admitted}) != len(admitted):
        raise ValueError('duplicate graded game IDs')
    return admitted, {'locked_rows': len(capture['locked']), 'eligible_graded_rows': len(admitted),
                      'excluded': excluded, 'capture_as_of': as_of}
