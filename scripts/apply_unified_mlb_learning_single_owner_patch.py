from __future__ import annotations

import re
from pathlib import Path
from typing import Callable


WORKFLOW_ROOT = Path(".github/workflows")
DEPLOY = WORKFLOW_ROOT / "deploy.yml"
RECOVERY = WORKFLOW_ROOT / "mlb-r7-overnight-advance.yml"
INSTALLERS = {
    WORKFLOW_ROOT / "fix-unified-mlb-learning-ownership-on-branch.yml",
    WORKFLOW_ROOT / "fix-unified-mlb-learning-ownership-on-branch-v2.yml",
}


def replace_region(path: Path, start: str, end: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError(
            f"unique patch markers missing in {path}: {start!r} / {end!r}"
        )
    prefix, remainder = text.split(start, 1)
    _removed, suffix = remainder.split(end, 1)
    path.write_text(
        prefix + "\n" + replacement.rstrip() + "\n" + end + suffix,
        encoding="utf-8",
    )


def top_level_range(
    text: str, predicate: Callable[[str], bool]
) -> tuple[int, int] | None:
    lines = text.splitlines(keepends=True)
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if predicate(line.rstrip("\r\n"))
        ),
        None,
    )
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith((" ", "\t")):
            end = index
            break
    return start, end


def replace_top_level(
    text: str,
    predicate: Callable[[str], bool],
    replacement: str,
    *,
    insert_before: str | None = None,
) -> str:
    lines = text.splitlines(keepends=True)
    bounds = top_level_range(text, predicate)
    block = replacement.rstrip() + "\n\n"
    if bounds is not None:
        start, end = bounds
        return "".join(lines[:start]) + block + "".join(lines[end:])
    if insert_before is None:
        raise RuntimeError("required top-level section is missing")
    insertion = next(
        (
            index
            for index, line in enumerate(lines)
            if line.rstrip("\r\n") == insert_before
        ),
        None,
    )
    if insertion is None:
        raise RuntimeError(f"top-level insertion anchor is missing: {insert_before}")
    return "".join(lines[:insertion]) + block + "".join(lines[insertion:])


def invokes_training(text: str) -> bool:
    return (
        "invoke_mlb_trainer_with_retry.py" in text
        and "--payload" in text
        and bool(
            re.search(
                r'''["']mode["']\s*:\s*["'](?:scheduled|training)["']''',
                text,
            )
        )
    )


def normalize_manual_training_workflows() -> list[str]:
    changed: list[str] = []
    for path in sorted(WORKFLOW_ROOT.glob("*.y*ml")):
        if path in {DEPLOY, RECOVERY, *INSTALLERS}:
            continue
        text = path.read_text(encoding="utf-8")
        if not invokes_training(text):
            continue
        text = replace_top_level(
            text,
            lambda line: line in {"on:", '"on":', "'on':"},
            '"on":\n  workflow_dispatch:',
        )
        text = replace_top_level(
            text,
            lambda line: line == "concurrency:" or line.startswith("concurrency:"),
            (
                "concurrency:\n"
                "  group: unified-mlb-learning\n"
                "  cancel-in-progress: false"
            ),
            insert_before="jobs:",
        )
        path.write_text(text, encoding="utf-8")
        changed.append(path.as_posix())
    return changed


def patch_deploy() -> None:
    replace_region(
        DEPLOY,
        "\n      - name: Run AWS-native MLB trainer and verify fresh split health\n",
        "\n      - name: Smoke test health and MLB read runtime\n",
        """      - name: Preserve unified MLB learning ownership
        run: |
          set -euo pipefail
          echo "UNIFIED_MLB_LEARNING_OWNER=eventbridge_schedule"
          python scripts/verify_unified_mlb_learning_ownership.py
          python -m pytest -q tests/unit/test_unified_mlb_learning_ownership.py""",
    )


def patch_stabilizer() -> None:
    path = Path("scripts/stabilize_mlb_deploy_source.py")
    text = path.read_text(encoding="utf-8")
    old_required = (
        "Run AWS-native MLB trainer and verify fresh split health",
        "--retry-execution-lease",
        "--deadline-seconds 1200",
        "--status-training-result /tmp/mlb-ml-v2-training.json",
        "--status-selection-capture-result /tmp/mlb-ml-v2-selection-capture.json",
        "aws_native_fixed_prospective_shadow_training",
        "aws_native_prospective_selection_capture",
        "trainingHealth",
        "selectionCaptureHealth",
        "deploymentIdentityMatches",
    )
    for token in old_required:
        line = f'        "{token}",\n'
        if line not in text:
            raise RuntimeError(f"stabilizer old ownership token missing: {token}")
        text = text.replace(line, "", 1)
    anchor = '        "invoke_mlb_trainer_with_retry.py",\n'
    additions = (
        '        "Preserve unified MLB learning ownership",\n'
        '        "UNIFIED_MLB_LEARNING_OWNER=eventbridge_schedule",\n'
        '        "verify_unified_mlb_learning_ownership.py",\n'
        '        "test_unified_mlb_learning_ownership.py",\n'
    )
    if text.count(anchor) != 1:
        raise RuntimeError("stabilizer helper anchor is not unique")
    path.write_text(text.replace(anchor, anchor + additions, 1), encoding="utf-8")


def patch_workflow_authority() -> None:
    path = Path("scripts/verify_mlb_workflow_authority.py")
    replace_region(
        path,
        "        status_training_token = (\n",
        "        gate_call = (\n",
        """        active_trainer_invokes = [
            line.strip()
            for line in deploy.splitlines()
            if line.strip().startswith(
                "python scripts/invoke_mlb_trainer_with_retry.py"
            )
        ]
        if "UNIFIED_MLB_LEARNING_OWNER=eventbridge_schedule" not in deploy:
            errors.append(
                "canonical_deploy_unified_mlb_learning_owner_marker_missing"
            )
        if "Preserve unified MLB learning ownership" not in deploy:
            errors.append(
                "canonical_deploy_unified_mlb_learning_owner_step_missing"
            )
        if active_trainer_invokes:
            errors.append(
                "canonical_deploy_must_not_invoke_unified_mlb_training"
            )
        if "Run AWS-native MLB trainer and verify fresh split health" in deploy:
            errors.append("canonical_deploy_retains_training_owner_step")
        if "python scripts/verify_unified_mlb_learning_ownership.py" not in deploy:
            errors.append(
                "canonical_deploy_does_not_verify_single_learning_owner"
            )
        if "tests/unit/test_unified_mlb_learning_ownership.py" not in deploy:
            errors.append(
                "canonical_deploy_does_not_test_single_learning_owner"
            )
        if "invoke_mlb_trainer_deploy_probe.py" in deploy:
            errors.append("canonical_deploy_retains_duplicate_trainer_invoke_helper")
        if "aws lambda invoke" in deploy:
            errors.append("canonical_deploy_retains_unsafe_inline_trainer_invoke")""",
    )
    text = path.read_text(encoding="utf-8")
    for line in (
        '            "AWS_MAX_ATTEMPTS: \\"1\\"",\n',
        '            "python scripts/invoke_mlb_trainer_with_retry.py",\n',
    ):
        if line not in text:
            raise RuntimeError(f"authority capacity token missing: {line!r}")
        text = text.replace(line, "", 1)
    old_count = """        if deploy.count("python scripts/invoke_mlb_trainer_with_retry.py") != 3:
            errors.append(
                "canonical_deploy_must_use_bounded_invoke_retry_exactly_three_times"
            )
"""
    new_count = """        active_trainer_invokes = [
            line.strip()
            for line in deploy.splitlines()
            if line.strip().startswith(
                "python scripts/invoke_mlb_trainer_with_retry.py"
            )
        ]
        if active_trainer_invokes:
            errors.append(
                "canonical_deploy_must_not_invoke_unified_mlb_training"
            )
"""
    if text.count(old_count) != 1:
        raise RuntimeError("authority exact-three invocation block missing")
    path.write_text(text.replace(old_count, new_count, 1), encoding="utf-8")


def patch_workflow_authority_test() -> None:
    path = Path("tests/unit/test_mlb_workflow_authority.py")
    replacement = """def test_rejects_deploy_that_reintroduces_training_owner(
    tmp_path: Path,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    deploy.write_text(
        deploy.read_text(encoding="utf-8")
        + "\n      - name: Unsafe duplicate trainer owner\n"
        + "        run: |\n"
        + "          python scripts/invoke_mlb_trainer_with_retry.py \\\n"
        + "            --payload '{\"sport\":\"mlb\",\"mode\":\"scheduled\",\"run\":\"unsafe_duplicate\"}'\n",
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_must_not_invoke_unified_mlb_training"
        in authority.verify_repository(root)
    )


"""
    replace_region(
        path,
        "def test_rejects_deploy_that_bypasses_one_trainer_retry_helper_call(\n",
        "def test_rejects_reintroduced_inline_aws_trainer_invoke",
        replacement,
    )


def apply() -> list[str]:
    patch_deploy()
    normalized = normalize_manual_training_workflows()
    patch_stabilizer()
    patch_workflow_authority()
    patch_workflow_authority_test()
    return normalized


def main() -> int:
    normalized = apply()
    print(f"Normalized {len(normalized)} alternate trainer workflows to manual-only:")
    for workflow in normalized:
        print(workflow)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
