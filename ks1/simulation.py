"""Deterministic full-game score paths from the retained dual-Poisson inputs.

This is a terminal-run simulator, not a PA/inning state model. Each path is a
pair of full-game run counts. Tied pairs are rejected and redrawn: the recipe
models completed, decisive games conditional on no tie. It does not fabricate
PA transitions, extra-inning labels, automatic-runner rates, or new vendor data.
"""
import hashlib
import time

import numpy as np
from scipy.special import expit, logit

RECIPE = 'KS1-terminal-poisson-no-tie-v1'
IDENTITY = {'version': 'identity-v1', 'slope': 1.0, 'intercept': 0.0}
STATE_KEY = 'mlb/ks1/simulation-v1/state.json'  # New KS1 artifact, existing bucket.


def calibrate(p, mapping=None):
    mapping = mapping or IDENTITY
    slope, intercept = float(mapping['slope']), float(mapping['intercept'])
    if not np.isfinite([slope, intercept]).all() or slope <= 0:
        raise ValueError('invalid monotonic simulation calibration')
    return expit(slope * logit(np.clip(p, 1e-6, 1-1e-6)) + intercept)


def paths(game_id, home_rate, away_rate, fingerprint, n=5000, recipe=RECIPE):
    if recipe != RECIPE or n not in (3000, 5000):
        raise ValueError('unregistered recipe or unsupported path count')
    rates = np.asarray([home_rate, away_rate], float)
    if not np.isfinite(rates).all() or (rates < .05).any() or (rates > 50).any():
        raise ValueError('run inputs outside supported range [.05, 50]')
    seed = int.from_bytes(hashlib.sha256(
        f'{recipe}|{game_id}|{fingerprint}'.encode()).digest()[:16], 'big')
    rng = np.random.Generator(np.random.PCG64(seed))
    scores = rng.poisson(rates, size=(n, 2))
    tied = scores[:, 0] == scores[:, 1]
    for _ in range(1000):
        if not tied.any():
            return scores
        scores[tied] = rng.poisson(rates, size=(int(tied.sum()), 2))
        tied = scores[:, 0] == scores[:, 1]
    raise ValueError('decisive run paths failed to converge')


def summarize(scores, mapping=None):
    raw = float(np.mean(scores[:, 0] > scores[:, 1]))
    home, away = scores.mean(axis=0)
    return {'p_home_sim_raw': raw, 'p_home_sim': float(calibrate(raw, mapping)),
            'lambda_home_sim': float(home), 'lambda_away_sim': float(away),
            'proj_total_sim': float(scores.sum(axis=1).mean())}


class SlateSimulator:
    """Reuse row fingerprints; downgrade subsequent work after a 240s budget.

    A persisted previous slate over budget starts the next slate at 3000.
    The current slate also downgrades when measured per-game time projects an
    overrun. Runtime measurements belong in reports, never row fingerprints.
    """
    def __init__(self, games, previous_seconds=0, mapping=None, clock=time.perf_counter):
        self.games, self.mapping, self.clock = games, mapping or IDENTITY, clock
        self.started = clock()
        self.n = 3000 if previous_seconds > 240 else 5000
        self.counts = []

    def score(self, game_id, h, a, fingerprint):
        elapsed = self.clock()-self.started
        if elapsed > 240 or (self.counts and elapsed / len(self.counts) * self.games > 240):
            self.n = 3000
        result = summarize(paths(game_id, h, a, fingerprint, self.n), self.mapping)
        self.counts.append(self.n)
        return {**result, 'sim_paths': str(self.n), 'sim_recipe': RECIPE,
                'sim_calibration_version': self.mapping['version']}

    def report(self):
        seconds = self.clock()-self.started
        return {'seconds': seconds, 'games': len(self.counts), 'path_counts': self.counts,
                'over_budget': seconds > 240, 'runtime_environment': 'caller_measured',
                'fargate_measured': False}
