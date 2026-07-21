#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any, List

import yaml


ROOT = Path(__file__).resolve().parents[1]
PARAMETER_NAME = "SportsDataIoApiKey"
APPROVED_FUNCTIONS = {
    "ApiFunction",
    "MLBAuditedPullFunction",
    "MLBSignalApiFunction",
}
PROTECTED_FUNCTIONS = {"MLBV3ReadFunction", "MLBMLTrainingFunction"}
EXPECTED_ENVIRONMENT: dict[str, Any] = {
    "SPORTSDATAIO_API_KEY": {"Ref": PARAMETER_NAME},
    "INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS": "true",
    "INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE": "false",
    "SPORTSDATAIO_TIMEOUT_SECONDS": "25",
}


class CloudFormationLoader(yaml.SafeLoader):
    pass


def _construct_intrinsic(
    loader: CloudFormationLoader, tag_suffix: str, node: yaml.Node
) -> Any:
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {tag_suffix: value}


CloudFormationLoader.add_multi_constructor("!", _construct_intrinsic)


def _load_template(path: Path) -> dict[str, Any]:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=CloudFormationLoader)
    if not isinstance(data, dict):
        raise ValueError("SAM template must be a mapping")
    return data


def verify_repository(root: Path = ROOT) -> List[str]:
    errors: List[str] = []
    template_path = root / "template.yaml"
    deploy_path = root / ".github/workflows/deploy.yml"
    if not template_path.is_file():
        return ["sam_template_missing"]

    try:
        template = _load_template(template_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [f"sam_template_unreadable:{exc}"]

    parameters = template.get("Parameters", {})
    parameter = parameters.get(PARAMETER_NAME) if isinstance(parameters, dict) else None
    if not isinstance(parameter, dict):
        errors.append("sportsdataio_parameter_missing")
    else:
        if parameter.get("Type") != "String":
            errors.append("sportsdataio_parameter_must_be_string")
        if parameter.get("NoEcho") is not True:
            errors.append("sportsdataio_parameter_must_be_noecho")
        if parameter.get("Default") != "":
            errors.append("sportsdataio_parameter_must_default_empty")
    if isinstance(parameters, dict) and "SportsDataIOApiKey" in parameters:
        errors.append("legacy_sportsdataio_parameter_name_present")

    globals_variables = (
        template.get("Globals", {})
        .get("Function", {})
        .get("Environment", {})
        .get("Variables", {})
    )
    if isinstance(globals_variables, dict):
        for key in EXPECTED_ENVIRONMENT:
            if key in globals_variables:
                errors.append(f"sportsdataio_environment_must_not_be_global:{key}")

    resources = template.get("Resources", {})
    if not isinstance(resources, dict):
        errors.append("sam_resources_missing")
        resources = {}
    functions = {
        name: resource
        for name, resource in resources.items()
        if isinstance(resource, dict)
        and resource.get("Type") == "AWS::Serverless::Function"
    }
    for name, resource in functions.items():
        variables = (
            resource.get("Properties", {})
            .get("Environment", {})
            .get("Variables", {})
        )
        variables = variables if isinstance(variables, dict) else {}
        if name in APPROVED_FUNCTIONS:
            for key, expected in EXPECTED_ENVIRONMENT.items():
                if variables.get(key) != expected:
                    errors.append(f"sportsdataio_environment_incorrect:{name}:{key}")
        else:
            for key in EXPECTED_ENVIRONMENT:
                if key in variables:
                    errors.append(f"sportsdataio_environment_leaked:{name}:{key}")
    for name in APPROVED_FUNCTIONS:
        if name not in functions:
            errors.append(f"approved_sportsdataio_function_missing:{name}")
    for name in PROTECTED_FUNCTIONS:
        if name not in functions:
            errors.append(f"protected_function_missing:{name}")

    deploy = deploy_path.read_text(encoding="utf-8") if deploy_path.is_file() else ""
    if not deploy:
        errors.append("canonical_deploy_workflow_missing")
    else:
        secret_binding = "SPORTSDATAIO_API_KEY_VALUE: ${{ secrets.SPORTSDATAIO_API_KEY }}"
        parameter_override = 'SportsDataIoApiKey="${SPORTSDATAIO_API_KEY_VALUE}"'
        if deploy.count(secret_binding) != 1:
            errors.append("deploy_must_bind_optional_sportsdataio_secret_exactly_once")
        if deploy.count(parameter_override) != 1:
            errors.append("deploy_must_pass_sportsdataio_parameter_exactly_once")
        forbidden_fragments = (
            'test -n "${SPORTSDATAIO_API_KEY_VALUE',
            "Missing SPORTSDATAIO_API_KEY",
            'echo "${SPORTSDATAIO_API_KEY_VALUE',
            'echo "$SPORTSDATAIO_API_KEY_VALUE',
        )
        for fragment in forbidden_fragments:
            if fragment in deploy:
                errors.append("deploy_must_not_require_or_print_optional_sportsdataio_secret")

    return sorted(set(errors))


def main() -> int:
    errors = verify_repository()
    if errors:
        for error in errors:
            print(error)
        return 1
    print("SportsDataIO SAM wiring and deployment secret handling verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
