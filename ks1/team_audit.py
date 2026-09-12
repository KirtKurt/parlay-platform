"""Read-only KS1 team-pick audit from official locked grades.

Does not write predictions, locks, calibration, or AWS state. Favorite pick is
p_home >= 0.5. Team names come from the original locked prediction row.
"""
import math


def metrics(y, p):
    if len(y) != len(p):
        raise ValueError('invalid aligned binary outcomes')
    if not len(y):
        return {'n': 0, 'brier': None, 'logloss': None}
    brier = sum((int(yi) - float(pi)) ** 2 for yi, pi in zip(y, p)) / len(y)
    logloss = 0.0
    for yi, pi in zip(y, p):
        pi = min(max(float(pi), 1e-6), 1 - 1e-6)
        logloss -= (int(yi) * math.log(pi) + (1 - int(yi)) * math.log(1 - pi))
    return {'n': len(y), 'brier': float(brier), 'logloss': float(logloss / len(y))}


def wilson(hits, n, z=1.96):
    if n <= 0:
        return None
    p = hits / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / den
    return {'low': float(centre - half), 'high': float(centre + half), 'z': z}


def pick_summary(rows):
    n = len(rows)
    if not n:
        return {'n': 0, 'hits': 0, 'accuracy': None, 'wilson_95': None,
                'mean_p_home': None, 'home_win_rate': None,
                'official_metrics': metrics([], []),
                'waiting_for_30_graded_official_rows': True,
                'note': 'No official locked grades yet.'}
    hits = sum(int((float(r['p_home']) >= 0.5) == bool(r['home_win'])) for r in rows)
    official = metrics([r['home_win'] for r in rows], [r['p_home'] for r in rows])
    return {
        'n': n,
        'hits': hits,
        'accuracy': hits / n,
        'wilson_95': wilson(hits, n),
        'mean_p_home': sum(float(r['p_home']) for r in rows) / n,
        'home_win_rate': sum(int(bool(r['home_win'])) for r in rows) / n,
        'official_metrics': official,
        'coin_brier': 0.25,
        'brier_skill_vs_coin': None if official['brier'] is None else 0.25 - official['brier'],
        'waiting_for_30_graded_official_rows': n < 30,
        'note': ('Thin MLB edge on official locks; do not promote from this audit. '
                 'Wait for at least 30 official grades before judging calibration.'),
    }


def _label(row, side):
    name = row.get(side + '_team')
    ident = row.get(side + '_id')
    if name:
        return str(name)
    if ident is not None and str(ident) != '':
        return str(ident)
    return None


def _bucket():
    return {'appearances': 0, 'picked_win': 0, 'picked_win_hits': 0,
            'picked_against': 0, 'picked_against_model_hits': 0,
            'home_games': 0, 'home_wins': 0, 'away_games': 0, 'away_wins': 0,
            'p_home_sum_when_home': 0.0}


def build(grades, locked):
    """Join official grades to locked prediction identities."""
    by_id = {}
    for entry in locked or []:
        row = entry.get('row') if isinstance(entry, dict) and 'row' in entry else entry
        if not isinstance(row, dict) or row.get('game_id') is None:
            continue
        by_id[str(row['game_id'])] = row
    games, unidentified, teams = [], [], {}
    for grade in grades:
        pk = str(grade['game_id'])
        pred = by_id.get(pk, {})
        home, away = _label(pred, 'home'), _label(pred, 'away')
        p = float(grade['p_home'])
        picked_home = p >= 0.5
        home_won = bool(grade['home_win'])
        picked = home if picked_home else away
        winner = home if home_won else away
        hit = picked_home == home_won
        if not home or not away or home == away:
            unidentified.append({'game_id': pk, 'reason': 'missing_or_identical_team_identity'})
            continue
        games.append({
            'game_id': pk,
            'home_team': home,
            'away_team': away,
            'p_home': p,
            'picked': picked,
            'winner': winner,
            'hit': hit,
            'home_score': grade.get('home_score'),
            'away_score': grade.get('away_score'),
            'locked_at': grade.get('locked_at'),
        })
        for team, is_home in ((home, True), (away, False)):
            rec = teams.setdefault(team, _bucket())
            rec['appearances'] += 1
            if is_home:
                rec['home_games'] += 1
                rec['home_wins'] += int(home_won)
                rec['p_home_sum_when_home'] += p
            else:
                rec['away_games'] += 1
                rec['away_wins'] += int(not home_won)
            if picked == team:
                rec['picked_win'] += 1
                rec['picked_win_hits'] += int(hit)
            else:
                rec['picked_against'] += 1
                rec['picked_against_model_hits'] += int(hit)
    table = []
    for team, rec in teams.items():
        home_n = rec['home_games']
        table.append({
            'team': team,
            'appearances': rec['appearances'],
            'picked_win': rec['picked_win'],
            'picked_win_hits': rec['picked_win_hits'],
            'picked_win_accuracy': (rec['picked_win_hits'] / rec['picked_win']) if rec['picked_win'] else None,
            'picked_against': rec['picked_against'],
            'model_accuracy_when_against': (
                rec['picked_against_model_hits'] / rec['picked_against']) if rec['picked_against'] else None,
            'home_games': home_n,
            'home_win_rate': (rec['home_wins'] / home_n) if home_n else None,
            'mean_p_home_when_home': (rec['p_home_sum_when_home'] / home_n) if home_n else None,
            'away_games': rec['away_games'],
            'away_win_rate': (rec['away_wins'] / rec['away_games']) if rec['away_games'] else None,
        })
    table.sort(key=lambda r: (-(r['picked_win'] or 0), r['team']))
    return {
        'system': 'KS1',
        'kind': 'official_locked_team_pick_audit',
        'authority_changed': False,
        'trained_LightGBM': False,
        'pick_summary': pick_summary(grades),
        'teams': table,
        'games': games,
        'unidentified_games': unidentified,
        'unidentified_count': len(unidentified),
    }
