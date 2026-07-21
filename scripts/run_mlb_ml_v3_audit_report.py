#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

REPORT_PATH = ROOT / "runtime_reports" / "mlb_ml_v3_audit_execution_latest.json"


def main() -> int:
    payload = {
        "ok": False,
        "proofType": "MLB_ML_V3_AWS_AUDIT_EXECUTION",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "snapshotsTableConfigured": bool(os.environ.get("SNAPSHOTS_TABLE")),
            "oddsApiKeyConfigured": bool(os.environ.get("ODDS_API_KEY")),
            "autoPromote": os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE"),
            "allowLocalFileChampion": os.environ.get("INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION"),
        },
    }
    try:
        # Install the authority thresholds and official-lock quality classifier
        # explicitly. The workflow adds hello_world to sys.path after Python
        # startup, so relying on sitecustomize would leave the AWS audit using
        # the pre-60%-gate official classification.
        import mlb_accuracy_target_policy_v1

        policy_install = mlb_accuracy_target_policy_v1.install()

        import mlb_rolling_24h_audit
        import mlb_locked_card_audit_v1
        import mlb_ml_audit_feature_bridge_v1
        import mlb_doubleheader_safe_audit_patch

        mlb_locked_card_audit_v1.apply(mlb_rolling_24h_audit)
        mlb_ml_audit_feature_bridge_v1.apply(mlb_locked_card_audit_v1)
        mlb_doubleheader_safe_audit_patch.apply(mlb_rolling_24h_audit)

        report = mlb_rolling_24h_audit.build(store=True, write_file=True)
        accuracy = report.get("realWorldAccuracy") or {}
        optimization = report.get("mlOptimizationV3") or {}
        authority = report.get("mlTrainingAuthority") or {}
        critical = accuracy.get("mlCriticalFixStatus") or {}
        failures = []
        if policy_install.get("ok") is not True:
            failures.append("accuracy_target_policy_install_failed")
        if "official_lock_60pct_confirmed_direction_gate" not in (policy_install.get("patched") or []):
            failures.append("official_lock_quality_gate_not_installed")
        if accuracy.get("applied") is not True:
            failures.append("real_world_accuracy_not_applied")
        if (report.get("accuracyLedger") or {}).get("immutable") is not True:
            failures.append("immutable_accuracy_ledger_not_enabled")
        if critical.get("ok") is not True:
            failures.append("critical_ml_blocker_installation_failed")
        if optimization.get("applied") is not True:
            failures.append("ml_optimization_v3_not_applied")
        if authority.get("authoritative") != "mlOptimizationV3_clean_dual_model_only":
            failures.append("wrong_training_authority")
        if os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE", "").lower() != "true":
            failures.append("authoritative_aws_audit_auto_promotion_not_enabled")
        if authority.get("automaticChampionPromotion") is not True:
            failures.append("automatic_champion_promotion_not_enabled")
        if authority.get("automaticPromotionGateRequired") is not True:
            failures.append("automatic_champion_promotion_gate_not_required")
        if authority.get("productionAuthoritySource") != "gate_promoted_DynamoDB_champion_bundle_only":
            failures.append("wrong_production_authority_source")
        if optimization.get("automaticPromotionSupported") is not True or optimization.get("automaticPromotionEnabled") is not True:
            failures.append("optimization_automatic_promotion_not_enabled")

        payload.update({
            "ok": not failures,
            "failures": failures,
            "reportCreatedAt": report.get("createdAt"),
            "reportOk": report.get("ok"),
            "summary": report.get("summary"),
            "accuracyTargetPolicyInstall": policy_install,
            "accuracyLedger": report.get("accuracyLedger"),
            "mlCriticalFixStatus": critical,
            "mlOptimizationV3": optimization,
            "mlTrainingAuthority": authority,
            "dailyLockAuditFallback": {
                "applied": False,
                "officialAuditEligible": False,
                "policy": "Daily-card and legacy fallback rows are diagnostic-only; official audit and learning require exact canonical LOCKED#GAME authority.",
            },
            "stored": report.get("stored"),
            "storeError": report.get("storeError"),
        })
    except Exception as exc:
        payload.update({
            "ok": False,
            "failures": ["audit_exception"],
            "exceptionType": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        })

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
