"""Phase 6: retained bullpen process statistics and guarded park environment."""
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import math

import numpy as np
import pandas as pd

from ks1.accuracy_features import snapshots_for
from ks1.features import day, number, utc
from ks1.inventory import encode
from ks1.table import team_identity

SIDES = ('home', 'away')
BULLPEN = ('rest_days', 'bb_per_9_30d', 'k_bb_pct_30d', 'relievers_used_2d')
VALUES = [f'{s}_phase6_bullpen_{f}' for s in SIDES for f in BULLPEN] + [
    'phase6_static_park_run_factor', 'phase6_park_run_factor']
FEATURES = VALUES + [c+'_missing' for c in VALUES]


def finite(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def pitching_totals(players, field):
    values = [number(p.get(field)) for p in players]
    return sum(values) if all(v is not None for v in values) else None


def bullpen_history(bundle):
    """Never read seasonStats. Each row is one team's relief in a past game."""
    compact = {str(g['officialGamePk']): g for g in bundle['compact']}
    full = {str(g['officialGamePk']): g for g in bundle['prior']['games']}
    games = {**compact, **full}
    rows, pitchers = [], []
    proof = {'full_team_walk_identities_verified': 0, 'compact_full_walk_checks': 0,
             'compact_full_walk_differences': 0, 'invalid_walk_decompositions': 0}
    for pk, g in games.items():
        if g.get('gameType') not in ('R', 'F', 'D', 'L', 'W'):
            continue
        for side in SIDES:
            opponent = 'away' if side == 'home' else 'home'
            team, other = g['teams'][side], g['teams'][opponent]
            is_full = 'teamStats' in team
            identity = {'game_id': pk, 'team_id': team_identity(team)[0],
                        'date': str(day(g['startAtUtc'])), 'completed': utc(g['completedAtUtc'])}
            k = bf = None
            if is_full:
                entries = [(str(p['person']['id']), p.get('stats', {}).get('pitching', {}))
                           for p in team.get('players', {}).values()]
                entries = [(pid, p) for pid, p in entries
                           if any((number(p.get(c)) or 0) > 0 for c in ('outs', 'numberOfPitches', 'battersFaced'))]
                classified = all(p.get('gamesStarted') in (0, 1) for _, p in entries)
                relief = [(pid, p) for pid, p in entries if p.get('gamesStarted') == 0]
                starters = [p for _, p in entries if p.get('gamesStarted') == 1]
                if not classified or not starters:
                    outs = pitches = bb = None
                else:
                    stats = [p for _, p in relief]
                    outs, pitches, bb, k, bf = [pitching_totals(stats, field) for field in
                        ('outs', 'numberOfPitches', 'baseOnBalls', 'strikeOuts', 'battersFaced')]
                    starter_bb = pitching_totals(starters, 'baseOnBalls')
                    total_bb = number(other.get('teamStats', {}).get('batting', {}).get('baseOnBalls'))
                    if total_bb is not None and starter_bb is not None and bb is not None:
                        if total_bb-starter_bb != bb:
                            proof['invalid_walk_decompositions'] += 1
                            bb = None
                        else:
                            proof['full_team_walk_identities_verified'] += 1
                    for pid, p in relief:
                        pitchers.append({**identity, 'player_id': pid})
            else:
                outs, pitches = [number(team.get('relief', {}).get(c)) for c in ('outs', 'pitches')]
                total_bb = number(other.get('batting', {}).get('baseOnBalls'))
                starter_bb = number(team.get('priorStarters', {}).get('baseOnBalls'))
                bb = total_bb-starter_bb if total_bb is not None and starter_bb is not None else None
                if bb is not None and bb < 0:
                    proof['invalid_walk_decompositions'] += 1
                    bb = None
            if is_full and pk in compact and bb is not None:
                c = compact[pk]['teams']; total = number(c[opponent]['batting'].get('baseOnBalls'))
                starter = number(c[side]['priorStarters'].get('baseOnBalls'))
                if total is not None and starter is not None:
                    proof['compact_full_walk_checks'] += 1
                    proof['compact_full_walk_differences'] += int(total-starter != bb)
            rows.append({**identity, 'outs': outs, 'pitches': pitches, 'bb': bb, 'k': k, 'bf': bf,
                         'full': is_full, 'role_complete': classified if is_full else False})
    columns = ['game_id', 'team_id', 'date', 'completed', 'outs', 'pitches', 'bb', 'k', 'bf', 'full', 'role_complete']
    return pd.DataFrame(rows, columns=columns), pd.DataFrame(pitchers, columns=[
        'game_id', 'team_id', 'date', 'completed', 'player_id']), proof


def bullpen_at(row, history, players, side):
    prefix = side+'_phase6_bullpen_'
    result = {prefix+field: None for field in BULLPEN}
    today = date.fromisoformat(row['date'])
    cutoff = utc(row['as_of_timestamp'])
    prior = history.loc[(history.date < row['date']) & (history.date >= str(today-timedelta(days=75)))
                        & history.date.str.startswith(str(row['season'])) & (history.completed < cutoff)
                        & (history.game_id != str(row['game_id']))]
    own = prior.loc[prior.team_id == str(row[side+'_id'])]
    # The accepted table tracks known missing final boxes. A conservative
    # 75-day guard also protects the shorter workload windows; never call an
    # unobserved outing a day of rest or assume a missing relief appearance zero.
    known_gaps = row.get(side+'_missing_history_boxes_75d')
    if known_gaps is None or pd.isna(known_gaps) or known_gaps > 0:
        return result, 'unavailable_known_or_unknown_history_gaps'
    if own.empty:
        return result, 'unavailable_no_prior_team_history'
    usage = own.dropna(subset=['outs', 'pitches'])
    appearances = usage.loc[(usage.outs > 0) | (usage.pitches > 0)]
    if len(usage) == len(own) and len(appearances):
        last = date.fromisoformat(appearances.date.max())
        result[prefix+'rest_days'] = float((today-last).days-1)
    recent = own.loc[own.date >= str(today-timedelta(days=30))]
    q = recent.dropna(subset=['bb', 'outs']); league = prior.dropna(subset=['bb', 'outs'])
    if len(q) == len(recent) and len(q) and q.outs.sum() > 0 and league.outs.sum() > 0:
        # Walks/9 = 27*BB/outs. Shrink 30 innings (90 outs) toward the
        # independently completed current-season league relief rate.
        prior_rate = league.bb.sum()/league.outs.sum()
        result[prefix+'bb_per_9_30d'] = float(27*(q.bb.sum()+90*prior_rate)/(q.outs.sum()+90))
    q = recent.dropna(subset=['bb', 'k', 'bf']); league = prior.dropna(subset=['bb', 'k', 'bf'])
    if len(q) == len(recent) and len(q) and q.bf.sum() > 0 and league.bf.sum() > 0:
        prior_rate = (league.k.sum()-league.bb.sum())/league.bf.sum()
        result[prefix+'k_bb_pct_30d'] = float(100*(q.k.sum()-q.bb.sum()+100*prior_rate)/(q.bf.sum()+100))
    last_two = own.loc[own.date >= str(today-timedelta(days=2))]
    if own.full.any() and last_two.full.all() and last_two.role_complete.all():
        used = players.loc[(players.team_id == str(row[side+'_id'])) & players.game_id.isin(last_two.game_id)]
        result[prefix+'relievers_used_2d'] = float(used.player_id.nunique())
    return result, ('recent_player_usage_proxy_roster_unverified' if result[prefix+'relievers_used_2d'] is not None
                    else 'team_usage_only_roster_unverified')


def context_park(row, record):
    if not record:
        return None
    s = record.get('snapshot', {})
    if hashlib.sha256(encode({k:v for k,v in s.items() if k != 'fingerprint'})).hexdigest() != s.get('fingerprint'):
        raise ValueError('park context fingerprint mismatch')
    e, p = [s.get(k, {}).get('park', {}) for k in ('featureEvidence', 'providerEvidence')]
    if not (str(record.get('officialGamePk')) == str(row['game_id'])
            and all(record.get(side+'Team') == row[side+'_team'] for side in SIDES)
            and s.get('pointInTimeVerified') is True and s.get('sameDayResultsExcluded') is True
            and s.get('postgameFieldsExcluded') is True and s.get('targetGameOutcomeUsed') is False
            and s.get('selectionUsedOutcomes') is False and s.get('featureEligibility', {}).get('park') is True
            and e.get('eligible') is True and e.get('pointInTimeProjectionVerified') is True
            and all(x.get('sourceEffectiveAtUtc') and utc(x['sourceEffectiveAtUtc']) < utc(row['as_of_timestamp']) for x in (e, p))):
        return None
    value = number(s.get('parkRunFactor'))
    if value is None or not .7 <= value <= 1.3:
        return None
    # Source implementation requires >=10 prior venue games, but the retained
    # snapshot lost its exact count. Use that lower bound as support and a
    # 20-game neutral prior, rather than inventing the missing sample size.
    return (10*value+20)/30


def environment_at(row, snapshots, context):
    snaps = snapshots_for(row, snapshots)
    static, source, temp = context_park(row, context), 'strict_prior_context_shrunk', None
    if static is None:
        source = 'unavailable'
    if snaps:
        latest = snaps[-1]
        conditions = latest.get('conditions', {})
        f = conditions.get('features', {})
        ratio, n = number(f.get('recentVenueScoringRatio')), number(f.get('observedVenueRunSample'))
        if ratio is not None and ratio > 0 and n is not None and n >= 5:
            static, source = (n*ratio+20)/(n+20), 'original_prior_venue_snapshot_shrunk'
        receipts = [r for r in conditions.get('receipts', []) if '/forecast/' in r.get('endpoint', '')]
        if (f.get('roofOpenObserved') == 1 and f.get('roofClosedObserved') != 1 and receipts
                and all(r.get('retrievedAtUtc') and utc(r['retrievedAtUtc']) <= utc(latest['capturedAtUtc']) for r in receipts)):
            temp = finite(f.get('forecastTemperatureF'))
    adjusted = static
    if static is not None and temp is not None:
        # Temperature-only component of the existing weather_run_factor in
        # hello_world/mlb_v8_historical_point_in_time_context_v1.py. This is a
        # bounded inherited heuristic, not a newly fitted physical model. No
        # invented wind/humidity inputs, and never realized game-time weather.
        adjusted = static*min(max(1+(temp-70)*.002, .85), 1.15)
        status = 'outdoor_forecast_temperature_adjusted_heuristic'
    else:
        status = 'static_only_weather_unavailable' if static is not None else 'static_park_unavailable'
    return {'phase6_static_park_run_factor': static, 'phase6_park_run_factor': adjusted,
            'phase6_park_source': source, 'phase6_environment_status': status,
            'phase6_weather_missing': float(temp is None)}


def build_phase6(frame, bundle):
    history, players, proof = bullpen_history(bundle)
    contexts = {str(r['officialGamePk']): r for c in bundle.get('contexts', []) if '/historical-context/' in c['key']
                for r in c['payload'].get('records', [])}
    records = []
    for row in frame.to_dict('records'):
        extra = {}
        for side in SIDES:
            values, status = bullpen_at(row, history, players, side)
            extra.update(values)
            extra[side+'_phase6_bullpen_status'] = status
        extra.update(environment_at(row, bundle.get('snapshots', []), contexts.get(str(row['game_id']))))
        for field in VALUES:
            extra[field+'_missing'] = float(extra[field] is None)
        records.append(extra)
    result = frame.copy()
    for c, values in pd.DataFrame(records, index=frame.index).items():
        result[c] = values
    return result, {**proof, 'team_history_rows': len(history), 'reliever_appearance_rows': len(players),
        'environment_status': result.phase6_environment_status.value_counts().to_dict(),
        'bullpen_status': {s: result[s+'_phase6_bullpen_status'].value_counts().to_dict() for s in SIDES},
        'new_numeric_contract_columns': len(FEATURES), 'weather_adjustment': 'Existing bounded temperature-only heuristic; forecasts with verified outdoor/as-of evidence only.'}
