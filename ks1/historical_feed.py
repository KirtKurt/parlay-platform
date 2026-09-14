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
TEAM_CONTEXT_PREFIX = 'mlb/development-data/ks1-historical-team-context-v1/'
FIELDS = ('gamePk,metaData,timeStamp,gameData,datetime,dateTime,status,codedGameState,'
          'abstractGameState,probablePitchers,home,away,id,fullName,teams,liveData,plays,allPlays,'
          'playEvents,isPitch,decisions,linescore,currentInning')


def _positive_ids(values):
    if not isinstance(values, list):
        return None
    result = []
    for value in values:
        identity = value.get('id') if isinstance(value, dict) else value
        if not isinstance(identity, int) or isinstance(identity, bool) or identity <= 0:
            return None
        result.append(str(identity))
    return result if len(result) == len(set(result)) else None


def _box_lineup(team):
    raw = team.get('battingOrder')
    if raw in (None, []):
        return None
    identities = _positive_ids(raw)
    if identities is None or len(identities) != 9:
        raise ValueError('malformed batting order')
    players = team.get('players')
    if not isinstance(players, dict):
        raise ValueError('lineup player map missing')
    for slot, identity in enumerate(identities, 1):
        player = players.get('ID'+identity)
        if (not isinstance(player, dict)
                or (player.get('person') or {}).get('id') != int(identity)
                or str(player.get('battingOrder')) != str(slot*100)
                or (player.get('gameStatus') or {}).get('isSubstitute') is not False):
            raise ValueError('lineup identity binding failed')
    return identities


def _box_bullpen(team):
    raw = team.get('bullpen')
    if raw in (None, []):
        return None
    identities = _positive_ids(raw)
    players = team.get('players')
    if identities is None or not isinstance(players, dict):
        raise ValueError('malformed bullpen roster')
    for identity in identities:
        player = players.get('ID'+identity)
        if (not isinstance(player, dict)
                or (player.get('person') or {}).get('id') != int(identity)):
            raise ValueError('bullpen identity binding failed')
    return identities


def feed_team_context(entry):
    """Admit lineups and bullpen identities from an MLB-rendered T-10 state.

    Historical retrieval time is not represented as original storage time.  The
    provider's embedded timecode, pregame status and empty play state jointly
    establish what the endpoint exposed at the requested cutoff.
    """
    try:
        payload, receipt = entry['payload'], entry['receipt']
        pk, start = str(entry['game_id']), utc(entry['commence_time'])
        cutoff = start-timedelta(minutes=10)
        at = datetime.strptime(payload['metaData']['timeStamp'], '%Y%m%d_%H%M%S').replace(
            tzinfo=timezone.utc)
        data, live = payload['gameData'], payload.get('liveData', {})
        box = live.get('boxscore', {}).get('teams', {})
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
                or not receipt.get('bucket')
                or receipt.get('key') != (TEAM_CONTEXT_PREFIX+'game='+pk+
                                          '/timecode='+entry['timecode']+'.json')
                or receipt.get('versionId') in (None, '', 'null')
                or hashlib.sha256(encode({k:v for k,v in entry.items() if k != 'receipt'})).hexdigest()
                   != receipt['sha256']):
            return None
        sides = {}
        for side in ('home', 'away'):
            team = box[side]
            lineup = _box_lineup(team)
            bullpen = _box_bullpen(team)
            probable = (data.get('probablePitchers') or {}).get(side) or {}
            raw_team_id = data['teams'][side]['id']
            raw_box_team_id = (team.get('team') or {}).get('id')
            if (not isinstance(raw_team_id, int) or isinstance(raw_team_id, bool)
                    or not isinstance(raw_box_team_id, int) or isinstance(raw_box_team_id, bool)
                    or raw_team_id <= 0 or raw_box_team_id <= 0):
                return None
            team_id, box_team = str(raw_team_id), str(raw_box_team_id)
            lineup_valid = lineup is not None and len(lineup) == 9
            bullpen_valid = bullpen is not None and bool(bullpen)
            if (box_team != team_id
                    or (lineup_valid and bullpen_valid and set(lineup) & set(bullpen))):
                return None
            raw_probable_id = probable.get('id')
            probable_id = (str(raw_probable_id)
                           if isinstance(raw_probable_id, int)
                           and not isinstance(raw_probable_id, bool)
                           and raw_probable_id > 0 else None)
            player = ((data.get('players') or {}).get('ID'+probable_id, {})
                      if probable_id else {})
            pitch_hand = (player.get('pitchHand') or {}).get('code')
            sides[side] = {'team_id': team_id,
                           'lineup_ids': lineup if lineup_valid else None,
                           'lineup_status': ('CONFIRMED' if lineup_valid
                                             else 'MISSING_FAIL_CLOSED'),
                           'bullpen_roster_ids': bullpen if bullpen_valid else None,
                           'bullpen_status': ('OBSERVED_ROSTER_ONLY' if bullpen_valid
                                              else 'MISSING_FAIL_CLOSED'),
                           'probable_pitcher_id': probable_id,
                           'probable_pitcher_name': probable.get('fullName'),
                           'probable_pitcher_hand': pitch_hand if pitch_hand in ('L', 'R') else None}
        home_people = set((sides['home']['lineup_ids'] or [])+
                          (sides['home']['bullpen_roster_ids'] or []))
        away_people = set((sides['away']['lineup_ids'] or [])+
                          (sides['away']['bullpen_roster_ids'] or []))
        if home_people & away_people:
            return None
        complete = all(sides[side]['lineup_status'] == 'CONFIRMED'
                       and sides[side]['bullpen_status'] == 'OBSERVED_ROSTER_ONLY'
                       and sides[side]['probable_pitcher_id']
                       for side in ('home', 'away'))
        return {'game_id': pk, 'commence_time': entry['commence_time'],
                'as_of': at.isoformat(), 'sides': sides,
                'coverage_status': ('SUPPORTED_V1_COMPLETE' if complete
                                    else 'SUPPORTED_V1_EXPLICIT_MISSING'),
                'source': {**receipt, 'source_type': 'mlb_statsapi_timecoded_team_context',
                           'provider': 'MLB Stats API', 'endpoint': entry['endpoint'],
                           'timecode': entry['timecode'], 'retrieved_at': entry['retrieved_at'],
                           'payload_sha256': entry['payload_sha256'],
                           'historical_retrieval_is_original_storage': False}}
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


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


def _collect(s3, bucket, games, *, prefix, validator, fields=None,
             limit=1000, seconds=600):
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
        key = prefix+'game='+pk+'/timecode='+code+'.json'
        expected_sha = None
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') not in ('NoSuchKey', '404'):
                return None, {'game_id': pk, 'reason': type(exc).__name__}
            if time.monotonic() >= deadline:
                return None, {'game_id': pk, 'reason': 'collection_budget_exhausted'}
            endpoint = 'https://statsapi.mlb.com/api/v1.1/game/'+pk+'/feed/live'
            url = endpoint+'?timecode='+code
            if fields:
                url += '&fields='+fields
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
                if validator(proposed) is None:
                    return None, {'game_id':pk,'reason':'historical_pregame_state_unavailable'}
                try:
                    expected_sha = hashlib.sha256(body).hexdigest()
                    put = s3.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch='*',
                                        Metadata={'sha256':hashlib.sha256(body).hexdigest(),'system':'KS1'})
                    response = s3.get_object(Bucket=bucket, Key=key, VersionId=put['VersionId'])
                except Exception as exc:
                    if getattr(exc,'response',{}).get('Error',{}).get('Code') != 'PreconditionFailed':
                        raise
                    expected_sha = None  # Admit the independently verified winner of a concurrent write.
                    response = s3.get_object(Bucket=bucket, Key=key)
            except Exception as exc:
                return None, {'game_id':pk,'reason':type(exc).__name__}
        try:
            body = response['Body'].read()
            actual_sha = hashlib.sha256(body).hexdigest()
            metadata_sha = response.get('Metadata', {}).get('sha256')
            if ((expected_sha and actual_sha != expected_sha)
                    or (metadata_sha and actual_sha != metadata_sha)):
                raise ValueError('historical feed readback mismatch')
            value = json.loads(body)
            if (str(value.get('game_id')) != pk or value.get('timecode') != code
                    or utc(value.get('commence_time')) != start):
                return None, {'game_id':pk,'reason':'retained_feed_identity_mismatch'}
            receipt = {'bucket':bucket,'key':key,'versionId':response.get('VersionId'),
                       'sha256':hashlib.sha256(body).hexdigest()}
            entry = {**value, 'receipt':receipt}
            if validator(entry) is None:
                return None, {'game_id':pk,'reason':'invalid_retained_pregame_feed'}
            return entry, None
        except Exception as exc:
            return None, {'game_id':pk,'reason':'invalid_cached_feed_'+type(exc).__name__}

    entries, errors = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for entry, error in pool.map(one, selected):
            if entry: entries.append(entry)
            if error: errors.append(error)
    return entries, {'selected_games':len(selected),'verified_games':len(entries),
                     'provider_requests':len(calls),'readback_verified_games':len(entries),
                     'errors':errors,'provider':'MLB Stats API timestamped feed',
                     'historical_retrieval_is_original_storage':False,
                     'source_prefix':prefix}


def collect(s3, bucket, games, *, limit=1000, seconds=600):
    return _collect(s3, bucket, games, prefix=PREFIX, validator=feed_identity,
                    fields=FIELDS, limit=limit, seconds=seconds)


def collect_team_context(s3, bucket, games, *, limit=5000, seconds=1800):
    """Collect richer T-10 state under a distinct immutable prefix.

    The original starter archive intentionally used a narrow fields response.
    A separate prefix prevents those cached bytes from being reinterpreted as
    lineup or bullpen evidence.
    """
    return _collect(s3, bucket, games, prefix=TEAM_CONTEXT_PREFIX,
                    validator=feed_team_context, fields=None,
                    limit=limit, seconds=seconds)
