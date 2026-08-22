from mlb_auto import autonomous_handler
from mlb_auto.ml import Model
from mlb_auto.threshold_policy import (
    attach_learned_threshold,
    model_threshold,
    qualifies,
)
from mlb_auto.training_search import _official_pick_audit_gate


def test_live_pick_eligibility_uses_champion_learned_threshold():
    champion = Model(
        0.0, (0.0,), ('signal',),
        metadata={'official_probability_threshold': .73},
    )
    assert model_threshold(champion, .58) == .73
    assert qualifies(champion, .729, .58) is False
    assert qualifies(champion, .73, .58) is True
    assert autonomous_handler.base._qualifies_official_pick(champion, .73) is True
    assert autonomous_handler.base._qualifies_official_pick(champion, .72) is False


def test_threshold_is_learned_from_search_validation_and_persisted_in_model_metadata():
    rows = []
    labels = []
    for i in range(40):
        x = -2.0 + i * .1
        rows.append({'signal': x})
        labels.append(1 if x >= 0 else 0)
    model = Model(0.0, (2.5,), ('signal',), metadata={'autonomous': True})

    adapted, metrics = attach_learned_threshold(model, rows, labels, .58)

    threshold = adapted.metadata['official_probability_threshold']
    assert .50 <= threshold <= .95
    assert adapted.metadata['official_threshold_source'] == metrics['threshold_source']
    assert metrics['threshold_source'] == 'AUTONOMOUS_CHRONOLOGICAL_VALIDATION'
    assert metrics['selection_policy'] == 'ACCURACY_WILSON_NO_ROI_V1'
    assert metrics['selection_count'] >= 10
    assert metrics['selection_accuracy'] is not None


def test_champion_promotion_requires_official_pick_audit_strength():
    assert _official_pick_audit_gate({
        'selection_count': 25,
        'selection_accuracy': .64,
        'selection_wilson_lower_bound': .45,
    })['pass'] is True
    assert _official_pick_audit_gate({
        'selection_count': 33,
        'selection_accuracy': .5758,
        'selection_wilson_lower_bound': .408,
    })['pass'] is False
