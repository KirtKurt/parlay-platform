from __future__ import annotations

from types import SimpleNamespace

from mlb_auto import autonomous_handler, handler
from mlb_auto.engine import market_depth_features
from mlb_auto.evolution import candidate_numeric_features
from mlb_auto.storage import Store


class _StateTable:
    def __init__(self):
        self.calls = []

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        return {'Attributes': {'PK': 'MLB_AUTO#STATE', 'SK': 'controller'}}


class _PagedTable:
    def __init__(self):
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if 'ExclusiveStartKey' not in kwargs:
            return {
                'Items': [{'SK': '2026-01-01#a'}, {'SK': '2026-01-01#b'}],
                'LastEvaluatedKey': {'PK': 'x', 'SK': 'page-2'},
            }
        return {'Items': [{'SK': '2026-01-01#c'}]}


def test_state_updates_are_field_level_and_timestamp_guarded():
    store = Store.__new__(Store)
    store.state = _StateTable()

    store.put_state(
        'controller',
        {'heartbeat_at': '2026-08-13T12:00:00+00:00', 'known_market_key_count': 98},
    )

    assert len(store.state.calls) == 2
    guarded, ordinary = store.state.calls
    assert guarded['ConditionExpression'] == 'attribute_not_exists(#field) OR #field <= :value'
    assert guarded['ExpressionAttributeNames'] == {'#field': 'heartbeat_at'}
    assert ordinary['UpdateExpression'].startswith('SET ')
    assert 'known_market_key_count' in ordinary['ExpressionAttributeNames'].values()
    assert not hasattr(store.state, 'put_item')


def test_training_query_reads_all_dynamodb_pages():
    store = Store.__new__(Store)
    store.outcomes = _PagedTable()

    rows = store.query_training_examples(limit=3)

    assert [row['SK'] for row in rows] == [
        '2026-01-01#a', '2026-01-01#b', '2026-01-01#c',
    ]
    assert len(store.outcomes.calls) == 2
    assert store.outcomes.calls[1]['ExclusiveStartKey']['SK'] == 'page-2'


def test_official_pick_policy_is_confidence_only_not_ev_or_price():
    champion = object()
    assert handler._qualifies_official_pick(champion, handler.MIN_OFFICIAL_PROB)
    assert not handler._qualifies_official_pick(champion, handler.MIN_OFFICIAL_PROB - .0001)
    assert not handler._qualifies_official_pick(None, .99)


def test_period_depth_counts_provider_inning_key_shapes():
    detail = {
        'bookmakers': [{
            'key': 'book',
            'markets': [
                {'key': 'h2h_1st_5_innings', 'outcomes': []},
                {'key': 'alternate_totals_2nd_inning', 'outcomes': []},
                {'key': 'pitcher_strikeouts', 'outcomes': []},
            ],
        }],
    }
    assert market_depth_features(detail)['period_market_count'] == 2.0


def test_score_and_result_fields_are_blocked_in_any_name_order():
    rows = [{
        'home_score': i,
        'score_away': i,
        'final_home_score': i,
        'label_home_win': i % 2,
        'actual_winner_code': i % 2,
        'market_outcome_count': 100 + i,
        'pregame_signal': i / 10,
    } for i in range(10)]

    names = candidate_numeric_features(rows)

    assert 'pregame_signal' in names
    assert 'market_outcome_count' in names
    assert not {'home_score', 'score_away', 'final_home_score', 'label_home_win', 'actual_winner_code'} & set(names)


class _TrainingStore:
    def __init__(self, examples, state=None):
        self.examples = examples
        self.state = dict(state or {})
        self.models = []
        self.state_writes = []

    def query_training_examples(self, limit=5000):
        return list(self.examples)[:limit]

    def get_state(self, name='controller'):
        return dict(self.state)

    def get_model(self, sk='CHAMPION'):
        return {}

    def put_model(self, sk, item):
        self.models.append((sk, item))

    def put_state(self, name, item, **kwargs):
        self.state.update(item)
        self.state_writes.append((name, dict(item), dict(kwargs)))


class _DummyModel:
    def dumps(self):
        return '{"intercept":0,"weights":[0],"feature_names":["signal"]}'


def _examples(count=250, label=lambda i: i % 2):
    return [{
        'SK': f'2026-01-{1 + i // 15:02d}#{i:04d}',
        'commence_time': f'2026-01-{1 + i // 15:02d}T12:00:00+00:00',
        'features': {'signal': i / max(1, count - 1), 'market_home_probability': .4 + .2 * (i % 2)},
        'label_home_win': int(label(i)),
    } for i in range(count)]


def test_autonomous_search_never_receives_final_audit_holdout(monkeypatch):
    store = _TrainingStore(_examples())
    seen = {}
    discovered = SimpleNamespace(
        model=_DummyModel(),
        search_manifest={'trainingRows': 160, 'validationRows': 40},
        feature_names=('signal',),
        metrics={'logLoss': .6},
    )

    def fake_discover(rows, labels, **kwargs):
        seen['search_rows'] = list(rows)
        seen['search_labels'] = list(labels)
        seen['search_kwargs'] = dict(kwargs)
        return discovered

    def fake_promote(**kwargs):
        seen['audit_rows'] = list(kwargs['validation_rows'])
        seen['audit_labels'] = list(kwargs['validation_labels'])
        return {'promote': True, 'reason': 'TEST', 'challengerLogLoss': .5}

    monkeypatch.setattr(autonomous_handler, 'Store', lambda: store)
    monkeypatch.setattr(autonomous_handler, 'discover_challenger', fake_discover)
    monkeypatch.setattr(autonomous_handler, 'promote_challenger', fake_promote)
    monkeypatch.setenv('MLB_AUTO_DEPLOY_GIT_SHA', 'hardening-test-sha')

    result = autonomous_handler.autonomous_train()

    assert result['trained'] is True
    assert len(seen['search_rows']) == 200
    assert len(seen['audit_rows']) == 50
    assert seen['search_kwargs'] == {'min_train': 160, 'min_validation': 40}
    assert result['search_manifest']['untouchedAuditRows'] == 50
    assert result['search_manifest']['validationPolicy'].endswith('untouched_audit_v1')
    assert any(sk == 'CHAMPION' for sk, _ in store.models)


def test_training_does_not_refit_without_minimum_new_evidence(monkeypatch):
    state = {
        'last_training_attempt_count': 250,
        'last_training_attempt_git_sha': 'same-sha',
    }
    store = _TrainingStore(_examples(260), state=state)
    monkeypatch.setattr(autonomous_handler, 'Store', lambda: store)
    monkeypatch.setenv('MLB_AUTO_DEPLOY_GIT_SHA', 'same-sha')

    result = autonomous_handler.autonomous_train()

    assert result['reason'] == 'NO_NEW_EVIDENCE'
    assert result['new_examples'] == 10
    assert store.models == []


def test_single_class_training_corpus_is_rejected(monkeypatch):
    store = _TrainingStore(_examples(250, label=lambda _: 1))
    monkeypatch.setattr(autonomous_handler, 'Store', lambda: store)
    monkeypatch.setenv('MLB_AUTO_DEPLOY_GIT_SHA', 'class-test-sha')

    result = autonomous_handler.autonomous_train()

    assert result['reason'] == 'INSUFFICIENT_LABEL_DIVERSITY'
    assert store.models == []
    assert store.state['last_training_attempt_result'] == 'INSUFFICIENT_LABEL_DIVERSITY'
