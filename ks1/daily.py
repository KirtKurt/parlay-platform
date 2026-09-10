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
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ks1.features import Features, day, utc
from ks1.inventory import encode
from ks1.poisson import home_probability, predict_exported
from ks1.publish import parquet_bytes
from ks1.refresh import change_reason, fingerprint, observe
from ks1.table import american, team_identity
from ks1.simulation import IDENTITY, RECIPE, SlateSimulator

PREFIX = 'mlb/ks1/predictions-v1/'
ROOT = Path(__file__).parent
FLOATS = ['p_home', 'lambda_home', 'lambda_away', 'proj_total', 'market_home_prob', 'edge_home',
          'market_total', 'edge_total', 'market_spread', 'p_home_poisson', 'odds_match_confidence',
          'p_home_sim_raw', 'p_home_sim', 'lambda_home_sim', 'lambda_away_sim', 'proj_total_sim',
          'lambda_home_poisson', 'lambda_away_poisson', 'proj_total_poisson']
STRINGS = ['date', 'game_id', 'bbs_game_id', 'odds_event_id', 'home_team', 'away_team', 'home_id', 'away_id',
           'bbs_home_id', 'bbs_away_id', 'home_starter_name', 'away_starter_name', 'home_starter_id', 'away_starter_id',
           'home_starter_status', 'away_starter_status', 'starter_source', 'commence_time', 'model_version',
           'as_of', 'lineup_status', 'prediction_status', 'market_status', 'history_source_as_of',
           'environment_status', 'history_status', 'input_fingerprint', 'status',
           'home_lineup_status', 'away_lineup_status', 'home_lineup_ids', 'away_lineup_ids',
           'home_offense_source', 'away_offense_source', 'lineup_source_status', 'starter_feature_source',
           'sim_recipe', 'sim_calibration_version', 'sim_paths', 'official_probability_field']
# Dictionary date also reads cleanly with Arrow's automatic Hive partitioning.
SCHEMA = pa.schema([pa.field(k, pa.dictionary(pa.int32(), pa.string()) if k == 'date' else pa.string())
                    for k in STRINGS] + [pa.field(k, pa.float64()) for k in FLOATS])


def name_key(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


class Crosswalk:
    """Only exact aliases learned from existing official IDs; never fuzzy guess."""
    def __init__(self, history, schedule):
        self.aliases, self.bbs_ids, self.rows = {}, {}, []
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
        matches = [g for g in schedule if all(sides[s] == str(g['teams'][s]['team']['id']) for s in sides)
                   and abs((utc(event['kickoff_utc'])-utc(g['gameDate'])).total_seconds()) <= 90]
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
    required.update(name for name in ('feeds.json', 'previous.parquet', 'simulation_state.json') if (folder/name).exists())
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


def predict(folder, output):
    prediction_started = time.perf_counter()
    manifest, inputs = load_inputs(folder)
    target_date, as_of = manifest['date'], manifest['as_of']
    state = json.loads((folder/'simulation_state.json').read_bytes()) if (folder/'simulation_state.json').exists() else {}
    if state.get('recipe', RECIPE) != RECIPE:
        raise ValueError('simulation state recipe does not match registered recipe')
    if state.get('as_of') and utc(state['as_of']) > utc(as_of):
        raise ValueError('future simulation calibration state')
    mapping = state.get('mapping', IDENTITY)
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
    model_version = 'KS1-LGB-'+refs['lightgbm']['sha256'][:12]+'-DP-'+refs['poisson']['sha256'][:12]
    official = inputs['official']['payload']
    schedule = [g for d in official['dates'] for g in d['games']]
    if len(schedule) != official['totalGames'] or len({g['gamePk'] for g in schedule}) != len(schedule):
        raise ValueError('incomplete or duplicate official schedule')
    schedule = [g for g in schedule if str(day(g['gameDate'])) == target_date and g['gameType'] in ('R', 'F', 'D', 'L', 'W')]
    history = inputs['history']['games']
    crosswalk = Crosswalk(history, schedule)
    assignments = bbs_assignments(inputs['bbs']['payload'], schedule, crosswalk, target_date)
    engine, rows, feature_rows, exclusions = Features(history), [], [], []
    previous = pq.ParquetFile(folder/'previous.parquet').read().to_pandas() if (folder/'previous.parquet').exists() else None
    frozen = preserve_frozen(previous.iloc[:0], previous, target_date, as_of) if previous is not None else pd.DataFrame()
    frozen_ids = set(frozen.game_id) if len(frozen) else set()
    prior_rows = {r['game_id']: r for r in pq.ParquetFile(folder/'previous.parquet').read().to_pylist()} if previous is not None else {}
    retained, changes, unchanged = [], [], []
    # Phase 6b never migrates or edits a frozen row, including legacy metadata.
    migrated_frozen = []
    for pk in sorted(frozen_ids):
        retained.append(dict(prior_rows[pk]))
    history_ids = {str(g['officialGamePk']) for g in history}
    missing_boxes = [g for g in inputs['history'].get('schedule', []) if str(g['gamePk']) not in history_ids
                     and g.get('status', {}).get('abstractGameState') == 'Final'
                     and str(day(g['gameDate'])) < target_date]
    for game in sorted(schedule, key=lambda g: (g['gameDate'], str(g['gamePk']))):
        pk, start = str(game['gamePk']), utc(game['gameDate'])
        if pk in frozen_ids:
            continue
        if (game['status'].get('abstractGameState') != 'Preview'
                or game['status'].get('detailedState') in ('Postponed', 'Cancelled')
                or utc(as_of) > start-timedelta(minutes=10)):
            exclusions.append({'game_id': pk, 'reason': 'not_scheduled_before_T10'})
            continue
        if pk not in assignments:
            raise ValueError('scheduled official game missing from BBS: '+pk)
        bbs = assignments[pk]
        if bbs['status'].lower() != 'scheduled':
            raise ValueError('BBS and official pregame status disagree: '+pk)
        row = {'date': target_date, 'game_id': pk, 'bbs_game_id': bbs['id'], 'commence_time': start.isoformat(),
               'model_version': model_version, 'as_of': as_of,
               'sim_recipe': RECIPE, 'sim_calibration_version': mapping['version'],
               'official_probability_field': 'p_home',
               'environment_status': 'unavailable_in_accepted_models',
               'history_status': 'available_retained_history',
               'history_source_as_of': inputs['history'].get('prior_observed_at')}
        row.update(observe(game, inputs['feeds']['games'].get(pk), as_of))
        features = {}
        for side in ('home', 'away'):
            team = game['teams'][side]
            tid, name = team_identity(team)
            pid = row[side+'_starter_id']
            row.update({side+'_id': tid, side+'_team': name, 'bbs_'+side+'_id': str(bbs[side]['id'])})
            features.update({side+'_'+k: v for k, v in engine.at(as_of, tid, pid, game_date=target_date).items()})
            gaps = [g for g in missing_boxes if any(str(t['team']['id']) == tid for t in g['teams'].values())]
            for window in (1, 3, 5):
                if any(0 < (calendar_date.fromisoformat(target_date)-day(g['gameDate'])).days <= window for g in gaps):
                    for stat in ('pitches', 'outs'):
                        features[f'{side}_bullpen_{stat}_{window}d'] = None
            if gaps:
                row['history_status'] = 'partial_known_missing_boxes'
        row.update(market_for(game, inputs['odds']['payload'], crosswalk, as_of))
        features['market_home_prob'] = row['market_home_prob']
        if needed - set(features):
            raise ValueError('inference feature contract missing: '+','.join(sorted(needed-set(features))))
        row['input_fingerprint'] = fingerprint(row, features, needed)
        old = prior_rows.get(pk)
        if old and old.get('status') and old.get('input_fingerprint') == row['input_fingerprint']:
            retained.append(old); unchanged.append(pk)
            continue
        changes.append({'game_id': pk, 'reason': change_reason(old, row)})
        rows.append(row); feature_rows.append(features)
    simulator = SlateSimulator(len(rows), state.get('last_slate_seconds', 0), mapping)
    simulator.started = prediction_started  # Include feature assembly/model loading in the budget.
    if rows:
        features = pd.DataFrame(feature_rows)
        x = features[classifier.feature_name()].astype(float)
        p_home = classifier.predict(x)
        h, a = [predict_exported(poisson[side], features) for side in ('home', 'away')]
        p_poisson, _ = home_probability(h, a)
        if not (np.isfinite(p_home).all() and (p_home >= 0).all() and (p_home <= 1).all()):
            raise ValueError('invalid LightGBM probabilities')
        for i, row in enumerate(rows):
            row.update(p_home=float(p_home[i]), lambda_home=float(h[i]), lambda_away=float(a[i]),
                       proj_total=float(h[i]+a[i]), p_home_poisson=float(p_poisson[i]),
                       edge_home=float(p_home[i]-row['market_home_prob']) if row['market_home_prob'] is not None else None,
                       edge_total=float(h[i]+a[i]-row['market_total']) if row['market_total'] is not None else None)
            seed_inputs = refs['poisson']['sha256']
            sim = simulator.score(row['game_id'], h[i], a[i], seed_inputs)
            row.update(lambda_home_poisson=float(h[i]), lambda_away_poisson=float(a[i]), proj_total_poisson=float(h[i]+a[i]), **sim)
    current = pa.Table.from_pylist(rows+retained, schema=SCHEMA).to_pandas()
    frame = current.sort_values(['commence_time', 'game_id']).reset_index(drop=True)
    table = pa.Table.from_pandas(frame, schema=SCHEMA, preserve_index=False)
    if frame.game_id.duplicated().any() or (len(frame) and set(frame.date) != {target_date}):
        raise ValueError('duplicate or wrong-date predictions')
    if len(frame):
        if frame.loc[~frame.game_id.isin(frozen_ids), 'status'].isna().any() or frame.loc[~frame.game_id.isin(frozen_ids), 'lineup_status'].isna().any():
            raise ValueError('every prediction requires status and lineup_status')
        rates = frame[['lambda_home', 'lambda_away', 'proj_total']].to_numpy(float)
        if not (np.isfinite(rates).all() and (rates > 0).all()):
            raise ValueError('invalid predicted runs')
        np.testing.assert_allclose(frame.proj_total, frame.lambda_home+frame.lambda_away, atol=1e-12, rtol=0)
    by_id = {r['game_id']: r for r in table.to_pylist()}
    for pk in frozen_ids:
        for key, value in prior_rows[pk].items():
            if by_id[pk].get(key) != value:
                raise ValueError('frozen prediction field changed: '+pk+'/'+key)
    output = output / ('date='+target_date); output.mkdir(parents=True, exist_ok=True)
    body = parquet_bytes(table)
    (output/'predictions.parquet').write_bytes(body)
    if not pq.ParquetFile(output/'predictions.parquet').read().equals(table):
        raise ValueError('local parquet readback mismatch')
    frame.to_csv(output/'predictions.csv', index=False)
    # Shadow output uses generic run names, all drawn from the same sim paths.
    # The accepted daily total and official probability are unchanged.
    sim_table = table.select(['date', 'game_id', 'as_of', 'status', 'sim_recipe', 'sim_calibration_version',
                              'sim_paths', 'p_home_sim', 'p_home_sim_raw',
                              'lambda_home_sim', 'lambda_away_sim', 'proj_total_sim'])
    sim_table = sim_table.rename_columns([k.removesuffix('_sim') if k.startswith(('lambda_', 'proj_total')) else k
                                         for k in sim_table.column_names])
    (output/'simulation.parquet').write_bytes(parquet_bytes(sim_table))
    odds_rows = [{'event_id': str(e['id']), 'as_of': inputs['odds']['receipt']['as_of'], 'payload_json': encode(e).decode()}
                 for e in inputs['odds']['payload']]
    odds_table = pa.Table.from_pylist(odds_rows, schema=pa.schema([(k, pa.string()) for k in ('event_id', 'as_of', 'payload_json')]))
    (output/'odds_cache.parquet').write_bytes(parquet_bytes(odds_table))
    (output/'lineup_cache.json').write_bytes(encode(inputs['feeds']))
    (output/'crosswalk.json').write_bytes(encode({'teams': crosswalk.rows, 'fuzzy_matches': 0}))
    report = {'system': 'KS1', 'phase': '6b', 'simulation': simulator.report(), 'date': target_date, 'as_of': as_of, 'model_version': model_version,
              'max_slate_seconds': max(simulator.report()['seconds'], state.get('last_slate_seconds', 0)),
              'rows': len(frame), 'newly_scored': len(rows), 'preserved_pregame_rows': len(frozen_ids),
              'unchanged_rows': len(unchanged), 'unchanged_game_ids': unchanged, 'changes': changes,
              'migrated_frozen_game_ids': migrated_frozen,
              'removed_game_ids': sorted(set(prior_rows)-set(frame.game_id)),
              'confirmed_lineups': int((frame.lineup_status == 'confirmed').sum()),
              'projected_lineups': int((frame.lineup_status == 'projected').sum()),
              'official_games': len(schedule), 'bbs_matched_games': len(assignments), 'exclusions': exclusions,
              'with_both_starters': int((frame.home_starter_id.notna() & frame.away_starter_id.notna()).sum()),
              'with_market_home_prob': int(frame.market_home_prob.notna().sum()), 'with_market_total': int(frame.market_total.notna().sum()),
              'parquet_sha256': hashlib.sha256(body).hexdigest(), 'parquet_readback_verified': True,
              'source_capture': manifest, 'provider_receipts': {k: inputs[k]['receipt'] for k in ('bbs', 'odds', 'official')},
              'lineup_receipts': {pk: entry['receipt'] for pk, entry in inputs['feeds']['games'].items()},
              'edge_home_units': 'probability difference', 'edge_total_units': 'runs', 'published': False,
              'limitations': ['Fixed research models; no model retraining or R8 authority change.',
                  'Simulation is a shadow terminal-run recipe conditional on decisive scores, not a PA state simulator.',
                  'Accepted p_home and proj_total remain official; simulation.parquet has means and total from the same paths.',
                  'Probable pitchers come from the verified pregame MLB feed, falling back to the existing official schedule.',
                  'Missing starters use the accepted team-starter features; individual-starter fields were not learned in 2025.',
                  'Confirmed batting orders are recorded; the accepted models still use shrunk team offense priors.',
                  'An identity-only scratch rebuilds the game but can leave the accepted model probabilities unchanged.',
                  'Unchanged rows retain their original as_of; this report records the latest poll. T-10 remains the cutoff.',
                  'Stored history can lag games completed since its last refresh.']}
    (output/'report.json').write_bytes(encode(report))
    print(json.dumps({k: v for k, v in report.items() if k not in ('source_capture', 'provider_receipts', 'lineup_receipts')}, indent=2))
    return table, report, output


def publish(s3, bucket, table, report, output):
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
    if hashlib.sha256(prediction_body).hexdigest() != report['parquet_sha256'] or not pq.read_table(io.BytesIO(prediction_body)).equals(table):
        raise ValueError('prediction publication content mismatch')
    for name in ('odds_cache.parquet', 'lineup_cache.json', 'crosswalk.json', 'predictions.parquet', 'simulation.parquet', 'report.json'):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    table, report, output = predict(args.inputs, args.output)
    if args.publish:
        from ks1.sources import aws_clients
        _, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
        result = publish(s3, bucket, table, report, output)
        report.update(published=True, publication=result)
        (output/'report.json').write_bytes(encode(report))
        (output/'publication.json').write_bytes(encode(result))
        print(json.dumps(result))


if __name__ == '__main__':
    main()
