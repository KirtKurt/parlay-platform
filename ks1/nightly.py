"""Grade retained KS1 locks, commit the ledger, then fit calibration at 01:00 ET.

Runs inside the existing hourly GitHub job. No prediction, lock, other sport's
audit, model engine, or AWS infrastructure is modified.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from ks1.calibration_store import PREFIX, commit_json, latest_checkpoint, read_json
from ks1.features import ET, utc
from ks1.inventory import Reader, RESEARCH, encode
from ks1.platt import compare, identity, metrics, ordered, raw_model_version, refit, temperature_identity
from ks1.platt_inputs import capture, dataset
from src.temperature_calibrator import fit_from_ledger


def due_date(as_of, checkpoint=None):
    local = utc(as_of).astimezone(ET)
    date = local.date().isoformat()
    if local.hour < 1 or (checkpoint and checkpoint['state']['night_date'] >= date):
        return None
    return date  # Catch delayed/failed 01:00 runs on the next existing hourly tick.


def require_main_workflow():
    if not (os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_REPOSITORY') == 'KirtKurt/parlay-platform'
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_EVENT_NAME') in ('schedule', 'push', 'workflow_dispatch')
            and os.environ.get('GITHUB_WORKFLOW_REF') ==
                'KirtKurt/parlay-platform/.github/workflows/mlb-research-ingestion.yml@refs/heads/main'):
        raise ValueError('nightly writes require the existing main research ingestion workflow')


def require_fresh_finals(prior, not_before, as_of):
    """Do not seal a nightly checkpoint from a previous tick's results cache.

    Ingestion can exit successfully with LEASE_BUSY, or publish partial source
    coverage. Neither is proof that this run refreshed all completed games.
    Source hashes/version IDs are still verified by Reader before this gate.
    """
    if not not_before:
        raise ValueError('nightly publication requires the source refresh start time')
    if prior.get('coverageComplete') is not True:
        raise ValueError('nightly finals coverage is incomplete; retry after ingestion')
    start, end = utc(not_before), utc(as_of)
    observed = prior.get('receipt', {}).get('retrievedAtUtc')
    updated = prior.get('updatedAtUtc')
    if not observed or not updated or not (start <= utc(observed) <= utc(updated) <= end):
        raise ValueError('nightly finals are stale or future-dated; retry after ingestion')


def ledger_rows(ledger, as_of):
    if ledger.get('system') != 'KS1' or ledger.get('raw_model_version') != raw_model_version():
        raise ValueError('invalid KS1 grading ledger model')
    rows = ordered(ledger['rows'], as_of)
    for row in rows:
        if row.get('official_probability_field') != 'p_home' or not row.get('lock_evidence'):
            raise ValueError('ledger requires original official lock evidence')
        metrics([row['home_win']], [row['p_home']])
    return rows


def build_ledger(source, previous=None):
    """Append new grades; preserve every existing official probability/grade."""
    source = deepcopy(source)
    prior = ledger_rows(previous, source['as_of']) if previous else []
    known = {r['game_id']: r for r in prior}
    # Preserve the actual first label availability across disposable runners.
    source.setdefault('platt_model', identity()).setdefault('label_first_seen', {}).update(
        {r['game_id']: {'signature': r['signature'], 'graded_at': r['graded_at']} for r in prior})
    admitted, admission = dataset(source)
    originals = {str(e['row']['game_id']): e for e in source['locked']}
    for row in admitted:
        entry = originals[row['game_id']]
        official = float(entry['row']['p_home'])
        metrics([row['home_win']], [official])
        if row['game_id'] in known:
            old = known[row['game_id']]
            if any(row[k] != old[k] for k in ('p_raw', 'home_win', 'signature', 'graded_at')) or official != old['p_home']:
                raise ValueError('refuse to rewrite an existing KS1 official grade')
            continue
        known[row['game_id']] = dict(row, p_home=official, official_probability_field='p_home',
                                    lock_evidence=entry['evidence'],
                                    home_score=source['finals'][row['game_id']]['home_score'],
                                    away_score=source['finals'][row['game_id']]['away_score'],
                                    final_evidence=source['final_sources'])
    rows = ordered(list(known.values()), source['as_of'])
    return {'system': 'KS1', 'raw_model_version': raw_model_version(),
            'night_date': utc(source['as_of']).astimezone(ET).date().isoformat(),
            'as_of': source['as_of'], 'rows': rows, 'admission': admission,
            'new_grades': len(rows)-len(prior),
            'official_metrics': metrics([r['home_win'] for r in rows], [r['p_home'] for r in rows])}


def execute(source, output, *, s3=None, bucket=None, checkpoint=None, clock=None):
    """An interrupted run resumes the committed ledger before any refit."""
    publish = s3 is not None
    if publish:
        require_main_workflow()
        if source.get('verification_only') or source.get('errors'):
            raise ValueError('verification/error captures cannot write the nightly ledger')
    date = due_date(source['as_of'], checkpoint)
    if date is None:
        return {'status': 'not_due_or_already_completed', 'published': False}
    output.mkdir(parents=True, exist_ok=True)
    prefix = PREFIX+'date='+date+'/'
    prior_ledger = checkpoint['ledger'] if checkpoint else None
    previous = checkpoint['state'] if checkpoint else source
    saved, proof = read_json(s3, bucket, prefix+'graded_ledger.json') if publish else (None, None)
    # Validate newly observed corrections even if an earlier attempt committed.
    proposed = build_ledger(source, saved if saved is not None else prior_ledger)
    ledger = saved if saved is not None else proposed
    rows = ledger_rows(ledger, source['as_of'])
    if ledger['night_date'] != date or utc(ledger['as_of']) > utc(source['as_of']):
        raise ValueError('nightly ledger date/as_of mismatch')
    if publish:
        ledger, proof = commit_json(s3, bucket, prefix+'graded_ledger.json', ledger)
    else:
        path = output/'graded_ledger.json'
        body = encode(ledger)
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(body); temporary.replace(path)
        if path.read_bytes() != body:
            raise ValueError('local ledger readback mismatch')
        ledger = json.loads(path.read_bytes())
    # This callback is deliberately below the successful write AND readback.
    rows = ledger_rows(ledger, source['as_of'])
    fitted_at = (clock() if clock else datetime.now(timezone.utc)).isoformat()
    if utc(fitted_at) < utc(source['as_of']):
        raise ValueError('calibration clock precedes source capture')
    old_temperature = previous.get('temperature_model') or temperature_identity()
    if old_temperature['raw_model_version'] != raw_model_version():
        raise ValueError('nightly temperature belongs to another raw model')
    temperature, temperature_decision = fit_from_ledger(
        rows, previous=old_temperature, as_of=fitted_at,
        model_path=output/'data/models/temperature.json')
    platt, platt_decision = refit(rows, previous.get('platt_model') or identity(), fitted_at)
    state = {'system': 'KS1', 'status': 'completed', 'night_date': date,
             'completed_at': fitted_at, 'ledger': proof,
             'temperature_model': temperature, 'platt_model': platt,
             'temperature_decision': temperature_decision, 'platt_decision': platt_decision}
    if publish:
        commit_json(s3, bucket, prefix+'calibration_state.json', state)
    (output/'calibration_state.json').write_bytes(encode(state))
    (output/'data/models/platt.json').write_bytes(encode(platt))
    report = {'status': 'completed', 'published': publish, 'night_date': date,
              'ledger_rows': len(rows), 'new_grades': ledger['new_grades'],
              'ledger_readback_verified_before_fit': True,
              'temperature_decision': temperature_decision, 'platt_decision': platt_decision,
              'official_metrics': ledger['official_metrics'], 'comparison': compare(rows, source['as_of']),
              'write_keys': [prefix+'graded_ledger.json', prefix+'calibration_state.json'] if publish else [],
              'prediction_writes': 0, 'provider_calls': 0, 'trained_LightGBM': False}
    (output/'report.json').write_bytes(encode(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, help='Retained capture for a local-only verification')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--sources-not-before', help='UTC start of this run\'s source ingestion; required for due publication')
    args = parser.parse_args()
    if args.publish:
        require_main_workflow()
        if args.inputs:
            raise ValueError('nightly publication must read retained AWS evidence directly')
        from ks1.sources import aws_clients
        _, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
        as_of = datetime.now(timezone.utc).isoformat()
        checkpoint = latest_checkpoint(s3, bucket, as_of)
        if due_date(as_of, checkpoint) is None:
            args.output.mkdir(parents=True, exist_ok=True)
            report = {'status': 'not_due_or_already_completed', 'published': False, 'as_of': as_of}
            (args.output/'report.json').write_bytes(encode(report))
            print(json.dumps(report)); return
        reader = Reader(s3, bucket)
        prior = reader.pointer(reader.read(RESEARCH+'prior-games.json')['artifact'])
        try:
            require_fresh_finals(prior, args.sources_not_before, as_of)
        except ValueError as exc:
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output/'report.json').write_bytes(encode({
                'status': 'source_refresh_not_verified', 'published': False,
                'as_of': as_of, 'reason': str(exc), 'ledger_writes': 0,
                'source_updated_at': prior.get('updatedAtUtc'),
                'sources_not_before': args.sources_not_before}))
            raise
        source = capture(s3, bucket, as_of, prior, reader.receipts)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output/'capture.json').write_bytes(encode(source))
        report = execute(source, args.output, s3=s3, bucket=bucket, checkpoint=checkpoint)
    else:
        if not args.inputs:
            parser.error('--inputs is required without --publish')
        source = json.loads(args.inputs.read_bytes())
        report = execute(source, args.output)
    print(json.dumps({k: report[k] for k in ('status', 'published', 'ledger_rows', 'new_grades',
                                           'temperature_decision') if k in report}, indent=2))


if __name__ == '__main__':
    main()
