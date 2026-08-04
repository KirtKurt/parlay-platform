#!/usr/bin/env python3
"""Apply the verified V7-V10 stall fixes idempotently.

This migration removes the retired BBS requirement from provider-neutral V7/V9
feature consumption, adds complete-slate-aware lightweight evaluation cadence,
prevents official V8 context from carrying legacy BBS manifest records forward,
and warms/verifies the lock Lambda directly before the API Gateway smoke.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURE_RUNNER = ROOT / "scripts" / "run_mlb_historical_supervised_v9_shadow_feature_aware.py"
FEATURE_BRIDGE = ROOT / "hello_world" / "mlb_historical_v7_feature_bridge_v1.py"
V8_ENTRYPOINT = ROOT / "scripts" / "run_mlb_v8_historical_context_backfill_entrypoint.py"
V9_WORKFLOW = ROOT / ".github" / "workflows" / "mlb-historical-supervised-v9-shadow.yml"
FEATURE_RUNNER_TEST = ROOT / "tests" / "unit" / "test_run_mlb_historical_supervised_v9_shadow_feature_aware.py"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
STABILIZER = ROOT / "scripts" / "stabilize_mlb_deploy_source.py"


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    # Check the full replacement first because many migration anchors are a
    # strict substring of the replacement block itself.
    if new in text:
        return text
    if old in text:
        return text.replace(old, new, 1)
    raise RuntimeError(f"migration marker missing:{label}")


def _replace_all(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new)
    if new in text:
        return text
    raise RuntimeError(f"migration marker missing:{label}")


def patch_feature_runner(text: str) -> str:
    text = text.replace(
        "loads the immutable BBS/context overlays first",
        "loads provider-neutral official context first and treats retired BBS context as disabled",
    )
    text = text.replace(
        'VERSION = "MLB-V7-V9-FEATURE-AWARE-SHADOW-RUNNER-v1"',
        'VERSION = "MLB-V7-V9-FEATURE-AWARE-SHADOW-RUNNER-v2-provider-neutral-complete-slate-aware"',
    )
    text = _replace_once(
        text,
        """    from scripts import run_mlb_historical_supervised_v9_shadow_cadence as cadence_state
""",
        """    from scripts import run_mlb_historical_supervised_v9_shadow_cadence as cadence_state
    from scripts import run_mlb_historical_supervised_v9_shadow_cadence_v3 as cadence_v3
""",
        "feature runner cadence v3 package import",
    )
    text = _replace_once(
        text,
        """    import run_mlb_historical_supervised_v9_shadow_cadence as cadence_state
""",
        """    import run_mlb_historical_supervised_v9_shadow_cadence as cadence_state
    import run_mlb_historical_supervised_v9_shadow_cadence_v3 as cadence_v3
""",
        "feature runner cadence v3 direct import",
    )
    text = _replace_all(
        text,
        'os.environ.setdefault("MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED", "true")',
        'os.environ.setdefault("MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED", "false")',
        "disable retired BBS overlay",
    )
    text = _replace_all(
        text,
        'os.environ.setdefault("MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED", "true")',
        'os.environ.setdefault("MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED", "false")',
        "remove retired BBS requirement",
    )
    text = _replace_once(
        text,
        """    if not isinstance(state, dict):
        raise RuntimeError("historical optimizer state is missing")

    raw_records = handler._load_training_records(state)
""",
        """    if not isinstance(state, dict):
        raise RuntimeError("historical optimizer state is missing")
    complete_slate_count = int(
        state.get("completeSlateCount")
        or len(state.get("completedSlates") or [])
        or 0
    )
    lightweight_slate_increment = _integer_env(
        "MLB_V7_LIGHTWEIGHT_INCREMENT_COMPLETE_SLATES", 1
    )

    raw_records = handler._load_training_records(state)
""",
        "complete slate cadence state",
    )
    text = _replace_once(
        text,
        """        value = original_decide(
            previous,
            feature_count=feature_count,
            feature_fingerprint=feature_fingerprint,
            full_feature_increment=full_feature_increment,
            lightweight_feature_increment=lightweight_feature_increment,
            **kwargs,
        )
""",
        """        value = cadence_v3.decide_cadence(
            previous,
            feature_count=feature_count,
            feature_fingerprint=feature_fingerprint,
            full_feature_increment=full_feature_increment,
            lightweight_feature_increment=lightweight_feature_increment,
            current_slate_count=complete_slate_count,
            lightweight_slate_increment=lightweight_slate_increment,
            **kwargs,
        )
""",
        "complete slate cadence decision",
    )
    text = _replace_once(
        text,
        """        return original_anchor_fields(
            decision,
            feature_count=feature_count,
            feature_fingerprint=feature_fingerprint,
            **kwargs,
        )
""",
        """        return cadence_v3.report_anchor_fields(
            decision,
            feature_count=feature_count,
            feature_fingerprint=feature_fingerprint,
            current_slate_count=complete_slate_count,
            **kwargs,
        )
""",
        "complete slate cadence report anchors",
    )
    text = _replace_once(
        text,
        """            "providerCallsMade": 0,
            "productionAuthorityChanged": False,
""",
        """            "newCompleteSlatesSinceLastShadowFit": int(
                decision.get("newCompleteSlatesSinceLastShadowFit") or 0
            ),
            "newCompleteSlatesSinceLastLightweightEvaluation": int(
                decision.get("newCompleteSlatesSinceLastLightweightEvaluation") or 0
            ),
            "remainingCompleteSlatesUntilLightweightEvaluation": int(
                decision.get("remainingCompleteSlatesUntilLightweightEvaluation") or 0
            ),
            "lightweightSelectiveEvaluationIncrementCompleteSlates": int(
                decision.get("lightweightSelectiveEvaluationIncrementCompleteSlates") or 1
            ),
            "providerNeutralOfficialContextRequired": True,
            "retiredBbsOverlayRequired": False,
            "providerCallsMade": 0,
            "productionAuthorityChanged": False,
""",
        "feature runner slate and provider proof",
    )
    text = text.replace(
        'value["stalledStage"] = "WAITING_FOR_NEW_GAMES_OR_FEATURE_ROWS"',
        'value["stalledStage"] = "WAITING_FOR_NEW_GAMES_FEATURE_ROWS_OR_COMPLETE_SLATES"',
    )
    stale = (
        'MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED", "true',
        'MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED", "true',
        "value = original_decide(",
        "return original_anchor_fields(",
    )
    remaining = [token for token in stale if token in text]
    if remaining:
        raise RuntimeError("stale V7/V9 stall contracts remain:" + ",".join(remaining))
    return text


def patch_feature_bridge(text: str) -> str:
    text = text.replace(
        'VERSION = "MLB-HISTORICAL-V7-FEATURE-BRIDGE-v1-point-in-time-signal-wiring"',
        'VERSION = "MLB-HISTORICAL-V7-FEATURE-BRIDGE-v2-provider-neutral-official-primary"',
    )
    text = _replace_once(
        text,
        """        "providerCallsMade": 0,
        "selectionUsedOutcomes": False,
""",
        """        "primaryFeatureAuthority": context_overlay.AUTHORITY,
        "providerNeutralOfficialContextPrimary": True,
        "retiredBbsOverlayRequired": False,
        "retiredBbsOverlayStatus": prior_proof.get("status"),
        "providerCallsMade": 0,
        "selectionUsedOutcomes": False,
""",
        "feature bridge provider-neutral proof",
    )
    return text


def patch_v8_entrypoint(text: str) -> str:
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

    def put_immutable(s3: Any, bucket: str, key: str, body: bytes) -> Dict[str, Any]:
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
            # Preserve optimistic concurrency but never carry retired BBS rows
            # into the provider-neutral official context corpus.
            return None, revision
        return original_load_previous_manifest(table, s3)

    module._load_previous_manifest = load_previous_manifest

    def put_immutable(s3: Any, bucket: str, key: str, body: bytes) -> Dict[str, Any]:
"""
    text = _replace_once(text, anchor, replacement, "V8 legacy manifest reset")
    text = _replace_once(
        text,
        """                "automaticWagerAllowed": False,
""",
        """                "legacyBbsCarryForwardAllowed": False,
                "pointerMigrationVersion": MIGRATION_VERSION,
                "officialContextPointerAuthoritative": True,
                "automaticWagerAllowed": False,
""",
        "V8 migration proof fields",
    )
    return text


def patch_feature_runner_test(text: str) -> str:
    text = _replace_once(
        text,
        '    cadence = types.ModuleType("scripts.run_mlb_historical_supervised_v9_shadow_cadence")\n',
        '    cadence = types.ModuleType("scripts.run_mlb_historical_supervised_v9_shadow_cadence")\n    cadence_v3 = types.ModuleType("scripts.run_mlb_historical_supervised_v9_shadow_cadence_v3")\n',
        "feature-aware test cadence v3 module",
    )
    text = _replace_once(
        text,
        '    package.run_mlb_historical_supervised_v9_shadow_cadence = cadence\n',
        '    package.run_mlb_historical_supervised_v9_shadow_cadence = cadence\n    package.run_mlb_historical_supervised_v9_shadow_cadence_v3 = cadence_v3\n',
        "feature-aware test cadence v3 package attribute",
    )
    text = _replace_once(
        text,
        '        bridge.__name__, package.__name__, legacy.__name__, cadence.__name__\n',
        '        bridge.__name__, package.__name__, legacy.__name__, cadence.__name__, cadence_v3.__name__\n',
        "feature-aware test module snapshot",
    )
    text = _replace_once(
        text,
        '    sys.modules[cadence.__name__] = cadence\n',
        '    sys.modules[cadence.__name__] = cadence\n    sys.modules[cadence_v3.__name__] = cadence_v3\n',
        "feature-aware test cadence v3 registration",
    )
    text = text.replace(
        'assert report["stalledStage"] == "WAITING_FOR_NEW_GAMES_OR_FEATURE_ROWS"',
        'assert report["stalledStage"] == "WAITING_FOR_NEW_GAMES_FEATURE_ROWS_OR_COMPLETE_SLATES"',
    )
    text = _replace_once(
        text,
        'module.cadence_state.decide_cadence = decide\n    module.cadence_state.report_anchor_fields = anchors',
        'module.cadence_state.decide_cadence = decide\n    module.cadence_state.report_anchor_fields = anchors\n    module.cadence_v3.decide_cadence = decide\n    module.cadence_v3.report_anchor_fields = anchors',
        "feature-aware test cadence v3 monkeypatch",
    )
    text = text.replace(
        'assert os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED"] == "true"',
        'assert os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED"] == "false"',
    )
    text = text.replace(
        'assert os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED"] == "true"',
        'assert os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED"] == "false"',
    )
    return text


def patch_v9_workflow(text: str) -> str:
    text = _replace_once(
        text,
        """  MLB_V7_LIGHTWEIGHT_INCREMENT_GAMES: '25'
""",
        """  MLB_V7_LIGHTWEIGHT_INCREMENT_GAMES: '25'
  MLB_V7_LIGHTWEIGHT_INCREMENT_COMPLETE_SLATES: '1'
""",
        "V7/V9 complete slate cadence env",
    )
    return text


def patch_deploy_workflow(text: str) -> str:
    text = _replace_once(
        text,
        """          python -m py_compile scripts/mlb_deploy_http_probe.py
""",
        """          python -m py_compile scripts/mlb_deploy_http_probe.py
          python -m py_compile scripts/warm_and_verify_mlb_lock_status.py
          python -m pytest -q tests/unit/test_warm_and_verify_mlb_lock_status.py
""",
        "lock warm verifier validation",
    )
    text = _replace_once(
        text,
        """        run: |
          set -euo pipefail
          python - <<'PY'
          import json
          import os
          import time
          from datetime import datetime, timezone
          import urllib.parse
          from scripts.mlb_deploy_http_probe import fetch_json_object
""",
        """        run: |
          set -euo pipefail
          LOCK_FUNCTION=$(aws cloudformation describe-stack-resource \
            --stack-name parlay-platform-dev \
            --logical-resource-id MLBDailyPickLockFunction \
            --region "${{ secrets.AWS_REGION }}" \
            --query 'StackResourceDetail.PhysicalResourceId' \
            --output text)
          test -n "$LOCK_FUNCTION"
          test "$LOCK_FUNCTION" != "None"
          python scripts/warm_and_verify_mlb_lock_status.py \
            --function-name "$LOCK_FUNCTION" \
            --region "${{ secrets.AWS_REGION }}" \
            --output /tmp/mlb-lock-status-direct.json \
            --invocation-output /tmp/mlb-lock-status-direct-invocation.json \
            --attempts 3 \
            --delay-seconds 4
          python - <<'PY'
          import json
          import os
          import time
          from datetime import datetime, timezone
          import urllib.parse
          from scripts.mlb_deploy_http_probe import fetch_json_object
""",
        "direct lock warm before API smoke",
    )
    return text


def patch_stabilizer(text: str) -> str:
    text = _replace_once(
        text,
        '        "verify_mlb_deploy_identity.py",\n',
        '        "verify_mlb_deploy_identity.py",\n        "warm_and_verify_mlb_lock_status.py",\n        "/tmp/mlb-lock-status-direct.json",\n',
        "stabilizer direct lock verification contract",
    )
    return text


PATCHES = {
    FEATURE_RUNNER: patch_feature_runner,
    FEATURE_BRIDGE: patch_feature_bridge,
    V8_ENTRYPOINT: patch_v8_entrypoint,
    V9_WORKFLOW: patch_v9_workflow,
    FEATURE_RUNNER_TEST: patch_feature_runner_test,
    DEPLOY_WORKFLOW: patch_deploy_workflow,
    STABILIZER: patch_stabilizer,
}


def apply(*, check: bool = False) -> list[str]:
    changed: list[str] = []
    for path, patcher in PATCHES.items():
        original = path.read_text(encoding="utf-8")
        updated = patcher(original)
        if updated != original:
            changed.append(str(path.relative_to(ROOT)))
            if not check:
                path.write_text(updated, encoding="utf-8")
    if check and changed:
        raise RuntimeError("V7-V10 stall migration not applied:" + ",".join(changed))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = apply(check=args.check)
    print("V7-V10 stall migration verified" if args.check else "Patched: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
