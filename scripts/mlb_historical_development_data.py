"""Separate reconstructed development data; never an original/live snapshot.

Only prior completed games feed baseball features. Historical current-game
starters and batting orders are unavailable without archived pregame evidence.
Current season totals and target-game boxscore statistics are never features.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hello_world'))
import mlb_r7_historical_walkforward_bridge as bridge
import mlb_statsapi_team_context as teams

VERSION = 'MLB-RECONSTRUCTED-DEVELOPMENT-DATA-v1'
PREFIX = 'mlb/development-data/reconstructed-v1/'
ET = ZoneInfo('America/New_York')


def is_final(game):
    return (game.get('status', {}).get('abstractGameState') == 'Final'
            and game.get('status', {}).get('detailedState') not in ('Postponed', 'Cancelled')
            and sum(t.get('isWinner') is True for t in game.get('teams', {}).values()) == 1)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def utc(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result.astimezone(timezone.utc)


def count(value):
    if isinstance(value, bool):
        raise ValueError('boolean statistic')
    n = float(value)
    if n < 0 or not n.is_integer():
        raise ValueError('nonnegative integer statistic required')
    return int(n)


def compact_game(payload, receipt):
    """Keep exact used fields plus a hash of the full provider response."""
    data = payload['gameData']
    if data['status']['abstractGameState'] != 'Final':
        raise ValueError('source game not final')
    plays = payload['liveData']['plays']['allPlays']
    ends = [utc(p['about']['endTime']) for p in plays]
    if not ends:
        raise ValueError('completion timestamp unavailable')
    box = payload['liveData']['boxscore']['teams']
    result = {'officialGamePk': data['game']['pk'],
              'startAtUtc': data['datetime']['dateTime'],
              'completedAtUtc': max(ends).isoformat(),
              'gameType': data['game']['type'], 'teams': {}, 'receipt': receipt}
    for side in ('home', 'away'):
        team = box[side]
        batting = team['teamStats']['batting']
        batting_counts = {k: count(batting[k]) for k in
                          ('atBats', 'hits', 'baseOnBalls', 'hitByPitch', 'sacFlies', 'doubles', 'triples', 'homeRuns')}
        starter_outs, starter_er, starter_k, starter_bb, starter_bf = 0, 0, 0, 0, 0
        relief = teams._relief(team)
        for pid in team['pitchers']:
            stat = team['players']['ID' + str(pid)]['stats']['pitching']
            if count(stat['gamesStarted']) == 1:
                starter_outs += count(stat['outs'])
                starter_er += count(stat['earnedRuns'])
                starter_k += count(stat['strikeOuts'])
                starter_bb += count(stat['baseOnBalls'])
                starter_bf += count(stat['battersFaced'])
        result['teams'][side] = {'id': team['team']['id'], 'name': team['team']['name'],
            'batting': batting_counts, 'relief': relief,
            'priorStarters': {'outs': starter_outs, 'earnedRuns': starter_er,
                             'strikeOuts': starter_k, 'baseOnBalls': starter_bb, 'battersFaced': starter_bf}}
    result['fingerprint'] = digest(result)
    return result


def team_features(identity, target_day, games):
    """Calendar-day windows exclude same-day games and late completions."""
    cutoff = datetime.combine(date.fromisoformat(target_day), datetime.min.time(), ET)
    batting, pitching = Counter(), Counter()
    usage = {str(n)+'d': {'pitches': 0, 'outs': 0} for n in (1, 3, 5)}
    sources = []
    for game in games:
        matching = [t for t in game['teams'].values() if t['id'] == identity]
        if not matching:
            continue
        if len(matching) != 1:
            raise ValueError('ambiguous historical team')
        started = utc(game['startAtUtc']).astimezone(ET).date()
        age = (date.fromisoformat(target_day) - started).days
        if not 1 <= age <= 14:
            continue
        if utc(game['completedAtUtc']) >= cutoff:
            raise ValueError('prior game completed after feature cutoff')
        team = matching[0]
        batting.update(team['batting']); pitching.update(team['priorStarters'])
        for n in (1, 3, 5):
            if age <= n:
                for k in ('pitches', 'outs'):
                    usage[str(n)+'d'][k] += team['relief'][k]
        sources.append(game['officialGamePk'])
    ab = batting['atBats']; den = ab + batting['baseOnBalls'] + batting['hitByPitch'] + batting['sacFlies']
    total_bases = batting['hits'] + batting['doubles'] + 2*batting['triples'] + 3*batting['homeRuns']
    ops = ((batting['hits']+batting['baseOnBalls']+batting['hitByPitch'])/den + total_bases/ab) if ab and den else None
    return {'teamBattingOps14d': ops,
            'priorStartingPitchersEra14d': 27*pitching['earnedRuns']/pitching['outs'] if pitching['outs'] else None,
            'priorStartingPitchersKMinusBbPct14d': 100*(pitching['strikeOuts']-pitching['baseOnBalls'])/pitching['battersFaced'] if pitching['battersFaced'] else None,
            'bullpenUsage1d3d5d': usage, 'priorCompletedGameIds': sorted(sources)}


def materialize(record, dataset, artifact, target_game, prior_games, reconstructed_at):
    """Archived market features first, prior-game features second, label last."""
    if target_game['gamePk'] != int(record['officialGamePk']):
        raise ValueError('official target identity mismatch')
    day = record['slateDateEt']
    start, lock = utc(record['commenceTime']), utc(record['predictionLockAtUtc'])
    if start.astimezone(ET).date().isoformat() != day or lock >= start:
        raise ValueError('invalid target date or lock')
    if utc(target_game['gameDate']).astimezone(ET).date().isoformat() != day:
        raise ValueError('archived game date differs from official played date')
    source_at = bridge._source_at(record, dataset.get('snapshotAudit') or [], dataset['officialGameCount'])
    home_p, away_p = (float(record[s+'Signal']['fairProbability']) for s in ('home', 'away'))
    if not 0 < home_p < 1 or not 0 < away_p < 1 or abs(home_p+away_p-1) > 1e-6:
        raise ValueError('invalid archived market probability pair')
    sides = {}
    for side in ('home', 'away'):
        team = target_game['teams'][side]['team']
        if bridge._norm(team['name']) != bridge._norm(record[side+'Team']):
            raise ValueError('historical team name or side mismatch')
        sides[side] = team_features(team['id'], day, prior_games)
    features = {'marketHomeProbability': home_p,
                'deltaGapHome': float(record['homeSignal']['delta']) - float(record['awaySignal']['delta']),
                'home': sides['home'], 'away': sides['away']}
    for name in ('teamBattingOps14d', 'priorStartingPitchersEra14d', 'priorStartingPitchersKMinusBbPct14d'):
        a, b = sides['home'][name], sides['away'][name]
        features[name+'GapHome'] = a-b if a is not None and b is not None else None
    features['bullpenPitches3dGapHome'] = sides['home']['bullpenUsage1d3d5d']['3d']['pitches']-sides['away']['bullpenUsage1d3d5d']['3d']['pitches']
    feature_fingerprint = digest(features)
    winner = record['winner']
    if winner not in (record['homeTeam'], record['awayTeam']):
        raise ValueError('invalid archived final label')
    return {'version': VERSION, 'officialGamePk': str(record['officialGamePk']), 'slateDateEt': day,
            'homeTeam': record['homeTeam'], 'awayTeam': record['awayTeam'],
            'commenceTime': record['commenceTime'], 'marketSourceAtUtc': source_at,
            'featureCutoffUtc': datetime.combine(date.fromisoformat(day), datetime.min.time(), ET).isoformat(),
            'reconstructedAtUtc': reconstructed_at, 'evidenceKind': 'RECONSTRUCTED_HISTORICAL_DEVELOPMENT',
            'features': features, 'featureFingerprint': feature_fingerprint,
            'label': {'homeWon': winner == record['homeTeam'], 'source': 'verified_historical_settlement_archive'},
            'sourceArtifact': artifact,
            'missingFeatures': ['archivedPregameCurrentStarterIdentity', 'archivedPregameBattingOrder'],
            'historicalCorrectionsMayBePresent': True, 'originalObservation': False,
            'prospectiveQualificationEvidence': False, 'productionAuthority': False}


def write_verified(s3, bucket, payload):
    body = encoded(payload); sha = hashlib.sha256(body).hexdigest()
    if s3.get_bucket_versioning(Bucket=bucket).get('Status') != 'Enabled':
        raise ValueError('versioned artifact bucket required')
    key = PREFIX + sha + '/dataset.json'
    response = s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json', Metadata={'sha256': sha})
    version = response.get('VersionId')
    if not version or version == 'null':
        raise ValueError('versioned dataset write failed')
    read = s3.get_object(Bucket=bucket, Key=key, VersionId=version)
    if hashlib.sha256(read['Body'].read()).hexdigest() != sha:
        raise ValueError('dataset readback mismatch')
    return {'bucket': bucket, 'key': key, 'versionId': version, 'sha256': sha, 'byteLength': len(body)}
