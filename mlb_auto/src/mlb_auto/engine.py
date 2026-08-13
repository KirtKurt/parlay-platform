from __future__ import annotations

import math
from statistics import mean
from typing import Any, Mapping, Iterable


def american_prob(price: Any) -> float | None:
    try:
        a = float(price)
    except Exception:
        return None
    if a == 0:
        return None
    return abs(a) / (abs(a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def american_decimal(price: Any) -> float | None:
    try:
        a = float(price)
    except Exception:
        return None
    if a == 0:
        return None
    return 1.0 + (100.0 / abs(a) if a < 0 else a / 100.0)


def devig_pair(home: Any, away: Any) -> tuple[float, float] | None:
    h, a = american_prob(home), american_prob(away)
    if h is None or a is None or h + a <= 0:
        return None
    return h / (h + a), a / (h + a)


def _outcome_price(market: Mapping[str, Any], name: str) -> Any:
    for out in market.get('outcomes') or []:
        if str(out.get('name')) == str(name):
            return out.get('price')
    return None


def moneyline_consensus(event: Mapping[str, Any]) -> dict[str, Any]:
    home, away = event.get('home_team'), event.get('away_team')
    vals_h, vals_a, books = [], [], {}
    for book in event.get('bookmakers') or []:
        ml = next((m for m in book.get('markets') or [] if m.get('key') == 'h2h'), None)
        if not ml:
            continue
        pair = devig_pair(_outcome_price(ml, home), _outcome_price(ml, away))
        if not pair:
            continue
        hp, ap = pair
        vals_h.append(hp)
        vals_a.append(ap)
        books[str(book.get('key'))] = {'home': hp, 'away': ap}
    if not vals_h:
        return {'home': .5, 'away': .5, 'book_count': 0, 'book_divergence': 1.0, 'books': {}}
    return {
        'home': mean(vals_h),
        'away': mean(vals_a),
        'book_count': len(vals_h),
        'book_divergence': max(vals_h) - min(vals_h) if len(vals_h) > 1 else 0.0,
        'books': books,
    }


def market_depth_features(event_detail: Mapping[str, Any]) -> dict[str, float]:
    """Count safe information without guessing player-to-team attribution."""
    books = event_detail.get('bookmakers') or []
    market_keys: set[str] = set()
    player_prop_keys: set[str] = set()
    period_keys: set[str] = set()
    outcome_count = 0
    for book in books:
        for market in book.get('markets') or []:
            key = str(market.get('key') or '')
            if not key:
                continue
            market_keys.add(key)
            outcome_count += len(market.get('outcomes') or [])
            if key.startswith(('pitcher_', 'batter_')):
                player_prop_keys.add(key)
            if key.startswith(('first_', 'innings')):
                period_keys.add(key)
    return {
        'market_key_count': float(len(market_keys)),
        'bookmaker_count_all_markets': float(len(books)),
        'player_prop_market_count': float(len(player_prop_keys)),
        'period_market_count': float(len(period_keys)),
        'market_outcome_count': float(outcome_count),
        'has_alternate_lines': float(any(k.startswith('alternate_') for k in market_keys)),
        'has_team_totals': float(any('team_totals' in k for k in market_keys)),
    }


def temporal_features(probabilities: Iterable[float]) -> dict[str, float]:
    p = [float(x) for x in probabilities]
    if not p:
        return {'move': 0., 'velocity': 0., 'volatility': 0., 'reversals': 0.}
    move = p[-1] - p[0]
    diffs = [p[i] - p[i - 1] for i in range(1, len(p))]
    signs = [1 if x > .0005 else -1 if x < -.0005 else 0 for x in diffs]
    reversals = sum(1 for i in range(1, len(signs)) if signs[i] and signs[i - 1] and signs[i] != signs[i - 1])
    vol = math.sqrt(mean([(x - mean(diffs)) ** 2 for x in diffs])) if len(diffs) > 1 else 0.
    return {'move': move, 'velocity': mean(diffs) if diffs else 0., 'volatility': vol, 'reversals': float(reversals)}
