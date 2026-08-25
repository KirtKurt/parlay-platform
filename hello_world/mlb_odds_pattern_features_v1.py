"""Odds-API-only market pattern features for the MLB historical optimizer.

Adds five capabilities using only T-minus-45-clipped snapshots:
1. deterministic market fingerprints and archetype similarity,
2. market regime classification,
3. curve-shape recognition,
4. sportsbook leadership/follow-through analysis, and
5. bounded automatic interaction features.

The same deterministic formulas are installed in compiled historical search and
live champion scoring. No post-lock or outcome data is read here.

Recovery note (2026-08-25): this source-touch intentionally triggers the isolated
MLB historical V7 deployment so AWS refreshes the historical Lambda with the
current provider secret before learning resumes. It does not change feature math.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Any, Dict, Mapping, Sequence, Tuple

VERSION = "MLB-ODDS-PATTERN-FEATURES-v1-lock-bounded-runtime-parity"

DEFAULTS = {
    "patternFingerprintWeight": 0.0,
    "patternRegimeWeight": 0.0,
    "patternCurveWeight": 0.0,
    "patternBookLeadershipWeight": 0.0,
    "patternShockPersistenceWeight": 0.0,
    "patternCompressionBreakoutWeight": 0.0,
    "patternConsensusPersistenceWeight": 0.0,
    "patternEntropyPenalty": 0.0,
}
BOUNDS = {
    "patternFingerprintWeight": (-0.08, 0.08),
    "patternRegimeWeight": (-0.08, 0.08),
    "patternCurveWeight": (-0.08, 0.08),
    "patternBookLeadershipWeight": (-0.08, 0.08),
    "patternShockPersistenceWeight": (-0.08, 0.08),
    "patternCompressionBreakoutWeight": (-0.08, 0.08),
    "patternConsensusPersistenceWeight": (-0.08, 0.08),
    "patternEntropyPenalty": (0.0, 0.08),
}
CHOICES = {
    name: (-0.04, -0.02, 0.0, 0.02, 0.04) if "Penalty" not in name else (0.0, 0.01, 0.02, 0.04)
    for name in DEFAULTS
}


def _f(v: Any, d: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except Exception:
        return d


def _side_points(observations: Sequence[Mapping[str, Any]], side: str):
    out = []
    for row in observations:
        at = str(row.get("providerTimestampUtc") or "")
        value = row.get(f"{side}Fair")
        if at and value not in (None, ""):
            out.append((at, _f(value)))
    return sorted(out)


def _book_series(observations: Sequence[Mapping[str, Any]], side: str):
    series: Dict[str, list] = {}
    for row in observations:
        at = str(row.get("providerTimestampUtc") or "")
        for key, book in (row.get("books") or {}).items():
            if not isinstance(book, Mapping):
                continue
            value = book.get(f"{side}Fair")
            if at and value not in (None, ""):
                series.setdefault(str(key), []).append((at, _f(value)))
    return series


def _sign(x: float, eps: float = 5e-4) -> int:
    return 1 if x > eps else -1 if x < -eps else 0


def _entropy(signs: Sequence[int]) -> float:
    values = [x for x in signs if x]
    if not values:
        return 0.0
    result = 0.0
    for token in (-1, 1):
        p = values.count(token) / len(values)
        if p:
            result -= p * math.log(p, 2)
    return result


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))
    return dot/(na*nb) if na and nb else 0.0


_ARCHETYPES = {
    "clean_steam": (1.0, 0.9, 0.8, 0.1, 0.1, 0.9),
    "late_breakout": (0.7, 1.0, 0.9, 0.2, 0.3, 0.8),
    "reversal_trap": (-0.2, -0.7, 0.2, 1.0, 0.9, 0.2),
    "dead_market": (0.0, 0.0, 0.0, 0.0, 0.1, 0.8),
}


def extract(signal: Mapping[str, Any], observations: Sequence[Mapping[str, Any]] | None = None, side: str | None = None) -> Dict[str, Any]:
    observations = list(observations or [])
    side = side or str(signal.get("side") or "home")
    pts = _side_points(observations, side)
    values = [v for _, v in pts]
    if len(values) < 2:
        frozen = signal.get("oddsPatternFeatures")
        if isinstance(frozen, Mapping):
            return dict(frozen)
        values = [_f(signal.get("probStart"), 0.5), _f(signal.get("probLatest"), 0.5)]
    changes = [b-a for a,b in zip(values, values[1:])]
    signs = [_sign(x) for x in changes]
    net = values[-1]-values[0]
    gross = sum(abs(x) for x in changes)
    efficiency = abs(net)/gross if gross else 0.0
    n = len(changes)
    late = sum(changes[max(0,n-3):]) if changes else 0.0
    early = sum(changes[:max(1,n//3)]) if changes else 0.0
    shocks = [abs(x) for x in changes]
    shock_threshold = max(0.004, (sum(shocks)/len(shocks))*2.0) if shocks else 0.004
    shock_count = sum(x >= shock_threshold for x in shocks)
    reversals = sum(a and b and a != b for a,b in zip(signs, signs[1:]))
    entropy = _entropy(signs)
    curvature = late-early
    compression = 0.0
    if len(changes) >= 6:
        first = sum(abs(x) for x in changes[:3])/3
        pre = sum(abs(x) for x in changes[-6:-3])/3
        last = sum(abs(x) for x in changes[-3:])/3
        compression = max(0.0, (first-pre)) * (1.0 if last > pre*1.5 else 0.0)
    consensus = max(0.0, 1.0-min(1.0, _f(signal.get("bookDivergence"))/0.075))
    vector = (
        max(-1.0,min(1.0,net/0.05)),
        max(-1.0,min(1.0,late/0.03)),
        max(-1.0,min(1.0,curvature/0.03)),
        min(1.0,reversals/5.0),
        min(1.0,entropy),
        consensus,
    )
    sims = {k: _cosine(vector,v) for k,v in _ARCHETYPES.items()}
    fingerprint_score = sims["clean_steam"] + 0.5*sims["late_breakout"] - sims["reversal_trap"]

    if efficiency >= .75 and net > .006 and reversals <= 1:
        regime = "clean_steam"; regime_score = 1.0
    elif shock_count and late*net > 0 and abs(late) >= abs(net)*.5:
        regime = "late_breakout"; regime_score = .7
    elif reversals >= 3 or entropy >= .9:
        regime = "chaotic_reversal"; regime_score = -.8
    elif gross < .004:
        regime = "dead_market"; regime_score = 0.0
    else:
        regime = "mixed"; regime_score = .1*_sign(net)

    if efficiency >= .8:
        curve = "linear"; curve_score = _sign(net)*1.0
    elif curvature*net > 0 and abs(curvature) > .003:
        curve = "accelerating"; curve_score = _sign(net)*.8
    elif curvature*net < 0 and abs(curvature) > .003:
        curve = "fading"; curve_score = _sign(net)*-.6
    elif reversals >= 2:
        curve = "oscillating"; curve_score = -.5
    else:
        curve = "plateau"; curve_score = .1*_sign(net)

    leader_score = 0.0; leader = None; follower_count = 0
    books = _book_series(observations, side)
    first_moves = []
    for book, seq in books.items():
        if len(seq) < 2: continue
        base = seq[0][1]
        hit = next(((at,v-base) for at,v in seq[1:] if abs(v-base)>=.004), None)
        if hit: first_moves.append((hit[0], book, hit[1]))
    if first_moves:
        first_moves.sort(); _, leader, move = first_moves[0]
        direction = _sign(move)
        for _, book, other in first_moves[1:]:
            if _sign(other)==direction: follower_count += 1
        leader_score = direction * min(1.0, follower_count/max(1,len(first_moves)-1))

    payload = {
        "fingerprintScore": round(max(-2.0,min(2.0,fingerprint_score)),8),
        "regimeScore": round(regime_score,8),
        "curveScore": round(curve_score,8),
        "bookLeadershipScore": round(leader_score,8),
        "shockPersistence": round(_sign(net)*min(1.0,shock_count/3.0)*efficiency,8),
        "compressionBreakout": round(_sign(late)*min(1.0,compression/0.01),8),
        "consensusPersistence": round(_sign(net)*consensus*efficiency,8),
        "pathEntropy": round(entropy,8),
        "regime": regime,
        "curveShape": curve,
        "leadingBook": leader,
        "followingBookCount": follower_count,
        "fingerprintVector": [round(x,8) for x in vector],
    }
    payload["fingerprintSha256"] = hashlib.sha256(json.dumps(payload["fingerprintVector"],separators=(",",":"),sort_keys=True).encode()).hexdigest()
    return payload


def _numeric(features: Mapping[str, Any]) -> Tuple[float,...]:
    return tuple(_f(features.get(k)) for k in (
        "fingerprintScore","regimeScore","curveScore","bookLeadershipScore",
        "shockPersistence","compressionBreakout","consensusPersistence","pathEntropy"))


def _adjust(values: Sequence[float], weights: Sequence[float]) -> float:
    raw = sum(values[i]*weights[i] for i in range(7)) - values[7]*weights[7]
    return max(-0.12,min(0.12,raw))


def install(optimizer: Any, policy_runtime: Any) -> None:
    if getattr(optimizer,"_INQSI_ODDS_PATTERN_V1_INSTALLED",False): return
    policy_runtime.BASELINE_POLICY.update({k:policy_runtime.BASELINE_POLICY.get(k,v) for k,v in DEFAULTS.items()})
    policy_runtime._NUMERIC_BOUNDS.update(BOUNDS)
    original_signal=optimizer._signal; original_candidate=optimizer._candidate_policy
    original_cs=optimizer._compile_signal_for_search; original_cp=optimizer._compile_policy_for_search
    original_score=optimizer._score_compiled_signal; original_prod=policy_runtime.production_optimized_signal

    def patched_signal(game, observations, side, expected_slots):
        out=original_signal(game,observations,side,expected_slots)
        out["oddsPatternFeatures"]=extract(out,observations,side)
        out["oddsPatternFeatureVersion"]=VERSION
        out["oddsPatternSource"]="the_odds_api_t_minus_45_clipped_snapshots_only"
        return out
    def patched_candidate(rng: random.Random):
        p=original_candidate(rng)
        for k,v in CHOICES.items(): p[k]=rng.choice(v)
        return p
    def patched_cs(signal): return tuple(original_cs(signal))+_numeric(extract(signal))
    def patched_cp(policy): return tuple(original_cp(policy))+tuple(_f(policy.get(k)) for k in DEFAULTS)
    def patched_score(signal,policy):
        base_signal=tuple(signal[:-8]); base_policy=tuple(policy[:-8])
        score,_=original_score(base_signal,base_policy)
        adj=_adjust(signal[-8:],policy[-8:])
        score=max(0.0,min(100.0,score+adj*100.0))
        prob=1/(1+math.exp(-(score-50)/12))
        return round(score,4),round(max(.05,min(.95,prob)),8)
    def patched_prod(signal,policy):
        out=original_prod(signal,policy)
        features=extract(signal)
        adj=_adjust(_numeric(features),tuple(_f(policy.get(k)) for k in DEFAULTS))
        score=max(0.0,min(100.0,_f(out.get("optimizedWinnerScore"),50)+adj*100))
        prob=1/(1+math.exp(-(score-50)/12))
        out.update({"oddsPatternFeatures":features,"oddsPatternFeatureVersion":VERSION,
                    "oddsPatternScoreAdjustment":round(adj*100,6),"optimizedWinnerScore":round(score,4),
                    "score":round(score,4),"winProbability":round(max(.05,min(.95,prob)),8),
                    "winProbabilityPct":round(max(.05,min(.95,prob))*100,4)})
        return out
    optimizer._signal=patched_signal; optimizer._candidate_policy=patched_candidate
    optimizer._compile_signal_for_search=patched_cs; optimizer._compile_policy_for_search=patched_cp
    optimizer._score_compiled_signal=patched_score; policy_runtime.production_optimized_signal=patched_prod
    optimizer.ODDS_PATTERN_FEATURE_VERSION=VERSION
    optimizer._INQSI_ODDS_PATTERN_V1_INSTALLED=True
