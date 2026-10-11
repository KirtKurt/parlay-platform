from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Mapping, Optional


VERSION = "MLB-ML-DEPLOYMENT-IDENTITY-v1-single-training-selection-inference-authority"


class DeploymentIdentityError(ValueError):
    pass


def _hex(value: Any, length: int) -> bool:
    text = str(value or "").strip().lower()
    if len(text) != length:
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def current_identity() -> Dict[str, Any]:
    identity = {
        "version": VERSION,
        "gitSha": str(os.environ.get("INQSI_DEPLOY_GIT_SHA") or "").strip(),
        "templateSha256": str(
            os.environ.get("INQSI_DEPLOY_TEMPLATE_SHA256") or ""
        ).strip(),
        "deployRunId": str(os.environ.get("INQSI_DEPLOY_RUN_ID") or "").strip(),
    }
    identity["valid"] = bool(
        _hex(identity["gitSha"], 40)
        and _hex(identity["templateSha256"], 64)
    )
    identity["fingerprint"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return identity


def normalized(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "version": VERSION,
            "gitSha": "",
            "templateSha256": "",
            "deployRunId": "",
            "valid": False,
        }
    nested = value.get("deploymentIdentity")
    source = nested if isinstance(nested, Mapping) else value
    result = {
        "version": str(source.get("version") or VERSION),
        "gitSha": str(
            source.get("gitSha")
            or source.get("git_sha")
            or source.get("deploymentGitSha")
            or ""
        ).strip(),
        "templateSha256": str(
            source.get("templateSha256")
            or source.get("template_sha256")
            or source.get("deploymentTemplateSha256")
            or ""
        ).strip(),
        "deployRunId": str(
            source.get("deployRunId") or source.get("deploy_run_id") or ""
        ).strip(),
    }
    result["valid"] = bool(
        _hex(result["gitSha"], 40) and _hex(result["templateSha256"], 64)
    )
    return result


def matches_current(value: Any) -> bool:
    current = current_identity()
    candidate = normalized(value)
    return bool(
        current.get("valid")
        and candidate.get("valid")
        and current.get("gitSha") == candidate.get("gitSha")
        and current.get("templateSha256") == candidate.get("templateSha256")
    )


def component_proof(
    *,
    training: Optional[Mapping[str, Any]],
    selection_capture: Optional[Mapping[str, Any]],
    live_inference: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    current = current_identity()
    components = {
        "training": normalized(training),
        "selectionCapture": normalized(selection_capture),
        "liveInference": normalized(live_inference),
    }
    matches = {
        name: bool(
            current.get("valid")
            and value.get("valid")
            and value.get("gitSha") == current.get("gitSha")
            and value.get("templateSha256") == current.get("templateSha256")
        )
        for name, value in components.items()
    }
    errors = [
        f"{name}_deployment_identity_mismatch"
        for name, matched in matches.items()
        if not matched
    ]
    return {
        "ok": not errors,
        "version": VERSION,
        "current": current,
        "components": components,
        "componentMatchesCurrent": matches,
        "errors": errors,
        "singleDeploymentIdentityRequired": True,
        "staleStatusMayNotBlockFreshExecution": True,
    }


def require_current(value: Any, *, component: str) -> Dict[str, Any]:
    candidate = normalized(value)
    if not matches_current(candidate):
        raise DeploymentIdentityError(
            f"{component}_deployment_identity_mismatch"
        )
    return candidate
