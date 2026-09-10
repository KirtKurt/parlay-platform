"""Read retained KS1/archive data only. No vendor clients or AWS mutations."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

from ks1.inventory import Reader, RECONSTRUCTED, RESEARCH, encode
from ks1.sources import aws_clients


def capture(phase2, output):
    proof = json.loads((phase2/'input_proof.json').read_bytes())
    _, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
    reader = Reader(s3, bucket)
    output.mkdir(parents=True, exist_ok=True)
    # Reuse exactly the historical source versions already bound by Phase 2.
    selected = [r for r in proof['baseline_sources'] if r['key'].startswith(RECONSTRUCTED+'source-games/')]
    def read(r):
        return reader.read(r['key'], bucket=r['bucket'], version=r.get('versionId'), sha=r['sha256'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        compact = list(pool.map(read, selected))
    prior = reader.pointer(reader.read(RESEARCH+'prior-games.json')['artifact'])
    statcast = reader.pointer(reader.read(RESEARCH+'statcast.json')['artifact'])
    snapshots = [dict(reader.read(k), source_key=k) for k in reader.keys(RESEARCH+'snapshots/')]
    archive_buckets = sorted({r['bucket'] for r in proof['baseline_sources'] if '/official-finals/' in r['key']})
    contexts, discovery, finals = [], [], []
    for archive in archive_buckets:
        # Delimited listing discovers existing data roots, not invented paths.
        for prefix in ('mlb/', 'mlb/v8/', 'mlb/historical-daily-v1/'):
            page = s3.list_objects_v2(Bucket=archive, Prefix=prefix, Delimiter='/')
            discovery.append({'bucket': archive, 'prefix': prefix,
                              'children': [p['Prefix'] for p in page.get('CommonPrefixes', [])],
                              'truncated': page.get('IsTruncated', False)})
        for context_prefix in ('mlb/v8/historical-bbs/manifests/', 'mlb/v8/historical-context/manifests/'):
            objects = [o for page in s3.get_paginator('list_objects_v2').paginate(
                Bucket=archive, Prefix=context_prefix) for o in page.get('Contents', [])]
            discovery.append({'bucket': archive, 'prefix': context_prefix, 'objects': len(objects)})
            if objects:
                latest = max(objects, key=lambda o: o['LastModified'])
                contexts.append({'bucket': archive, 'key': latest['Key'], 'payload': reader.read(latest['Key'], bucket=archive)})
        refs = [r for r in proof['baseline_sources'] if r['bucket'] == archive and '/official-finals/' in r['key']]
        with ThreadPoolExecutor(max_workers=8) as pool:
            finals.extend(pool.map(read, refs))
    bundle = {'compact': compact, 'prior': prior, 'snapshots': snapshots,
              'statcast': statcast, 'contexts': contexts, 'finals': finals, 'discovery': discovery}
    (output/'archive.json.gz').write_bytes(gzip.compress(encode(bundle), mtime=0))
    schema = {'compact_games': len(compact), 'prior_games': len(prior.get('games', [])),
              'snapshots': len(snapshots), 'statcast_rows': len(statcast.get('rows', [])),
              'context_records': [len(c['payload'].get('records', [])) for c in contexts],
              'finals_columns': sorted({k for v in finals for g in v.get('games', []) for k in g}),
              'context_sample': [c['payload'].get('records', [])[:1] for c in contexts],
              'snapshot_sample': snapshots[:1], 'discovery': discovery}
    (output/'inventory.json').write_bytes(encode(schema))
    (output/'receipt.json').write_bytes(encode({'system': 'KS1', 'purpose': 'accuracy add-on inventory',
        'as_of': datetime.now(timezone.utc).isoformat(), 'provider_calls': 0, 'aws_writes': 0,
        'sources': reader.receipts, 'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                           for p in output.iterdir() if p.name != 'receipt.json'}}))
    print(json.dumps({k: v for k, v in schema.items() if k not in ('context_sample', 'snapshot_sample')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase2-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    capture(args.phase2_dir, args.output)
