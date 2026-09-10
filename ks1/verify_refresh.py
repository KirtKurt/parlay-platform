"""Offline refresh proof using retained inputs and the accepted model artifacts.

Only the baseline is an unmodified source replay. Later captures and the
scratch are explicitly synthetic scenarios, not newly observed MLB events.
No provider calls, model training, or publication.
"""
import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from ks1.daily import load_inputs, predict
from ks1.features import utc
from ks1.inventory import encode


def seal(folder, manifest):
    manifest['files'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()
                         if p.name != 'capture.json'}
    (folder/'capture.json').write_bytes(encode(manifest))


def advance(folder, previous, minutes):
    manifest = json.loads((folder/'capture.json').read_bytes())
    step = timedelta(minutes=minutes)
    manifest['as_of'] = (utc(manifest['as_of'])+step).isoformat()
    manifest['verification_only'] = True
    (folder/'previous.parquet').write_bytes((previous/'predictions.parquet').read_bytes())
    for name in ('official', 'bbs', 'odds'):
        path = folder/(name+'.json'); entry = json.loads(path.read_bytes())
        entry['receipt']['as_of'] = (utc(entry['receipt']['as_of'])+step).isoformat()
        if name == 'odds':
            for event in entry['payload']:
                for book in event.get('bookmakers', []):
                    for item in [book, *book.get('markets', [])]:
                        if item.get('last_update'):
                            item['last_update'] = (utc(item['last_update'])+step).isoformat()
        entry['receipt']['sha256'] = hashlib.sha256(encode(entry['payload'])).hexdigest()
        path.write_bytes(encode(entry))
    if (folder/'feeds.json').exists():
        feeds = json.loads((folder/'feeds.json').read_bytes())
        for entry in feeds['games'].values():
            if entry.get('receipt', {}).get('as_of'):
                entry['receipt']['as_of'] = (utc(entry['receipt']['as_of'])+step).isoformat()
        (folder/'feeds.json').write_bytes(encode(feeds))
    seal(folder, manifest)


def changed_ids(before, after):
    old = {r['game_id']: r for r in before.to_pylist()}
    new = {r['game_id']: r for r in after.to_pylist()}
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def verify(inputs, output):
    manifest, _ = load_inputs(inputs)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ks1-refresh-proof-') as temp:
        folder = Path(temp)
        for name in [*manifest['files'], 'capture.json']:
            shutil.copy2(inputs/name, folder/name)
        baseline, initial_report, base_out = predict(folder, output/'baseline')
        if baseline.num_rows < 2:
            raise ValueError('proof needs at least two upcoming games in the retained capture')
        advance(folder, base_out, 1)
        repeat, repeat_report, repeat_out = predict(folder, output/'synthetic_unchanged')
        assert baseline.equals(repeat) and changed_ids(baseline, repeat) == []
        assert (base_out/'predictions.parquet').read_bytes() == (repeat_out/'predictions.parquet').read_bytes()
        assert repeat_report['newly_scored'] == 0
        advance(folder, repeat_out, 1)
        path = folder/'official.json'; official = json.loads(path.read_bytes())
        games = [g for d in official['payload']['dates'] for g in d['games']]
        rows = baseline.to_pylist()
        target = next(r for r in rows if r['home_starter_id'])
        # Reuse an observed provider player ID, with an explicit synthetic
        # assignment; never claim this replacement was announced in real life.
        replacement = next(g['teams'][s]['probablePitcher'] for g in games for s in ('home', 'away')
                           if str(g['teams'][s].get('probablePitcher', {}).get('id')) not in (target['home_starter_id'], 'None'))
        game = next(g for g in games if str(g['gamePk']) == target['game_id'])
        game['teams']['home']['probablePitcher'] = replacement
        official['receipt']['sha256'] = hashlib.sha256(encode(official['payload'])).hexdigest()
        path.write_bytes(encode(official))
        if (folder/'feeds.json').exists():
            feeds = json.loads((folder/'feeds.json').read_bytes())
            entry = feeds['games'].get(target['game_id'], {})
            if entry.get('payload'):
                entry['payload'].setdefault('gameData', {}).setdefault('probablePitchers', {})['home'] = replacement
                entry['receipt']['sha256'] = hashlib.sha256(encode(entry['payload'])).hexdigest()
                (folder/'feeds.json').write_bytes(encode(feeds))
        seal(folder, json.loads((folder/'capture.json').read_bytes()))
        scratched, scratch_report, scratch_out = predict(folder, output/'synthetic_scratch')
        assert changed_ids(repeat, scratched) == [target['game_id']]
        assert scratch_report['newly_scored'] == 1
        assert scratch_report['changes'] == [{'game_id': target['game_id'], 'reason': 'starter_changed'}]
        advance(folder, scratch_out, 1)
        final, final_report, final_out = predict(folder, output/'synthetic_scratch_repeat')
        assert final.equals(scratched) and changed_ids(scratched, final) == []
        assert (scratch_out/'predictions.parquet').read_bytes() == (final_out/'predictions.parquet').read_bytes()
        assert final_report['newly_scored'] == 0
        result = {'system': 'KS1', 'phase': 5, 'date': manifest['date'], 'source_as_of': manifest['as_of'],
                  'rows': baseline.num_rows, 'accepted_model_version': initial_report['model_version'],
                  'baseline_status_rows': sum(bool(r['status']) for r in rows),
                  'baseline_projected_lineups': initial_report['projected_lineups'],
                  'unchanged_poll': {'changed_game_ids': [], 'newly_scored': 0, 'byte_identical': True},
                  'synthetic_scratch': {'changed_game_ids': [target['game_id']], 'newly_scored': 1,
                                        'unchanged_rows': baseline.num_rows-1, 'replacement_is_synthetic': True},
                  'scratch_repeat': {'changed_game_ids': [], 'newly_scored': 0, 'byte_identical': True},
                  'parquet_sha256': {'baseline': initial_report['parquet_sha256'], 'scratch': scratch_report['parquet_sha256']},
                  'aws_writes': 0, 'provider_calls': 0, 'trained_models': 0, 'all_assertions_passed': True}
        (output/'proof.json').write_bytes(encode(result))
        print(json.dumps(result, indent=2))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.inputs, args.output)


if __name__ == '__main__':
    main()
