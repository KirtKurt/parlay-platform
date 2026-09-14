"""KS1 daily inference: frozen LightGBM winner and dual-Poisson run models."""
import argparse
from datetime import date as calendar_date, timedelta
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import statistics

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ks1.features import (Features, STATCAST_METRIC_NAMES, day, pitcher_context,
                          starter_matchup, utc)
from ks1.inventory import encode
from ks1.poisson import home_probability, predict_exported
from ks1.publish import parquet_bytes
from ks1.refresh import change_reason, fingerprint, observe, pregame_status
from ks1.passive_context import (CONTRACT as LINEUP_BULLPEN_CONTRACT,
                                 SUPPORTED_FROZEN_CONTRACTS,
                                 LINEUP_PERFORMANCE_FEATURES,
                                 BULLPEN_PERFORMANCE_FEATURES,
                                 MODEL_FEATURES as LINEUP_BULLPEN_FEATURES,
                                 build_profile as lineup_bullpen_profile)
from ks1.table import american, team_identity

PREFIX = 'mlb/ks1/predictions-v1/'
ROOT = Path(__file__).parent
FLOATS = ['p_home', 'lambda_home', 'lambda_away', 'proj_total', 'market_home_prob', 'edge_home',
          'market_total', 'edge_total', 'market_spread', 'p_home_poisson', 'odds_match_confidence', 'p_raw']
STRINGS = ['date', 'game_id', 'bbs_game_id', 'odds_event_id', 'home_team', 'away_team', 'home_id', 'away_id',
           'bbs_home_id', 'bbs_away_id', 'home_starter_name', 'away_starter_name', 'home_starter_id', 'away_starter_id',
           'home_starter_status', 'away_starter_status', 'starter_source', 'commence_time', 'model_version',
           'as_of', 'lineup_status', 'prediction_status', 'market_status', 'history_source_as_of',
           'environment_status', 'history_status', 'input_fingerprint', 'status',
           'home_lineup_status', 'away_lineup_status', 'home_lineup_ids', 'away_lineup_ids',
           'home_offense_source', 'away_offense_source', 'lineup_source_status', 'starter_feature_source', 'calibration_version', 'calibration_method']
STRINGS += ['signal_contributions_json']
STRINGS += ['starter_profile_contract', 'starter_profile_sha256',
            'starter_profile_semantic_sha256', 'starter_profile_json']
STRINGS += ['lineup_bullpen_profile_contract', 'lineup_bullpen_profile_sha256',
            'lineup_bullpen_profile_semantic_sha256', 'lineup_bullpen_profile_json',
            'lineup_bullpen_profile_status']
SCHEMA = pa.schema([pa.field(k, pa.dictionary(pa.int32(), pa.string()) if k == 'date' else pa.string())
                    for k in STRINGS] + [pa.field(k, pa.float64()) for k in FLOATS])
STARTER_PROFILE_CONTRACT = 'KS1-starter-profile-v2'
STATCAST_METRICS = ('complete', 'pitches', 'hard_hit_pct', 'barrel_pct', 'avg_ev_allowed',
                    'xwoba_contact', 'xwoba', 'xwoba_pa', 'swstr_pct', 'csw_pct', 'velocity',
                    'spin', 'horizontal_break_in', 'vertical_break_in', 'extension', 'fly_balls',
                    'active_spin_pct', 'stuff_plus', 'location_plus', 'pitching_plus')


def mask_starter_sources(values, coverage):
    """Preserve proven official results independently of Savant availability."""
    values = dict(values)
    pitch_metrics = set(STATCAST_METRIC_NAMES) | {'xfip'}
    for window in ('7d', '10d', '30d', 'last3', 'prior_year'):
        source_window = '30d' if window == '10d' else window
        for key in values:
            if key.startswith('starter_') and key.endswith('_'+window):
                metric = key.removeprefix('starter_').removesuffix('_'+window)
                source = source_window if metric in pitch_metrics else 'results_'+source_window
                if not coverage.get(source):
                    values[key] = None
    context_prior = values.get('starter_context_basis_code') in (1.0, 2.0, 3.0)
    if (not coverage.get('current_season_context')
            or (context_prior and not coverage.get('results_prior_year'))):
        values.update({key: None for key in values if key.startswith('starter_context_')})
        values['starter_expected_innings_last5'] = None
    recent = {name: values.get('starter_'+name+'_30d') for name in STATCAST_METRIC_NAMES}
    prior = {name: values.get('starter_'+name+'_prior_year') for name in STATCAST_METRIC_NAMES}
    for metric in ('velocity', 'spin', 'extension', 'ff_velocity', 'ff_spin'):
        values['starter_'+metric+'_talent'] = Features.talent(recent, prior, metric)
    return values


def starter_profile(row, features, as_of, history_as_of):
    """Persist the full pregame starter inputs even before model promotion."""
    profile = {
        'contract': STARTER_PROFILE_CONTRACT,
        'as_of': as_of,
        'history_as_of': history_as_of,
        'source_roles': {
            'starter_identity': row['starter_source'],
            'results_and_counts': 'official_MLB_completed_game_logs',
            'cold_start_context': 'explicit_prior_year_pitcher_or_pregame_league_starter_prior',
            'contact_physics_and_arsenal': 'retained_Baseball_Savant_pitch_rows',
            'fixture_crosscheck': 'Big_Balls_Data_matches_only',
            'market_context': 'The_Odds_API_only',
        },
        'coverage': row.get('_pitcher_history_coverage', {}),
        'sides': {},
    }
    for side in ('home', 'away'):
        prefix = side+'_starter_'
        metrics = {key.removeprefix(prefix): value for key, value in sorted(features.items())
                   if key.startswith(prefix)}
        unavailable = sorted(key for key, value in metrics.items()
                             if value is None and any(name in key for name in
                                ('xera', 'siera', 'stuff_plus', 'location_plus',
                                 'pitching_plus', 'active_spin_pct')))
        def window_status(window):
            if row[side+'_starter_id'] is None:
                return 'MISSING_STARTER'
            if not row.get('_pitcher_history_coverage', {}).get(window):
                return 'SOURCE_INCOMPLETE'
            observed = (metrics.get('starts_observed_last3') == 3 if window == 'last3'
                        else (metrics.get('appearances_'+window) or 0) > 0)
            if not observed:
                return 'NO_APPEARANCES_OR_INCOMPLETE'
            return ('COMPLETE' if metrics.get('complete_'+window) == 1
                    else 'SOURCE_INCOMPLETE')
        profile['sides'][side] = {
            'context_basis': {0.0: 'current_season_pitcher', 1.0: 'prior_year_pitcher',
                              2.0: 'current_season_league_prior', 3.0: 'prior_year_league_prior'}.get(
                                  metrics.get('context_basis_code'), 'unavailable'),
            'starter_id': row[side+'_starter_id'],
            'starter_name': row[side+'_starter_name'],
            'starter_status': row[side+'_starter_status'],
            'metrics': metrics,
            'window_statuses': {window: window_status(window)
                                for window in ('7d', '30d', 'last3', 'prior_year')},
            'results_window_statuses': {
                window: ('MISSING_STARTER' if row[side+'_starter_id'] is None
                         else 'SOURCE_INCOMPLETE' if not profile['coverage'].get('results_'+window)
                         else 'COMPLETE' if metrics.get('era_'+window) is not None
                         else 'NO_APPEARANCES_OR_INCOMPLETE')
                for window in ('7d', '30d', 'last3', 'prior_year')},
            'unavailable_exact_metrics': unavailable,
        }
    semantic = {key: value for key, value in profile.items() if key not in ('as_of', 'history_as_of')}
    profile['semantic_sha256'] = hashlib.sha256(encode(semantic)).hexdigest()
    body = encode(profile)
    return {**profile, 'sha256': hashlib.sha256(body).hexdigest()}


def name_key(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


def signal_contributions(classifier, values):
    """Return signed LightGBM log-odds contributions and absolute influence."""
    if not classifier.__class__.__module__.startswith('lightgbm'):
        return [None]*len(values)
    try:
        matrix = np.asarray(classifier.predict(values, pred_contrib=True), dtype=float)
    except (TypeError, ValueError, AttributeError):
        return [None]*len(values)
    names = classifier.feature_name()
    if matrix.shape != (len(values), len(names)+1) or not np.isfinite(matrix).all():
        raise ValueError('invalid LightGBM contribution matrix')
    raw_scores = np.asarray(classifier.predict(values, raw_score=True), dtype=float)
    if (raw_scores.shape != (len(values),)
            or not np.isfinite(raw_scores).all()
            or not np.allclose(matrix.sum(axis=1), raw_scores, atol=1e-8, rtol=1e-8)):
        raise ValueError('LightGBM contributions do not reconstruct raw prediction')
    inputs = np.asarray(values, dtype=float)
    performance_names = {side+'_'+name for side in ('home', 'away')
                         for name in LINEUP_PERFORMANCE_FEATURES+BULLPEN_PERFORMANCE_FEATURES}
    def group(name):
        if name.startswith('market_'): return 'market'
        if '_lineup_' in name: return 'batters'
        if '_bullpen_' in name: return 'bullpen'
        if '_starter_' in name or '_pitcher_context_' in name: return 'starter'
        if '_offense_' in name or name.endswith(('_rest_days', '_history_games')): return 'team_form'
        return 'other'
    result = []
    for row_number, vector in enumerate(matrix):
        grouped, grouped_abs = {}, {}
        evidence = {}
        features = []
        for column, (name, score) in enumerate(zip(names, vector[:-1])):
            bucket = group(name)
            grouped[bucket] = grouped.get(bucket, 0.0)+float(score)
            grouped_abs[bucket] = grouped_abs.get(bucket, 0.0)+abs(float(score))
            features.append({'feature': name, 'score': float(score)})
            if bucket in ('batters', 'bullpen'):
                value = inputs[row_number, column]
                kind = ('missingness_indicator' if name.endswith('_missing')
                        else 'missing_value' if not np.isfinite(value)
                        else 'observed_performance' if name in performance_names
                        else 'other_observed_context')
                entry = evidence.setdefault(bucket, {}).setdefault(kind, {
                    'signal_score': 0.0, 'absolute_contribution': 0.0,
                    'nonzero_features': 0, 'top_features': []})
                entry['signal_score'] += float(score)
                entry['absolute_contribution'] += abs(float(score))
                if score != 0:
                    entry['nonzero_features'] += 1
                    entry['top_features'].append({'feature': name, 'score': float(score),
                                                  'value': float(value) if np.isfinite(value) else None})
        for buckets in evidence.values():
            for entry in buckets.values():
                entry['top_features'] = sorted(entry['top_features'],
                    key=lambda item: abs(item['score']), reverse=True)[:5]
        denominator = sum(grouped_abs.values())
        result.append({'schema_version': 2, 'scale': 'raw_log_odds_SHAP', 'bias': float(vector[-1]),
                       'raw_score': float(raw_scores[row_number]), 'additivity_verified': True,
                       'performance_evidence': evidence,
                       'groups': {key: {'signal_score': value,
                                        'decision_influence_pct': 100*grouped_abs[key]/denominator if denominator else 0.0}
                                  for key, value in sorted(grouped.items())},
                       'top_features': sorted(features, key=lambda item: abs(item['score']), reverse=True)[:10]})
    return result


def upgrade_signal_evidence(classifier, features, previous):
    """Explain identical pre-cutoff inputs without changing stored probabilities."""
    existing = json.loads(previous['signal_contributions_json']) if previous.get('signal_contributions_json') else {}
    if existing.get('schema_version') == 2 or previous.get('p_raw') is None:
        return previous
    values = pd.DataFrame([features])[classifier.feature_name()].astype(float)
    proof = signal_contributions(classifier, values)[0]
    if proof is None:
        return previous
    probability = float(classifier.predict(values)[0])
    if not np.isclose(probability, float(previous['p_raw']), atol=1e-12, rtol=0):
        raise ValueError('explanation upgrade does not match retained raw probability')
    return {**previous, 'signal_contributions_json': encode(proof).decode()}


class Crosswalk:
    """Only exact aliases learned from existing official IDs; never fuzzy guess."""
    def __init__(self, history, schedule):
        self.aliases, self.bbs_ids, self.rows = {}, {}, []
        self.game_time_adjustments = []
        for g in history:
            for t in g['teams'].values():
                self.add(*team_identity(t))
        for g in schedule:
            for t in g['teams'].values():
                self.add(*team_identity(t))

    def add(self, team_id, name):
        self.aliases.setdefault(name_key(name), set()).add(str(team_id))

    def resolve(self, name):
        ids = self.aliases.get(name_key(name), set())
        return next(iter(ids)) if len(ids) == 1 else None

    def bind(self, bbs_id, official_id, name):
        old = self.bbs_ids.get(str(bbs_id))
        if old and old != str(official_id):
            raise ValueError('conflicting BBS/MLB team crosswalk')
        if not old:
            self.bbs_ids[str(bbs_id)] = str(official_id)
            self.rows.append({'bbs_id': str(bbs_id), 'mlb_id': str(official_id), 'observed_name': name,
                              'method': 'unique_exact_alias_and_game_start', 'confidence': 1.0})


def bbs_assignments(payload, schedule, crosswalk, target_date):
    data = payload.get('data')
    if not isinstance(data, list):
        raise ValueError('BBS matches require data array; scores are not match identities')
    if len(data) >= 200:
        raise ValueError('BBS result may be truncated; refuse partial catalogue')
    ids, assigned = set(), {}
    for event in data:
        if not all(event.get(k) for k in ('id', 'kickoff_utc', 'home', 'away')):
            raise ValueError('BBS match identity schema changed')
        if str(day(event['kickoff_utc'])) != target_date:
            continue
        if event['sport'].lower() != 'baseball' or event['league'].lower() != 'mlb':
            raise ValueError('non-MLB BBS event')
        if event['id'] in ids:
            raise ValueError('duplicate BBS match ID')
        ids.add(event['id'])
        sides = {s: crosswalk.resolve(event[s]['name']) for s in ('home', 'away')}
        same_teams = [g for g in schedule
                      if str(day(g['gameDate'])) == target_date
                      and all(sides[s] == str(g['teams'][s]['team']['id']) for s in sides)]
        matches = [g for g in same_teams
                   if abs((utc(event['kickoff_utc'])-utc(g['gameDate'])).total_seconds()) <= 90]
        if not matches and len(same_teams) == 1:
            game = same_teams[0]
            provider_fixtures = [e for e in data
                                 if str(day(e['kickoff_utc'])) == target_date
                                 and all(crosswalk.resolve(e[s]['name']) == sides[s] for s in sides)]
            delta = abs((utc(event['kickoff_utc'])-utc(game['gameDate'])).total_seconds())
            if (len(provider_fixtures) == 1 and delta <= 300
                    and game.get('doubleHeader') == 'N'
                    and game.get('status', {}).get('startTimeTBD') is False):
                matches = [game]
                crosswalk.game_time_adjustments.append({
                    'game_id': str(game['gamePk']), 'bbs_game_id': str(event['id']),
                    'bbs_start': event['kickoff_utc'], 'official_start': game['gameDate'],
                    'difference_seconds': delta,
                    'method': 'unique_exact_teams_single_game_within_5_minutes'})
        if len(matches) != 1:
            raise ValueError('ambiguous or unmatched BBS game identity: ' + str(event['id']))
        pk = str(matches[0]['gamePk'])
        if pk in assigned:
            raise ValueError('multiple BBS IDs map to one official game')
        assigned[pk] = event
        for side in sides:
            crosswalk.bind(event[side]['id'], sides[side], event[side]['name'])
    return assigned


def market_for(game, events, crosswalk, as_of):
    candidates = [e for e in events if all(crosswalk.resolve(e[s+'_team']) == str(game['teams'][s]['team']['id'])
                  for s in ('home', 'away')) and abs((utc(e['commence_time'])-utc(game['gameDate'])).total_seconds()) <= 90]
    empty = {'market_home_prob': None, 'market_total': None, 'market_spread': None,
             'odds_event_id': None, 'odds_match_confidence': None, 'market_status': 'unavailable'}
    if len(candidates) > 1:
        return {**empty, 'market_status': 'ambiguous_event'}
    if not candidates:
        return empty
    event = candidates[0]
    probabilities, totals, spreads, seen_books = [], [], [], set()
    for book in event.get('bookmakers', []):
        if book.get('key') in seen_books:
            raise ValueError('duplicate odds bookmaker')
        seen_books.add(book.get('key'))
        seen_markets = set()
        for market in book.get('markets', []):
            kind = market.get('key')
            if kind in seen_markets:
                raise ValueError('duplicate odds market')
            seen_markets.add(kind)
            at = market.get('last_update') or book.get('last_update')
            if not at or not 0 <= (utc(as_of)-utc(at)).total_seconds() <= 900:
                continue
            values = market.get('outcomes', [])
            if len(values) != 2:
                continue
            if kind in ('h2h', 'spreads'):
                home = [r for r in values if crosswalk.resolve(r['name']) == str(game['teams']['home']['team']['id'])]
                away = [r for r in values if crosswalk.resolve(r['name']) == str(game['teams']['away']['team']['id'])]
                if len(home) != 1 or len(away) != 1:
                    continue
                if kind == 'h2h':
                    h, a = american(home[0].get('price')), american(away[0].get('price'))
                    if h and a:
                        probabilities.append(h/(h+a))
                else:
                    h, a = home[0].get('point'), away[0].get('point')
                    if all(isinstance(x, (int, float)) and np.isfinite(x) for x in (h, a)) and h == -a:
                        spreads.append(float(h))
            elif kind == 'totals':
                pairs = {r.get('name'): r.get('point') for r in values}
                t = pairs.get('Over')
                if isinstance(t, (int, float)) and np.isfinite(t) and t > 0 and t == pairs.get('Under'):
                    totals.append(float(t))
    return {**empty, 'market_home_prob': statistics.mean(probabilities) if probabilities else None,
            'market_total': statistics.median(totals) if totals else None,
            'market_spread': statistics.median(spreads) if spreads else None, 'odds_event_id': str(event['id']),
            'odds_match_confidence': 1.0, 'market_status': 'available' if probabilities else 'missing_fresh_h2h'}


def load_inputs(folder):
    manifest = json.loads((folder/'capture.json').read_bytes())
    if manifest.get('system') != 'KS1' or manifest.get('errors'):
        raise ValueError('incomplete KS1 capture')
    required = {'bbs.json', 'odds.json', 'official.json', 'history.json.gz', 'model.txt'}
    if manifest.get('phase', 4) >= 5:
        required.add('feeds.json')
    required.update(name for name in ('feeds.json', 'passive_context.json', 'previous.parquet') if (folder/name).exists())
    if required - set(manifest['files']):
        raise ValueError('unbound capture input')
    for name, expected in manifest['files'].items():
        if Path(name).name != name or hashlib.sha256((folder/name).read_bytes()).hexdigest() != expected:
            raise ValueError('capture file hash mismatch')
    values = {}
    for provider in ('bbs', 'odds', 'official'):
        value = json.loads((folder/(provider+'.json')).read_bytes())
        if hashlib.sha256(encode(value['payload'])).hexdigest() != value['receipt']['sha256']:
            raise ValueError('provider payload hash mismatch')
        age = (utc(manifest['as_of'])-utc(value['receipt']['as_of'])).total_seconds()
        if not 0 <= age <= 900:
            raise ValueError('capture contains stale or future provider data')
        values[provider] = value
    values['history'] = json.loads(gzip.decompress((folder/'history.json.gz').read_bytes()))
    values['feeds'] = json.loads((folder/'feeds.json').read_bytes()) if (folder/'feeds.json').exists() else {'games': {}}
    values['passive_context'] = (json.loads((folder/'passive_context.json').read_bytes())
                                 if (folder/'passive_context.json').exists()
                                 else {'status': 'UNAVAILABLE_FAIL_CLOSED', 'games': {}})
    return manifest, values


def preserve_frozen(current, previous, target_date, as_of):
    if previous is None or previous.empty:
        return current
    if previous.game_id.duplicated().any() or set(previous.date) != {target_date}:
        raise ValueError('invalid previous date predictions')
    if any(utc(t) > utc(as_of) for t in previous.as_of):
        raise ValueError('refuse to overwrite a newer prediction snapshot')
    frozen = previous.loc[previous.commence_time.map(lambda t: utc(t)-timedelta(minutes=10) < utc(as_of))]
    if any(utc(r.as_of) > utc(r.commence_time)-timedelta(minutes=10) for r in frozen.itertuples()):
        raise ValueError('previous prediction violated pregame cutoff')
    return pd.concat([current.loc[~current.game_id.isin(frozen.game_id)], frozen], ignore_index=True)


def validate_preservation(previous, current, as_of, withdrawals=()):
    keyed = {(r['date'], r['game_id']): r for r in current}
    if len(keyed) != len(current):
        raise ValueError('duplicate publication game IDs')
    allowed = {(r['date'], r['game_id']) for r in withdrawals
               if r['reason'] in ('Postponed', 'Cancelled')}
    legacy_defaults = {'status', 'lineup_status', 'lineup_source_status', 'starter_feature_source',
                       'home_lineup_status', 'away_lineup_status', 'home_lineup_ids', 'away_lineup_ids',
                       'home_offense_source', 'away_offense_source'}
    for old in previous:
        key = old['date'], old['game_id']
        new = keyed.get(key)
        if utc(old['as_of']) > utc(as_of):
            raise ValueError('refuse stale prediction publication')
        frozen = utc(old['commence_time'])-timedelta(minutes=10) < utc(as_of)
        if frozen:
            fields = set(old) - (legacy_defaults if not old.get('status') else set())
            if new is None or any(new.get(k) != old[k] for k in fields):
                raise ValueError('refuse changed or missing frozen prediction: '+old['game_id'])
        elif new is None and key not in allowed:
            raise ValueError('refuse unexpected published prediction removal: '+old['game_id'])


def predict(folder, output):
    manifest, inputs = load_inputs(folder)
    target_date, as_of = manifest['date'], manifest['as_of']
    if calendar_date.fromisoformat(target_date).isoformat() != target_date:
        raise ValueError('invalid date')
    refs = json.loads((ROOT/'model_refs.json').read_bytes())
    model_bytes = (folder/'model.txt').read_bytes()
    poisson_bytes = (ROOT/refs['poisson']['file']).read_bytes()
    if hashlib.sha256(model_bytes).hexdigest() != refs['lightgbm']['sha256'] or hashlib.sha256(poisson_bytes).hexdigest() != refs['poisson']['sha256']:
        raise ValueError('accepted model hash mismatch')
    classifier = lgb.Booster(model_str=model_bytes.decode())
    poisson = json.loads(poisson_bytes)
    needed = set(classifier.feature_name()) | set(poisson['home']['features']) | set(poisson['away']['features'])
    individual_learned = any(name.startswith(side+'_starter_')
                             for name in classifier.feature_name() for side in ('home', 'away'))
    pitcher_context_learned = [name for name in classifier.feature_name()
                               if any(name.startswith(side+'_pitcher_context_')
                                      for side in ('home', 'away'))]
    model_version = 'KS1-LGB-'+refs['lightgbm']['sha256'][:12]+'-DP-'+refs['poisson']['sha256'][:12]
    official = inputs['official']['payload']
    schedule = [g for d in official['dates'] for g in d['games']]
    if len(schedule) != official['totalGames'] or len({g['gamePk'] for g in schedule}) != len(schedule):
        raise ValueError('incomplete or duplicate official schedule')
    schedule = [g for g in schedule if str(day(g['gameDate'])) == target_date and g['gameType'] in ('R', 'F', 'D', 'L', 'W')]
    history = inputs['history']['games']
    crosswalk = Crosswalk(history, schedule)
    assignments = bbs_assignments(inputs['bbs']['payload'], schedule, crosswalk, target_date)
    engine = Features(history, inputs['history'].get('statcast', []),
                      statcast_complete=inputs['history'].get('statcast_coverage_complete') is True,
                      prior_statcast_profiles=inputs['history'].get('prior_statcast_profiles'),
                      statcast_retained_dates=inputs['history'].get('statcast_retained_dates', []),
                      prior_statcast_year=inputs['history'].get('prior_statcast_year'))
    rows, feature_rows, exclusions = [], [], []
    previous = pq.ParquetFile(folder/'previous.parquet').read().to_pandas() if (folder/'previous.parquet').exists() else None
    frozen = preserve_frozen(previous.iloc[:0], previous, target_date, as_of) if previous is not None else pd.DataFrame()
    frozen_ids = set(frozen.game_id) if len(frozen) else set()
    prior_rows = {r['game_id']: r for r in pq.ParquetFile(folder/'previous.parquet').read().to_pylist()} if previous is not None else {}
    retained, changes, unchanged, withdrawals = [], [], [], []
    provider_status_disagreement_retained = []
    upgraded_signal_evidence = []
    migrated_frozen = []
    for pk in sorted(frozen_ids):
        row = dict(prior_rows[pk])
        if not row.get('status'):
            migrated_frozen.append(row['game_id'])
            row.update(status=row.get('prediction_status') or 'projected', lineup_status='projected',
                       lineup_source_status='legacy_projected', starter_feature_source='team_starter_prior')
            for side in ('home', 'away'):
                row.update({side+'_lineup_status': 'projected', side+'_lineup_ids': None, side+'_offense_source': 'team_prior'})
        retained.append(row)
    history_ids = {str(g['officialGamePk']) for g in history}
    missing_boxes = [g for g in inputs['history'].get('schedule', []) if str(g['gamePk']) not in history_ids
                     and g.get('status', {}).get('abstractGameState') == 'Final'
                     and str(day(g['gameDate'])) < target_date]
    for game in sorted(schedule, key=lambda g: (g['gameDate'], str(g['gamePk']))):
        pk, start = str(game['gamePk']), utc(game['gameDate'])
        if pk in frozen_ids:
            continue
        if (not pregame_status(game.get('status'))
                or utc(as_of) > start-timedelta(minutes=10)):
            exclusions.append({'game_id': pk, 'reason': 'not_scheduled_before_T10'})
            if game['status'].get('detailedState') in ('Postponed', 'Cancelled'):
                withdrawals.append({'date': target_date, 'game_id': pk,
                                    'reason': game['status']['detailedState']})
            continue
        if pk not in assignments:
            raise ValueError('scheduled official game missing from BBS: '+pk)
        bbs = assignments[pk]
        if bbs['status'].lower() != 'scheduled':
            old = prior_rows.get(pk)
            if old:
                retained.append(old)
                provider_status_disagreement_retained.append(pk)
                exclusions.append({'game_id': pk,
                                   'reason': 'provider_pregame_status_disagreement_retained',
                                   'official_detailed_state': game.get('status', {}).get('detailedState'),
                                   'bbs_status': bbs.get('status')})
                continue
            raise ValueError('BBS and official pregame status disagree: '+pk)
        row = {'date': target_date, 'game_id': pk, 'bbs_game_id': bbs['id'], 'commence_time': start.isoformat(),
               'model_version': model_version, 'as_of': as_of,
               'environment_status': 'unavailable_in_accepted_models',
               'history_status': 'available_retained_history',
               'history_source_as_of': inputs['history'].get('prior_observed_at')}
        coverage = {
            'results_7d': inputs['history'].get('current30_history_complete') is True,
            'results_30d': inputs['history'].get('current30_history_complete') is True,
            'results_last3': (inputs['history'].get('current_year_history_complete') is True
                              and inputs['history'].get('prior_year_history_complete') is True),
            'results_prior_year': inputs['history'].get('prior_year_history_complete') is True,
            '7d': (inputs['history'].get('current30_history_complete') is True
                   and inputs['history'].get('statcast_coverage_complete') is True),
            '30d': (inputs['history'].get('current30_history_complete') is True
                    and inputs['history'].get('statcast_coverage_complete') is True),
            'last3': (inputs['history'].get('current_year_history_complete') is True
                      and inputs['history'].get('prior_year_history_complete') is True
                      and inputs['history'].get('current_year_statcast_complete') is True
                      and inputs['history'].get('prior_year_statcast_complete') is True),
            'current_season_context': inputs['history'].get('current_year_history_complete') is True,
            'prior_year': (inputs['history'].get('prior_year_history_complete') is True
                           and inputs['history'].get('prior_year_statcast_complete') is True),
            'statcast_30d': inputs['history'].get('statcast_coverage_complete') is True,
            'player_history_complete': (
                inputs['history'].get('current_year_history_complete') is True
                and inputs['history'].get('prior_year_history_complete') is True
                and not any(day(g['gameDate']).year in (
                    calendar_date.fromisoformat(target_date).year-1,
                    calendar_date.fromisoformat(target_date).year) for g in missing_boxes)),
        }
        row['_pitcher_history_coverage'] = coverage
        if not all(coverage.values()):
            row['history_status'] = 'partial_pitcher_history_fail_closed'
        row.update(observe(game, inputs['feeds']['games'].get(pk), as_of))
        features = {side+'_'+name: (1.0 if name.endswith('_missing') else None)
                    for side in ('home', 'away') for name in LINEUP_BULLPEN_FEATURES}
        for side in ('home', 'away'):
            team = game['teams'][side]
            tid, name = team_identity(team)
            pid = row[side+'_starter_id']
            row.update({side+'_id': tid, side+'_team': name, 'bbs_'+side+'_id': str(bbs[side]['id'])})
            side_values = mask_starter_sources(engine.at(as_of, tid, pid, game_date=target_date), coverage)
            side_values.update({'pitcher_context_'+key: value for key, value in pitcher_context(side_values).items()})
            features.update({side+'_'+k: v for k, v in side_values.items()})
            opposing = 'away' if side == 'home' else 'home'
            features.update({side+'_starter_'+key: value
                             for key, value in starter_matchup(row.get('_'+side+'_starter_pitch_hand'),
                                                               row.get('_'+opposing+'_lineup_bat_sides')).items()})
            gaps = [g for g in missing_boxes if any(str(t['team']['id']) == tid for t in g['teams'].values())]
            for window in (1, 3, 5):
                if any(0 < (calendar_date.fromisoformat(target_date)-day(g['gameDate'])).days <= window for g in gaps):
                    for stat in ('pitches', 'outs'):
                        features[f'{side}_bullpen_{stat}_{window}d'] = None
            if gaps:
                row['history_status'] = 'partial_known_missing_boxes'
        passive = inputs['passive_context'].get('games', {}).get(pk)
        if passive is None:
            row['lineup_bullpen_profile_status'] = ('SOURCE_UNAVAILABLE_FAIL_CLOSED'
                                                    if inputs['passive_context'].get('status') != 'READ'
                                                    else 'GAME_OBSERVATION_MISSING_FAIL_CLOSED')
        else:
            try:
                context_profile, context_features = lineup_bullpen_profile(
                    passive, game, row, as_of, engine,
                    inputs['history'].get('prior_observed_at'),
                    inputs['history'].get('statcast_observed_at'), coverage)
            except (KeyError, TypeError, ValueError) as exc:
                reason = re.sub(r'[^A-Z0-9]+', '_', str(exc).upper()).strip('_')
                row['lineup_bullpen_profile_status'] = 'INVALID_FAIL_CLOSED:'+reason[:120]
            else:
                features.update(context_features)
                row.update(lineup_bullpen_profile_contract=LINEUP_BULLPEN_CONTRACT,
                           lineup_bullpen_profile_sha256=context_profile['sha256'],
                           lineup_bullpen_profile_semantic_sha256=context_profile['semantic_sha256'],
                           lineup_bullpen_profile_json=encode(context_profile).decode(),
                           lineup_bullpen_profile_status=context_profile['coverage_status'])
                for side in ('home', 'away'):
                    row.update({side+'_lineup_status': 'confirmed',
                                side+'_lineup_ids': encode(context_profile['sides'][side]['lineup_ids']).decode(),
                                side+'_offense_source': 'persisted_MLB_lineup_season_batting'})
                row.update(lineup_status='confirmed', lineup_source_status='verified_persisted_pregame_observation')
                if all(row[side+'_starter_id'] is not None for side in ('home', 'away')):
                    row.update(status='confirmed_lineups', prediction_status='confirmed_lineups')
        row.update(market_for(game, inputs['odds']['payload'], crosswalk, as_of))
        features.update({key: row[key] for key in ('market_home_prob', 'market_total', 'market_spread')})
        if individual_learned:
            row['starter_feature_source'] = 'individual_and_team_starter_history'
            if any(not features.get(side+'_starter_bf_30d') for side in ('home', 'away')):
                row['starter_feature_source'] = 'individual_history_partial_with_team_prior'
        if needed - set(features):
            raise ValueError('inference feature contract missing: '+','.join(sorted(needed-set(features))))
        profile = starter_profile(row, features, as_of, inputs['history'].get('prior_observed_at'))
        row.update(starter_profile_contract=STARTER_PROFILE_CONTRACT,
                   starter_profile_sha256=profile['sha256'],
                   starter_profile_semantic_sha256=profile['semantic_sha256'],
                   starter_profile_json=encode(profile).decode())
        row['input_fingerprint'] = fingerprint(row, features, needed)
        old = prior_rows.get(pk)
        if old and old.get('status') and old.get('input_fingerprint') == row['input_fingerprint']:
            upgraded = upgrade_signal_evidence(classifier, features, old)
            if upgraded is not old:
                upgraded_signal_evidence.append(pk)
            retained.append(upgraded); unchanged.append(pk); continue
        changes.append({'game_id': pk, 'reason': change_reason(old, row)})
        rows.append(row); feature_rows.append(features)
    if rows:
        features = pd.DataFrame(feature_rows)
        x = features[classifier.feature_name()].astype(float)
        p_home = classifier.predict(x)
        contributions = signal_contributions(classifier, x)
        h, a = [predict_exported(poisson[side], features) for side in ('home', 'away')]
        p_poisson, _ = home_probability(h, a)
        if not (np.isfinite(p_home).all() and (p_home >= 0).all() and (p_home <= 1).all()):
            raise ValueError('invalid LightGBM probabilities')
        for i, row in enumerate(rows):
            row.update(p_home=float(p_home[i]), lambda_home=float(h[i]), lambda_away=float(a[i]),
                       proj_total=float(h[i]+a[i]), p_home_poisson=float(p_poisson[i]),
                       edge_home=float(p_home[i]-row['market_home_prob']) if row['market_home_prob'] is not None else None,
                       edge_total=float(h[i]+a[i]-row['market_total']) if row['market_total'] is not None else None)
            row['signal_contributions_json'] = encode(contributions[i]).decode() if contributions[i] is not None else None
    current = pa.Table.from_pylist(rows+retained, schema=SCHEMA).to_pandas()
    frame = current.sort_values(['commence_time', 'game_id']).reset_index(drop=True)
    table = pa.Table.from_pandas(frame, schema=SCHEMA, preserve_index=False)
    validate_preservation(list(prior_rows.values()), table.to_pylist(), as_of, withdrawals)
    if frame.game_id.duplicated().any() or (len(frame) and set(frame.date) != {target_date}):
        raise ValueError('duplicate or wrong-date predictions')
    if len(frame):
        if frame.status.isna().any() or frame.lineup_status.isna().any():
            raise ValueError('every prediction requires status and lineup_status')
        rates = frame[['lambda_home', 'lambda_away', 'proj_total']].to_numpy(float)
        if not (np.isfinite(rates).all() and (rates > 0).all()):
            raise ValueError('invalid predicted runs')
        np.testing.assert_allclose(frame.proj_total, frame.lambda_home+frame.lambda_away, atol=1e-12, rtol=0)
        for record in frame.loc[frame.starter_profile_json.notna()].itertuples():
            profile = json.loads(record.starter_profile_json)
            claimed = profile.pop('sha256')
            semantic_claimed = profile['semantic_sha256']
            semantic = {key: value for key, value in profile.items()
                        if key not in ('as_of', 'history_as_of', 'semantic_sha256')}
            if (record.starter_profile_contract != STARTER_PROFILE_CONTRACT
                    or record.starter_profile_sha256 != claimed
                    or record.starter_profile_semantic_sha256 != semantic_claimed
                    or hashlib.sha256(encode(semantic)).hexdigest() != semantic_claimed
                    or hashlib.sha256(encode(profile)).hexdigest() != claimed):
                raise ValueError('starter profile binding failed')
        for record in frame.loc[frame.lineup_bullpen_profile_json.notna()].itertuples():
            profile = json.loads(record.lineup_bullpen_profile_json)
            claimed = profile.pop('sha256')
            semantic_claimed = profile.pop('semantic_sha256')
            semantic = {key: value for key, value in profile.items() if key != 'as_of'}
            if (record.lineup_bullpen_profile_contract not in SUPPORTED_FROZEN_CONTRACTS
                    or profile.get('contract') != record.lineup_bullpen_profile_contract
                    or record.lineup_bullpen_profile_sha256 != claimed
                    or record.lineup_bullpen_profile_semantic_sha256 != semantic_claimed
                    or hashlib.sha256(encode(semantic)).hexdigest() != semantic_claimed
                    or hashlib.sha256(encode({**profile, 'semantic_sha256': semantic_claimed})).hexdigest() != claimed
                    or profile['game_id'] != record.game_id
                    or utc(profile['as_of']) != utc(record.as_of)
                    or (profile.get('history_as_of') and utc(profile['history_as_of']) > utc(profile['as_of']))
                    or (profile.get('statcast_as_of') and utc(profile['statcast_as_of']) > utc(profile['as_of']))
                    or utc(profile['as_of']) > utc(record.commence_time)-timedelta(minutes=10)):
                raise ValueError('lineup/bullpen profile binding failed')
    output = output / ('date='+target_date); output.mkdir(parents=True, exist_ok=True)
    body = parquet_bytes(table)
    (output/'predictions.parquet').write_bytes(body)
    if not pq.ParquetFile(output/'predictions.parquet').read().equals(table):
        raise ValueError('local parquet readback mismatch')
    frame.to_csv(output/'predictions.csv', index=False)
    odds_rows = [{'event_id': str(e['id']), 'as_of': inputs['odds']['receipt']['as_of'], 'payload_json': encode(e).decode()}
                 for e in inputs['odds']['payload']]
    odds_table = pa.Table.from_pylist(odds_rows, schema=pa.schema([(k, pa.string()) for k in ('event_id', 'as_of', 'payload_json')]))
    (output/'odds_cache.parquet').write_bytes(parquet_bytes(odds_table))
    (output/'lineup_cache.json').write_bytes(encode(inputs['feeds']))
    (output/'crosswalk.json').write_bytes(encode({'teams': crosswalk.rows, 'fuzzy_matches': 0,
                                               'game_time_adjustments': crosswalk.game_time_adjustments}))
    report = {'system': 'KS1', 'phase': 5, 'date': target_date, 'as_of': as_of, 'model_version': model_version,
              'learned_feature_names': classifier.feature_name(),
              'individual_starter_features_learned': individual_learned,
              'pitcher_context_features_learned': pitcher_context_learned,
              'lineup_bullpen_features_learned': [name for name in classifier.feature_name()
                                                   if name in {side+'_'+feature for side in ('home', 'away')
                                                               for feature in LINEUP_BULLPEN_FEATURES}],
              'lineup_bullpen_profile_rows': int(frame.lineup_bullpen_profile_json.notna().sum()),
              'upgraded_signal_evidence_game_ids': upgraded_signal_evidence,
              'lineup_bullpen_profile_status_counts': {
                  str(key): int(value) for key, value in frame.lineup_bullpen_profile_status.value_counts(dropna=False).items()},
              'passive_context_capture_status': inputs['passive_context'].get('status'),
              'rows': len(frame), 'newly_scored': len(rows), 'preserved_pregame_rows': len(frozen_ids),
              'provider_status_disagreement_retained_rows': len(provider_status_disagreement_retained),
              'provider_status_disagreement_retained_game_ids': provider_status_disagreement_retained,
              'unchanged_rows': len(unchanged), 'unchanged_game_ids': unchanged, 'changes': changes,
              'migrated_frozen_game_ids': migrated_frozen,
              'removed_game_ids': sorted(set(prior_rows)-set(frame.game_id)),
              'withdrawn_games': withdrawals,
              'confirmed_lineups': int((frame.lineup_status == 'confirmed').sum()),
              'projected_lineups': int((frame.lineup_status == 'projected').sum()),
              'official_games': len(schedule), 'bbs_matched_games': len(assignments), 'exclusions': exclusions,
              'schedule_observations': [{'game_id': str(g['gamePk']), 'commence_time': g['gameDate'], 'status': g['status']}
                                        for g in schedule],
              'bbs_time_adjustments': crosswalk.game_time_adjustments,
              'with_both_starters': int((frame.home_starter_id.notna() & frame.away_starter_id.notna()).sum()),
              'with_bound_starter_profiles': int(frame.starter_profile_json.notna().sum()),
              'with_market_home_prob': int(frame.market_home_prob.notna().sum()),
              'with_market_total': int(frame.market_total.notna().sum()),
              'parquet_sha256': hashlib.sha256(body).hexdigest(), 'parquet_readback_verified': True,
              'source_capture': manifest, 'provider_receipts': {k: inputs[k]['receipt'] for k in ('bbs', 'odds', 'official')},
              'lineup_receipts': {pk: entry['receipt'] for pk, entry in inputs['feeds']['games'].items()},
              'edge_home_units': 'probability difference', 'edge_total_units': 'runs', 'published': False,
              'limitations': ['Fixed research models; no model retraining or R8 authority change.',
                  'Probable pitchers come from the verified pregame MLB feed, falling back to the existing official schedule.',
                  'Individual pitcher features influence predictions only when present in the accepted model; missing history retains explicit counts and team priors.',
                  'Batter and individual-bullpen performance can influence predictions only through fields learned by the accepted model; SHAP evidence separates observed performance from missing inputs and indicators.',
                  'An identity-only scratch rebuilds the game but can leave the accepted model probabilities unchanged.',
                  'Unchanged rows retain their original as_of; this report records the latest poll. T-10 remains the cutoff.',
                  'Stored history can lag games completed since its last refresh.']}
    (output/'report.json').write_bytes(encode(report))
    print(json.dumps({k: v for k, v in report.items() if k not in ('source_capture', 'provider_receipts', 'lineup_receipts')}, indent=2))
    return table, report, output


def publish(s3, bucket, table, report, output, *, calibration='temperature', platt_model_path=None, temperature_model_path=None):
    if report.get('source_capture', {}).get('verification_only'):
        raise ValueError('synthetic verification captures cannot be published')
    if not (os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == 'KirtKurt/parlay-platform'
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_EVENT_NAME') in ('schedule', 'push', 'workflow_dispatch')
            and '/.github/workflows/mlb-research-ingestion.yml@' in os.environ.get('GITHUB_WORKFLOW_REF', '')):
        raise ValueError('publication requires the existing main research ingestion workflow')
    date = report['date']
    if calendar_date.fromisoformat(date).isoformat() != date or (table.num_rows and set(table['date'].to_pylist()) != {date}):
        raise ValueError('publication escaped date scope')
    prefix, writes = PREFIX+'date='+date+'/', []
    prediction_body = (output/'predictions.parquet').read_bytes()
    if hashlib.sha256(prediction_body).hexdigest() != report['parquet_sha256']:
        raise ValueError('prediction publication content mismatch')
    local_table = pq.read_table(io.BytesIO(prediction_body))
    from ks1.platt import MODEL_PATH, TEMPERATURE_PATH, prepare_rows
    models = {'platt': json.loads((platt_model_path or MODEL_PATH).read_bytes()),
              'temperature': json.loads((temperature_model_path or TEMPERATURE_PATH).read_bytes())}
    if calibration not in models:
        raise ValueError('unknown publication calibration')
    prepared, changed = prepare_rows(table.to_pylist(), models[calibration], report['as_of'], calibration)
    prepared_table = pa.Table.from_pylist(prepared, schema=table.schema) if changed else table
    if not (local_table.equals(table) or local_table.equals(prepared_table)):
        raise ValueError('prediction publication content mismatch')
    if changed:
        table = prepared_table
        prediction_body = parquet_bytes(table)
        (output/'predictions.parquet').write_bytes(prediction_body)
        table.to_pandas().to_csv(output/'predictions.csv', index=False)
        report['parquet_sha256'] = hashlib.sha256(prediction_body).hexdigest()
    try:
        existing = s3.get_object(Bucket=bucket, Key=prefix+'predictions.parquet')
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') not in ('NoSuchKey', '404'):
            raise
        existing = None
    if existing:
        prior_bytes = existing['Body'].read()
        if (prior_bytes != prediction_body and report['source_capture'].get('previous_etag') != existing['ETag']):
            raise ValueError('date predictions changed since input capture')
        prior_rows = pq.ParquetFile(io.BytesIO(prior_bytes)).read().to_pylist()
        validate_preservation(prior_rows, table.to_pylist(), report['as_of'], report.get('withdrawn_games', []))
    elif report['source_capture'].get('previous_etag'):
        raise ValueError('previous prediction publication disappeared since input capture')
    report['calibration'] = {'method': calibration, 'changed_game_ids': changed,
                             'n': models[calibration]['n'], 'fitted_at': models[calibration]['fitted_at']}
    for method, model in models.items():
        (output/(method+'.json')).write_bytes(encode(model))
    (output/'report.json').write_bytes(encode(report))
    for name in ('odds_cache.parquet', 'lineup_cache.json', 'crosswalk.json', 'predictions.parquet', 'platt.json', 'temperature.json'):
        key, body = prefix+name, (output/name).read_bytes()
        sha = hashlib.sha256(body).hexdigest()
        existing = None
        try:
            existing = s3.get_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') not in ('NoSuchKey', '404'):
                raise
        if existing:
            prior_bytes = existing['Body'].read()
            if hashlib.sha256(prior_bytes).hexdigest() == sha:
                continue
            if name == 'predictions.parquet':
                prior = pq.read_table(io.BytesIO(prior_bytes)).to_pandas()
                if any(utc(t) > utc(report['as_of']) for t in prior.as_of):
                    raise ValueError('refuse stale prediction publication')
                captured_etag = report['source_capture'].get('previous_etag')
                if captured_etag != existing['ETag']:
                    raise ValueError('date predictions changed since input capture')
        kwargs = {'IfMatch': existing['ETag']} if existing else {'IfNoneMatch': '*'}
        result = s3.put_object(Bucket=bucket, Key=key, Body=body, Metadata={'sha256': sha, 'system': 'KS1'}, **kwargs)
        stored = s3.get_object(Bucket=bucket, Key=key, **({'VersionId': result['VersionId']} if result.get('VersionId') else {}))['Body'].read()
        if hashlib.sha256(stored).hexdigest() != sha:
            raise ValueError('published date artifact readback mismatch')
        writes.append(key)
    return {'bucket': bucket, 'prefix': prefix, 'write_keys': writes, 'readback_verified': True}


def publication_proof(output, report):
    """Expose exact published rows for review only after successful readback."""
    if not (report.get('published') and report.get('publication', {}).get('readback_verified')):
        raise ValueError('publication proof requires successful AWS readback')
    body = (output/'predictions.parquet').read_bytes()
    if hashlib.sha256(body).hexdigest() != report['parquet_sha256']:
        raise ValueError('publication proof hash mismatch')
    rows = pq.read_table(io.BytesIO(body)).to_pylist()
    columns = ('date', 'game_id', 'home_team', 'away_team', 'commence_time', 'as_of',
               'model_version', 'p_home', 'p_raw', 'lineup_status', 'prediction_status',
               'home_starter_id', 'away_starter_id', 'home_lineup_ids', 'away_lineup_ids',
               'lineup_bullpen_profile_status', 'lineup_bullpen_profile_sha256')
    proof_rows = []
    for row in rows:
        item = {key: row.get(key) for key in columns}
        item['signal_contributions'] = (json.loads(row['signal_contributions_json'])
                                         if row.get('signal_contributions_json') else None)
        item['signal_evidence_status'] = ('observed_performance_separated'
            if (item['signal_contributions'] or {}).get('schema_version') == 2
            else 'legacy_or_unavailable')
        profile = json.loads(row['lineup_bullpen_profile_json']) if row.get('lineup_bullpen_profile_json') else {}
        item['team_context'] = {
            side: {key: value.get(key) for key in ('lineup_ids', 'bullpen_roster_ids',
                   'opposing_starter_id', 'opposing_starter_pitch_hand', 'availability_status')}
            for side, value in profile.get('sides', {}).items()}
        proof_rows.append(item)
    return {'kind': 'KS1_verified_publication_rows', 'publication_as_of': report['as_of'],
            'parquet_sha256': report['parquet_sha256'], 'readback_verified': True,
            'rows': proof_rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--calibration', choices=['platt', 'temperature'], default='temperature')
    parser.add_argument('--platt-model', type=Path)
    parser.add_argument('--temperature-model', type=Path)
    args = parser.parse_args()
    table, report, output = predict(args.inputs, args.output)
    if args.publish:
        from ks1.sources import aws_clients
        _, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
        result = publish(s3, bucket, table, report, output, calibration=args.calibration,
                         platt_model_path=args.platt_model, temperature_model_path=args.temperature_model)
        report.update(published=True, publication=result)
        (output/'report.json').write_bytes(encode(report))
        (output/'publication.json').write_bytes(encode(result))
        print(json.dumps(result))
        proof = publication_proof(output, report)
        (output/'publication_proof.json').write_bytes(encode(proof))
        print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
