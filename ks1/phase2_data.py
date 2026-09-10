"""Verify the deployed Phase 1 table and recover a timestamped record baseline."""
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ks1.inventory import Reader, RECONSTRUCTED
from ks1.publish import PREFIX
from ks1.sources import FINALS
from ks1.table import team_identity, score_pair
from ks1.features import day


def load_deployed(s3, bucket):
    reader = Reader(s3, bucket)
    keys = sorted(k for k in reader.keys(PREFIX) if k.endswith('/manifest.json'))
    if not keys:
        raise ValueError('no deployed KS1 table manifests')
    sources, tables, verified = {}, [], []
    for key in keys:
        manifest = reader.read(key)
        prefix = PREFIX + 'date=' + manifest['date'] + '/'
        if key != prefix+'manifest.json' or manifest.get('system') != 'KS1':
            raise ValueError('invalid KS1 manifest')
        for field in ('parquet_key', 'source_receipts_key'):
            if not manifest[field].startswith(prefix):
                raise ValueError('manifest escaped date prefix')
        kwargs = {'Bucket': bucket, 'Key': manifest['parquet_key']}
        if manifest.get('parquet_version_id'):
            kwargs['VersionId'] = manifest['parquet_version_id']
        body = s3.get_object(**kwargs)['Body'].read()
        if hashlib.sha256(body).hexdigest() != manifest['parquet_sha256']:
            raise ValueError('deployed parquet hash mismatch')
        table = pq.read_table(io.BytesIO(body))
        if table.num_rows != manifest['rows'] or set(table['date'].to_pylist()) != {manifest['date']}:
            raise ValueError('deployed partition count/date mismatch')
        tables.append(table)
        verified.append(manifest)
        digest = manifest['source_receipts_sha256']
        if digest not in sources:
            kwargs = {'Bucket': bucket, 'Key': manifest['source_receipts_key']}
            if manifest.get('source_receipts_version_id'):
                kwargs['VersionId'] = manifest['source_receipts_version_id']
            body = s3.get_object(**kwargs)['Body'].read()
            if hashlib.sha256(body).hexdigest() != digest:
                raise ValueError('source-receipt hash mismatch')
            sources[digest] = json.loads(gzip.decompress(body))
    table = pa.concat_tables(tables)
    if len(set(table['game_id'].to_pylist())) != table.num_rows:
        raise ValueError('duplicate deployed game IDs')
    receipts = {}
    for values in sources.values():
        for r in values:
            receipts[(r['bucket'], r['key'], r.get('versionId'), r['sha256'])] = r
    selected = [r for r in receipts.values() if
                r['key'].startswith(RECONSTRUCTED+'source-games/') or r['key'].startswith(FINALS)
                or '/research-v1/prior-games/' in r['key']]
    def read(r):
        return r, reader.read(r['key'], bucket=r['bucket'], version=r.get('versionId'), sha=r['sha256'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(read, selected))
    compact, full, finals = {}, {}, {}
    for receipt, value in values:
        key = receipt['key']
        if key.startswith(RECONSTRUCTED+'source-games/'):
            compact[str(value['officialGamePk'])] = value
        elif key.startswith(FINALS):
            for game in value['games']:
                if game.get('completed') is True:
                    pair = score_pair(game.get('homeScore'), game.get('awayScore'))
                    if pair:
                        pk = str(game['officialGamePk'])
                        if pk in finals and finals[pk] != pair:
                            raise ValueError('conflicting baseline final scores')
                        finals[pk] = pair
        else:
            full.update({str(g['officialGamePk']): g for g in value['games']})
    baseline = []
    for pk, game in {**compact, **full}.items():
        if game.get('gameType') != 'R':
            continue  # regular-season standings, not postseason outcomes
        pair = finals.get(pk)
        if pk in full:
            box_pair = score_pair(*[game['teams'][s].get('teamStats', {}).get('batting', {}).get('runs') for s in ('home', 'away')])
            if pair and box_pair and pair != box_pair:
                raise ValueError('baseline box/final disagreement')
            pair = pair or box_pair
        if not pair or pair[0] == pair[1]:
            continue
        baseline.append({'game_id': pk, 'date': str(day(game['startAtUtc'])),
                         'season': day(game['startAtUtc']).year, 'completed_at': game['completedAtUtc'],
                         'home_id': team_identity(game['teams']['home'])[0],
                         'away_id': team_identity(game['teams']['away'])[0], 'home_win': int(pair[0] > pair[1])})
    if not baseline:
        raise ValueError('no timestamped baseline results')
    return table, pd.DataFrame(baseline).sort_values(['date', 'game_id']), {
        'bucket': bucket, 'table_prefix': PREFIX, 'partitions': verified,
        'rows': table.num_rows, 'readback_verified': True, 'baseline_games': len(baseline),
        'baseline_sources': selected, 'provider_calls': 0}
