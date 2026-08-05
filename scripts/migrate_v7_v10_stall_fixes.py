#!/usr/bin/env python3
"""Install the feature-aware V7/V9 and autonomous V8 stall fixes.

This is a bounded, idempotent repository migration. It deliberately preserves
production authority and only changes shadow/training cadence, historical context,
and deployment verification behavior.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    # Check the full replacement first because many migration anchors are a
    # strict substring of the replacement block itself.
    if new in text:
        return text
    if old in text:
        return text.replace(old, new, 1)
    raise RuntimeError(f"migration marker missing:{label}")


def patch_feature_bridge(text: str) -> str:
    text = _replace_once(
        text,
        '"historicalFeatureFamily": overlay.TARGET_FAMILY,\n        "trainingEligible": True,',
        '"historicalFeatureFamily": overlay.TARGET_FAMILY,\n        "historicalFeatureEligibility": dict(snapshot.get("featureEligibility") or {}),\n        "historicalFeatureMissingness": dict(snapshot.get("featureMissingness") or {}),\n        "historicalFeatureAvailabilityMode": dict(snapshot.get("featureAvailabilityMode") or {}),\n        "trainingEligible": bool(snapshot.get("trainingEligibleCore", snapshot.get("trainingEligible"))),',
        "feature bridge eligibility fields",
    )
    text = _replace_once(
        text,
        '"providerCallsMade": 0,\n        "selectionUsedOutcomes": False,',
        '"primaryFeatureAuthority": context_overlay.AUTHORITY,\n        "providerNeutralOfficialContextPrimary": True,\n        "retiredBbsOverlayRequired": False,\n        "retiredBbsOverlayStatus": prior_proof.get("status"),\n        "providerCallsMade": 0,\n        "selectionUsedOutcomes": False,',
        "feature bridge provider-neutral proof",
    )
    return text


def patch_v8_entrypoint(text: str) -> str:
    # The feature-aware replay contract supersedes the old one-shot pointer
    # migration. Detect the semantic policy markers rather than one exact source
    # formatting so generated assignments and report.update blocks are equivalent.
    feature_aware_markers = (
        "eligibilityPolicyVersion",
        "eligibility.VERSION",
        "materializerVersion",
        "eligibility.MATERIALIZER_VERSION",
        "replayFromStartApplied",
    )
    if all(marker in text for marker in feature_aware_markers):
        return text
    text = text.replace(
        'VERSION = "MLB-V8-HISTORICAL-CONTEXT-BACKFILL-v2-official-only"',
        'VERSION = "MLB-V8-HISTORICAL-CONTEXT-BACKFILL-v3-official-only-no-legacy-carry-forward"',
    )
    text = _replace_once(
        text,
        'ARCHIVED_WEATHER_MODEL = "ecmwf_ifs"\n',
        'ARCHIVED_WEATHER_MODEL = "ecmwf_ifs"\nMIGRATION_VERSION = "MLB-V8-CONTEXT-POINTER-MIGRATION-v1-reset-retired-bbs-records"\n',
        "V8 migration version",
    )
    anchor = """    module.VERSION = VERSION
    module.REPORT_TYPE = REPORT_TYPE
"""
    replacement = """    module.VERSION = VERSION
    module.REPORT_TYPE = REPORT_TYPE
    original_load_previous_manifest = getattr(
        module, "_load_previous_manifest", lambda _table, _s3: (None, 0)
    )

    def load_previous_manifest(table: Any, s3: Any):
        item = table.get_item(
            Key={"PK": target_overlay.POINTER_PK, "SK": target_overlay.POINTER_SK},
            ConsistentRead=True,
        ).get("Item")
        if not item:
            return None, 0
        revision = int(item.get("revision") or 0)
        plain = getattr(module, "_plain", None)
        raw_data = item.get("data") or {}
        data = plain(raw_data) if callable(plain) else dict(raw_data)
        provider = str(data.get("provider") or "")
        official_pointer = bool(
            item.get("record_type") == RECORD_TYPE
            and data.get("authority") == AUTHORITY
            and provider.startswith("official_mlb")
        )
        if not official_pointer:
            return None, revision
        return original_load_previous_manifest(table, s3)

    module._load_previous_manifest = load_previous_manifest
"""
    text = _replace_once(text, anchor, replacement, "V8 isolated manifest loader")
    text = _replace_once(
        text,
        '"legacyBbsCarryForwardAllowed": False,\n',
        '"legacyBbsCarryForwardAllowed": False,\n                "pointerMigrationVersion": MIGRATION_VERSION,\n',
        "V8 pointer migration report field",
    )
    return text


def patch_cadence_v3(text: str) -> str:
    text = _replace_once(
        text,
        'VERSION = "MLB-HISTORICAL-SUPERVISED-V9-CADENCE-STATE-v1"',
        'VERSION = "MLB-HISTORICAL-SUPERVISED-V9-CADENCE-STATE-v3-feature-aware"',
        "cadence version",
    )
    text = _replace_once(
        text,
        'new_eligible = max(0, eligible - int(state.get("lastEvaluatedEligibleGameCount") or 0))\n',
        'new_eligible = max(0, eligible - int(state.get("lastEvaluatedEligibleGameCount") or 0))\n    new_slates = max(0, complete_slates - int(state.get("lastEvaluatedCompleteSlateCount") or 0))\n',
        "cadence slate delta",
    )
    text = _replace_once(
        text,
        'evaluate = new_eligible >= evaluation_games or new_features >= evaluation_features\n',
        'evaluate = (\n        new_eligible >= evaluation_games\n        or new_features >= evaluation_features\n        or new_slates >= 1\n    )\n',
        "cadence evaluation decision",
    )
    text = _replace_once(
        text,
        '"newFeatureRowsSinceEvaluation": new_features,\n',
        '"newFeatureRowsSinceEvaluation": new_features,\n        "newCompleteSlatesSinceEvaluation": new_slates,\n',
        "cadence report slate field",
    )
    return text


def patch_feature_aware_trainer(text: str) -> str:
    text = _replace_once(
        text,
        'module.cadence_state.decide_cadence = decide\n    module.cadence_state.report_anchor_fields = anchors',
        'module.cadence_state.decide_cadence = decide\n    module.cadence_state.report_anchor_fields = anchors\n    module.cadence_v3.decide_cadence = decide\n    module.cadence_v3.report_anchor_fields = anchors',
        "feature-aware trainer cadence v3 wiring",
    )
    return text


def patch_tests(text: str) -> str:
    text = _replace_once(
        text,
        'module.cadence_state.decide_cadence = decide\n    module.cadence_state.report_anchor_fields = anchors',
        'module.cadence_state.decide_cadence = decide\n    module.cadence_state.report_anchor_fields = anchors\n    module.cadence_v3.decide_cadence = decide\n    module.cadence_v3.report_anchor_fields = anchors',
        "feature-aware test cadence v3 monkeypatch",
    )
    return text


def migrate(*, check: bool = False) -> list[str]:
    targets = {
        "hello_world/mlb_historical_v7_feature_bridge_v1.py": patch_feature_bridge,
        "scripts/run_mlb_v8_historical_context_backfill_entrypoint.py": patch_v8_entrypoint,
        "hello_world/mlb_historical_supervised_v9_cadence_state_v1.py": patch_cadence_v3,
        "scripts/run_mlb_historical_supervised_v9_shadow_feature_aware.py": patch_feature_aware_trainer,
        "tests/unit/test_run_mlb_historical_supervised_v9_shadow_feature_aware.py": patch_tests,
    }
    changed = []
    for relative, patcher in targets.items():
        path = ROOT / relative
        before = path.read_text(encoding="utf-8")
        after = patcher(before)
        if after != before:
            changed.append(relative)
            if not check:
                path.write_text(after, encoding="utf-8")
    if check and changed:
        raise SystemExit("V7-V10 migration not applied: " + ", ".join(changed))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = migrate(check=args.check)
    if not args.check:
        print("Patched:", ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
