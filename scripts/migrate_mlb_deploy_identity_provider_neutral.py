#!/usr/bin/env python3
"""Migrate MLB deploy-identity verification to the provider-neutral contract.

The deployed Lambda package attestation remains unchanged. This migration only
removes stale requirements for the retired BBD/BBS credential and shadow
capture environment, then requires those retired variables to be absent from
every canonical and discovered Lambda.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify_mlb_deploy_identity.py"
TEST = ROOT / "tests" / "unit" / "test_mlb_deploy_identity.py"
NO_BBD = ROOT / "scripts" / "verify_mlb_no_bbd_runtime.py"


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"migration marker missing: {label}")


def _replace_regex(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count == 1:
        return updated
    if replacement in text:
        return text
    raise RuntimeError(f"migration regex marker missing: {label}")


def patch_verifier(text: str) -> str:
    old_constants = '''BBS_EXPECTED_INGEST_ENVIRONMENT = {
    "BBS_SHADOW_CAPTURE_ENABLED": "true",
    "BBS_SHADOW_SCHEMA_VERSION": (
        "MLB-BBS-SHADOW-v2-canonical-bound-raw-only"
    ),
}
BBS_SECRET_ARN_ENVIRONMENT = "BBS_API_SECRET_ARN"
BBS_FORBIDDEN_PLAINTEXT_ENVIRONMENT = "BBS_API_KEY"
RETIRED_PROVIDER_ENVIRONMENT = (
    "SPORTSDATAIO_API_KEY",
    "SPORTSDATAIO_BASE_URL",
    "SPORTSDATAIO_MLB_GAMES_ENDPOINT",
    "SPORTSDATAIO_MLB_PBP_ENDPOINT",
)
'''
    new_constants = '''_RETIRED_BBS_ENVIRONMENT = (
    "BBS" + "_API_KEY",
    "BBS" + "_API_SECRET_ARN",
    "BBS" + "_SHADOW_CAPTURE_ENABLED",
    "BBS" + "_SHADOW_S3_BUCKET",
    "BBS" + "_SHADOW_SCHEMA_VERSION",
    "Bbs" + "ApiKey",
    "Bbs" + "ApiSecret",
)
RETIRED_PROVIDER_ENVIRONMENT = (
    *_RETIRED_BBS_ENVIRONMENT,
    "SPORTSDATAIO_API_KEY",
    "SPORTSDATAIO_BASE_URL",
    "SPORTSDATAIO_MLB_GAMES_ENDPOINT",
    "SPORTSDATAIO_MLB_PBP_ENDPOINT",
)
'''
    text = _replace_once(text, old_constants, new_constants, "provider constants")

    old_proof = '''    provider_credential_proof: Dict[str, Any] = {
        "provider": "Big Balls Sports Data",
        "exactGithubSecretName": "BBS_API_KEY",
        "runtimeSecretArnEnvironment": BBS_SECRET_ARN_ENVIRONMENT,
        "consumerRole": "ingest",
        "secretArnPresentOnIngest": False,
        "shadowEnvironmentMatches": False,
        "plaintextKeyEnvironmentAbsent": True,
        "retiredProviderEnvironmentAbsent": True,
        "otherCanonicalFunctionsWithoutBbsAuthority": True,
    }
'''
    new_proof = '''    provider_credential_proof: Dict[str, Any] = {
        "provider": "PROVIDER_NEUTRAL_OFFICIAL_INTERNAL",
        "credentialRequired": False,
        "credentialEnvironmentPresent": False,
        "retiredProviderEnvironmentAbsent": True,
        "ingestRetiredProviderEnvironmentAbsent": True,
        "allCanonicalFunctionsWithoutRetiredProviderAuthority": True,
    }
'''
    text = _replace_once(text, old_proof, new_proof, "provider proof")

    canonical_replacement = '''        retired_present = sorted(
            key
            for key in environment
            if key in RETIRED_PROVIDER_ENVIRONMENT
            or str(key).startswith("BBS" + "_")
            or str(key).startswith("Bbs" + "Api")
        )
        if retired_present:
            configuration_matches = False
            provider_credential_proof["retiredProviderEnvironmentAbsent"] = False
            provider_credential_proof[
                "allCanonicalFunctionsWithoutRetiredProviderAuthority"
            ] = False
            if role == "ingest":
                provider_credential_proof[
                    "ingestRetiredProviderEnvironmentAbsent"
                ] = False
            blockers.append(
                f"RETIRED_PROVIDER_ENVIRONMENT_PRESENT:{logical_id}:"
                + ",".join(retired_present)
            )
        if role == "lock":'''
    text = _replace_regex(
        text,
        r'''        retired_present = sorted\(\n            key for key in RETIRED_PROVIDER_ENVIRONMENT if key in environment\n        \)\n[\s\S]*?        if role == "lock":''',
        canonical_replacement,
        "canonical function provider boundary",
    )

    discovered_replacement = '''            retired_present = sorted(
                key
                for key in environment
                if key in RETIRED_PROVIDER_ENVIRONMENT
                or str(key).startswith("BBS" + "_")
                or str(key).startswith("Bbs" + "Api")
            )
            if retired_present:
                provider_credential_proof["retiredProviderEnvironmentAbsent"] = False
                provider_credential_proof[
                    "allCanonicalFunctionsWithoutRetiredProviderAuthority"
                ] = False
                if _base_lambda_arn(arn) == _base_lambda_arn(
                    function_arns.get("ingest")
                ):
                    provider_credential_proof[
                        "ingestRetiredProviderEnvironmentAbsent"
                    ] = False
                blockers.append(
                    f"RETIRED_PROVIDER_ENVIRONMENT_PRESENT_ON_DISCOVERED_LAMBDA:{name}:"
                    + ",".join(retired_present)
                )
            if not arn or not _is_mlb_pull_or_training_writer('''
    text = _replace_regex(
        text,
        r'''            retired_present = sorted\(\n                key for key in RETIRED_PROVIDER_ENVIRONMENT if key in environment\n            \)\n[\s\S]*?            if not arn or not _is_mlb_pull_or_training_writer\(''',
        discovered_replacement,
        "discovered function provider boundary",
    )

    stale = (
        "BBS_SECRET_ARN_MISSING_ON_INGEST",
        "BBS_SHADOW_ENVIRONMENT_MISMATCH_ON_INGEST",
        "secretArnPresentOnIngest",
        "shadowEnvironmentMatches",
        "otherCanonicalFunctionsWithoutBbsAuthority",
        "BBS_AUTHORITY_LEAKED_TO_",
        "BBS_AUTHORITY_PRESENT_ON_NON_INGEST_LAMBDA",
    )
    remaining = [token for token in stale if token in text]
    if remaining:
        raise RuntimeError("stale provider identity requirements remain: " + ",".join(remaining))
    return text


def patch_test(text: str) -> str:
    text = re.sub(
        r'''            if role == "ingest":\n                environment\.update\(\n                    \{\n                        "BBS_API_SECRET_ARN":[\s\S]*?                    \}\n                \)\n''',
        "",
        text,
        count=1,
    )

    old_proof = '''    assert result["providerCredentialBoundary"] == {
        "provider": "Big Balls Sports Data",
        "exactGithubSecretName": "BBS_API_KEY",
        "runtimeSecretArnEnvironment": "BBS_API_SECRET_ARN",
        "consumerRole": "ingest",
        "secretArnPresentOnIngest": True,
        "shadowEnvironmentMatches": True,
        "plaintextKeyEnvironmentAbsent": True,
        "retiredProviderEnvironmentAbsent": True,
        "otherCanonicalFunctionsWithoutBbsAuthority": True,
    }
'''
    new_proof = '''    assert result["providerCredentialBoundary"] == {
        "provider": "PROVIDER_NEUTRAL_OFFICIAL_INTERNAL",
        "credentialRequired": False,
        "credentialEnvironmentPresent": False,
        "retiredProviderEnvironmentAbsent": True,
        "ingestRetiredProviderEnvironmentAbsent": True,
        "allCanonicalFunctionsWithoutRetiredProviderAuthority": True,
    }
'''
    text = _replace_once(text, old_proof, new_proof, "test provider proof")

    read_test = '''def test_rejects_retired_provider_authority_on_public_read_lambda(aws) -> None:
    environment = aws["lambda"].configurations["physical-read"]["Environment"]["Variables"]
    environment["BBS" + "_API_SECRET_ARN"] = "arn:forbidden"

    result = _verify()

    assert result["ok"] is False
    assert result["providerCredentialBoundary"][
        "allCanonicalFunctionsWithoutRetiredProviderAuthority"
    ] is False
    assert any(
        blocker.startswith("RETIRED_PROVIDER_ENVIRONMENT_PRESENT:MLBV3ReadFunction:")
        for blocker in result["blockers"]
    )


def test_rejects_retired_provider_authority_on_ingest_lambda(aws) -> None:
    environment = aws["lambda"].configurations["physical-ingest"]["Environment"]["Variables"]
    environment["BBS" + "_SHADOW_CAPTURE_ENABLED"] = "true"

    result = _verify()

    assert result["ok"] is False
    boundary = result["providerCredentialBoundary"]
    assert boundary["retiredProviderEnvironmentAbsent"] is False
    assert boundary["ingestRetiredProviderEnvironmentAbsent"] is False
    assert boundary["allCanonicalFunctionsWithoutRetiredProviderAuthority"] is False
    assert any(
        blocker.startswith("RETIRED_PROVIDER_ENVIRONMENT_PRESENT:MLBAuditedPullFunction:")
        for blocker in result["blockers"]
    )


'''
    text = _replace_regex(
        text,
        r'''def test_rejects_bbs_credential_authority_on_public_read_lambda\(aws\) -> None:\n[\s\S]*?(?=def test_rejects_plaintext_or_retired_provider_environment_drift)''',
        read_test,
        "retired provider canonical tests",
    )

    drift_test = '''def test_rejects_plaintext_or_retired_provider_environment_drift(aws) -> None:
    ingest = aws["lambda"].configurations["physical-ingest"]["Environment"]["Variables"]
    ingest["BBS" + "_API_KEY"] = "must-not-be-a-lambda-environment-value"
    ingest["SPORTSDATAIO_API_KEY"] = "retired"

    result = _verify()

    assert result["ok"] is False
    boundary = result["providerCredentialBoundary"]
    assert boundary["retiredProviderEnvironmentAbsent"] is False
    assert boundary["ingestRetiredProviderEnvironmentAbsent"] is False
    assert boundary["allCanonicalFunctionsWithoutRetiredProviderAuthority"] is False
    blocker = next(
        value
        for value in result["blockers"]
        if value.startswith("RETIRED_PROVIDER_ENVIRONMENT_PRESENT:MLBAuditedPullFunction:")
    )
    assert "BBS_API_KEY" in blocker
    assert "SPORTSDATAIO_API_KEY" in blocker


'''
    text = _replace_regex(
        text,
        r'''def test_rejects_plaintext_or_retired_provider_environment_drift\(aws\) -> None:\n[\s\S]*?(?=def )''',
        drift_test,
        "retired provider drift test",
    )

    stale = (
        "Big Balls Sports Data",
        "secretArnPresentOnIngest",
        "shadowEnvironmentMatches",
        "otherCanonicalFunctionsWithoutBbsAuthority",
        "BBS_AUTHORITY_LEAKED_TO_READ",
        "BBS_PLAINTEXT_KEY_ENVIRONMENT_PRESENT",
    )
    remaining = [token for token in stale if token in text]
    if remaining:
        raise RuntimeError("stale provider tests remain: " + ",".join(remaining))
    return text


def patch_no_bbd(text: str) -> str:
    marker = '    Path("scripts/stabilize_mlb_deploy_source.py"),\n'
    insertion = marker + '    Path("scripts/verify_mlb_deploy_identity.py"),\n'
    if insertion in text:
        return text
    if marker not in text:
        raise RuntimeError("no-BBD active-file marker missing")
    return text.replace(marker, insertion, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    before = {
        VERIFIER: VERIFIER.read_text(encoding="utf-8"),
        TEST: TEST.read_text(encoding="utf-8"),
        NO_BBD: NO_BBD.read_text(encoding="utf-8"),
    }
    after = {
        VERIFIER: patch_verifier(before[VERIFIER]),
        TEST: patch_test(before[TEST]),
        NO_BBD: patch_no_bbd(before[NO_BBD]),
    }
    changed = [path for path in before if before[path] != after[path]]
    if args.check:
        if changed:
            for path in changed:
                print(f"pending_provider_neutral_identity_migration:{path.relative_to(ROOT)}")
            return 1
        print("MLB deploy identity is already provider-neutral")
        return 0

    for path, content in after.items():
        path.write_text(content, encoding="utf-8")
    for path in changed:
        print(f"migrated:{path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
