"""Local/CI retrospective simulation comparison; never call this a locked test."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np
import pandas as pd

from ks1.inventory import encode
from ks1.poisson import align_reference, home_probability, predict_exported, verify_reference
from ks1.sim_lifecycle import comparison
from ks1.simulation import RECIPE, SlateSimulator
from ks1.train import split


def run(phase2, output):
    proof = verify_reference(phase2)
    frame = pd.read_parquet(phase2/'input_table.parquet')
    train, test = split(frame)
    ref = align_reference(test, pd.read_parquet(phase2/'test_predictions.parquet'))
    root = Path(__file__).parent
    refs = json.loads((root/'model_refs.json').read_bytes())
    body = (root/refs['poisson']['file']).read_bytes()
    if hashlib.sha256(body).hexdigest() != refs['poisson']['sha256']:
        raise ValueError('champion Poisson artifact mismatch')
    model = json.loads(body)
    h, a = [predict_exported(model[s], test) for s in ('home', 'away')]
    p, _ = home_probability(h, a)
    rows, slate_timings = [], []
    simulator, current_date = None, None
    counts = test.groupby('date').size().to_dict()
    started = time.perf_counter()
    for i, row in enumerate(test.to_dict('records')):
        if row['date'] != current_date:
            if simulator:
                slate_timings.append({'date': current_date, **simulator.report()})
            current_date = row['date']
            simulator = SlateSimulator(int(counts[current_date]))
        seed = hashlib.sha256(encode([float(h[i]), float(a[i]), refs['poisson']['sha256']])).hexdigest()
        sim = simulator.score(row['game_id'], h[i], a[i], seed)
        rows.append({k: row[k] for k in ('game_id', 'date', 'home_score', 'away_score')} |
                    {'p_home': float(ref.iloc[i].p_home), 'p_home_poisson': float(p[i]),
                     'proj_total_poisson': float(h[i]+a[i]), **sim})
    if simulator:
        slate_timings.append({'date': current_date, **simulator.report()})
    report = {'system': 'KS1', 'phase': '6b', 'recipe': RECIPE,
              'cohort_kind': 'retrospective_accepted_holdout_NOT_locked',
              'train_dates': [train.date.min(), train.date.max()],
              'test_dates': [test.date.min(), test.date.max()],
              'comparison': comparison(rows), 'runtime_seconds': time.perf_counter()-started,
              'max_slate_seconds': max(s['seconds'] for s in slate_timings),
              'slates': slate_timings, 'runtime_platform': platform.platform(),
              'fargate_measured': False, 'proof': proof,
              'promotion': 'none; prospective locked comparison required'}
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(output/'retrospective_predictions.parquet', index=False)
    (output/'retrospective_comparison.json').write_bytes(encode(report))
    print(json.dumps({k: v for k, v in report.items() if k not in ('proof', 'slates', 'comparison')}
                     | {'metrics': report['comparison']['metrics']}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase2-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.phase2_dir, args.output)
