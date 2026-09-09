"""All active pitchers and batters, exact calendar windows and honest missingness."""
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlencode
import mlb_research_sources_v1 as source
from mlb_research_store_v1 import utc

VERSION = 'MLB-PLAYER-WINDOWS-v1-active-roster-original-observation'
PITCHING = ('outs', 'earnedRuns', 'hits', 'baseOnBalls', 'strikeOuts', 'battersFaced', 'numberOfPitches')
BATTING = ('atBats', 'hits', 'doubles', 'triples', 'homeRuns', 'baseOnBalls', 'hitByPitch', 'sacFlies', 'strikeOuts', 'plateAppearances')


def rates(stats, role):
    def ratio(a, b):
        return a/b if b else None
    if role == 'pitching':
        return {'era': ratio(27*stats['earnedRuns'], stats['outs']),
                'whip': ratio(3*(stats['hits']+stats['baseOnBalls']), stats['outs']),
                'kMinusBbPct': ratio(100*(stats['strikeOuts']-stats['baseOnBalls']), stats['battersFaced'])}
    ab, hits = stats['atBats'], stats['hits']
    obp = ratio(hits+stats['baseOnBalls']+stats['hitByPitch'], ab+stats['baseOnBalls']+stats['hitByPitch']+stats['sacFlies'])
    slg = ratio(hits+stats['doubles']+2*stats['triples']+3*stats['homeRuns'], ab)
    avg = ratio(hits, ab)
    return {'avg': avg, 'obp': obp, 'slg': slg,
            'ops': obp+slg if obp is not None and slg is not None else None,
            'iso': slg-avg if slg is not None and avg is not None else None,
            'kPct': ratio(100*stats['strikeOuts'], stats['plateAppearances']),
            'bbPct': ratio(100*stats['baseOnBalls'], stats['plateAppearances'])}


def logs(person, role, completed_games, cutoff):
    blocks = [s for s in person.get('stats', []) if s.get('type', {}).get('displayName') == 'gameLog'
              and s.get('group', {}).get('displayName') == role]
    if not blocks:
        return None
    result, seen = [], set()
    lower = cutoff.astimezone(source.ET).date()-timedelta(days=30)
    for block in blocks:
        for split in block.get('splits', []):
            if split.get('gameType') not in source.GAME_TYPES or str(split.get('sport', {}).get('id', 1)) != '1':
                continue
            day = date.fromisoformat(split['date'])
            if day < lower:
                continue
            pk = source.count(split['game']['gamePk'])
            if pk in seen:
                raise ValueError('duplicate player game log')
            seen.add(pk)
            official = completed_games.get(pk)
            # Only independently completed games. Same-day game one must have
            # an end timestamp before the current game-two observation cutoff.
            if not official:
                raise ValueError('player log missing independently completed source')
            if utc(official['completedAtUtc']) >= cutoff:
                continue
            if utc(official['startAtUtc']).astimezone(source.ET).date() != day:
                raise ValueError('player game-log date mismatch')
            keys = PITCHING if role == 'pitching' else BATTING
            values = {k: source.count(split['stat'][k]) for k in keys}
            result.append({'gamePk': pk, 'date': day.isoformat(), 'stats': values})
    return result


def aggregate(entries, role, cutoff, days):
    if entries is None:
        return {'status': 'INCOMPLETE', 'appearances': None, 'stats': None, 'rates': {}}
    # Inclusive current ET date, through completed games only.
    upper = cutoff.astimezone(source.ET).date()
    lower = upper-timedelta(days=days-1)
    chosen = [e for e in entries if lower <= date.fromisoformat(e['date']) <= upper]
    keys = PITCHING if role == 'pitching' else BATTING
    totals = {k: sum(e['stats'][k] for e in chosen) for k in keys}
    return {'status': 'OBSERVED' if chosen else 'NO_APPEARANCES', 'appearances': len(chosen),
            'stats': totals, 'rates': rates(totals, role), 'gameIds': [e['gamePk'] for e in chosen]}


def pooled(players, window, role):
    selected = [p[role][str(window)+'d'] for p in players]
    if not selected or any(s['stats'] is None for s in selected):
        return {}
    keys = PITCHING if role == 'pitching' else BATTING
    totals = {k: sum(s['stats'][k] for s in selected) for k in keys}
    return {**rates(totals, role), **totals}


def observe(game, payload, completed_games, cutoff):
    if payload['gameData']['status']['abstractGameState'] != 'Preview':
        raise ValueError('player observation requires pregame feed')
    day = cutoff.astimezone(source.ET).date().isoformat()
    receipts, teams = [], {}
    boxes = payload['liveData']['boxscore']['teams']
    for side in ('home', 'away'):
        tid = game['teams'][side]['team']['id']
        roster, receipt = source.fetch(source.API+f'/v1/teams/{tid}/roster?'+urlencode({'rosterType': 'active', 'date': day}))
        receipts.append(receipt)
        members = roster.get('roster')
        if not isinstance(members, list) or not 20 <= len(members) <= 40:
            raise ValueError('active roster unavailable')
        ids = [source.count(r['person']['id']) for r in members]
        if len(set(ids)) != len(ids) or 0 in ids:
            raise ValueError('invalid active roster identities')
        order = boxes[side].get('battingOrder', [])
        confirmed = (len(order) == 9 and len(set(order)) == 9 and set(order).issubset(ids))
        if confirmed:
            for slot, pid in enumerate(order, 1):
                player = boxes[side].get('players', {}).get('ID'+str(pid), {})
                if (player.get('person', {}).get('id') != pid or str(player.get('battingOrder')) != str(100*slot)
                        or player.get('gameStatus', {}).get('isSubstitute') is not False):
                    confirmed = False
        # Date-bounded hydration keeps full-roster collection to one request
        # per team per season; late-year requests can include postseason logs.
        people = {}
        lower = cutoff.astimezone(source.ET).date()-timedelta(days=30)
        for season in sorted({lower.year, cutoff.year}):
            hydrate = f'stats(group=[pitching,hitting],type=[gameLog],season={season},startDate={lower},endDate={day})'
            data, receipt = source.fetch(source.API+'/v1/people?'+urlencode({'personIds': ','.join(map(str, ids)), 'hydrate': hydrate}))
            receipts.append(receipt)
            returned = data.get('people', [])
            if len({p['id'] for p in returned}) != len(returned) or any(p['id'] not in ids for p in returned):
                raise ValueError('ambiguous player response')
            for person in returned:
                previous = people.get(person['id'])
                people[person['id']] = {**person, 'stats': [*(previous or {}).get('stats', []), *person.get('stats', [])]}
        starter = payload['gameData'].get('probablePitchers', {}).get(side, {}).get('id')
        players = []
        for member in members:
            pid = member['person']['id']
            person = people.get(pid, {})
            position = member.get('position', {}).get('abbreviation')
            pitcher = position in ('P', 'TWP') or pid == starter
            value = {'id': pid, 'name': member['person'].get('fullName'), 'position': position,
                     'pitcher': pitcher, 'probableStarter': pid == starter,
                     'lineupSlot': order.index(pid)+1 if confirmed and pid in order else None,
                     'pitchHand': person.get('pitchHand', {}).get('code'), 'batSide': person.get('batSide', {}).get('code')}
            for role in (('pitching', 'hitting') if pitcher else ('hitting',)):
                try:
                    entries = logs(person, role, completed_games, cutoff)
                except (ValueError, KeyError, TypeError):
                    entries = None
                value[role] = {str(n)+'d': aggregate(entries, role, cutoff, n) for n in (7, 15, 30)}
                if role == 'pitching':
                    value['workload'] = {}
                    for n in (1, 3, 5):
                        # Previous n calendar days plus completed games today.
                        usage = aggregate(entries, role, cutoff, n+1)
                        value['workload'][str(n)+'d'] = {'pitches': (usage['stats'] or {}).get('numberOfPitches'),
                                                       'outs': (usage['stats'] or {}).get('outs')}
            players.append(value)
        teams[side] = {'teamId': tid, 'activeRosterCount': len(ids), 'lineupConfirmed': confirmed,
                       'battingOrder': order if confirmed else None, 'starterId': starter, 'players': players}
    return {'version': VERSION, 'teams': teams, 'receipts': receipts}


def features(observation):
    result = {}
    for side, team in observation['teams'].items():
        players = team['players']
        pitchers = [p for p in players if p['pitcher']]
        starter = [p for p in pitchers if p['probableStarter']]
        bullpen = [p for p in pitchers if not p['probableStarter']]
        lineup = [p for p in players if p['lineupSlot']] if team['lineupConfirmed'] else []
        other='away' if side=='home' else 'home'
        opposing=[p for p in observation['teams'][other]['players'] if p['probableStarter']]
        hand=opposing[0].get('pitchHand') if len(opposing)==1 else None
        result[side+'LineupPlatoonAdvantageFraction']=(sum(p['batSide']=='S' or p['batSide']!=hand for p in lineup)/9
            if len(lineup)==9 and hand in ('L','R') and all(p.get('batSide') in ('L','R','S') for p in lineup) else None)
        result[side+'LineupConfirmed'] = float(team['lineupConfirmed'])
        result[side+'IncompletePitcherWindows'] = sum(p['pitching']['30d']['status'] == 'INCOMPLETE' for p in pitchers)
        for n in (7, 15, 30):
            for group, members, role, keys in (('Starter', starter, 'pitching', ('era', 'whip', 'kMinusBbPct', 'outs')),
                    ('Bullpen', bullpen, 'pitching', ('era', 'whip', 'kMinusBbPct', 'outs')),
                    ('Lineup', lineup, 'hitting', ('ops', 'iso', 'obp', 'slg', 'kPct', 'bbPct'))):
                values = pooled(members, n, role)
                for key in keys:
                    result[f'{side}{group}{key[0].upper()+key[1:]}{n}d'] = values.get(key)
        for n in (1, 3, 5):
            vals = [p['workload'][str(n)+'d']['pitches'] for p in bullpen]
            result[f'{side}BullpenPitches{n}d'] = sum(vals) if vals and all(v is not None for v in vals) else None
        for group, metric in (('Starter', 'Era'), ('Starter', 'KMinusBbPct'), ('Bullpen', 'Era'), ('Lineup', 'Ops'), ('Lineup', 'Iso')):
            for n in (7, 15):
                a, b = result.get(f'{side}{group}{metric}{n}d'), result.get(f'{side}{group}{metric}30d')
                result[f'{side}{group}{metric}{n}vs30d'] = a-b if a is not None and b is not None else None
    for key in list(result):
        if key.startswith('home') and 'away'+key[4:] in result:
            h, a = result[key], result['away'+key[4:]]
            result[key[4:]+'GapHome'] = h-a if h is not None and a is not None else None
    return result
