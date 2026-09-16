import json

from ks1.forensic_consideration import CONTRACT, evaluate


def row(**updates):
    base = {
        'date': '2026-09-17', 'game_id': '1', 'home_team': 'Home', 'away_team': 'Away',
        'as_of': '2026-09-17T22:50:00Z', 'commence_time': '2026-09-17T23:00:00Z',
        'p_home': '0.56', 'market_home_prob': '0.58',
        'starter_profile_json': json.dumps({'sides': {
            'home': {'metrics': {'era_7d': 3.0, 'era_30d': 3.2, 'fip_7d': 3.1, 'fip_30d': 3.3,
                                 'xwoba_7d': .310, 'xwoba_30d': .315, 'expected_innings_last5': 5.7}},
            'away': {'metrics': {'era_7d': 4.2, 'era_30d': 4.0, 'fip_7d': 4.1, 'fip_30d': 4.0,
                                 'xwoba_7d': .330, 'xwoba_30d': .325, 'expected_innings_last5': 5.4}},
        }}),
        'lineup_bullpen_profile_json': json.dumps({'sides': {
            'home': {'features': {'lineup_ops_7d': .760, 'lineup_xwoba_7d': .325, 'lineup_top4_ops': .800,
                                  'bullpen_context_fip_7d': 3.40, 'bullpen_context_era_7d': 3.20,
                                  'bullpen_context_available_count': 8}},
            'away': {'features': {'lineup_ops_7d': .720, 'lineup_xwoba_7d': .310, 'lineup_top4_ops': .760,
                                  'bullpen_context_fip_7d': 3.80, 'bullpen_context_era_7d': 3.70,
                                  'bullpen_context_available_count': 7}},
        }}),
    }
    base.update(updates)
    return base


def test_clean_card_has_no_counter_signal():
    result = evaluate(row())
    assert result['contract'] == CONTRACT
    assert result['selected_team'] == 'Home'
    assert result['severity'] == 'none'
    assert result['flags'] == []
    assert result['authority_effect'].startswith('diagnostic_only')


def test_market_and_recent_starter_warning_are_high_visibility():
    starter = json.loads(row()['starter_profile_json'])
    starter['sides']['home']['metrics'].update(era_7d=7.4, era_30d=3.2, fip_7d=6.2, fip_30d=3.3,
                                               xwoba_7d=.380, xwoba_30d=.315)
    result = evaluate(row(p_home='0.56', market_home_prob='0.42', starter_profile_json=json.dumps(starter)))
    codes = {flag['code'] for flag in result['flags']}
    assert 'MARKET_FAVORS_OPPOSITE_SIDE' in codes
    assert 'SELECTED_STARTER_RECENT_DETERIORATION' in codes
    assert result['severity'] == 'high'


def test_bullpen_and_lineup_counter_signals_use_frozen_profile():
    context = json.loads(row()['lineup_bullpen_profile_json'])
    context['sides']['away']['features'].update(lineup_ops_7d=.880, lineup_xwoba_7d=.370,
                                                bullpen_context_fip_7d=2.10,
                                                bullpen_context_era_7d=1.20,
                                                bullpen_context_available_count=11)
    result = evaluate(row(lineup_bullpen_profile_json=json.dumps(context)))
    codes = {flag['code'] for flag in result['flags']}
    assert 'OPPONENT_LINEUP_OPS_ADVANTAGE' in codes
    assert 'OPPONENT_LINEUP_XWOBA_ADVANTAGE' in codes
    assert 'OPPONENT_BULLPEN_FIP_ADVANTAGE' in codes
    assert 'OPPONENT_BULLPEN_ERA_ADVANTAGE' in codes
    assert 'OPPONENT_BULLPEN_DEPTH_ADVANTAGE' in codes
    assert result['severity'] == 'high'


def test_low_expected_ip_warns_about_bullpen_exposure():
    starter = json.loads(row()['starter_profile_json'])
    starter['sides']['home']['metrics']['expected_innings_last5'] = 1.2
    result = evaluate(row(starter_profile_json=json.dumps(starter)))
    assert any(flag['code'] == 'SELECTED_STARTER_LOW_EXPECTED_IP' for flag in result['flags'])
