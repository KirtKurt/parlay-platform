"""Official source receipts and exact game identity; no historical observation claims."""
import csv
import io
import json
import math
import os
from collections import Counter
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from mlb_research_store_v1 import digest, now, utc

ET = ZoneInfo('America/New_York')
API = 'https://statsapi.mlb.com/api'
GAME_TYPES = {'R', 'F', 'D', 'L', 'W'}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def count(value):
    n = number(value)
    if n is None or n < 0 or not n.is_integer():
        raise ValueError('nonnegative integer required')
    return int(n)


def fetch(url, *, raw=False):
    req = Request(url, headers={'User-Agent': 'MLBResearch/1.0 source verification', 'Accept': 'application/json,text/csv'})
    with urlopen(req, timeout=20) as response:
        content = response.read(30_000_001)
    if len(content) > 30_000_000:
        raise ValueError('oversized source')
    value = content.decode('utf-8-sig') if raw else json.loads(content)
    # Secret-bearing odds URLs never enter logs or artifacts.
    endpoint = url.split('apiKey=')[0].rstrip('&?')
    return value, {'endpoint': endpoint, 'retrievedAtUtc': now().isoformat(), 'sha256': digest(value)}


def schedule(first, last=None):
    payload, receipt = fetch(API+'/v1/schedule?'+urlencode({'sportId': 1, 'startDate': first,
                           'endDate': last or first, 'hydrate': 'probablePitcher,venue(location)'}))
    games = [g for d in payload.get('dates', []) for g in d.get('games', [])]
    if payload.get('totalGames') != len(games):
        raise ValueError('incomplete official schedule')
    ids = [count(g['gamePk']) for g in games]
    if 0 in ids or len(set(ids)) != len(ids):
        raise ValueError('duplicate or ambiguous official game')
    return [g for g in games if g['gameType'] in GAME_TYPES], receipt


def final(game):
    return game.get('status', {}).get('abstractGameState') == 'Final'


def playable(game):
    return game.get('status', {}).get('detailedState') not in ('Postponed', 'Cancelled')


def feed(game,fields=None):
    url=API+f"/v1.1/game/{game['gamePk']}/feed/live"
    if fields:url+='?'+urlencode({'fields':fields})
    data, receipt = fetch(url)
    identity = data['gameData']
    if (identity['game']['pk'] != game['gamePk'] or utc(identity['datetime']['dateTime']) != utc(game['gameDate'])
            or any(identity['teams'][s]['id'] != game['teams'][s]['team']['id'] for s in ('home', 'away'))):
        raise ValueError('official feed identity changed')
    return data, receipt


def final_source(game):
    fields=('gameData,game,pk,type,datetime,dateTime,status,abstractGameState,teams,home,away,id,name,'
            'venue,location,defaultCoordinates,latitude,longitude,liveData,plays,allPlays,about,endTime,'
            'boxscore,team,teamStats,batting,runs,atBats,hits,doubles,triples,homeRuns,baseOnBalls,'
            'hitByPitch,sacFlies,strikeOuts,plateAppearances,pitchers,players,person,stats,pitching,'
            'gamesStarted,outs,earnedRuns,battersFaced,numberOfPitches')
    payload, receipt = feed(game,fields)
    if not final(payload['gameData']):
        raise ValueError('source game not final')
    ends = [utc(p['about']['endTime']) for p in payload['liveData']['plays']['allPlays']]
    if not ends:
        raise ValueError('source completion time unavailable')
    value = {'officialGamePk': game['gamePk'], 'startAtUtc': game['gameDate'],
             'completedAtUtc': max(ends).isoformat(), 'gameType': game['gameType'],
             'teams': payload['liveData']['boxscore']['teams'], 'receipt': receipt,
             'venue': payload['gameData'].get('venue', {}), 'homeWon': game['teams']['home']['isWinner']}
    return value


def statcast(day):
    query = {'all': 'true', 'type': 'details', 'game_date_gt': day, 'game_date_lt': day,
             'group_by': 'name', 'player_type': 'pitcher'}
    raw, receipt = fetch('https://baseballsavant.mlb.com/statcast_search/csv?'+urlencode(query), raw=True)
    reader = csv.DictReader(io.StringIO(raw))
    required = {'game_pk', 'at_bat_number', 'pitch_number', 'pitcher', 'batter', 'type', 'pitch_type', 'release_speed', 'launch_speed'}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError('invalid Statcast schema')
    rows, seen = [], set()
    keep = required | {'estimated_woba_using_speedangle', 'events', 'game_date', 'p_throws', 'stand'}
    for row in reader:
        identity = tuple(count(row[k]) for k in ('game_pk', 'at_bat_number', 'pitch_number'))
        if identity in seen or row.get('game_date') != day:
            raise ValueError('duplicate pitch or Statcast date mismatch')
        seen.add(identity)
        rows.append({k: row.get(k) for k in keep})
    return {'date': day, 'rows': rows, 'receipt': receipt}


def statcast_player(rows, identity, role):
    chosen = [r for r in rows if str(r[role]) == str(identity)]
    contacts = [r for r in chosen if r['type'] == 'X']
    speed = [number(r['launch_speed']) for r in contacts]
    xwoba = [number(r.get('estimated_woba_using_speedangle')) for r in contacts]
    velocities = [number(r['release_speed']) for r in chosen]
    def average(values):
        return sum(values)/len(values) if values and all(v is not None for v in values) else None
    return {'pitches': len(chosen), 'fairContacts': len(contacts),
            'hardHitRate': sum(v >= 95 for v in speed)/len(speed) if speed and all(v is not None for v in speed) else None,
            'xwobaOnContact': average(xwoba), 'meanVelocity': average(velocities) if role == 'pitcher' else None,
            'pitchMix': {k: v/len(chosen) for k, v in Counter(r['pitch_type'] for r in chosen if r['pitch_type']).items()}}


def markets(games):
    key = os.environ.get('ODDS_API_KEY')
    if not key:
        raise ValueError('odds source key unavailable')
    payload, receipt = fetch('https://api.the-odds-api.com/v4/sports/baseball_mlb/odds?'+urlencode(
        {'regions': 'us,us2,uk,eu,au', 'markets': 'h2h', 'oddsFormat': 'decimal', 'apiKey': key}))
    result = {}
    for game in games:
        home, away = (game['teams'][s]['team']['name'] for s in ('home', 'away'))
        matches = [e for e in payload if e['home_team'] == home and e['away_team'] == away
                   and utc(e['commence_time']) == utc(game['gameDate'])]
        if len(matches) != 1:
            continue
        pairs = []
        for book in matches[0].get('bookmakers', []):
            for market in book.get('markets', []):
                prices = {o['name']: number(o['price']) for o in market.get('outcomes', [])}
                if market['key'] == 'h2h' and set(prices) == {home, away} and all(v and v > 1 for v in prices.values()):
                    at = utc(market.get('last_update') or book['last_update'])
                    age = (utc(receipt['retrievedAtUtc'])-at).total_seconds()
                    if 0 <= age <= 900:
                        h, a = 1/prices[home], 1/prices[away]
                        pairs.append({'book': book['key'], 'homeProbability': h/(h+a), 'sourceAtUtc': at.isoformat()})
        if pairs:
            result[str(game['gamePk'])] = {'marketHomeProbability': sum(p['homeProbability'] for p in pairs)/len(pairs),
                                         'books': pairs, 'receipt': receipt}
    return result
