"""Read-only T-10 lock coverage telemetry for the KS1 daily artifact.

Coverage requires an unchanged retained row and immutable S3 evidence of storage
by that row's own T-10. The current schedule determines which games are due.
Missing locks are reported, never backfilled or converted into predictions.
"""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import re

import pyarrow.parquet as pq

from ks1.features import day, utc
from ks1.inventory import encode
from ks1.platt_inputs import PREFIX, read_locked_predictions

SUPPORTED_GAME_TYPES = {'R', 'F', 'D', 'L', 'W'}
INELIGIBLE_STATES = {'Postponed', 'Cancelled'}


def measure(schedule, prediction_rows, target_date, as_of, locked_predictions=()):
    now = utc(as_of)
    rows = {str(r['game_id']): r for r in prediction_rows}
    if len(rows) != len(prediction_rows):
        raise ValueError('duplicate prediction game ID in coverage input')
    games = [g for g in schedule if str(day(g['gameDate'])) == target_date
             and g.get('gameType') in SUPPORTED_GAME_TYPES]
    cutoff = []
    future = []
    invalid = []
    proofs = {}
    for entry in locked_predictions:
        row, proof = entry['row'], entry['evidence']
        pk = str(row['game_id'])
        # Evidence is tied to the complete retained row, not just its ID or
        # capture timestamp. A later rewrite cannot borrow an older proof.
        if rows.get(pk) != row or row.get('date') != target_date:
            continue
        row_cutoff = utc(row['commence_time']) - timedelta(minutes=10)
        if (proof.get('version_id') and proof['version_id'] != 'null'
                and proof.get('key') == PREFIX+'date='+target_date+'/predictions.parquet'
                and re.fullmatch('[0-9a-f]{64}', proof.get('sha256', ''))
                and utc(row['as_of']) <= utc(proof['stored_at']) <= row_cutoff < now):
            proofs[pk] = proof
    for game in games:
        pk = str(game['gamePk'])
        start = utc(game['gameDate'])
        due = start - timedelta(minutes=10)
        state = game.get('status', {}).get('detailedState')
        if state in INELIGIBLE_STATES:
            continue
        if now <= due:
            future.append(pk)
            continue
        cutoff.append(pk)
        row = rows.get(pk)
        if row is not None and utc(row['as_of']) > utc(row['commence_time']) - timedelta(minutes=10):
            invalid.append(pk)
    locked = sorted(pk for pk in cutoff if pk in proofs and pk not in invalid)
    missing = sorted(set(cutoff) - set(locked) - set(invalid))
    denominator = len(cutoff)
    return {
        'status': 'invalid_predictions' if invalid else 'ok',
        'target_date': target_date,
        'as_of': as_of,
        'official_supported_games': len(games),
        'future_before_t10_game_ids': sorted(future),
        'cutoff_reached_games': denominator,
        'valid_locked_games': len(locked),
        'valid_locked_game_ids': locked,
        'valid_locked_evidence': {pk: proofs[pk] for pk in locked},
        'missing_locked_game_ids': missing,
        'invalid_post_cutoff_prediction_game_ids': sorted(invalid),
        'lock_coverage_rate': (len(locked) / denominator) if denominator else None,
        'production_authority_changed': False,
        'backfilled_after_cutoff': False,
        'aws_writes': 0,
    }


def build(inputs, output, *, s3=None):
    report_dir = output
    result = {'status': 'unavailable', 'production_authority_changed': False,
              'backfilled_after_cutoff': False, 'aws_writes': 0}
    try:
        manifest = json.loads((inputs/'capture.json').read_bytes())
        target_date, as_of = manifest['date'], manifest['as_of']
        # Validate the date before constructing a local path or S3 prefix.
        if date.fromisoformat(target_date).isoformat() != target_date:
            raise ValueError('invalid prediction date')
        report_dir = output / ('date='+target_date)
        result.update(target_date=target_date, as_of=as_of)
        official = json.loads((inputs/'official.json').read_bytes())['payload']
        schedule = [g for d in official.get('dates', []) for g in d.get('games', [])]
        rows = pq.ParquetFile(report_dir/'predictions.parquet').read().to_pylist()
        if s3 is None:
            import boto3
            s3 = boto3.client('s3')
        locked, inventory = read_locked_predictions(s3, manifest['bucket'], as_of, target_date=target_date)
        result = measure(schedule, rows, target_date, as_of, locked)
        result['storage_inventory'] = inventory
    except Exception as exc:
        # Keep missing evidence visibly unavailable; never fabricate zero or
        # full coverage and never write any prediction or remote state.
        result.update(status='unavailable', error_type=type(exc).__name__, error=str(exc))
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir/'t10_coverage.json').write_bytes(encode(result))
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.inputs, args.output)
    raise SystemExit(0 if result['status'] == 'ok' else 1)


if __name__ == '__main__':
    main()
