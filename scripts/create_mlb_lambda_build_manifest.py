from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

try:
    from scripts.mlb_lambda_artifact_identity import (
        MANIFEST_SCHEMA_VERSION,
        directory_content_manifest,
    )
except ModuleNotFoundError:
    from mlb_lambda_artifact_identity import (
        MANIFEST_SCHEMA_VERSION,
        directory_content_manifest,
    )


FUNCTION_LOGICAL_IDS = (
    "MLBAuditedPullFunction",
    "MLBDailyPickLockFunction",
    "MLBMLTrainingFunction",
    "MLBProductionVerifierFunction",
    "MLBV3ReadFunction",
    "MLBResultsSchedulerFunction",
    "SoccerSchedulerFunction",
    "InqsiAutopsySchedulerFunction",
)


def create_manifest(
    *,
    build_root: Path,
    expected_git_sha: str,
    expected_template_sha256: str,
) -> Dict[str, Any]:
    git_sha = str(expected_git_sha or "").strip()
    template_sha = str(expected_template_sha256 or "").strip()
    if len(git_sha) != 40:
        raise ValueError("expected Git SHA must contain exactly 40 characters")
    if len(template_sha) != 64:
        raise ValueError("expected template SHA-256 must contain exactly 64 characters")
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "expectedGitSha": git_sha,
        "expectedTemplateSha256": template_sha,
        "functions": {
            logical_id: directory_content_manifest(build_root / logical_id)
            for logical_id in FUNCTION_LOGICAL_IDS
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-root", required=True)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-template-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    manifest = create_manifest(
        build_root=Path(args.build_root),
        expected_git_sha=args.expected_git_sha,
        expected_template_sha256=args.expected_template_sha256,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
