"""Read-only T-10 lock coverage telemetry for the KS1 daily artifact.

A game whose cutoff has passed can only remain in predictions.parquet if KS1
preserved a prediction observed no later than T-10. Missing locks are reported,
never backfilled post-cutoff and never converted into invented predictions.
"""
import argparse
from datetime import timedelta
import json
from pathlib import Path

import pyarrow.parquet as pq

from ks1.features import day, utc
from ks1.inventory import encode

SUPPORTED_GAME_TYPES = {'R', 'F', 'D', 'L', 'W'}
INELIGIBLE_STATES = {'Postponed', 'Cancelled'}


def measure(schedule, prediction_rows, target_date, as_of):
    now = utc(as_of)
    rows = {str(r['game_id']): r for r in prediction_rows}
    if len(rows) != len(prediction_rows):
        raise ValueError('duplicate prediction game ID in coverage input')
    games = [g for g in schedule if str(day(g['gameDate'])) == target_date
             and g.get('gameType') in SUPPORTED_GAME_TYPES]
    cutoff = []
    future = []
    invalid = []
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
        if row is not None and utc(row['as_of']) > due:
            invalid.append(pk)
    locked = sorted(pk for pk in cutoff if pk in rows and pk not in invalid)
    missing = sorted(set(cutoff) - set(locked) - set(invalid))
    denominator = len(cutoff)
    return {
        'target_date': target_date,
        'as_of': as_of,
        'official_supported_games': len(games),
        'future_before_t10_game_ids': sorted(future),
        'cutoff_reached_games': denominator,
        'valid_locked_games': len(locked),
        'valid_locked_game_ids': locked,
        'missing_locked_game_ids': missing,
        'invalid_post_cutoff_prediction_game_ids': sorted(invalid),
        'lock_coverage_rate': (len(locked) / denominator) if denominator else None,
        'production_authority_changed': False,
        'backfilled_after_cutoff': False,
    }


def build(inputs, output):
    manifest = json.loads((inputs/'capture.json').read_bytes())
    target_date, as_of = manifest['date'], manifest['as_of']
    official = json.loads((inputs/'official.json').read_bytes())['payload']
    schedule = [g for d in official.get('dates', []) for g in d.get('games', [])]
    path = output / ('date='+target_date) / 'predictions.parquet'
    rows = pq.ParquetFile(path).read().to_pylist() if path.exists() else []
    result = measure(schedule, rows, target_date, as_of)
    (output/('date='+target_date)/'t10_coverage.json').write_bytes(encode(result))
    print(json.dumps(result, indent=2))
    if result['invalid_post_cutoff_prediction_game_ids']:
        raise ValueError('post-cutoff prediction detected in KS1 coverage audit')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.inputs, args.output)


if __name__ == '__main__':
    main()
