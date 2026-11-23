from __future__ import annotations

from mlb_auto import team_form


def _game(pk, dt, home_id, home_name, away_id, away_name, home_score, away_score, game_type='R'):
    return {
        'gamePk': pk,
        'gameDate': dt,
        'officialDate': dt[:10],
        'gameType': game_type,
        'status': {'abstractGameState': 'Final'},
        'teams': {
            'home': {'team': {'id': home_id, 'name': home_name}, 'score': home_score},
            'away': {'team': {'id': away_id, 'name': away_name}, 'score': away_score},
        },
    }


def _payload():
    games = [
        _game(1, '2026-08-01T17:00:00Z', 1, 'Home Club', 3, 'Other Club', 5, 2),
        _game(2, '2026-08-02T17:00:00Z', 2, 'Away Club', 1, 'Home Club', 4, 2),
        _game(3, '2026-08-03T17:00:00Z', 1, 'Home Club', 2, 'Away Club', 3, 1),
        _game(4, '2026-08-04T17:00:00Z', 3, 'Other Club', 2, 'Away Club', 5, 1),
        _game(5, '2026-08-05T17:00:00Z', 2, 'Away Club', 3, 'Other Club', 1, 2),
        _game(6, '2026-08-06T17:00:00Z', 3, 'Other Club', 2, 'Away Club', 3, 1),
        _game(7, '2026-08-07T17:00:00Z', 1, 'Home Club', 3, 'Other Club', 6, 2),
        _game(8, '2026-08-08T17:00:00Z', 1, 'Home Club', 2, 'Away Club', 2, 3),
        _game(9, '2026-08-09T17:00:00Z', 1, 'Home Club', 3, 'Other Club', 4, 1),
        _game(10, '2026-08-10T17:00:00Z', 2, 'Away Club', 3, 'Other Club', 1, 5),
        _game(11, '2026-08-11T17:00:00Z', 1, 'Home Club', 2, 'Away Club', 7, 1),
        _game(12, '2026-08-12T17:00:00Z', 2, 'Away Club', 3, 'Other Club', 2, 3),
        # Same-day final is intentionally excluded from historical reconstruction.
        _game(13, '2026-08-13T15:00:00Z', 1, 'Home Club', 3, 'Other Club', 9, 0),
    ]
    return {'dates': [{'games': [game]} for game in games]}


def test_point_in_time_records_recent_windows_and_streaks_exclude_same_day_history():
    context = team_form.build_team_form_context(
        'Home Club',
        'Away Club',
        '2026-08-13T20:00:00Z',
        historical=True,
        schedule_payload=_payload(),
    )

    assert context['ok'] is True
    features = context['features']
    assert features['form_home_wins'] == 5
    assert features['form_home_losses'] == 2
    assert features['form_home_last3_wins'] == 2
    assert features['form_home_last7_wins'] == 5
    assert features['form_home_streak_signed'] == 2
    assert features['form_home_win_streak'] == 2
    assert features['form_home_loss_streak'] == 0
    assert features['form_away_last3_wins'] == 0
    assert features['form_away_last7_wins'] == 1
    assert features['form_away_streak_signed'] == -3
    assert features['form_away_loss_streak'] == 3
    assert features['form_home_wl_lag1'] == 1
    assert features['form_home_wl_lag3'] == -1
    assert features['form_same_day_results_included'] == 0
    assert context['metadata']['same_day_policy'].startswith('EXCLUDE_ALL_SAME_DAY')


def test_live_context_can_use_same_day_result_already_final_at_pull_time():
    context = team_form.build_team_form_context(
        'Home Club',
        'Away Club',
        '2026-08-13T20:00:00Z',
        historical=False,
        schedule_payload=_payload(),
    )

    assert context['ok'] is True
    assert context['features']['form_home_wins'] == 6
    assert context['features']['form_home_streak_signed'] == 3
    assert context['features']['form_same_day_results_included'] == 1


def test_existing_training_rows_are_rematerialized_without_changing_labels(monkeypatch):
    row = {
        'PK': 'MLB_AUTO#TRAINING_EXAMPLES',
        'SK': '2026-08-13#event-1',
        'slate_date': '2026-08-13',
        'event_id': 'event-1',
        'commence_time': '2026-08-13T21:00:00Z',
        'source_pull_at': '2026-08-13T20:15:00Z',
        'home_team': 'Home Club',
        'away_team': 'Away Club',
        'label_home_win': 1,
        'features': {'market_home_probability': 0.55},
    }

    class FakeStore:
        instance = None

        def __init__(self):
            FakeStore.instance = self
            self.rows = [dict(row)]
            self.states = {}
            self.updated = []

        def query_training_examples(self, limit=5000):
            return list(self.rows)

        def get_state(self, name='controller'):
            return dict(self.states.get(name) or {})

        def put_state(self, name, item, **kwargs):
            self.states[name] = dict(item)

        def update_training_example_features(self, slate, event_id, features, fields=None):
            self.updated.append((slate, event_id, dict(features), dict(fields or {})))

    monkeypatch.setattr(team_form, '_season_schedule', lambda season: _payload())
    result = team_form.rematerialize_training_examples(Store=FakeStore, limit=10)

    assert result['ok'] is True
    assert result['enriched'] == 1
    assert result['failed'] == 0
    assert result['coverage_fraction'] == 1
    assert result['feature_family_became_ready'] is True
    store = FakeStore.instance
    assert store is not None and len(store.updated) == 1
    _, _, features, fields = store.updated[0]
    assert features['market_home_probability'] == 0.55
    assert features['form_available'] == 1
    assert fields['team_form_available'] is True
    assert row['label_home_win'] == 1


def test_autonomous_model_and_llm_receive_form_pattern_features():
    from mlb_auto.evolution import candidate_numeric_features
    from mlb_auto.llm_rd import _safe_names

    rows = [{
        'market_home_probability': 0.45 + 0.01 * (index % 5),
        'form_home_last3_wins': float(index % 4),
        'form_away_last7_wins': float(index % 8),
        'form_home_streak_signed': float((index % 5) - 2),
        'form_wl_lag1_delta': float((index % 3) - 1),
    } for index in range(20)]

    discovered = candidate_numeric_features(rows)
    llm_allowed = _safe_names(rows)
    for name in (
        'form_home_last3_wins',
        'form_away_last7_wins',
        'form_home_streak_signed',
        'form_wl_lag1_delta',
    ):
        assert name in discovered
        assert name in llm_allowed
