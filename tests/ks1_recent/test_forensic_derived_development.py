import pandas as pd

from ks1.forensic_derived_development import _exclude_reserved_holdout_pitches


def test_reserved_holdout_pitch_rows_never_reach_development_feature_engine():
    context = {
        'rows': [
            {'game_pk': '10', 'pitcher': '1'},
            {'game_pk': '20', 'pitcher': '2'},
            {'game_pk': '30', 'pitcher': '3'},
        ],
        'retained_dates': ['2026-09-01'],
    }
    evidence = {'enabled': True, 'provider_requests': 0}
    holdout = pd.DataFrame({'game_id': ['20', '99']})

    filtered, replay = _exclude_reserved_holdout_pitches(context, evidence, holdout)

    assert [row['game_pk'] for row in filtered['rows']] == ['10', '30']
    assert replay['retained_pitch_rows_before_holdout_exclusion'] == 3
    assert replay['retained_pitch_rows_after_holdout_exclusion'] == 2
    assert replay['reserved_holdout_pitch_rows_removed'] == 1
    assert replay['reserved_holdout_game_ids_supplied_to_feature_engine'] == 0
    assert context['rows'][1]['game_pk'] == '20'
