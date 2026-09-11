"""Serving adjustments on newly scored KS1 rows. Frozen locks are never rewritten."""
DISAGREE = 0.08
TOTAL_BLEND = 0.5


def _finite(value):
    return value is not None and value == value


def starter_unverified(row):
    return (row.get('status') == 'projected_missing_starter'
            or row.get('home_starter_status') != 'probable'
            or row.get('away_starter_status') != 'probable'
            or row.get('lineup_status') != 'confirmed')


def side(prob):
    return 'home' if prob >= 0.5 else 'away'


def apply(row, p_lgb, p_poisson, lambda_home, lambda_away):
    """Shrink totals toward market; pass winner picks on disagreement or weak starters."""
    reasons = []
    pick_status = 'bet'
    raw_total = float(lambda_home + lambda_away)
    market_total = row.get('market_total')
    if _finite(market_total) and raw_total > 0:
        proj_total = TOTAL_BLEND * raw_total + (1.0 - TOTAL_BLEND) * float(market_total)
        scale = proj_total / raw_total
        lambda_home = float(lambda_home) * scale
        lambda_away = float(lambda_away) * scale
        reasons.append('total_market_blend')
    else:
        proj_total = raw_total
    # Official p_home stays the raw LightGBM value so calibration still sees the
    # unscored engine. Serving uses pick_status instead of rewriting the lock.
    p_home = float(p_lgb)
    market_p = row.get('market_home_prob')
    if _finite(p_poisson) and abs(float(p_lgb) - float(p_poisson)) > DISAGREE:
        reasons.append('engine_disagreement')
        pick_status = 'pass'
    if _finite(market_p) and _finite(p_poisson) and not (side(p_lgb) == side(p_poisson) == side(market_p)):
        reasons.append('side_disagreement')
        pick_status = 'pass'
    if starter_unverified(row):
        reasons.append('starter_unverified')
        pick_status = 'pass'
    edge_home = (p_home - float(market_p)) if _finite(market_p) else None
    edge_total = (proj_total - float(market_total)) if _finite(market_total) else None
    return {
        'p_lgb': float(p_lgb),
        'p_home': p_home,
        'lambda_home': float(lambda_home),
        'lambda_away': float(lambda_away),
        'proj_total': float(lambda_home + lambda_away),
        'p_home_poisson': float(p_poisson),
        'edge_home': None if edge_home is None else float(edge_home),
        'edge_total': None if edge_total is None else float(edge_total),
        'pick_status': pick_status,
        'selection_reason': ','.join(reasons) or 'clear',
    }
