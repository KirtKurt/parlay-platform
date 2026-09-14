"""Retain MLB's timestamped pregame feed, never substitute a final starter.

Collection is restricted to the authorized challenger workflow. The archived
feed's own timestamp and pregame state are checked independently of retrieval.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import time
from urllib.request import Request, urlopen

from ks1.features import utc
from ks1.inventory import encode

PREFIX = 'mlb/development-data/ks1-historical-pregame-v1/'
FIELDS = ('gamePk,metaData,timeStamp,gameData,datetime,dateTime,status,codedGameState,'
          'abstractGameState,probablePitchers,home,away,id,fullName,teams,liveData,plays,allPlays,'
          'playEvents,isPitch,decisions,linescore,currentInning')


def feed_identity(entry):
    """Admit only the historical feed's own pre-T10 identity state."""
    try:
        payload, receipt = entry['payload'], entry['receipt']
        pk, start = str(entry['game_id']), utc(entry['commence_time'])
        cutoff = start-timedelta(minutes=10)
        at = datetime.strptime(payload['metaData']['timeStamp'], '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
        data, live = payload['gameData'], payload.get('liveData', {})
        if (entry.get('provider') != 'MLB Stats API'
                or str(payload['gamePk']) != pk or utc(data['datetime']['dateTime']) != start
                or not start-timedelta(days=1) <= at <= cutoff
                or entry['timecode'] != cutoff.strftime('%Y%m%d_%H%M%S')
                or entry['endpoint'] != 'https://statsapi.mlb.com/api/v1.1/game/'+pk+'/feed/live'
                or utc(entry['retrieved_at']) < at
                or data['status']['codedGameState'] not in ('P', 'S')
                or live.get('decisions')
                or any(event.get('isPitch') is True
                       for play in live.get('plays', {}).get('allPlays', [])
                       for event in play.get('playEvents', []))
                or hashlib.sha256(encode(payload)).hexdigest() != entry['payload_sha256']
                or not receipt.get('bucket') or not receipt.get('key', '').startswith(PREFIX)
                or receipt['key'] != PREFIX+'game='+pk+'/timecode='+entry['timecode']+'.json'
                or receipt.get('versionId') in (None, '', 'null')
                or hashlib.sha256(encode({k:v for k,v in entry.items() if k != 'receipt'})).hexdigest() != receipt['sha256']):
            return None
        sides = {side: {'pitcher_id': str(data['probablePitchers'][side]['id']),
                        'name': data['probablePitchers'][side].get('fullName'),
                        'team_id': str(data['teams'][side]['id']),
                        'as_of': at.isoformat(), 'commence_time': entry['commence_time'],
                        'source': {**receipt, 'provider': 'MLB Stats API',
                                   'endpoint': entry['endpoint'], 'timecode': entry['timecode'],
                                   'retrieved_at': entry['retrieved_at'],
                                   'payload_sha256': entry['payload_sha256']}}
                 for side in ('home', 'away')}
        return {'game_id': pk, 'sides': sides}
    except (KeyError, TypeError, ValueError):
        return None


def collect(s3, bucket, games, *, limit=1000, seconds=600):
    from ks1.train import artifact_write_authorized
    if (not artifact_write_authorized() or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_EVENT_NAME') not in ('push', 'schedule', 'workflow_dispatch')):
        raise ValueError('historical feed writes require the authorized KS1 challenger workflow')
    selected = sorted(games, key=lambda g: (utc(g['completedAtUtc']), str(g['officialGamePk'])),
                      reverse=True)[:limit]
    deadline = time.monotonic()+seconds
    calls = []

    def one(game):
        pk, start = str(game['officialGamePk']), utc(game['startAtUtc'])
        code = (start-timedelta(minutes=10)).strftime('%Y%m%d_%H%M%S')
        key = PREFIX+'game='+pk+'/timecode='+code+'.json'
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') not in ('NoSuchKey', '404'):
                return None, {'game_id': pk, 'reason': type(exc).__name__}
            if time.monotonic() >= deadline:
                return None, {'game_id': pk, 'reason': 'collection_budget_exhausted'}
            endpoint = 'https://statsapi.mlb.com/api/v1.1/game/'+pk+'/feed/live'
            url = endpoint+'?timecode='+code+'&fields='+FIELDS
            try:
                calls.append(pk)
                with urlopen(Request(url, headers={'User-Agent':'KS1 historical pregame research'}), timeout=25) as response:
                    payload = json.loads(response.read())
                value = {'game_id': pk, 'commence_time': game['startAtUtc'], 'provider':'MLB Stats API',
                         'endpoint': endpoint, 'timecode': code,
                         'retrieved_at': datetime.now(timezone.utc).isoformat(),
                         'payload': payload, 'payload_sha256': hashlib.sha256(encode(payload)).hexdigest()}
                body = encode(value)
                # Validate before any write; the real version receipt is checked
                # again below after an exact S3 readback.
                proposed = {**value, 'receipt': {'bucket':bucket,'key':key,'versionId':'pending-validation',
                            'sha256':hashlib.sha256(body).hexdigest()}}
                if feed_identity(proposed) is None:
                    return None, {'game_id':pk,'reason':'historical_pregame_state_unavailable'}
                try:
                    put = s3.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch='*',
                                        Metadata={'sha256':hashlib.sha256(body).hexdigest(),'system':'KS1'})
                    response = s3.get_object(Bucket=bucket, Key=key, VersionId=put['VersionId'])
                except Exception as exc:
                    if getattr(exc,'response',{}).get('Error',{}).get('Code') != 'PreconditionFailed':
                        raise
                    response = s3.get_object(Bucket=bucket, Key=key)
            except Exception as exc:
                return None, {'game_id':pk,'reason':type(exc).__name__}
        body = response['Body'].read()
        value = json.loads(body)
        if (str(value.get('game_id')) != pk or value.get('timecode') != code
                or utc(value.get('commence_time')) != start):
            return None, {'game_id':pk,'reason':'retained_feed_identity_mismatch'}
        receipt = {'bucket':bucket,'key':key,'versionId':response.get('VersionId'),
                   'sha256':hashlib.sha256(body).hexdigest()}
        entry = {**value, 'receipt':receipt}
        if feed_identity(entry) is None:
            return None, {'game_id':pk,'reason':'invalid_retained_pregame_feed'}
        return entry, None

    entries, errors = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for entry, error in pool.map(one, selected):
            if entry: entries.append(entry)
            if error: errors.append(error)
    return entries, {'selected_games':len(selected),'verified_games':len(entries),
                     'provider_requests':len(calls),'readback_verified_games':len(entries),
                     'errors':errors,'provider':'MLB Stats API timestamped feed',
                     'historical_retrieval_is_original_storage':False,
                     'source_prefix':PREFIX}
