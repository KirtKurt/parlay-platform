from __future__ import annotations

import mlb_v8_observational_audit_v1_2 as audit


def test_incomplete_candidate_is_held_when_new_trainer_identity_arrives(monkeypatch):
    candidate = {
        "sourceTrainingIdentity": "training-old",
        "candidateDigest": "candidate-old",
    }
    pointer = {
        "sourceTrainingIdentity": "training-old",
        "observationalEvidenceComplete": False,
        "candidateArtifact": {
            "bucket": "bucket",
            "key": "candidate.json",
            "sha256": "digest",
        },
    }
    monkeypatch.setattr(
        audit._core,
        "_load_pointer_value",
        lambda _s3, _pointer: dict(candidate),
    )
    monkeypatch.setattr(audit._core, "verify_candidate", lambda _candidate: None)

    loaded, artifact, held = audit._load_reusable_candidate(
        pointer,
        current_source_identity="training-new",
        s3=object(),
    )

    assert loaded == candidate
    assert artifact == pointer["candidateArtifact"]
    assert held is True


def test_completed_candidate_may_rotate_to_new_trainer_identity(monkeypatch):
    pointer = {
        "sourceTrainingIdentity": "training-old",
        "observationalEvidenceComplete": True,
        "candidateArtifact": {
            "bucket": "bucket",
            "key": "candidate.json",
            "sha256": "digest",
        },
    }
    called = False

    def load(_s3, _pointer):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(audit._core, "_load_pointer_value", load)

    loaded, artifact, held = audit._load_reusable_candidate(
        pointer,
        current_source_identity="training-new",
        s3=object(),
    )

    assert loaded is None
    assert artifact == {}
    assert held is False
    assert called is False


def test_completed_candidate_is_reused_when_trainer_identity_is_unchanged(monkeypatch):
    candidate = {
        "sourceTrainingIdentity": "training-same",
        "candidateDigest": "candidate-same",
    }
    pointer = {
        "sourceTrainingIdentity": "training-same",
        "observationalEvidenceComplete": True,
        "candidateArtifact": {
            "bucket": "bucket",
            "key": "candidate.json",
            "sha256": "digest",
        },
    }
    monkeypatch.setattr(
        audit._core,
        "_load_pointer_value",
        lambda _s3, _pointer: dict(candidate),
    )
    monkeypatch.setattr(audit._core, "verify_candidate", lambda _candidate: None)

    loaded, artifact, held = audit._load_reusable_candidate(
        pointer,
        current_source_identity="training-same",
        s3=object(),
    )

    assert loaded == candidate
    assert artifact == pointer["candidateArtifact"]
    assert held is False
