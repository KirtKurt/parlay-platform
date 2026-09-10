"""Compact optional add-ons from retained, past-only evidence."""
from collections import defaultdict
from datetime import timedelta
import gzip
import hashlib
import json

import numpy as np
import pandas as pd

from ks1.features import day, number, utc
from ks1.inventory import encode
from ks1.table import team_identity

SIDES = ('home', 'away')
VALUES = [f'{s}_starter_expected_ip' for s in SIDES] + [f'{s}_starter_expected_pitches' for s in SIDES] + [
    'home_opener', 'away_opener', 'platoon_advantage_count_diff', 'middle_order_absent_count_diff',
    'defense_oaa_diff', 'defense_drs_diff', 'park_hr_factor_lhb', 'park_hr_factor_rhb', 'outdoor_forecast_temp_f']
ADDONS = VALUES + [v+'_missing' for v in VALUES] + ['home_starter_projection_missing', 'away_starter_projection_missing']


def load_archive(folder):
    receipt = json.loads((folder/'receipt.json').read_bytes())
    for name, sha in receipt['files'].items():
        if '/' in name or hashlib.sha256((folder/name).read_bytes()).hexdigest() != sha:
            raise ValueError('archive capture hash mismatch')
    return json.loads(gzip.decompress((folder/'archive.json.gz').read_bytes())), receipt


def snapshots_for(row, snapshots):
    valid = []
    for s in snapshots:
        if str(s.get('officialGamePk')) != str(row['game_id']): continue
        if s.get('originalObservation') is not True or s.get('outcomeKnownAtCapture') is not False: continue
        if not (utc(s['capturedAtUtc']) <= utc(row['as_of_timestamp'])
                and utc(s['capturedAtUtc']) <= utc(s['featureCutoffUtc']) <= utc(row['commence_time'])-timedelta(minutes=10)
                and utc(s['commenceTime']) == utc(row['commence_time'])): continue
        if hashlib.sha256(encode(s['features'])).hexdigest() != s['featureFingerprint']:
            raise ValueError('snapshot feature fingerprint mismatch')
        teams = s.get('playerWindows', {}).get('teams', {})
        if not all(str(teams.get(side, {}).get('teamId')) == str(row[side+'_id']) for side in SIDES): continue
        receipts = [s.get('feedReceipt', {}), *s.get('playerWindows', {}).get('receipts', [])]
        if not receipts or any(not r.get('retrievedAtUtc') or utc(r['retrievedAtUtc']) > utc(s['capturedAtUtc']) for r in receipts): continue
        valid.append(s)
    return sorted(valid, key=lambda s: utc(s['capturedAtUtc']))


def projected_ip(row, record, side):
    """A retained rotation projection is evidence of an estimate, not an identity.

    Context manifests do not retain sample counts. Treat the exported estimate
    as one pseudo-observation and shrink it 75% toward the team workload prior.
    """
    if not record: return None
    s = record.get('snapshot', {})
    e = s.get('featureEvidence', {}).get('pitchers', {})
    p = s.get('providerEvidence', {}).get('pitchers', {})
    if hashlib.sha256(encode({k:v for k,v in s.items() if k != 'fingerprint'})).hexdigest() != s.get('fingerprint'):
        raise ValueError('context snapshot fingerprint mismatch')
    if not (s.get('pointInTimeVerified') is True and s.get('sameDayResultsExcluded') is True
            and s.get('targetGameOutcomeUsed') is False and s.get('selectionUsedOutcomes') is False
            and s.get('postgameFieldsExcluded') is True and s.get('featureEligibility', {}).get('pitchers') is True
            and e.get('availabilityMode') == 'strict_prior_projection' and e.get('eligible') is True
            and e.get('pointInTimeProjectionVerified') is True and p.get('pointInTimeProjectionVerified') is True
            and e.get('sourceEffectiveAtUtc') and utc(e['sourceEffectiveAtUtc']) < utc(row['as_of_timestamp'])
            and p.get('sourceEffectiveAtUtc') and utc(p['sourceEffectiveAtUtc']) < utc(row['as_of_timestamp'])
            and str(record.get('officialGamePk')) == str(row['game_id'])
            and all(record.get(x+'Team') == row[x+'_team'] for x in SIDES)):
        return None
    value = number(s.get(side, {}).get('starterExpectedInnings'))
    return value if value is not None and value <= 9 else None


def workload_rows(games):
    result = []
    for g in games:
        if g.get('gameType') not in ('R', 'F', 'D', 'L', 'W'): continue
        for side in SIDES:
            team = g['teams'][side]
            full = 'teamStats' in team
            starters = [p for p in team.get('players', {}).values() if p.get('stats', {}).get('pitching', {}).get('gamesStarted') == 1]
            stats = starters[0]['stats']['pitching'] if full and len(starters) == 1 else team.get('priorStarters', {}) if not full else {}
            outs, pitches = number(stats.get('outs')), number(stats.get('numberOfPitches'))
            result.append({'game_id': str(g['officialGamePk']), 'date': str(day(g['startAtUtc'])),
                           'completed': utc(g['completedAtUtc']), 'team_id': team_identity(team)[0],
                           'starter_id': str(starters[0]['person']['id']) if len(starters) == 1 else None,
                           'ip': outs/3 if outs is not None else None, 'pitches': pitches,
                           'venue_id': str(g.get('venue', {}).get('id', ''))})
    return pd.DataFrame(result)


def estimate_workload(row, history, side, projection=None):
    tid, pid = str(row[side+'_id']), row.get(side+'_starter_id')
    recent = history.loc[(history.date < row['date']) & (history.date >= str(day(row['commence_time'])-timedelta(days=30)))
                         & (history.completed < utc(row['as_of_timestamp']))]
    result, sources = {}, []
    for field in ('ip', 'pitches'):
        known = recent.dropna(subset=[field])
        prior = float(known[field].mean()) if len(known) else None
        own = known.loc[known.team_id == tid]
        team_prior = (float(own[field].sum())+5*prior)/(len(own)+5) if prior is not None else None
        starter = known.loc[known.starter_id == str(pid)] if pid else known.iloc[:0]
        if len(starter) and team_prior is not None:
            value = (float(starter[field].sum())+3*team_prior)/(len(starter)+3)
            sources.append('observed_starter_prior_starts')
        elif field == 'ip' and projection is not None and team_prior is not None:
            value = (projection+3*team_prior)/4
            sources.append('archived_rotation_projection_shrunk_to_team')
        else:
            value = team_prior
            sources.append('team_prior' if team_prior is not None else 'unavailable')
        result[side+'_starter_expected_'+field] = value
    result[side+'_starter_projection_missing'] = float(projection is None and not pid)
    return result, sources


def lineup_features(row, snaps):
    result = {'platoon_advantage_count_diff': None, 'middle_order_absent_count_diff': None,
              'outdoor_forecast_temp_f': None}
    if not snaps: return result
    s = snaps[-1]; teams = s['playerWindows']['teams']; counts, absent = {}, {}
    for side in SIDES:
        own, other = teams[side], teams['away' if side == 'home' else 'home']
        order = own.get('battingOrder') or []
        players = {str(p['id']): p for p in own.get('players', [])}
        opposing = [p for p in other.get('players', []) if str(p['id']) == str(other.get('starterId'))]
        hand = opposing[0].get('pitchHand') if len(opposing) == 1 else None
        hands = [players.get(str(pid), {}).get('batSide') for pid in order]
        confirmed = own.get('lineupConfirmed') is True and len(order) == 9 and len(set(order)) == 9
        if confirmed and hand in ('L','R') and all(h in ('L','R','S') for h in hands):
            counts[side] = sum(h == 'S' or h != hand for h in hands)
        # Only earlier pregame orders define expected 2–5 hitters. Last game's
        # order and a postgame box are not evidence of a current scratch.
        earlier = [p['playerWindows']['teams'][side] for p in snaps[:-1]
                   if p['playerWindows']['teams'][side].get('lineupConfirmed') is True
                   and len(set(p['playerWindows']['teams'][side].get('battingOrder') or [])) == 9]
        if confirmed and earlier:
            absent[side] = len(set(earlier[0]['battingOrder'][1:5])-set(order))
    for target, values in [('platoon_advantage_count_diff',counts),('middle_order_absent_count_diff',absent)]:
        if len(values) == 2: result[target] = float(values['home']-values['away'])
    conditions = s.get('conditions', {}); f = conditions.get('features', {})
    receipts = [r for r in conditions.get('receipts', []) if '/forecast/' in r.get('endpoint','')]
    if (f.get('roofOpenObserved') == 1 and f.get('roofClosedObserved') != 1 and receipts
            and all(r.get('retrievedAtUtc') and utc(r['retrievedAtUtc']) <= utc(s['capturedAtUtc']) for r in receipts)):
        result['outdoor_forecast_temp_f'] = number(f.get('forecastTemperatureF'))
    return result


def first_five(game):
    """Only complete recorded first-five innings can supply these targets."""
    innings = game.get('linescore', {}).get('innings', [])
    chosen = [v for v in innings if v.get('num') in (1,2,3,4,5)]
    if len(chosen) != 5 or {v['num'] for v in chosen} != {1,2,3,4,5}: return None
    totals = []
    for side in SIDES:
        runs = [number(i.get(side, {}).get('runs')) for i in chosen]
        if any(v is None or not float(v).is_integer() for v in runs): return None
        totals.append(sum(runs))
    return totals


def park_contacts(bundle, games):
    by_id = {str(g['officialGamePk']): g for g in games}
    values = []
    for p in bundle.get('statcast', {}).get('rows', []):
        g = by_id.get(str(p.get('game_pk')))
        if not g or g.get('gameType') not in ('R','F','D','L','W') or not g.get('venue', {}).get('id') or not p.get('events') or p.get('stand') not in ('L','R'): continue
        values.append({'game_id': str(p['game_pk']), 'pa': str(p['at_bat_number']), 'hand': p['stand'],
                       'hr': int(p['events'] == 'home_run'), 'venue': str(g['venue']['id']),
                       'date': str(day(g['startAtUtc'])), 'completed': utc(g['completedAtUtc'])})
    frame = pd.DataFrame(values, columns=['game_id','pa','hand','hr','venue','date','completed'])
    if frame.duplicated(['game_id','pa']).any(): raise ValueError('duplicate completed plate appearance')
    return frame


def build(frame, bundle):
    frame = frame.copy()
    games = {str(g['officialGamePk']): g for g in bundle['compact']}
    games.update({str(g['officialGamePk']): g for g in bundle['prior']['games']})
    history = workload_rows(games.values())
    contacts = park_contacts(bundle, list(games.values()))
    contexts = {str(r['officialGamePk']): r for c in bundle.get('contexts', []) if '/historical-context/' in c['key']
                for r in c['payload'].get('records', [])}
    schedule = {str(g['gamePk']): g for g in bundle['prior'].get('schedule', [])}
    appearances = defaultdict(set)
    for r in frame.to_dict('records'):
        for side in SIDES: appearances[(r['date'],str(r[side+'_id']))].add(r['game_id'])
    records, sources = [], defaultdict(int)
    for row in frame.to_dict('records'):
        value = {k: None for k in VALUES}
        snaps = snapshots_for(row, bundle.get('snapshots', []))
        for side in SIDES:
            # Base table starter IDs already require original pregame evidence.
            projection = projected_ip(row, contexts.get(str(row['game_id'])), side)
            work, provenance = estimate_workload(row, history, side, projection)
            value.update(work)
            for p in provenance:sources[p] += 1
        value.update(lineup_features(row, snaps))
        if len(contacts) and row.get('park_id'):
            valid = contacts.loc[(contacts.date < row['date']) & (contacts.completed < utc(row['as_of_timestamp']))]
            for hand, name in [('L','park_hr_factor_lhb'),('R','park_hr_factor_rhb')]:
                league = valid.loc[valid.hand == hand]; park = league.loc[league.venue == str(row['park_id'])]
                if len(league) and len(park) and league.hr.sum() > 0:
                    prior = league.hr.mean()
                    value[name] = float((park.hr.sum()+1000*prior)/(len(park)+1000)/prior)
        for name in VALUES:value[name+'_missing'] = float(value[name] is None)
        sch = schedule.get(str(row['game_id']), {})
        multiple = any(len(appearances[(row['date'],str(row[s+'_id']))]) > 1 for s in SIDES)
        value['doubleheader_status'] = ('flagged' if multiple or sch.get('doubleHeader') in ('Y','S')
                                         else 'official_single' if sch.get('doubleHeader') == 'N' else 'unknown')
        value['bullpen_game_status'] = 'unknown_no_archived_scheduled_role'
        value['season_status'] = 'excluded_2020' if row['season'] == 2020 else 'eligible_season'
        f5 = first_five(sch)
        value.update(f5_home_runs=f5[0] if f5 else None, f5_away_runs=f5[1] if f5 else None)
        records.append(value)
    extra = pd.DataFrame(records, index=frame.index)
    for column in extra: frame[column] = extra[column]
    coverage = {'by_season': {str(y): {c: int(g[c].notna().sum()) for c in VALUES} for y,g in frame.groupby('season')},
                'core_unchanged': True, 'new_feature_columns': len(ADDONS), 'workload_sources': dict(sources),
                'doubleheader_status': frame.doubleheader_status.value_counts().to_dict(),
                'bullpen_game_status': frame.bullpen_game_status.value_counts().to_dict(),
                'season_2020_rows': int((frame.season == 2020).sum()),
                'f5_label_games': int(frame.f5_home_runs.notna().sum()),
                'archive_sources': {'compact_games': len(bundle['compact']), 'full_games': len(bundle['prior']['games']),
                                    'original_snapshots': len(bundle.get('snapshots', [])), 'rotation_projection_records': len(contexts)},
                'limitations': ['Retrospective prior statistics can contain later scoring corrections.',
                    'Archived rotation projections are estimates, not observed current starter identities; sample counts were not retained.',
                    'Opener/scheduled bullpen flags require explicit pregame evidence, not short realized outings.',
                    'Defense rating in legacy context is runs allowed, not OAA or DRS; it is excluded.',
                    'Platoon and 2-5 absences require original pregame lineup evidence; postgame orders are excluded.',
                    'Handed park factors are HR/PA ratios shrunk with 1000 league PA, not handed run factors.',
                    'Only an archived forecast at a park observed open/outdoors can contribute weather; realized weather is never read.',
                    'F5 labels require inning 1-5 home and away run records; full-game scores cannot substitute.']}
    return frame, coverage
