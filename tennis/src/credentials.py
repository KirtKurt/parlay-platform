from __future__ import annotations

from typing import Any


def resolve_secret(secret_arn: str, *, secrets_client: Any = None) -> str:
    """Read one stack-scoped credential without exposing SDK details."""

    if not str(secret_arn or "").strip():
        raise RuntimeError("tennis_provider_credential_not_configured")
    if secrets_client is None:
        import boto3

        secrets_client = boto3.client("secretsmanager")
    try:
        response = secrets_client.get_secret_value(SecretId=str(secret_arn).strip())
    except Exception:
        raise RuntimeError("tennis_provider_credential_retrieval_failed") from None
    value = response.get("SecretString")
    if not isinstance(value, str) or not value.strip() or value == "NOT_CONFIGURED":
        raise RuntimeError("tennis_provider_credential_not_configured")
    return value.strip()
