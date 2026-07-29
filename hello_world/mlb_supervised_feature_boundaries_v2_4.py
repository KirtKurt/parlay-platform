"""Keep MLB V8 target-game fundamentals and BBD prior-game evidence distinct."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Mapping, Sequence

VERSION = "MLB-SUPERVISED-FEATURE-BOUNDARIES-v2.4"
BBD_PRIOR_GAME_SNAPSHOT_ROLE = (
    "BBD_STRICTLY_PRIOR_COMPLETED_GAME_FEATURES_AT_T_MINUS_45"
)
BBD_SUPPORT_START_DATE = date(2026, 3, 1)


def install_features(feature_module: Any) -> Any:
    if getattr(feature_module, "_INQSI_MLB_FEATURE_BOUNDARIES_V2_4_INSTALLED", False):
        return feature_module
    original = feature_module._fundamentals_payload

    def target_game_fundamentals_only(record: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = original(record)
        if (
            isinstance(payload, Mapping)
            and str(payload.get("snapshotRole") or "")
            == BBD_PRIOR_GAME_SNAPSHOT_ROLE
        ):
            return {}
        return payload

    feature_module._fundamentals_payload = target_game_fundamentals_only
    feature_module.VERSION = f"{feature_module.VERSION}+{VERSION}"
    feature_module._INQSI_MLB_FEATURE_BOUNDARIES_V2_4_INSTALLED = True
    return feature_module


def _supported_game_count(records: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for record in records:
        try:
            if date.fromisoformat(str(record.get("slateDateEt") or "")) >= BBD_SUPPORT_START_DATE:
                count += 1
        except Exception:
            continue
    return count


def install_model(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_FEATURE_COVERAGE_V2_4_INSTALLED", False):
        return model_module
    original = model_module.train_and_evaluate

    def with_explicit_coverage(
        records: Sequence[Mapping[str, Any]], **kwargs: Any
    ) -> Dict[str, Any]:
        result = original(records, **kwargs)
        feature_coverage = result.setdefault("featureCoverage", {})
        example_count = int(feature_coverage.get("exampleCount") or len(records))
        supported_count = _supported_game_count(records)
        proof = result.get("historicalBbsFundamentals") or {}
        applied_count = int(proof.get("appliedGameCount") or 0)
        feature_coverage.update(
            {
                "bbsPriorSupported": round(
                    supported_count / example_count, 8
                )
                if example_count
                else 0.0,
                "bbsPriorAvailable": round(
                    applied_count / example_count, 8
                )
                if example_count
                else 0.0,
                "bbsPriorWithinSupported": round(
                    applied_count / supported_count, 8
                )
                if supported_count
                else 0.0,
            }
        )
        architecture = result.setdefault("architecture", {})
        architecture["targetGameFundamentalsExcludeBbsPriorGameSnapshots"] = True
        architecture["bbsPriorCoverageDenominator"] = "provider_supported_cohort"
        digest = getattr(model_module, "_sha", None)
        if callable(digest):
            result["resultDigest"] = digest(
                {key: value for key, value in result.items() if key != "resultDigest"}
            )
        return result

    model_module.train_and_evaluate = with_explicit_coverage
    model_module._INQSI_MLB_FEATURE_COVERAGE_V2_4_INSTALLED = True
    return model_module
