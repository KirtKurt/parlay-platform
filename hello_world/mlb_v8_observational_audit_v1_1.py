"""Repeatability hardening for the independent MLB V8 observational audit."""
from __future__ import annotations

from typing import Any, Mapping

import mlb_v8_observational_audit_v1 as _base


VERSION = "MLB-V8-OBSERVATIONAL-AUDIT-v1.1-stable-identities"
_ORIGINAL_BUILD_CANDIDATE = _base.build_candidate
_ORIGINAL_PUT_IMMUTABLE = _base._put_immutable


def build_candidate(*args: Any, **kwargs: Any):
    candidate = _ORIGINAL_BUILD_CANDIDATE(*args, **kwargs)
    # The exact controller-run digest contains the run timestamp.  It is useful
    # in the public runner report but must not rotate an otherwise identical
    # frozen model candidate on every scheduled controller cycle.
    candidate.pop("sourceTrainingResultDigest", None)
    candidate["candidateDigest"] = _base._sha(
        {key: item for key, item in candidate.items() if key != "candidateDigest"}
    )
    return candidate


def _put_immutable(*args: Any, **kwargs: Any):
    pointer = _ORIGINAL_PUT_IMMUTABLE(*args, **kwargs)
    # Existence is an operation result, not immutable artifact identity.  Drop
    # it before grade pointers contribute to the audit digest.
    return {
        key: item for key, item in pointer.items() if key != "alreadyExisted"
    }


def _accuracy(wins: int, losses: int):
    denominator = wins + losses
    return wins / denominator if denominator else None


_base.VERSION = VERSION
_base.build_candidate = build_candidate
_base._put_immutable = _put_immutable
_base._accuracy = _accuracy

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

globals()["VERSION"] = VERSION
