from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple


VERSION = (
    "MLB-R7-SOURCE-HONEST-TRAINING-ADMISSION-"
    "v3-trusted-process-receipt"
)
SNAPSHOT_POLICY_VERSION = "MLB-R7-SNAPSHOT-POLICY-v1-lock-safe-missingness"
LABEL_LOCK_BINDING_VERSION = "MLB-R7-LABEL-LOCK-BINDING-v1-exact-immutable"
JOINED_SNAPSHOT_BINDING_VERSION = (
    "MLB-R7-JOINED-SNAPSHOT-BINDING-v2-trusted-process-receipt"
)
JOINED_TRUSTED_RECEIPT_VERSION = (
    "MLB-R7-JOINED-TRUSTED-RECEIPT-v1-immutable-label-lock-source"
)
MODEL_POLICY_VERSION = "MLB-R7-MODEL-POLICY-v1-inactive-all-missing-features"
INCOMPLETE_PREFIX = "fundamentals_v2_incomplete:"
REQUIRED_MISSINGNESS_MASKS = frozenset(
    {
        "fundamentalPitchingMissing",
        "fundamentalOffenseLineupMissing",
    }
)
OPTIONAL_ALL_MISSING_NUMERIC_FEATURES = frozenset(
    {
        "starterCompositeGapHome",
        "bullpenCompositeGapHome",
        "lineupWrcPlusGapHome",
    }
)
OPTIONAL_FEATURE_MASK = {
    "starterCompositeGapHome": "fundamentalPitchingMissing",
    "bullpenCompositeGapHome": "fundamentalPitchingMissing",
    "lineupWrcPlusGapHome": "fundamentalOffenseLineupMissing",
}
_AUTHORITY_INTEGRITY_FLAGS = (
    "verified",
    "consistentRead",
    "immutableLocked",
    "stageAuthorityVerified",
    "persistedStageAuthorityValidated",
    "officialAuditEligible",
    "exactLockVectorValidated",
    "selectionLockVectorStatusValidated",
)
_LABEL_PATCH_FLAG = "_INQSI_MLB_R7_SOURCE_HONEST_LABEL_PATCH_V3"
_EXPERIMENT_PATCH_FLAG = "_INQSI_MLB_R7_SOURCE_HONEST_EXPERIMENT_PATCH_V3"
_DUAL_PATCH_FLAG = "_INQSI_MLB_R7_SOURCE_HONEST_DUAL_PATCH_V3"
_JOINED_RECEIPT_ID_FIELD = "r7SourceHonestTrustedReceiptId"
_PROSPECTIVE_READ_REPAIR_VERSION_FIELD = (
    "prospectiveTrainerReadRepairVersion"
)
_JOINED_RECEIPT_REGISTRY_MAX_ITEMS = 4096
_JOINED_RECEIPT_REGISTRY: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_JOINED_RECEIPT_REGISTRY_LOCK = threading.RLock()


def _strings(values: Any) -> set[str]:
    return {str(value) for value in (values or []) if str(value)}


def _optional_number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _vector(row: Mapping[str, Any]) -> Dict[str, Any]:
    value = row.get("featureSnapshot") or row.get("frozenFeatureVector") or {}
    return dict(value) if isinstance(value, Mapping) else {}


def _snapshot(row: Mapping[str, Any]) -> Dict[str, Any]:
    vector = _vector(row)
    value = row.get("fundamentalsSnapshotV2") or vector.get(
        "fundamentalsSnapshotV2"
    )
    return dict(value) if isinstance(value, Mapping) else {}


def _prediction_time(row: Mapping[str, Any]) -> Any:
    vector = _vector(row)
    return row.get("predictionPersistedAtUtc") or vector.get(
        "predictionPersistedAtUtc"
    )


def _lock_time(row: Mapping[str, Any]) -> Any:
    vector = _vector(row)
    slate_lock = row.get("slatePredictionLock") or {}
    return (
        vector.get("lockAtUtc")
        or row.get("lockAtUtc")
        or row.get("lockedAtUtc")
        or row.get("lockedAt")
        or (slate_lock.get("lockAtUtc") if isinstance(slate_lock, Mapping) else None)
    )


def _source_honest_incomplete_reasons(values: Iterable[Any]) -> set[str]:
    return {
        str(value)
        for value in (values or [])
        if str(value).startswith(INCOMPLETE_PREFIX)
    }


def _authority_integrity_proven(authority: Any) -> bool:
    return bool(
        isinstance(authority, Mapping)
        and authority
        and all(authority.get(flag) is True for flag in _AUTHORITY_INTEGRITY_FLAGS)
    )


def validate_snapshot_for_r7_training(
    snapshot: Any,
    prediction_time_utc: Any,
    lock_time_utc: Any,
) -> Tuple[bool, List[str]]:
    """Accept complete or honestly incomplete immutable pre-lock V2 evidence.

    This is intentionally narrower than production-pick eligibility and broader
    than ``validate_snapshot``. The schema, exact fingerprint, source
    provenance, missing-value masks, no-postgame policy, and time boundary must
    all validate. The only tolerated training exclusions are the V2 builder's
    exact ``fundamentals_v2_incomplete:<group>`` reasons.
    """

    try:
        import mlb_fundamentals_snapshot_v2 as fundamentals
    except Exception:
        return False, ["r7_fundamentals_v2_validator_unavailable"]

    errors = list(fundamentals.validate(snapshot))
    if errors:
        return False, sorted(set(str(value) for value in errors if str(value)))
    if not fundamentals.provenance_is_lock_safe(
        snapshot,
        prediction_persisted_at=prediction_time_utc,
        lock_at=lock_time_utc,
    ):
        return False, ["r7_fundamentals_v2_evidence_not_lock_safe"]

    missing_groups = {
        str(value) for value in (snapshot.get("missingGroups") or []) if str(value)
    }
    expected_exclusions = {
        f"{INCOMPLETE_PREFIX}{group}" for group in missing_groups
    }
    actual_exclusions = _strings(snapshot.get("trainingExclusionReasons"))
    reasons: List[str] = []
    if actual_exclusions != expected_exclusions:
        reasons.append("r7_fundamentals_v2_incomplete_reason_contract_mismatch")
    expected_complete = not missing_groups
    if snapshot.get("pregameComplete") is not expected_complete:
        reasons.append("r7_fundamentals_v2_pregame_complete_contract_mismatch")
    if snapshot.get("trainingEligibleAtCapture") is not expected_complete:
        reasons.append("r7_fundamentals_v2_capture_eligibility_contract_mismatch")
    if snapshot.get("missingValuesAreNull") is not True:
        reasons.append("r7_fundamentals_v2_null_missingness_policy_missing")
    if snapshot.get("immutableAtTMinus45") is not True:
        reasons.append("r7_fundamentals_v2_immutable_lock_policy_missing")
    if snapshot.get("latePlayabilityMayRewriteSnapshotOrVector") is not False:
        reasons.append("r7_fundamentals_v2_late_rewrite_protection_missing")
    if snapshot.get("closingLineValueCountsTowardPregameCompleteness") is not False:
        reasons.append("r7_fundamentals_v2_postgame_clv_exclusion_missing")
    forbidden = {"closing_line_value", "closingLineValue", "beatsClose"}
    if forbidden & set((snapshot.get("groups") or {}).keys()):
        reasons.append("r7_fundamentals_v2_contains_postgame_group")
    return not reasons, sorted(set(reasons))


def derived_missingness(snapshot: Mapping[str, Any]) -> Dict[str, float]:
    groups = snapshot.get("groups") or {}

    def group_missing(name: str, required_values: Sequence[str]) -> float:
        group = groups.get(name) if isinstance(groups, Mapping) else None
        if not isinstance(group, Mapping):
            return 1.0
        if str(group.get("status") or "").upper() not in {
            "CONNECTED",
            "PARTIAL",
            "PARTIAL_MISSING_REQUIRED_VALUES",
        }:
            return 1.0
        values = group.get("values") or {}
        return (
            0.0
            if all(_optional_number(values.get(key)) is not None for key in required_values)
            else 1.0
        )

    return {
        "fundamentalPitchingMissing": max(
            group_missing(
                "starter_quality", ("homeComposite", "awayComposite")
            ),
            group_missing(
                "bullpen_availability", ("homeComposite", "awayComposite")
            ),
        ),
        "fundamentalOffenseLineupMissing": group_missing(
            "confirmed_lineups", ("homeWrcPlus", "awayWrcPlus")
        ),
    }


def _exact_decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        return str(value)
    sign, raw_digits, exponent = value.as_tuple()
    digits = list(raw_digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if not digits:
        return "0"
    coefficient = "".join(str(digit) for digit in digits)
    return f"{'-' if sign else ''}{coefficient}e{exponent}"


def _canonical_receipt_value(value: Any) -> Any:
    """Encode trusted join material without float/Decimal round-trip drift."""

    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, Decimal):
        return ["number", _exact_decimal_text(value)]
    if isinstance(value, int):
        return ["number", _exact_decimal_text(Decimal(value))]
    if isinstance(value, float):
        return ["number", _exact_decimal_text(Decimal(str(value)))]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, (list, tuple)):
        return ["list", [_canonical_receipt_value(item) for item in value]]
    if isinstance(value, Mapping):
        encoded_items = [
            (
                _canonical_receipt_value(key),
                _canonical_receipt_value(item),
            )
            for key, item in value.items()
        ]
        encoded_items.sort(
            key=lambda pair: json.dumps(
                pair[0],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )
        return [
            "object",
            encoded_items,
        ]
    return [
        "other",
        f"{type(value).__module__}.{type(value).__qualname__}",
        str(value),
    ]


def _receipt_material_fingerprint(material: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_receipt_value(material),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _register_joined_receipt(
    material: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> str:
    """Register one bounded in-process proof derived only from label + lock."""

    material_fingerprint = _receipt_material_fingerprint(material)
    receipt = {
        "version": JOINED_TRUSTED_RECEIPT_VERSION,
        "materialFingerprint": material_fingerprint,
        "joinedBinding": copy.deepcopy(dict(binding)),
    }
    with _JOINED_RECEIPT_REGISTRY_LOCK:
        existing = _JOINED_RECEIPT_REGISTRY.get(material_fingerprint)
        if existing is not None and existing != receipt:
            raise RuntimeError("R7_JOINED_TRUSTED_RECEIPT_COLLISION")
        _JOINED_RECEIPT_REGISTRY[material_fingerprint] = receipt
        _JOINED_RECEIPT_REGISTRY.move_to_end(material_fingerprint)
        while (
            len(_JOINED_RECEIPT_REGISTRY)
            > _JOINED_RECEIPT_REGISTRY_MAX_ITEMS
        ):
            _JOINED_RECEIPT_REGISTRY.popitem(last=False)
    return material_fingerprint


def _joined_receipt(receipt_id: Any) -> Optional[Dict[str, Any]]:
    try:
        value = str(receipt_id or "")
    except Exception:
        return None
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        return None
    with _JOINED_RECEIPT_REGISTRY_LOCK:
        receipt = _JOINED_RECEIPT_REGISTRY.get(value)
        if receipt is None:
            return None
        _JOINED_RECEIPT_REGISTRY.move_to_end(value)
        try:
            return copy.deepcopy(receipt)
        except Exception:
            return None


def _locked_vector(locked: Mapping[str, Any]) -> Dict[str, Any]:
    value = locked.get("frozenFeatureVector")
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _locked_snapshot(locked: Mapping[str, Any]) -> Dict[str, Any]:
    value = locked.get("fundamentalsSnapshotV2")
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _locked_snapshot_reference(locked: Mapping[str, Any]) -> Dict[str, Any]:
    value = locked.get("fundamentalsSnapshotV2Ref") or locked.get(
        "fundamentalsSnapshotRefV2"
    )
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _installed_prospective_join_version(
    labels: Any,
    original_join: Any,
) -> Tuple[Optional[str], List[str]]:
    """Trust the read-repair annotation only from its exact installed wrapper."""

    try:
        import mlb_prospective_trainer_read_repair as prospective_repair
    except Exception:
        prospective_repair = None

    installed_version = getattr(
        labels,
        "MLB_PROSPECTIVE_TRAINER_READ_REPAIR_VERSION",
        None,
    )
    if prospective_repair is None:
        if installed_version is None:
            return None, []
        return None, ["r7_prospective_read_repair_contract_unavailable"]

    wrapper_installed = bool(
        getattr(original_join, prospective_repair._JOIN_FLAG, False)
    )
    module_installed = bool(
        getattr(labels, prospective_repair._INSTALL_FLAG, False)
    )
    if (
        not wrapper_installed
        and not module_installed
        and installed_version is None
    ):
        return None, []
    if (
        not wrapper_installed
        or not module_installed
        or installed_version != prospective_repair.VERSION
    ):
        return None, ["r7_prospective_read_repair_installation_mismatch"]
    return prospective_repair.VERSION, []


def _joined_binding(
    label: Mapping[str, Any],
    locked: Mapping[str, Any],
    *,
    prospective_read_repair_version: Optional[str] = None,
) -> Dict[str, Any]:
    authority = locked.get("canonicalLockAuthority") or {}
    snapshot = _locked_snapshot(locked)
    vector = _locked_vector(locked)
    reference = _locked_snapshot_reference(locked)
    binding = {
        "version": JOINED_SNAPSHOT_BINDING_VERSION,
        "snapshotFingerprint": snapshot.get("fingerprint"),
        "referenceFingerprint": reference.get("fingerprint"),
        "vectorSnapshotFingerprint": vector.get(
            "fundamentalsSnapshotV2Fingerprint"
        ),
        "vectorFingerprint": vector.get("fingerprint"),
        "officialGamePk": label.get("official_game_pk"),
        "canonicalLockPk": label.get("canonical_lock_pk"),
        "canonicalLockSk": label.get("canonical_lock_sk"),
        "stageFingerprint": authority.get("stageFingerprint"),
        "lockPayloadFingerprint": label.get(
            "canonical_lock_payload_fingerprint"
        ),
        "settlementFingerprint": label.get("settlement_fingerprint"),
        "recordFingerprint": label.get("record_fingerprint"),
        "labelVectorFingerprint": label.get(
            "frozen_feature_vector_fingerprint"
        ),
        "labelSnapshotFingerprint": label.get(
            "fundamentals_snapshot_v2_fingerprint"
        ),
    }
    if prospective_read_repair_version is not None:
        binding[_PROSPECTIVE_READ_REPAIR_VERSION_FIELD] = (
            prospective_read_repair_version
        )
    return binding


def _trusted_join_material(
    *,
    slate_date: str,
    slate_finalized: bool,
    label: Mapping[str, Any],
    locked: Mapping[str, Any],
    binding: Mapping[str, Any],
    prospective_read_repair_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the exact admitted row expectation from immutable sources only."""

    vector = _locked_vector(locked)
    snapshot = _locked_snapshot(locked)
    reference = _locked_snapshot_reference(locked)
    masks = derived_missingness(snapshot)
    joined_row = {
        "gameId": locked.get("gameId") or locked.get("gameIdentity"),
        "officialGamePk": label.get("official_game_pk"),
        "providerEventId": label.get("provider_event_id"),
        "slateDateEt": slate_date,
        "slateFinalized": slate_finalized,
        "commenceTime": locked.get("commenceTime"),
        "homeTeam": locked.get("homeTeam"),
        "awayTeam": locked.get("awayTeam"),
        "predictedWinner": locked.get("predictedWinner"),
        "predictedSide": locked.get("predictedSide"),
        "lockedAmericanOdds": locked.get(
            "lockedAmericanOdds",
            locked.get("americanOdds"),
        ),
        "predictionPersistedAtUtc": locked.get(
            "predictionPersistedAtUtc"
        ),
        "winner": label.get("winner"),
        "homeWon": label.get("home_won"),
        "correct": label.get("correct"),
        "pickCorrect": label.get("correct"),
        "labelStatus": "FINAL",
        "labelFingerprint": label.get("settlement_fingerprint"),
        "labelRecordFingerprint": label.get("record_fingerprint"),
        "labelSource": label.get("source"),
        "labelSourcePayloadFingerprint": label.get(
            "source_payload_fingerprint"
        ),
        "labelRetrievedAtUtc": label.get("observed_at_utc"),
        "canonicalLockPk": label.get("canonical_lock_pk"),
        "canonicalLockSk": label.get("canonical_lock_sk"),
        "canonicalStageFingerprint": label.get(
            "canonical_stage_fingerprint"
        ),
        "canonicalLockPayloadFingerprint": label.get(
            "canonical_lock_payload_fingerprint"
        ),
        "featureSnapshot": vector,
        "frozenFeatureVector": copy.deepcopy(vector),
        "fundamentalsSnapshotV2": snapshot,
        "fundamentalsSnapshotV2Ref": reference,
        "trainingEligible": True,
        "trainingExclusionReasons": [],
        "r7SourceHonestTrainingAdmission": True,
        "r7SourceHonestTrainingPolicyVersion": VERSION,
        "r7SourceHonestSnapshotPolicyVersion": SNAPSHOT_POLICY_VERSION,
        "r7SourceHonestLabelLockBindingVersion": (
            LABEL_LOCK_BINDING_VERSION
        ),
        "r7SourceHonestMissingnessMasks": masks,
        "r7SourceHonestSafetyReasons": [],
        "r7SourceHonestJoinedBinding": copy.deepcopy(dict(binding)),
        "immutablePregameVectorMutated": False,
        "immutableLockPayloadMutated": False,
        "immutableLabelPayloadMutated": False,
        "productionPickEligibilityChanged": False,
    }
    if prospective_read_repair_version is not None:
        joined_row[_PROSPECTIVE_READ_REPAIR_VERSION_FIELD] = (
            prospective_read_repair_version
        )
    return {
        "receiptVersion": JOINED_TRUSTED_RECEIPT_VERSION,
        "joinedRow": joined_row,
    }


def _current_join_material(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "receiptVersion": JOINED_TRUSTED_RECEIPT_VERSION,
        "joinedRow": {
            key: copy.deepcopy(value)
            for key, value in row.items()
            if key != _JOINED_RECEIPT_ID_FIELD
        },
    }


def _joined_snapshot_binding_reasons(row: Mapping[str, Any]) -> List[str]:
    reasons: List[str] = []
    snapshot = row.get("fundamentalsSnapshotV2")
    reference = row.get("fundamentalsSnapshotV2Ref")
    feature = row.get("featureSnapshot")
    frozen = row.get("frozenFeatureVector")
    if not all(
        isinstance(value, Mapping)
        for value in (snapshot, reference, feature, frozen)
    ):
        return ["r7_joined_snapshot_vector_or_reference_missing"]
    if dict(feature) != dict(frozen):
        reasons.append("r7_joined_frozen_vector_copy_mismatch")
    snapshot_fingerprint = snapshot.get("fingerprint")
    if reference.get("fingerprint") != snapshot_fingerprint:
        reasons.append(
            "r7_joined_fundamentals_snapshot_v2_fingerprint_not_bound_to_reference"
        )
    if (
        frozen.get("fundamentalsSnapshotV2Fingerprint")
        != snapshot_fingerprint
        or frozen.get("fundamentalsSnapshotV2Ref") != reference
    ):
        reasons.append(
            "r7_joined_fundamentals_snapshot_v2_not_bound_to_frozen_vector"
        )
    try:
        import mlb_ml_clean_cohort_v1 as cohort

        frozen_fingerprint_ok = bool(
            frozen.get("fingerprintVersion") == cohort.FINGERPRINT_VERSION
            and frozen.get("fingerprint")
            == cohort.fingerprint_for_vector(dict(frozen))
        )
    except Exception:
        frozen_fingerprint_ok = False
    if not frozen_fingerprint_ok:
        reasons.append("r7_joined_frozen_vector_fingerprint_mismatch")

    binding_raw = row.get("r7SourceHonestJoinedBinding")
    binding = dict(binding_raw) if isinstance(binding_raw, Mapping) else {}
    if binding.get("version") != JOINED_SNAPSHOT_BINDING_VERSION:
        reasons.append("r7_joined_binding_version_mismatch")
    if not all(
        str(binding.get(field) or "")
        for field in (
            "stageFingerprint",
            "lockPayloadFingerprint",
            "settlementFingerprint",
            "recordFingerprint",
            "labelVectorFingerprint",
            "labelSnapshotFingerprint",
            "officialGamePk",
            "canonicalLockPk",
            "canonicalLockSk",
        )
    ):
        reasons.append("r7_joined_canonical_stage_or_lock_proof_mismatch")
    row_has_prospective_version = (
        _PROSPECTIVE_READ_REPAIR_VERSION_FIELD in row
    )
    binding_has_prospective_version = (
        _PROSPECTIVE_READ_REPAIR_VERSION_FIELD in binding
    )
    if (
        row_has_prospective_version != binding_has_prospective_version
        or (
            row_has_prospective_version
            and (
                not str(
                    row.get(_PROSPECTIVE_READ_REPAIR_VERSION_FIELD) or ""
                )
                or row.get(_PROSPECTIVE_READ_REPAIR_VERSION_FIELD)
                != binding.get(_PROSPECTIVE_READ_REPAIR_VERSION_FIELD)
            )
        )
    ):
        reasons.append("r7_joined_prospective_read_repair_version_mismatch")

    receipt_id = row.get(_JOINED_RECEIPT_ID_FIELD)
    receipt = _joined_receipt(receipt_id)
    if receipt is None:
        reasons.append("r7_joined_trusted_receipt_missing_or_evicted")
    else:
        try:
            current_material_fingerprint = _receipt_material_fingerprint(
                _current_join_material(row)
            )
        except Exception:
            current_material_fingerprint = None
        if (
            receipt.get("version") != JOINED_TRUSTED_RECEIPT_VERSION
            or receipt.get("joinedBinding") != binding
            or receipt.get("materialFingerprint")
            != current_material_fingerprint
            or str(receipt_id or "")
            != str(receipt.get("materialFingerprint") or "")
        ):
            reasons.append("r7_joined_trusted_receipt_material_mismatch")
    return sorted(set(reasons))


def _exact_lock_proven(row: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    vector = _vector(row)
    freeze = row.get("mlFeatureFreeze") or {}
    authority = row.get("canonicalLockAuthority") or {}
    exact_errors = sorted(
        {
            *_strings(row.get("exactVectorValidationErrors")),
            *_strings(
                freeze.get("exactVectorValidationErrors")
                if isinstance(freeze, Mapping)
                else []
            ),
            *_strings(
                authority.get("exactLockVectorValidationErrors")
                if isinstance(authority, Mapping)
                else []
            ),
            *_strings(
                authority.get("selectionLockVectorStatusValidationErrors")
                if isinstance(authority, Mapping)
                else []
            ),
        }
    )
    stored_values: List[Any] = []
    if "exactVectorVerified" in row:
        stored_values.append(row.get("exactVectorVerified"))
    if isinstance(freeze, Mapping) and "exactVectorVerified" in freeze:
        stored_values.append(freeze.get("exactVectorVerified"))
    stored_exact = bool(stored_values and all(value is True for value in stored_values))
    authority_exact = _authority_integrity_proven(authority)
    immutable = bool(
        row.get("immutablePerGameStage") is True
        or row.get("immutableLockedStorage") is True
    )
    reasons: List[str] = []
    if row.get("lockedPrediction") is not True:
        reasons.append("r7_locked_prediction_marker_missing")
    if not immutable:
        reasons.append("r7_immutable_lock_marker_missing")
    if not isinstance(vector, Mapping) or not str(vector.get("fingerprint") or ""):
        reasons.append("r7_frozen_vector_fingerprint_missing")
    if exact_errors:
        reasons.extend(exact_errors)
    if not stored_exact and not authority_exact:
        reasons.append("r7_exact_vector_proof_missing")
    return not reasons, sorted(set(reasons))


def _label_lock_binding_reasons(
    label: Mapping[str, Any], locked: Mapping[str, Any]
) -> List[str]:
    """Prove the write-once FINAL label belongs to this exact immutable lock."""

    authority = locked.get("canonicalLockAuthority") or {}
    vector = _locked_vector(locked)
    snapshot = _locked_snapshot(locked)
    reasons: List[str] = []
    if not _authority_integrity_proven(authority):
        reasons.append("r7_label_lock_canonical_authority_not_proven")
    if label.get("write_once") is not True or label.get("completed") is not True:
        reasons.append("r7_label_not_write_once_final")
    if not str(label.get("settlement_fingerprint") or ""):
        reasons.append("r7_label_settlement_fingerprint_missing")
    if not str(label.get("record_fingerprint") or ""):
        reasons.append("r7_label_record_fingerprint_missing")

    official_game_pk = str(
        locked.get("officialGamePk")
        or vector.get("officialGamePk")
        or authority.get("officialGamePk")
        or ""
    )
    if (
        not official_game_pk
        or label.get("official_game_pk") != official_game_pk
    ):
        reasons.append("r7_label_official_game_pk_not_bound_to_lock")

    comparisons = {
        "canonical_lock_pk": authority.get("sourcePk"),
        "canonical_lock_sk": authority.get("sourceSk"),
        "canonical_stage_fingerprint": authority.get("stageFingerprint"),
        "frozen_feature_vector_fingerprint": vector.get("fingerprint"),
        "fundamentals_snapshot_v2_version": snapshot.get("version"),
        "fundamentals_snapshot_v2_fingerprint": snapshot.get("fingerprint"),
    }
    for field, expected in comparisons.items():
        if not str(expected or "") or label.get(field) != expected:
            reasons.append(f"r7_label_{field}_not_bound_to_lock")
    return sorted(set(reasons))


def _joined_row_source_honest_proof(
    row: Mapping[str, Any],
) -> Optional[Tuple[bool, List[str], Dict[str, float]]]:
    annotation_fields = {
        "r7SourceHonestTrainingAdmission",
        "r7SourceHonestTrainingPolicyVersion",
        "r7SourceHonestSnapshotPolicyVersion",
        "r7SourceHonestLabelLockBindingVersion",
        "r7SourceHonestMissingnessMasks",
        "r7SourceHonestJoinedBinding",
    }
    if not (annotation_fields & set(row)):
        return None

    reasons: List[str] = []
    if row.get("r7SourceHonestTrainingAdmission") is not True:
        reasons.append("r7_joined_source_honest_admission_not_proven")
    if row.get("r7SourceHonestTrainingPolicyVersion") != VERSION:
        reasons.append("r7_joined_training_policy_version_mismatch")
    if row.get("r7SourceHonestSnapshotPolicyVersion") != SNAPSHOT_POLICY_VERSION:
        reasons.append("r7_joined_snapshot_policy_version_mismatch")
    if (
        row.get("r7SourceHonestLabelLockBindingVersion")
        != LABEL_LOCK_BINDING_VERSION
    ):
        reasons.append("r7_joined_label_lock_binding_version_mismatch")
    if row.get("slateFinalized") is not True or row.get("labelStatus") != "FINAL":
        reasons.append("r7_joined_row_not_finalized")
    if not str(row.get("labelFingerprint") or ""):
        reasons.append("r7_joined_label_fingerprint_missing")
    if not str(row.get("labelRecordFingerprint") or ""):
        reasons.append("r7_joined_label_record_fingerprint_missing")
    for field in (
        "immutablePregameVectorMutated",
        "immutableLockPayloadMutated",
        "immutableLabelPayloadMutated",
        "productionPickEligibilityChanged",
    ):
        if row.get(field) is not False:
            reasons.append(f"r7_joined_{field}_must_be_false")

    vector = _vector(row)
    if not str(vector.get("fingerprint") or ""):
        reasons.append("r7_joined_frozen_vector_fingerprint_missing")
    snapshot = _snapshot(row)
    snapshot_ok, snapshot_reasons = validate_snapshot_for_r7_training(
        snapshot,
        _prediction_time(row),
        _lock_time(row),
    )
    if not snapshot_ok:
        reasons.extend(snapshot_reasons)
    reasons.extend(_joined_snapshot_binding_reasons(row))

    masks_raw = row.get("r7SourceHonestMissingnessMasks") or {}
    masks = dict(masks_raw) if isinstance(masks_raw, Mapping) else {}
    expected_masks = derived_missingness(snapshot) if snapshot_ok else {}
    if set(masks) != REQUIRED_MISSINGNESS_MASKS:
        reasons.append("r7_joined_missingness_mask_set_mismatch")
    if masks != expected_masks:
        reasons.append("r7_joined_missingness_mask_values_mismatch")
    if any(value not in {0.0, 1.0} for value in masks.values()):
        reasons.append("r7_joined_missingness_mask_not_binary")
    return not reasons, sorted(set(reasons)), masks if not reasons else {}


def row_is_source_honest_training_safe(
    row: Mapping[str, Any],
) -> Tuple[bool, List[str], Dict[str, float]]:
    joined_proof = _joined_row_source_honest_proof(row)
    if joined_proof is not None:
        return joined_proof

    exact, exact_reasons = _exact_lock_proven(row)
    if not exact:
        return False, exact_reasons, {}
    snapshot = _snapshot(row)
    valid, snapshot_reasons = validate_snapshot_for_r7_training(
        snapshot,
        _prediction_time(row),
        _lock_time(row),
    )
    if not valid:
        return False, snapshot_reasons, {}
    return True, [], derived_missingness(snapshot)


def manifest_missingness_reasons(
    manifest: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> List[str]:
    if snapshot.get("pregameComplete") is True:
        return []
    feature_names = {
        str(value) for value in (manifest.get("featureNames") or []) if str(value)
    }
    reasons = [
        f"r7_prespecified_missingness_mask_absent:{name}"
        for name in sorted(REQUIRED_MISSINGNESS_MASKS - feature_names)
    ]
    schemas = manifest.get("modelFeatureSchemas") or {}
    if isinstance(schemas, Mapping):
        for model_name in ("outcome", "reliability"):
            model_features = {
                str(value)
                for value in (schemas.get(model_name) or [])
                if str(value)
            }
            for name in sorted(REQUIRED_MISSINGNESS_MASKS - model_features):
                reasons.append(
                    f"r7_{model_name}_missingness_mask_absent:{name}"
                )
    else:
        reasons.append("r7_model_feature_schemas_missing")
    return sorted(set(reasons))


def _install_label_patch(labels: Any) -> None:
    if getattr(labels, _LABEL_PATCH_FLAG, False):
        return

    original_verdict = getattr(labels, "_training_verdict", None)
    if callable(original_verdict):

        @functools.wraps(original_verdict)
        def training_verdict(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
            _eligible, reasons = original_verdict(row)
            safe, safety_reasons, _masks = row_is_source_honest_training_safe(row)
            if not safe:
                return False, sorted(
                    _strings(reasons) | set(safety_reasons)
                )
            remaining = sorted(
                set(str(value) for value in (reasons or []) if str(value))
                - _source_honest_incomplete_reasons(reasons)
            )
            return not remaining, remaining

        training_verdict._mlb_r7_source_honest_policy_version = VERSION
        labels._training_verdict = training_verdict

    original_join = getattr(labels, "_joined_training_row", None)
    if callable(original_join):
        prospective_read_repair_version, prospective_install_reasons = (
            _installed_prospective_join_version(labels, original_join)
        )

        @functools.wraps(original_join)
        def joined_training_row(
            slate_date: str,
            label: Dict[str, Any],
            locked: Dict[str, Any],
            *,
            slate_finalized: bool,
        ) -> Dict[str, Any]:
            # Capture the two strongly-read inputs before the wrapped join can
            # construct or otherwise affect its mutable output object.
            trusted_label = copy.deepcopy(label)
            trusted_locked = copy.deepcopy(locked)
            joined = original_join(
                slate_date,
                label,
                locked,
                slate_finalized=slate_finalized,
            )
            if not isinstance(joined, dict):
                return joined
            safe, safety_reasons, masks = row_is_source_honest_training_safe(
                trusted_locked
            )
            binding_reasons = list(prospective_install_reasons)
            binding_reasons.extend(_label_lock_binding_reasons(
                trusted_label,
                trusted_locked,
            ))

            label_record_validator = getattr(
                labels,
                "_label_record_errors",
                None,
            )
            if not callable(label_record_validator):
                binding_reasons.append(
                    "r7_label_record_validator_unavailable"
                )
            else:
                try:
                    label_record_errors = label_record_validator(
                        trusted_label,
                        str(slate_date),
                        str(trusted_label.get("official_game_pk") or ""),
                    )
                    if label_record_errors is None:
                        label_record_errors = []
                    if isinstance(label_record_errors, (str, bytes, Mapping)):
                        raise TypeError("invalid label validator result")
                    label_record_errors = list(label_record_errors)
                except Exception:
                    binding_reasons.append(
                        "r7_label_record_validation_failed"
                    )
                else:
                    binding_reasons.extend(
                        f"r7_label_record_invalid:{value}"
                        for value in label_record_errors
                        if str(value)
                    )

            lock_payload_fingerprint = getattr(
                labels,
                "_canonical_lock_payload_fingerprint",
                None,
            )
            try:
                expected_lock_payload_fingerprint = (
                    lock_payload_fingerprint(trusted_locked)
                    if callable(lock_payload_fingerprint)
                    else None
                )
            except Exception:
                expected_lock_payload_fingerprint = None
            if (
                not expected_lock_payload_fingerprint
                or trusted_label.get("canonical_lock_payload_fingerprint")
                != expected_lock_payload_fingerprint
            ):
                binding_reasons.append(
                    "r7_label_canonical_lock_payload_fingerprint_"
                    "not_bound_to_lock"
                )
            binding_reasons = sorted(set(binding_reasons))
            if not safe or binding_reasons:
                rejected = copy.deepcopy(joined)
                rejection_reasons = sorted(
                    set(
                        _strings(
                            rejected.get("trainingExclusionReasons")
                        )
                        | set(safety_reasons)
                        | set(binding_reasons)
                    )
                )
                rejected.update(
                    {
                        "trainingEligible": False,
                        "trainingExclusionReasons": rejection_reasons,
                        "r7SourceHonestTrainingAdmission": False,
                        "r7SourceHonestTrainingPolicyVersion": VERSION,
                        "r7SourceHonestSnapshotPolicyVersion": (
                            SNAPSHOT_POLICY_VERSION
                        ),
                        "r7SourceHonestLabelLockBindingVersion": (
                            LABEL_LOCK_BINDING_VERSION
                        ),
                        "r7SourceHonestSafetyReasons": rejection_reasons,
                        "productionPickEligibilityChanged": False,
                    }
                )
                return rejected
            exclusions = _strings(joined.get("trainingExclusionReasons"))
            remaining = sorted(
                exclusions - _source_honest_incomplete_reasons(exclusions)
            )
            admitted = bool(
                slate_finalized is True
                and joined.get("labelStatus") == "FINAL"
                and bool(joined.get("labelFingerprint"))
                and bool(joined.get("labelRecordFingerprint"))
                and not remaining
            )
            observed_prospective_version = joined.get(
                _PROSPECTIVE_READ_REPAIR_VERSION_FIELD
            )
            if prospective_read_repair_version is None:
                if _PROSPECTIVE_READ_REPAIR_VERSION_FIELD in joined:
                    binding_reasons.append(
                        "r7_untrusted_prospective_read_repair_annotation"
                    )
            elif (
                observed_prospective_version
                != prospective_read_repair_version
            ):
                binding_reasons.append(
                    "r7_prospective_read_repair_annotation_mismatch"
                )
            binding_reasons = sorted(set(binding_reasons))
            if binding_reasons:
                rejected = copy.deepcopy(joined)
                rejection_reasons = sorted(
                    set(
                        _strings(
                            rejected.get("trainingExclusionReasons")
                        )
                        | set(safety_reasons)
                        | set(binding_reasons)
                    )
                )
                rejected.update(
                    {
                        "trainingEligible": False,
                        "trainingExclusionReasons": rejection_reasons,
                        "r7SourceHonestTrainingAdmission": False,
                        "r7SourceHonestTrainingPolicyVersion": VERSION,
                        "r7SourceHonestSnapshotPolicyVersion": (
                            SNAPSHOT_POLICY_VERSION
                        ),
                        "r7SourceHonestLabelLockBindingVersion": (
                            LABEL_LOCK_BINDING_VERSION
                        ),
                        "r7SourceHonestSafetyReasons": rejection_reasons,
                        "productionPickEligibilityChanged": False,
                    }
                )
                return rejected
            binding = _joined_binding(
                trusted_label,
                trusted_locked,
                prospective_read_repair_version=(
                    prospective_read_repair_version
                ),
            )
            out = copy.deepcopy(joined)
            out.update(
                {
                    "trainingEligible": admitted,
                    "trainingExclusionReasons": remaining,
                    "r7SourceHonestTrainingAdmission": admitted,
                    "r7SourceHonestMissingnessMasks": masks,
                    "r7SourceHonestTrainingPolicyVersion": VERSION,
                    "r7SourceHonestSnapshotPolicyVersion": SNAPSHOT_POLICY_VERSION,
                    "r7SourceHonestLabelLockBindingVersion": LABEL_LOCK_BINDING_VERSION,
                    "r7SourceHonestJoinedBinding": binding,
                    "canonicalLockPayloadFingerprint": trusted_label.get(
                        "canonical_lock_payload_fingerprint"
                    ),
                    "r7SourceHonestSafetyReasons": sorted(
                        set(safety_reasons + binding_reasons)
                    ),
                    "immutablePregameVectorMutated": False,
                    "immutableLockPayloadMutated": False,
                    "immutableLabelPayloadMutated": False,
                    "productionPickEligibilityChanged": False,
                }
            )
            joined_binding_reasons: List[str] = []
            if admitted:
                try:
                    trusted_material = _trusted_join_material(
                        slate_date=str(slate_date),
                        slate_finalized=slate_finalized,
                        label=trusted_label,
                        locked=trusted_locked,
                        binding=binding,
                        prospective_read_repair_version=(
                            prospective_read_repair_version
                        ),
                    )
                    out[_JOINED_RECEIPT_ID_FIELD] = (
                        _register_joined_receipt(
                            trusted_material,
                            binding,
                        )
                    )
                except Exception:
                    joined_binding_reasons.append(
                        "r7_joined_trusted_receipt_registration_failed"
                    )
                else:
                    joined_binding_reasons.extend(
                        _joined_snapshot_binding_reasons(out)
                    )
            if joined_binding_reasons:
                out["trainingEligible"] = False
                out["r7SourceHonestTrainingAdmission"] = False
                out["trainingExclusionReasons"] = sorted(
                    set(remaining + joined_binding_reasons)
                )
                out["r7SourceHonestSafetyReasons"] = sorted(
                    set(
                        out["r7SourceHonestSafetyReasons"]
                        + joined_binding_reasons
                    )
                )
            return out

        joined_training_row._mlb_r7_source_honest_policy_version = VERSION
        joined_training_row._mlb_r7_joined_snapshot_binding_version = (
            JOINED_SNAPSHOT_BINDING_VERSION
        )
        labels._joined_training_row = joined_training_row

    labels.MLB_R7_SOURCE_HONEST_TRAINING_POLICY_VERSION = VERSION
    labels.MLB_R7_JOINED_SNAPSHOT_BINDING_VERSION = (
        JOINED_SNAPSHOT_BINDING_VERSION
    )
    labels.MLB_R7_JOINED_TRUSTED_RECEIPT_VERSION = (
        JOINED_TRUSTED_RECEIPT_VERSION
    )
    setattr(labels, _LABEL_PATCH_FLAG, True)


def _install_experiment_patch(experiment: Any) -> None:
    if getattr(experiment, _EXPERIMENT_PATCH_FLAG, False):
        return
    original_validate = getattr(experiment, "validate_record", None)
    if not callable(original_validate):
        raise RuntimeError("R7_EXPERIMENT_VALIDATE_RECORD_UNAVAILABLE")

    @functools.wraps(original_validate)
    def validate_record(
        row: Dict[str, Any],
        manifest: Dict[str, Any],
        snapshot_validator: Optional[Any] = None,
    ) -> Tuple[bool, List[str]]:
        production_id = str(
            getattr(experiment, "PRODUCTION_EXPERIMENT_ID", "") or ""
        )
        is_r7 = bool(
            production_id
            and str(manifest.get("experimentId") or "") == production_id
        )
        effective_validator = snapshot_validator
        if is_r7 and effective_validator is None:
            effective_validator = validate_snapshot_for_r7_training
        _ok, reasons = original_validate(
            row,
            manifest,
            snapshot_validator=effective_validator,
        )
        normalized = {
            str(value) for value in (reasons or []) if str(value)
        }
        if is_r7:
            safe, safety_reasons, masks = row_is_source_honest_training_safe(row)
            if not safe:
                normalized.update(safety_reasons)
            else:
                normalized.update(
                    manifest_missingness_reasons(manifest, _snapshot(row))
                )
                if set(masks) != REQUIRED_MISSINGNESS_MASKS:
                    normalized.add("r7_derived_missingness_mask_set_mismatch")
        final_reasons = sorted(normalized)
        return not final_reasons, final_reasons

    validate_record._mlb_r7_source_honest_policy_version = VERSION
    validate_record._mlb_r7_joined_snapshot_binding_version = (
        JOINED_SNAPSHOT_BINDING_VERSION
    )
    experiment.validate_record = validate_record
    experiment.MLB_R7_SOURCE_HONEST_TRAINING_POLICY_VERSION = VERSION
    experiment.MLB_R7_JOINED_SNAPSHOT_BINDING_VERSION = (
        JOINED_SNAPSHOT_BINDING_VERSION
    )
    experiment.MLB_R7_JOINED_TRUSTED_RECEIPT_VERSION = (
        JOINED_TRUSTED_RECEIPT_VERSION
    )
    setattr(experiment, _EXPERIMENT_PATCH_FLAG, True)


def _install_dual_model_patch(dual_model: Any) -> None:
    if getattr(dual_model, _DUAL_PATCH_FLAG, False):
        return
    original_fit = getattr(dual_model, "fit_logistic", None)
    if not callable(original_fit):
        raise RuntimeError("R7_DUAL_MODEL_FIT_UNAVAILABLE")

    @functools.wraps(original_fit)
    def fit_logistic(
        records: Sequence[Dict[str, Any]],
        features: Sequence[str],
        target: str,
        version: str,
        epochs: int = 320,
        learning_rate: float = 0.035,
        l2: float = 0.02,
    ) -> Dict[str, Any]:
        requested = list(features or [])
        result = original_fit(
            records,
            requested,
            target,
            version,
            epochs=epochs,
            learning_rate=learning_rate,
            l2=l2,
        )
        if not isinstance(result, dict) or result.get("reason") != (
            "prespecified_feature_has_no_observed_training_values"
        ):
            return result
        empty = {
            str(value) for value in (result.get("emptyFeatures") or []) if str(value)
        }
        if not empty or not empty <= OPTIONAL_ALL_MISSING_NUMERIC_FEATURES:
            return result
        if any(OPTIONAL_FEATURE_MASK[name] not in requested for name in empty):
            return result
        active = [name for name in requested if name not in empty]
        if not active:
            return result
        retry = original_fit(
            records,
            active,
            target,
            version,
            epochs=epochs,
            learning_rate=learning_rate,
            l2=l2,
        )
        if not isinstance(retry, dict) or retry.get("ok") is not True:
            return retry
        out = copy.deepcopy(retry)
        weights = dict(out.get("weights") or {})
        means = dict(out.get("means") or {})
        scales = dict(out.get("scales") or {})
        for name in sorted(empty):
            weights[name] = 0.0
            means[name] = 0.0
            scales[name] = 1.0
        out.update(
            {
                "features": requested,
                "weights": weights,
                "means": means,
                "scales": scales,
                "inactiveAllMissingFeatures": sorted(empty),
                "effectiveFeatures": active,
                "effectiveFeatureCount": len(active),
                "sourceHonestMissingnessPolicyVersion": MODEL_POLICY_VERSION,
                "missingValuePolicy": (
                    "train_mean_imputation_with_prespecified_frozen_missingness_"
                    "masks_and_zero_weight_for_all_missing_optional_fundamentals"
                ),
            }
        )
        return out

    fit_logistic._mlb_r7_source_honest_policy_version = VERSION
    fit_logistic._mlb_r7_joined_snapshot_binding_version = (
        JOINED_SNAPSHOT_BINDING_VERSION
    )
    dual_model.fit_logistic = fit_logistic
    dual_model.MLB_R7_SOURCE_HONEST_TRAINING_POLICY_VERSION = VERSION
    dual_model.MLB_R7_JOINED_SNAPSHOT_BINDING_VERSION = (
        JOINED_SNAPSHOT_BINDING_VERSION
    )
    dual_model.MLB_R7_JOINED_TRUSTED_RECEIPT_VERSION = (
        JOINED_TRUSTED_RECEIPT_VERSION
    )
    dual_model.MLB_R7_SOURCE_HONEST_MODEL_POLICY_VERSION = MODEL_POLICY_VERSION
    setattr(dual_model, _DUAL_PATCH_FLAG, True)


def install(
    *,
    labels: Optional[Any] = None,
    experiment: Optional[Any] = None,
    dual_model: Optional[Any] = None,
) -> Dict[str, Any]:
    if labels is None:
        import mlb_canonical_final_labels_v1 as labels
    if experiment is None:
        import mlb_ml_experiment_v2 as experiment
    if dual_model is None:
        import mlb_ml_dual_model_v2 as dual_model

    _install_label_patch(labels)
    _install_experiment_patch(experiment)
    _install_dual_model_patch(dual_model)
    return {
        "ok": True,
        "version": VERSION,
        "snapshotPolicyVersion": SNAPSHOT_POLICY_VERSION,
        "labelLockBindingVersion": LABEL_LOCK_BINDING_VERSION,
        "joinedSnapshotBindingVersion": JOINED_SNAPSHOT_BINDING_VERSION,
        "joinedTrustedReceiptVersion": JOINED_TRUSTED_RECEIPT_VERSION,
        "modelPolicyVersion": MODEL_POLICY_VERSION,
        "immutablePredictionOrLockMutated": False,
        "immutableLabelMutated": False,
        "productionPickEligibilityChanged": False,
        "promotionGateChanged": False,
        "retiredAuthorityRestored": False,
    }
