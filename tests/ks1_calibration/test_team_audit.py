from ks1.team_audit import build, pick_summary, wilson


def test_wilson_empty_and_known_interval():
    assert wilson(0, 0) is None
    interval = wilson(12, 21)
    assert interval['low'] < 12 / 21 < interval['high']
    assert 0.36 < interval['low'] < 0.38
    assert 0.75 < interval['high'] < 0.76


def test_pick_summary_flags_thin_sample_and_matches_official_brier():
    rows = [
        {'game_id': '1', 'p_home': 0.6, 'home_win': 1},
        {'game_id': '2', 'p_home': 0.4, 'home_win': 1},
        {'game_id': '3', 'p_home': 0.7, 'home_win': 0},
    ]
    summary = pick_summary(rows)
    assert summary['n'] == 3 and summary['hits'] == 1
    assert summary['accuracy'] == 1 / 3
    assert summary['waiting_for_30_graded_official_rows'] is True
    assert summary['official_metrics']['n'] == 3
    assert summary['brier_skill_vs_coin'] == 0.25 - summary['official_metrics']['brier']
    assert pick_summary([])['accuracy'] is None


def test_team_pick_audit_attributes_favorites_without_rewriting_grades():
    grades = [
        {'game_id': '10', 'p_home': 0.62, 'home_win': 1, 'home_score': 5, 'away_score': 2, 'locked_at': '2026-09-12T16:00:00+00:00'},
        {'game_id': '11', 'p_home': 0.41, 'home_win': 0, 'home_score': 1, 'away_score': 4, 'locked_at': '2026-09-12T17:00:00+00:00'},
        {'game_id': '12', 'p_home': 0.55, 'home_win': 0, 'home_score': 2, 'away_score': 12, 'locked_at': '2026-09-12T17:25:00+00:00'},
    ]
    locked = [
        {'row': {'game_id': '10', 'home_team': 'Cubs', 'away_team': 'Pirates', 'home_id': '112', 'away_id': '134'}},
        {'row': {'game_id': '11', 'home_team': 'Tigers', 'away_team': 'Rockies', 'home_id': '116', 'away_id': '115'}},
        {'row': {'game_id': '12', 'home_team': 'Yankees', 'away_team': 'Mets', 'home_id': '147', 'away_id': '121'}},
    ]
    original = [dict(r) for r in grades]
    audit = build(grades, locked)
    assert grades == original
    assert audit['authority_changed'] is False and audit['trained_LightGBM'] is False
    assert audit['pick_summary']['hits'] == 2 and audit['pick_summary']['n'] == 3
    by = {row['team']: row for row in audit['teams']}
    assert by['Cubs']['picked_win'] == 1 and by['Cubs']['picked_win_hits'] == 1
    assert by['Rockies']['picked_win'] == 1 and by['Rockies']['picked_win_hits'] == 1
    assert by['Yankees']['picked_win'] == 1 and by['Yankees']['picked_win_hits'] == 0
    assert by['Mets']['picked_against'] == 1
    assert {g['picked'] for g in audit['games']} == {'Cubs', 'Rockies', 'Yankees'}
    assert audit['unidentified_count'] == 0


def test_missing_team_identity_is_counted_not_invented():
    grades = [{'game_id': '99', 'p_home': 0.6, 'home_win': 1}]
    audit = build(grades, [])
    assert audit['teams'] == []
    assert audit['unidentified_count'] == 1
    assert audit['pick_summary']['n'] == 1
