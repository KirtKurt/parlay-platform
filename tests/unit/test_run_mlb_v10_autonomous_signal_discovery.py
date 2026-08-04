from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path

import pytest

from hello_world import mlb_v10_autonomous_signal_discovery_v1 as v10


PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_mlb_v10_autonomous_signal_discovery.py"
SPEC = importlib.util.spec_from_file_location("run_v10", PATH)
subject = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(subject)


def _fingerprint(rows):
    material = [
        {
            "slateDateEt": row.get("slateDateEt"),
            "officialGamePk": row.get("officialGamePk"),
            "winner": row.get("winner"),
            "homeSignal": row.get("homeSignal"),
            "awaySignal": row.get("awaySignal"),
            "predictionLockAtUtc": row.get("predictionLockAtUtc"),
        }
        for row in sorted(rows, key=lambda row: (str(row.get("slateDateEt")), str(row.get("officialGamePk"))))
    ]
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class Optimizer:
    FULL_SLATE_LOCK_MINUTES = 45
    dataset_fingerprint = staticmethod(_fingerprint)


class Handler:
    optimizer = Optimizer()

    def __init__(self, dataset):
        self.dataset = dataset

    def _get_s3_json(self, key):
        assert key == "dataset.json"
        return self.dataset, {"sha256": "abc", "key": key, "bucket": "bucket"}


def _row(**extra):
    value = {
        "version": "dataset-v1",
        "slateDateEt": "2026-07-24",
        "officialGamePk": 123,
        "winner": "HOME",
        "homeWon": True,
        "homeSignal": {"marketConsensusProbability": 0.61},
        "awaySignal": {"marketConsensusProbability": 0.39},
        "predictionLockAtUtc": "2026-07-24T22:15:00Z",
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
    }
    value.update(extra)
    return value


def _dataset(row=None, **extra):
    rows = [row or _row()]
    value = {
        "slateDateEt": "2026-07-24",
        "officialGameCount": len(rows),
        "eligibleGameCount": len(rows),
        "exactSlateCoverage": 1.0,
        "completeSlate": True,
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
        "records": rows,
        "exclusions": [],
        "fingerprint": _fingerprint(rows),
    }
    value.update(extra)
    return value


def _state(dataset):
    return {
        "eligibleGameCount": len(dataset["records"]),
        "completeSlateCount": 1,
        "featureRematerializedSlateCount": 1,
        "featureDatasetVersion": "dataset-v1",
        "completedSlates": [
            {
                "slateDateEt": dataset["slateDateEt"],
                "fingerprint": dataset["fingerprint"],
                "artifact": {"key": "dataset.json", "sha256": "abc"},
            }
        ],
    }


def test_derives_v10_row_flags_only_after_immutable_slate_validation():
    dataset = _dataset()
    records, proof = subject._load_canonical_records(Handler(dataset), _state(dataset))
    assert len(records) == 1
    row = records[0]
    assert row["trainingEligible"] is True
    assert row["canonicalLockValid"] is True
    assert row["duplicateContaminated"] is False
    assert row["featureCutoff"] == "each_game_t_minus_45"
    assert row["featureVectorFingerprint"]
    assert row["canonicalEligibilityAuthority"] == "IMMUTABLE_COMPLETE_SLATE_ARTIFACT"
    assert proof["recordCountLoaded"] == 1
    assert proof["rowEligibilityAliasesDerived"] == 1
    assert proof["artifactChecksumValidationApplied"] is True
    assert proof["slateFingerprintValidationApplied"] is True


def test_explicit_row_contradiction_fails_closed():
    dataset = _dataset(_row(canonicalLockValid=False))
    with pytest.raises(RuntimeError, match="explicitly contradicts canonicalLockValid"):
        subject._load_canonical_records(Handler(dataset), _state(dataset))


def test_incomplete_or_non_clipped_dataset_cannot_be_upgraded_to_canonical():
    dataset = _dataset(gameSpecificLockClipping=False)
    with pytest.raises(RuntimeError, match="lost canonical integrity proof"):
        subject._load_canonical_records(Handler(dataset), _state(dataset))


def test_state_count_must_match_immutable_artifact_count():
    dataset = _dataset()
    state = _state(dataset)
    state["eligibleGameCount"] = 2
    with pytest.raises(RuntimeError, match="eligible count disagrees"):
        subject._load_canonical_records(Handler(dataset), state)


def test_material_state_anchor_excludes_heartbeat_revision_and_timestamp():
    dataset = _dataset()
    first = _state(dataset)
    second = dict(first)
    first.update({"revision": 10, "updatedAtUtc": "before"})
    second.update({"revision": 999, "updatedAtUtc": "after"})

    assert subject._state_anchor(first) == subject._state_anchor(second)


def test_unchanged_material_state_uses_fast_path_without_artifact_scan():
    dataset = _dataset()
    state = _state(dataset)
    previous = {
        "ok": True,
        "version": v10.VERSION,
        "state": {
            "eligibleGameCount": 1,
            "completeSlateCount": 1,
            "featureRematerializedSlateCount": 1,
            "featureDatasetVersion": "dataset-v1",
        },
    }

    assert subject._material_state_unchanged(
        previous,
        state,
        expected_version=v10.VERSION,
        force_full=False,
    ) is True


def test_new_slate_or_feature_dataset_forces_v10_canonical_proof():
    dataset = _dataset()
    state = _state(dataset)
    previous = {
        "ok": True,
        "version": v10.VERSION,
        "cadenceAnchor": subject._state_anchor(state),
    }
    state["completeSlateCount"] = 2
    state["featureRematerializedSlateCount"] = 2

    assert subject._material_state_unchanged(
        previous,
        state,
        expected_version=v10.VERSION,
        force_full=False,
    ) is False
    state = _state(dataset)
    state["featureDatasetVersion"] = "dataset-v2"
    assert subject._material_state_unchanged(
        previous,
        state,
        expected_version=v10.VERSION,
        force_full=False,
    ) is False


def test_force_full_bypasses_v10_fast_no_change_cadence():
    dataset = _dataset()
    state = _state(dataset)
    previous = {
        "ok": True,
        "version": v10.VERSION,
        "cadenceAnchor": subject._state_anchor(state),
    }
    assert subject._material_state_unchanged(
        previous,
        state,
        expected_version=v10.VERSION,
        force_full=True,
    ) is False


def test_exact_binomial_is_finite_and_bounded_at_full_corpus_scale():
    for correct, total in ((1954, 3964), (3000, 3964), (0, 3964), (1982, 3964)):
        value = v10._two_sided_binomial_pvalue(correct, total)
        assert math.isfinite(value)
        assert 0.0 <= value <= 1.0
    assert v10._two_sided_binomial_pvalue(1982, 3964) == 1.0
    assert v10._two_sided_binomial_pvalue(3000, 3964) < 1e-20
