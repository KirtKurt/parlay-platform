from ks1.coverage import measure


def game(pk, start, *, state='Scheduled'):
    return {'gamePk': pk, 'gameDate': start, 'gameType': 'R',
            'status': {'detailedState': state}}


def row(pk, as_of):
    return {'game_id': str(pk), 'as_of': as_of}


def test_before_cutoff_is_not_counted_as_missing_lock():
    result = measure([game(1, '2026-09-11T20:00:00Z')], [row(1, '2026-09-11T18:00:00Z')],
                     '2026-09-11', '2026-09-11T19:00:00Z')
    assert result['cutoff_reached_games'] == 0
    assert result['future_before_t10_game_ids'] == ['1']
    assert result['lock_coverage_rate'] is None


def test_preserved_pre_cutoff_prediction_counts_as_valid_lock():
    result = measure([game(1, '2026-09-11T20:00:00Z')], [row(1, '2026-09-11T19:45:00Z')],
                     '2026-09-11', '2026-09-11T19:55:00Z')
    assert result['valid_locked_game_ids'] == ['1']
    assert result['missing_locked_game_ids'] == []
    assert result['lock_coverage_rate'] == 1.0


def test_missing_prediction_after_cutoff_is_reported_not_backfilled():
    result = measure([game(1, '2026-09-11T20:00:00Z')], [],
                     '2026-09-11', '2026-09-11T19:55:00Z')
    assert result['valid_locked_games'] == 0
    assert result['missing_locked_game_ids'] == ['1']
    assert result['lock_coverage_rate'] == 0.0
    assert result['backfilled_after_cutoff'] is False


def test_post_cutoff_prediction_is_invalid_not_counted_as_lock():
    result = measure([game(1, '2026-09-11T20:00:00Z')], [row(1, '2026-09-11T19:51:00Z')],
                     '2026-09-11', '2026-09-11T19:55:00Z')
    assert result['invalid_post_cutoff_prediction_game_ids'] == ['1']
    assert result['valid_locked_games'] == 0
    assert result['lock_coverage_rate'] == 0.0


def test_postponed_and_cancelled_games_do_not_reduce_coverage():
    schedule = [game(1, '2026-09-11T20:00:00Z', state='Postponed'),
                game(2, '2026-09-11T20:00:00Z', state='Cancelled'),
                game(3, '2026-09-11T20:00:00Z')]
    result = measure(schedule, [row(3, '2026-09-11T19:40:00Z')],
                     '2026-09-11', '2026-09-11T19:55:00Z')
    assert result['cutoff_reached_games'] == 1
    assert result['valid_locked_game_ids'] == ['3']
    assert result['lock_coverage_rate'] == 1.0


def test_doubleheader_games_are_independent_ids():
    schedule = [game(10, '2026-09-11T18:00:00Z'), game(11, '2026-09-11T22:00:00Z')]
    result = measure(schedule, [row(10, '2026-09-11T17:40:00Z')],
                     '2026-09-11', '2026-09-11T20:00:00Z')
    assert result['valid_locked_game_ids'] == ['10']
    assert result['future_before_t10_game_ids'] == ['11']
    assert result['missing_locked_game_ids'] == []
