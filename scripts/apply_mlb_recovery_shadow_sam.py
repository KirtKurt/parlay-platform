#!/usr/bin/env python3
"""Apply the isolated MLB recovery-shadow SAM resource and CI contract.

The resource is intentionally non-authoritative. It reads canonical snapshots and
private ML artifacts, writes only to the snapshots table under recovery-specific
keys enforced by its runtime, and has no PredictionsTable or signal-ledger policy.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template.yaml"
SOURCE_CONTRACT = ROOT / ".github" / "workflows" / "mlb-production-source-contract.yml"

RESOURCE = r'''  MLBRecoveryShadowFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_recovery_shadow_v1.lambda_handler
      Runtime: python3.11
      Timeout: 300
      MemorySize: 1024
      Environment:
        Variables:
          SNAPSHOTS_TABLE: !Ref SnapshotsTable
          MLB_ML_ARTIFACTS_BUCKET: !Ref MLBMLArtifactsBucket
          INQSI_RECOVERY_SHADOW_ONLY: 'true'
          INQSI_RECOVERY_PRODUCTION_AUTHORITY: 'false'
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref SnapshotsTable
        - S3ReadPolicy:
            BucketName: !Ref MLBMLArtifactsBucket
      EventInvokeConfig:
        MaximumRetryAttempts: 0
        MaximumEventAgeInSeconds: 900
      Events:
        MLBRecoveryShadowCaptureEvery15Minutes:
          Type: Schedule
          Properties:
            Schedule: cron(9/15 * * * ? *)
            Enabled: true
            Input: '{"mode":"capture"}'
        MLBRecoveryShadowGradeEvery6Hours:
          Type: Schedule
          Properties:
            Schedule: cron(37 0/6 * * ? *)
            Enabled: true
            Input: '{"mode":"grade","days":3}'

'''


def _patch_template() -> bool:
    text = TEMPLATE.read_text(encoding="utf-8")
    changed = False
    if "  MLBRecoveryShadowFunction:\n" not in text:
        anchor = "  SoccerSchedulerFunction:\n"
        if anchor not in text:
            raise SystemExit("SoccerSchedulerFunction insertion anchor missing")
        text = text.replace(anchor, RESOURCE + anchor, 1)
        changed = True
    block = text.split("  MLBRecoveryShadowFunction:\n", 1)[1].split(
        "\n  SoccerSchedulerFunction:\n", 1
    )[0]
    forbidden = ("PredictionsTable", "SignalLedgerTable", "PREDICTIONS_TABLE", "SIGNAL_LEDGER_TABLE")
    violations = [token for token in forbidden if token in block]
    if violations:
        raise SystemExit("Recovery shadow resource gained forbidden authority: " + ",".join(violations))
    required = (
        "Handler: mlb_recovery_shadow_v1.lambda_handler",
        "TableName: !Ref SnapshotsTable",
        "BucketName: !Ref MLBMLArtifactsBucket",
        "INQSI_RECOVERY_SHADOW_ONLY: 'true'",
        "INQSI_RECOVERY_PRODUCTION_AUTHORITY: 'false'",
        "MLBRecoveryShadowCaptureEvery15Minutes",
        "MLBRecoveryShadowGradeEvery6Hours",
    )
    missing = [token for token in required if token not in block]
    if missing:
        raise SystemExit("Recovery shadow SAM resource incomplete: " + ",".join(missing))
    if changed:
        TEMPLATE.write_text(text, encoding="utf-8")
    return changed


def _insert_after(text: str, anchor: str, line: str) -> tuple[str, bool]:
    if line in text:
        return text, False
    if anchor not in text:
        raise SystemExit(f"CI insertion anchor missing: {anchor}")
    return text.replace(anchor, anchor + line, 1), True


def _patch_source_contract() -> bool:
    text = SOURCE_CONTRACT.read_text(encoding="utf-8")
    changed = False
    text, did = _insert_after(
        text,
        "            tests/unit/test_mlb_public_per_game_authority.py \\\n",
        "            tests/unit/test_mlb_recovery_shadow_v1.py \\\n",
    )
    changed = changed or did
    compile_anchor = "          python -m py_compile hello_world/mlb_probability_actionability_guard.py\n"
    compile_lines = (
        "          python -m py_compile hello_world/mlb_recovery_v10_engine.py\n"
        "          python -m py_compile hello_world/mlb_recovery_v11_engine.py\n"
        "          python -m py_compile hello_world/mlb_recovery_shadow_v1.py\n"
    )
    if "python -m py_compile hello_world/mlb_recovery_shadow_v1.py" not in text:
        if compile_anchor not in text:
            raise SystemExit("CI compile insertion anchor missing")
        text = text.replace(compile_anchor, compile_anchor + compile_lines, 1)
        changed = True
    if changed:
        SOURCE_CONTRACT.write_text(text, encoding="utf-8")
    return changed


def main() -> int:
    changed = {
        "template": _patch_template(),
        "sourceContract": _patch_source_contract(),
    }
    print(changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
