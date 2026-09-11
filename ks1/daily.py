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

from ks1.features import Features, day, utc
from ks1.inventory import encode
from ks1.identity import reconcile_bbs
from ks1.poisson import home_probability, predict_exported
from ks1.publish import parquet_bytes
from ks1.refresh import change_reason, fingerprint, observe
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
# Dictionary date also reads cleanly with Arrow's automatic Hive partitioning.
SCHEMA = pa.schema([pa.field(k, pa.dictionary(pa.int32(), pa.string()) if k == 'date' else pa.string())
                    for k in STRINGS] + [pa.field(k, pa.float64()) for k in FLOATS])


def name_key(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


class Crosswalk:
    """Only exact aliases learned from existing official IDs; never fuzzy guess."""
    def __init__(self, history, schedule):
        self.aliases, self.bbs_ids, self.rows, self.game_rows = {}, {}, [], []
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

    def bind(self, bbs_id, official_id, name, *, method='unique_exact_alias_and_game_start'):
        old = self.bbs_ids.get(str(bbs_id))
        if old and old != str(official_id):
            raise ValueError('conflicting BBS/MLB team crosswalk')
        if not old:
            self.bbs_ids[str(bbs_id)] = str(official_id)
            self.rows.append({'bbs_id': str(bbs_id), 'mlb_id': str(official_id), 'observed_name': name,
                              'method': method, 'confidence': 1.0})


def bbs_assignments(payload, schedule, crosswalk, target_date):
    assigned, evidence = reconcile_bbs(payload, schedule, crosswalk.resolve, target_date)
    for record in evidence:
        event = assigned[record['game_id']]
        for side in ('home', 'away'):
            crosswalk.bind(event[side]['id'], record[side+'_id'], event[side]['name'], method=record['method'])
    crosswalk.game_rows = evidence
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
    required.update(name for name in ('feeds.json', 'previous.parquet') if (folder/name).exists())
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
    # Frozen Phase 4 rows acquire only conservative status defaults; their
    # predictions and original cutoff are retained, never rescored postgame.
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
    current = pa.Table.from_pylist(rows+retained, schema=SCHEMA).to_pandas()
    frame = current.sort_values(['commence_time', 'game_id']).reset_index(drop=True)
    table = pa.Table.from_pandas(frame, schema=SCHEMA, preserve_index=False)
    if frame.game_id.duplicated().any() or (len(frame) and set(frame.date) != {target_date}):
        raise ValueError('duplicate or wrong-date predictions')
    if len(frame):
        if frame.status.isna().any() or frame.lineup_status.isna().any():
            raise ValueError('every prediction requires status and lineup_status')
        rates = frame[['lambda_home', 'lambda_away', 'proj_total']].to_numpy(float)
        if not (np.isfinite(rates).all() and (rates > 0).all()):
            raise ValueError('invalid predicted runs')
        np.testing.assert_allclose(frame.proj_total, frame.lambda_home+frame.lambda_away, atol=1e-12, rtol=0)
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
    (output/'crosswalk.json').write_bytes(encode({'teams': crosswalk.rows, 'games': crosswalk.game_rows, 'fuzzy_matches': 0}))
    report = {'system': 'KS1', 'phase': 5, 'date': target_date, 'as_of': as_of, 'model_version': model_version,
              'rows': len(frame), 'newly_scored': len(rows), 'preserved_pregame_rows': len(frozen_ids),
              'unchanged_rows': len(unchanged), 'unchanged_game_ids': unchanged, 'changes': changes,
              'migrated_frozen_game_ids': migrated_frozen,
              'removed_game_ids': sorted(set(prior_rows)-set(frame.game_id)),
              'confirmed_lineups': int((frame.lineup_status == 'confirmed').sum()),
              'projected_lineups': int((frame.lineup_status == 'projected').sum()),
              'official_games': len(schedule), 'bbs_matched_games': len(assignments), 'exclusions': exclusions,
              'schedule_time_reconciliations': [r for r in crosswalk.game_rows if r['method'] != 'unique_exact_alias_and_game_start'],
              'with_both_starters': int((frame.home_starter_id.notna() & frame.away_starter_id.notna()).sum()),
              'with_market_home_prob': int(frame.market_home_prob.notna().sum()), 'with_market_total': int(frame.market_total.notna().sum()),
              'parquet_sha256': hashlib.sha256(body).hexdigest(), 'parquet_readback_verified': True,
              'source_capture': manifest, 'provider_receipts': {k: inputs[k]['receipt'] for k in ('bbs', 'odds', 'official')},
              'lineup_receipts': {pk: entry['receipt'] for pk, entry in inputs['feeds']['games'].items()},
              'edge_home_units': 'probability difference', 'edge_total_units': 'runs', 'published': False,
              'limitations': ['Fixed research models; no model retraining or R8 authority change.',
                  'Probable pitchers come from the verified pregame MLB feed, falling back to the existing official schedule.',
                  'Missing starters use the accepted team-starter features; individual-starter fields were not learned in 2025.',
                  'Confirmed batting orders are recorded; the accepted models still use shrunk team offense priors.',
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
    # The raw scorer and lock function are unchanged. Apply exactly once here.
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


if __name__ == '__main__':
    main()
