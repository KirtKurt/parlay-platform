"""Bounded Bedrock analyst: proposes trials but has no publication authority."""
from __future__ import annotations

import json
from botocore.config import Config
from typing import Any, Mapping, Sequence

try:
    import boto3  # type: ignore
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore

from .canonical import digest, now_utc
from .features import FEATURE_NAMES

BASELINE_TRIALS: tuple[dict[str, Any], ...] = (
    {"learning_rate": 0.010, "l2": 0.0005, "epochs": 60},
    {"learning_rate": 0.025, "l2": 0.0010, "epochs": 80},
    {"learning_rate": 0.040, "l2": 0.0030, "epochs": 110},
    {"learning_rate": 0.020, "l2": 0.0100, "epochs": 130},
)


def _validate_trial(value: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        learning_rate = float(value["learning_rate"])
        l2 = float(value["l2"])
        epochs = int(value["epochs"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0.002 <= learning_rate <= 0.08:
        return None
    if not 0.00005 <= l2 <= 0.05:
        return None
    if not 30 <= epochs <= 180:
        return None
    return {"learning_rate": learning_rate, "l2": l2, "epochs": epochs}


def validated_trials(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        values = payload.get("trials")
    else:
        values = payload
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values[:8]:
        if not isinstance(raw, Mapping):
            continue
        trial = _validate_trial(raw)
        if trial is None:
            continue
        key = digest(trial)
        if key not in seen:
            seen.add(key)
            output.append(trial)
    return output


def _extract_json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        stripped = stripped.rsplit("```", 1)[0]
    first = min([index for index in (stripped.find("{"), stripped.find("[")) if index >= 0] or [0])
    last_object = stripped.rfind("}")
    last_array = stripped.rfind("]")
    last = max(last_object, last_array)
    if last < first:
        raise ValueError("NFL_LLM_JSON_NOT_FOUND")
    return json.loads(stripped[first : last + 1])


def propose_trials(
    *,
    target: str,
    corpus_summary: Mapping[str, Any],
    model_id: str,
    region_name: str,
    bedrock_client: Any = None,
) -> dict[str, Any]:
    """Return allowlisted trial parameters plus transparent analyst telemetry.

    The analyst receives only aggregate training/validation information. It does
    not receive 2025 audit labels or any live outcome, cannot write model state,
    and cannot relax promotion gates.
    """
    if boto3 is None and bedrock_client is None:
        return {
            "ok": False,
            "status": "BEDROCK_CLIENT_UNAVAILABLE",
            "trials": list(BASELINE_TRIALS),
            "generated_at": now_utc(),
        }
    client = bedrock_client or boto3.client("bedrock-runtime", region_name=region_name, config=Config(connect_timeout=5, read_timeout=45, retries={"mode": "adaptive", "total_max_attempts": 4}))
    system = (
        "You are a bounded NFL model-search analyst. Return JSON only. "
        "Propose up to four residual-logistic hyperparameter trials. "
        "Allowed fields: learning_rate, l2, epochs. Do not propose features, "
        "data deletion, audit access, publication, thresholds, or provider changes."
    )
    prompt = {
        "target": target,
        "feature_schema": list(FEATURE_NAMES),
        "corpus_summary": dict(corpus_summary),
        "allowed_ranges": {
            "learning_rate": [0.002, 0.08],
            "l2": [0.00005, 0.05],
            "epochs": [30, 180],
        },
        "required_output": {
            "trials": [
                {"learning_rate": 0.02, "l2": 0.001, "epochs": 80}
            ],
            "rationale": "brief",
        },
    }
    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": json.dumps(prompt, sort_keys=True)}]}],
            inferenceConfig={"maxTokens": 900, "temperature": 0.1, "topP": 0.8},
        )
        content = (((response.get("output") or {}).get("message") or {}).get("content") or [])
        text = "\n".join(str(row.get("text") or "") for row in content if isinstance(row, Mapping))
        parsed = _extract_json(text)
        llm_trials = validated_trials(parsed)
        combined = list(BASELINE_TRIALS)
        existing = {digest(row) for row in combined}
        for trial in llm_trials:
            if digest(trial) not in existing:
                combined.append(trial)
                existing.add(digest(trial))
        return {
            "ok": bool(llm_trials),
            "status": "VALIDATED" if llm_trials else "NO_VALID_TRIALS",
            "trials": combined,
            "llm_trials": llm_trials,
            "response_digest": digest(parsed),
            "generated_at": now_utc(),
            "usage": response.get("usage") or {},
        }
    except Exception as exc:
        response = getattr(exc, "response", {})
        code = ((response.get("Error") or {}).get("Code")) if isinstance(response, Mapping) else None
        return {
            "ok": False,
            "status": "BEDROCK_DEFERRED",
            "error_code": str(code or type(exc).__name__)[:80],
            "trials": list(BASELINE_TRIALS),
            "generated_at": now_utc(),
        }
