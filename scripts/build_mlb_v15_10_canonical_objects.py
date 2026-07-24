#!/usr/bin/env python3
"""Build validated Git blobs for the MLB V15.10 canonical source migration.

This helper never moves a branch ref.  It updates the checked-out worktree,
then optionally uploads the resulting file bodies as unattached Git blobs.
The caller must run the repository's production tests before requesting blob
publication and must create/move the final commit through a separately audited
GitHub action or connector.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict

RUNTIME_VERSION = (
    "MLB-ML-RUNTIME-INSTALL-v4.4-ranked-winner-v15.10-"
    "prelock-persistence-verified-stage-promotion-authority-"
    "verified-active-model-authority"
)

MIGRATED_PATHS = (
    ".github/workflows/deploy.yml",
    "scripts/verify_api_function_mlb_v3_import.py",
    "scripts/verify_mlb_workflow_authority.py",
    "tests/unit/test_mlb_workflow_authority.py",
)


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count == 1:
        return text.replace(old, new)
    if count == 0 and new in text:
        return text
    raise RuntimeError(f"{label}: replacement count was {count}, expected 1")


def _migrate_deploy_workflow() -> None:
    path = Path(".github/workflows/deploy.yml")
    text = path.read_text(encoding="utf-8")
    old = """          if payload.get('model_version') != 'INQSI-MLB-v4.0-canonical-probability-aws-v2-shadow-manual-first':
              raise SystemExit('Live MLB model version is stale')
          if payload.get('productionAuthoritySource') != 'persisted_canonical_rules_market_prediction_v2_shadow_only':
              raise SystemExit('Live MLB authority source is stale')
          if payload.get('automaticPromotionPolicy') != 'disabled_manual_review_creates_shadow_pointer_only':
              raise SystemExit('Live MLB promotion policy is stale')
          if payload.get('legacyV1AuthorityEnabled') is not False:
              raise SystemExit('Legacy V1 authority is not disabled')
          if payload.get('awsNativeTrainingInstalled') is not True:
              raise SystemExit('AWS-native V2 training is not installed')
          if payload.get('awsNativeTrainingAuthority') is not False:
              raise SystemExit('AWS-native V2 training incorrectly claims live authority')
          if payload.get('awsNativeTrainingHealthSource') != 'separate_mode_specific_status_contract':
              raise SystemExit('Live MLB training health source is stale')
          if payload.get('firstPromotionRequiresManualReview') is not True:
              raise SystemExit('Manual-first V2 promotion is not enforced')
          if payload.get('manualReviewCreatesShadowApprovalOnly') is not True:
              raise SystemExit('Manual V2 review is not shadow-only')
          if payload.get('v2InferenceConsumerInstalled') is not False:
              raise SystemExit('Unreviewed V2 inference consumer is installed')
          if payload.get('runtimeAuthorityActivationAvailable') is not False:
              raise SystemExit('Unreviewed V2 runtime authority activation is available')
          runtime = payload.get('ml_runtime_install') or {}
          if runtime.get('ok') is not True:
              raise SystemExit('Live MLB runtime installation is not healthy')
          if runtime.get('version') != 'MLB-ML-RUNTIME-INSTALL-v4.1-verified-stage-promotion-authority-aws-v2-shadow-manual-first':
              raise SystemExit('Live MLB runtime contract is stale')
          steps = runtime.get('steps') or {}
          for name in (
              'legacyV1ChampionRuntimeInstalledForShadowDiagnostics',
              'legacyV1AuthorityDisabled',
              'v2ShadowManualFirst',
              'canonicalProbabilityAndPersistedPrelockAuthority',
              'providerNeutralCalibrationAndActionability',
              'sourceHonestFundamentalsV2',
          ):
"""
    new = """          expected = {
              'model_version': 'INQSI-MLB-v5.0-ranked-winner-v15.10-active-ensemble',
              'primaryAlgorithm': 'INQSI-MLB-RANKED-WINNER-v15.10.0-active-ensemble',
              'primaryAlgorithmActive': True,
              'rankedWinnerPolicyVersion': '2026-07-24-mlb-ranked-winner-primary-v1',
              'productionAuthoritySource': 'mlb_ranked_winner_v15_10_active_ensemble',
              'automaticPromotionPolicy': 'winner model fixed for release; precision/trade promotion remains disabled',
              'legacyV1AuthorityEnabled': False,
              'awsNativeTrainingInstalled': True,
              'awsNativeTrainingAuthority': False,
              'awsNativeTrainingHealthSource': 'separate_mode_specific_status_contract',
              'firstPromotionRequiresManualReview': True,
              'manualReviewCreatesShadowApprovalOnly': True,
              'v2InferenceConsumerInstalled': False,
              'runtimeAuthorityActivationAvailable': True,
              'allowedProductionOutput': ['PICK'],
              'productionSelectionAllowed': True,
              'automaticWagerAllowed': False,
              'legacyRecommendationAuthority': False,
              'legacyFallbackAllowed': False,
              'precisionHitRateEvidencePassed': False,
          }
          mismatches = {
              key: {'expected': value, 'actual': payload.get(key)}
              for key, value in expected.items()
              if payload.get(key) != value
          }
          if mismatches:
              raise SystemExit(
                  'Live MLB V15.10 authority contract is stale: '
                  + json.dumps(mismatches, sort_keys=True)
              )
          runtime = payload.get('ml_runtime_install') or {}
          if runtime.get('ok') is not True:
              raise SystemExit('Live MLB runtime installation is not healthy')
          if runtime.get('version') != 'MLB-ML-RUNTIME-INSTALL-v4.4-ranked-winner-v15.10-prelock-persistence-verified-stage-promotion-authority-verified-active-model-authority':
              raise SystemExit('Live MLB V15.10 runtime contract is stale')
          steps = runtime.get('steps') or {}
          for name in (
              'legacyV1ChampionRuntimeInstalledForShadowDiagnostics',
              'legacyV1AuthorityDisabled',
              'v2ShadowManualFirst',
              'canonicalProbabilityAndPersistedPrelockAuthority',
              'providerNeutralCalibrationAndActionability',
              'sourceHonestFundamentalsV2',
              'lastPrelockPromotionAuthority',
              'rankedWinnerV15_10DirectionInstalled',
              'rankedWinnerV15_10SelectionInstalled',
              'canonicalLockedStorageFinalizer',
          ):
"""
    text = _replace_once(text, old, new, label="deploy live-authority contract")
    ranked_test = "            tests/unit/test_mlb_ranked_primary_v15_10.py\n"
    insertion = "            tests/unit/test_mlb_ml_runtime_install_explicit.py\n"
    if ranked_test not in text:
        text = _replace_once(
            text,
            insertion,
            ranked_test + insertion,
            label="deploy ranked regression insertion",
        )
    path.write_text(text, encoding="utf-8")


def _migrate_cold_import_verifier() -> None:
    path = Path("scripts/verify_api_function_mlb_v3_import.py")
    text = path.read_text(encoding="utf-8")
    old = """EXPECTED_RUNTIME = (
    "MLB-ML-RUNTIME-INSTALL-v4.4-ranked-winner-v15.10-"
    "prelock-persistence-verified-active-model-authority"
)"""
    new = """EXPECTED_RUNTIME = (
    "MLB-ML-RUNTIME-INSTALL-v4.4-ranked-winner-v15.10-"
    "prelock-persistence-verified-stage-promotion-authority-"
    "verified-active-model-authority"
)"""
    path.write_text(
        _replace_once(text, old, new, label="cold-import runtime identity"),
        encoding="utf-8",
    )


def _migrate_workflow_authority_verifier() -> None:
    path = Path("scripts/verify_mlb_workflow_authority.py")
    text = path.read_text(encoding="utf-8")
    old = """        shadow_runtime_tokens = {
            "disabled_manual_review_creates_shadow_pointer_only": (
                "canonical_deploy_does_not_require_shadow_only_manual_review"
            ),
            "payload.get('awsNativeTrainingInstalled') is not True": (
                "canonical_deploy_does_not_require_aws_training_installation"
            ),
            "payload.get('awsNativeTrainingAuthority') is not False": (
                "canonical_deploy_allows_training_to_claim_live_authority"
            ),
            "separate_mode_specific_status_contract": (
                "canonical_deploy_does_not_require_split_training_health"
            ),
            "payload.get('manualReviewCreatesShadowApprovalOnly') is not True": (
                "canonical_deploy_does_not_require_shadow_only_approval"
            ),
            "payload.get('v2InferenceConsumerInstalled') is not False": (
                "canonical_deploy_allows_unreviewed_v2_inference"
            ),
            "payload.get('runtimeAuthorityActivationAvailable') is not False": (
                "canonical_deploy_allows_unreviewed_runtime_activation"
            ),
        }
        for token, error in shadow_runtime_tokens.items():
            if token not in deploy:
                errors.append(error)
"""
    new = """        ranked_runtime_tokens = {
            "'automaticPromotionPolicy': 'winner model fixed for release; precision/trade promotion remains disabled'": (
                "canonical_deploy_does_not_require_fixed_ranked_release_policy"
            ),
            "'awsNativeTrainingInstalled': True": (
                "canonical_deploy_does_not_require_aws_training_installation"
            ),
            "'awsNativeTrainingAuthority': False": (
                "canonical_deploy_allows_training_to_claim_live_authority"
            ),
            "'awsNativeTrainingHealthSource': 'separate_mode_specific_status_contract'": (
                "canonical_deploy_does_not_require_split_training_health"
            ),
            "'manualReviewCreatesShadowApprovalOnly': True": (
                "canonical_deploy_does_not_require_shadow_only_training_approval"
            ),
            "'v2InferenceConsumerInstalled': False": (
                "canonical_deploy_allows_unreviewed_v2_inference"
            ),
            "'runtimeAuthorityActivationAvailable': True": (
                "canonical_deploy_does_not_require_ranked_runtime_activation"
            ),
            "'primaryAlgorithm': 'INQSI-MLB-RANKED-WINNER-v15.10.0-active-ensemble'": (
                "canonical_deploy_does_not_require_ranked_winner_primary"
            ),
            "'allowedProductionOutput': ['PICK']": (
                "canonical_deploy_does_not_require_ranked_pick_output"
            ),
            "'legacyRecommendationAuthority': False": (
                "canonical_deploy_allows_legacy_recommendation_authority"
            ),
            "'legacyFallbackAllowed': False": (
                "canonical_deploy_allows_legacy_fallback"
            ),
            "'automaticWagerAllowed': False": (
                "canonical_deploy_allows_automatic_wagering"
            ),
            "MLB-ML-RUNTIME-INSTALL-v4.4-ranked-winner-v15.10-prelock-persistence-verified-stage-promotion-authority-verified-active-model-authority": (
                "canonical_deploy_does_not_require_ranked_runtime_identity"
            ),
        }
        for token, error in ranked_runtime_tokens.items():
            if token not in deploy:
                errors.append(error)
"""
    path.write_text(
        _replace_once(text, old, new, label="workflow authority token contract"),
        encoding="utf-8",
    )


def _migrate_workflow_authority_test() -> None:
    path = Path("tests/unit/test_mlb_workflow_authority.py")
    text = path.read_text(encoding="utf-8")
    old = """        text.replace(
            "payload.get('awsNativeTrainingAuthority') is not False",
            "payload.get('awsNativeTrainingAuthority') is not True",
            1,
        ),"""
    new = """        text.replace(
            "'awsNativeTrainingAuthority': False",
            "'awsNativeTrainingAuthority': True",
            1,
        ),"""
    path.write_text(
        _replace_once(text, old, new, label="workflow authority mutation test"),
        encoding="utf-8",
    )


def apply_migration() -> None:
    _migrate_deploy_workflow()
    _migrate_cold_import_verifier()
    _migrate_workflow_authority_verifier()
    _migrate_workflow_authority_test()


def _create_blob(repository: str, token: str, path: str) -> Dict[str, object]:
    file_path = Path(path)
    payload = json.dumps(
        {
            "content": base64.b64encode(file_path.read_bytes()).decode("ascii"),
            "encoding": "base64",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/git/blobs",
        data=payload,
        method="POST",
        headers={
            "accept": "application/vnd.github+json",
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "user-agent": "mlb-v15-10-canonical-object-builder/1.0",
            "x-github-api-version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {"blob_sha": body["sha"], "size": file_path.stat().st_size}


def publish_blobs(output: Path) -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    head_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    result = {
        "base_head_sha": head_sha,
        "repository": repository,
        "runtime_version": RUNTIME_VERSION,
        "files": {
            path: _create_blob(repository, token, path) for path in MIGRATED_PATHS
        },
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/mlb-v15-10-canonical-git-objects.json"),
    )
    args = parser.parse_args()
    if not args.apply and not args.publish:
        parser.error("select --apply and/or --publish")
    if args.apply:
        apply_migration()
    if args.publish:
        publish_blobs(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
