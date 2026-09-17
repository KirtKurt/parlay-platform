import pytest

import ks1.forensic_derived_affine_semantic_selection as subject


def test_development_selector_scopes_three_candidate_shortlist(monkeypatch):
    original_screen = subject.base.screen_derived_features
    original_base_shortlist = subject.base.JOINT_SHORTLIST_PER_GROUP
    original_stable_shortlist = subject.stable.JOINT_SHORTLIST_PER_GROUP
    observed = {}

    def fake_development_select(train):
        observed["base_shortlist"] = subject.base.JOINT_SHORTLIST_PER_GROUP
        observed["stable_shortlist"] = subject.stable.JOINT_SHORTLIST_PER_GROUP
        observed["screen"] = subject.base.screen_derived_features
        return None, {}

    monkeypatch.setattr(subject.outer, "development_select", fake_development_select)
    selected, report = subject.development_select(object())

    assert selected is None
    assert observed == {
        "base_shortlist": 3,
        "stable_shortlist": 3,
        "screen": subject.screen_derived_features,
    }
    assert report["contract"].endswith("-v10")
    assert report["bounded_joint_shortlist"] == {
        "per_group": 3,
        "candidate_source": "already_single_family_split_used_only",
        "new_feature_definitions": False,
        "new_hyperparameters": False,
        "outer_development_used_for_shortlisting": False,
        "final_holdout_used_for_shortlisting": False,
    }
    assert subject.base.screen_derived_features is original_screen
    assert subject.base.JOINT_SHORTLIST_PER_GROUP == original_base_shortlist
    assert subject.stable.JOINT_SHORTLIST_PER_GROUP == original_stable_shortlist


def test_development_selector_restores_shortlist_after_failure(monkeypatch):
    original_screen = subject.base.screen_derived_features
    original_base_shortlist = subject.base.JOINT_SHORTLIST_PER_GROUP
    original_stable_shortlist = subject.stable.JOINT_SHORTLIST_PER_GROUP

    def fail(_train):
        assert subject.base.JOINT_SHORTLIST_PER_GROUP == 3
        assert subject.stable.JOINT_SHORTLIST_PER_GROUP == 3
        assert subject.base.screen_derived_features is subject.screen_derived_features
        raise RuntimeError("expected test failure")

    monkeypatch.setattr(subject.outer, "development_select", fail)
    with pytest.raises(RuntimeError, match="expected test failure"):
        subject.development_select(object())

    assert subject.base.screen_derived_features is original_screen
    assert subject.base.JOINT_SHORTLIST_PER_GROUP == original_base_shortlist
    assert subject.stable.JOINT_SHORTLIST_PER_GROUP == original_stable_shortlist
