"""Collect bounded live MLB inputs and existing S3 history; no AWS writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ks1.features import ET, day, utc
from ks1.inventory import Reader, RESEARCH, RECONSTRUCTED, encode
from ks1.sources import aws_clients


def shape(value, depth=0):
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): shape(v, depth+1) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return {'type': 'array', 'count': len(value), 'item': shape(value[0], depth+1) if value else None}
    return type(value).__name__


class ProviderFailure(ValueError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(json.dumps(receipt, sort_keys=True))


def fetch(provider, base, path, params, *, key=None, opener=urlopen):
    headers = {'Accept': 'application/json', 'User-Agent': 'KS1-daily/1.0'}
    query = dict(params)
    if provider == 'bbs':
        if not key:
            raise ProviderFailure({'provider': provider, 'status': 'BBS_API_KEY_MISSING', 'body_shape': None})
        headers['Authorization'] = 'Bearer ' + key
    if provider == 'odds':
        if not key:
            raise ProviderFailure({'provider': provider, 'status': 'ODDS_API_KEY_MISSING', 'body_shape': None})
        query['apiKey'] = key
    request = Request(base + path + '?' + urlencode(query), headers=headers)
    for attempt in range(2):
        try:
            with opener(request, timeout=25) as response:
                body = response.read(30_000_001)
                status = response.status
                response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            if len(body) > 30_000_000:
                raise ProviderFailure({'provider': provider, 'status': 'RESPONSE_TOO_LARGE', 'body_shape': 'bytes'})
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise ProviderFailure({'provider': provider, 'status': status, 'body_shape': 'non_json'}) from None
            if isinstance(payload, dict) and payload.get('error'):
                raise ProviderFailure({'provider': provider, 'status': status, 'body_shape': shape(payload)})
            receipt = {'provider': provider, 'endpoint': base+path, 'params': params, 'status': status,
                       'as_of': datetime.now(timezone.utc).isoformat(), 'sha256': hashlib.sha256(encode(payload)).hexdigest(),
                       'body_shape': shape(payload), 'quota': {k: v for k, v in response_headers.items()
                           if k in ('x-requests-remaining', 'x-requests-used', 'x-requests-last',
                                    'x-ratelimit-remaining', 'x-ratelimit-limit', 'x-ratelimit-reset')}}
            return {'payload': payload, 'receipt': receipt}
        except HTTPError as exc:
            body = exc.read(100_000)
            try:
                body_shape = shape(json.loads(body))
            except (ValueError, UnicodeError):
                body_shape = 'non_json'
            receipt = {'provider': provider, 'endpoint': base+path, 'status': exc.code, 'body_shape': body_shape}
            # Never log URL/query/header strings: Odds credentials live in query.
            if exc.code == 429:
                raise ProviderFailure(receipt) from None
            if exc.code >= 500 and attempt == 0:
                time.sleep(1)
                continue
            raise ProviderFailure(receipt) from None
        except (URLError, TimeoutError, OSError):
            if attempt == 0:
                time.sleep(1)
                continue
            raise ProviderFailure({'provider': provider, 'status': 'NETWORK_ERROR', 'body_shape': None}) from None


def capture(target_date, output):
    date.fromisoformat(target_date)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    calls = [('bbs', 'https://api.bigballsdata.com', '/v1/matches',
              {'sport': 'baseball', 'league': 'mlb', 'date': target_date, 'limit': 200}, os.environ.get('BBS_API_KEY')),
             ('odds', 'https://api.the-odds-api.com', '/v4/sports/baseball_mlb/odds',
              {'regions': 'us', 'markets': 'h2h,spreads,totals', 'oddsFormat': 'american'}, os.environ.get('ODDS_API_KEY'))]
    for provider, base, path, params, key in calls:
        try:
            value = fetch(provider, base, path, params, key=key)
            (output / (provider+'.json')).write_bytes(encode(value))
            print(json.dumps(value['receipt']))
        except ProviderFailure as exc:
            errors.append(exc.receipt)
            print(json.dumps(exc.receipt))
    # Existing repository source. One bulk schedule call supplies official IDs
    # and probable pitchers: the documented BBS stored lineup route is empty.
    official = fetch('official_schedule', 'https://statsapi.mlb.com', '/api/v1/schedule',
                     {'sportId': 1, 'date': target_date, 'hydrate': 'probablePitcher,venue(location)'})
    (output / 'official.json').write_bytes(encode(official))
    _, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
    reader = Reader(s3, bucket)
    prior = reader.pointer(reader.read(RESEARCH+'prior-games.json')['artifact'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        compact = list(pool.map(reader.read, sorted(reader.keys(RECONSTRUCTED+'source-games/'))))
    games = {str(g['officialGamePk']): g for g in compact}
    games.update({str(g['officialGamePk']): g for g in prior['games']})
    games = [g for g in games.values() if day(g['startAtUtc']).year == date.fromisoformat(target_date).year]
    history = {'games': games, 'source_receipts': reader.receipts,
               'prior_observed_at': prior.get('updatedAtUtc') or prior.get('receipt', {}).get('retrievedAtUtc')}
    (output / 'history.json.gz').write_bytes(gzip.compress(encode(history), mtime=0))
    refs = json.loads((Path(__file__).parent/'model_refs.json').read_bytes())
    ref = refs['lightgbm']
    body = s3.get_object(Bucket=bucket, Key=ref['key'], VersionId=ref['version_id'])['Body'].read()
    if hashlib.sha256(body).hexdigest() != ref['sha256']:
        raise ValueError('accepted LightGBM artifact hash mismatch')
    (output / 'model.txt').write_bytes(body)
    manifest = {'system': 'KS1', 'phase': 4, 'date': target_date, 'as_of': datetime.now(timezone.utc).isoformat(),
                'bucket': bucket, 'aws_writes': 0, 'errors': errors,
                'source_history_games': len(games), 'github_sha': os.environ.get('GITHUB_SHA'),
                'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()
                          if p.is_file() and p.name != 'capture.json'}}
    (output / 'capture.json').write_bytes(encode(manifest))
    print(json.dumps(manifest))
    if errors:
        raise ValueError('provider capture failed; see redacted receipts')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=datetime.now(timezone.utc).astimezone(ET).date().isoformat())
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    capture(args.date, args.output)


if __name__ == '__main__':
    main()
