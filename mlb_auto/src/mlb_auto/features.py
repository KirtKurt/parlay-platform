from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Mapping, Sequence

from .engine import devig_pair, market_depth_features, moneyline_consensus, temporal_features


def _dt(value: str) -> datetime:
    d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _market(event: Mapping[str, Any], key: str):
    rows = []
    for book in event.get('bookmakers') or []:
        m = next((x for x in book.get('markets') or [] if x.get('key') == key), None)
        if m:
            rows.append((book.get('key'), m))
    return rows


def _spread_features(event: Mapping[str, Any]) -> dict[str, float]:
    home = event.get('home_team')
    points, probs = [], []
    for _, m in _market(event, 'spreads'):
        outs = m.get('outcomes') or []
        h = next((x for x in outs if x.get('name') == home), None)
        a = next((x for x in outs if x.get('name') != home), None)
        if h and h.get('point') is not None:
            points.append(float(h['point']))
        if h and a:
            pair = devig_pair(h.get('price'), a.get('price'))
            if pair:
                probs.append(pair[0])
    return {
        'home_spread_consensus': mean(points) if points else 0.0,
        'home_spread_cover_probability': mean(probs) if probs else .5,
    }


def _total_features(event: Mapping[str, Any]) -> dict[str, float]:
    points, over_probs = [], []
    for _, m in _market(event, 'totals'):
        outs = m.get('outcomes') or []
        over = next((x for x in outs if str(x.get('name')).lower() == 'over'), None)
        under = next((x for x in outs if str(x.get('name')).lower() == 'under'), None)
        if over and over.get('point') is not None:
            points.append(float(over['point']))
        if over and under:
            pair = devig_pair(over.get('price'), under.get('price'))
            if pair:
                over_probs.append(pair[0])
    return {
        'game_total_consensus': mean(points) if points else 0.0,
        'over_probability': mean(over_probs) if over_probs else .5,
    }


def build_feature_vector(*, event: Mapping[str, Any], detail: Mapping[str, Any] | None,
                         home_probability_history: Sequence[float], pulled_at: str,
                         pull_count: int) -> dict[str, float]:
    fair = moneyline_consensus(event)
    hp = float(fair['home'])
    tp = temporal_features(home_probability_history)
    depth = market_depth_features(detail or {})
    start = _dt(event['commence_time'])
    current = _dt(pulled_at)
    hours = max(0.0, (start - current).total_seconds() / 3600.0)
    return {
        'market_home_probability': hp,
        'market_home_logit': math.log(max(1e-8, hp) / max(1e-8, 1 - hp)),
        'book_divergence': float(fair['book_divergence']),
        'book_count_log1p': math.log1p(int(fair['book_count'])),
        'pull_count_log1p': math.log1p(max(0, pull_count)),
        'market_move': tp['move'],
        'market_velocity': tp['velocity'],
        'market_volatility': tp['volatility'],
        'market_reversals': tp['reversals'],
        'hours_to_first_pitch': hours,
        **depth,
        **_spread_features(event),
        **_total_features(event),
    }


def bootstrap_home_probability(features: Mapping[str, float]) -> float:
    p = float(features.get('market_home_probability', .5))
    move = max(-.03, min(.03, float(features.get('market_move', 0)) * .70))
    div = float(features.get('book_divergence', 0))
    if div > .035:
        move -= min(.012, (div - .035) * .5)
    rev = float(features.get('market_reversals', 0))
    move -= min(.018, rev * .004)
    return max(.05, min(.95, p + move))
