from __future__ import annotations

from mlb_auto_llm import capacity_manager as subject


def test_relevant_quota_selects_text_throughput_only() -> None:
    assert subject._relevant_quota(
        {
            "QuotaName": (
                "Cross-region model inference tokens per minute for "
                "Anthropic Claude"
            )
        }
    )
    assert subject._relevant_quota(
        {"QuotaName": "OpenAI GPT OSS tokens per day"}
    )
    assert not subject._relevant_quota(
        {"QuotaName": "Amazon Titan image tokens per minute"}
    )
    assert not subject._relevant_quota(
        {"QuotaName": "Concurrent custom model copy jobs"}
    )


def test_desired_quota_values_request_material_headroom() -> None:
    assert subject._desired_quota_value(
        "OpenAI tokens per day", 100_000.0, 10.0
    ) == 1_100_000.0
    assert subject._desired_quota_value(
        "Claude tokens per minute", 10_000.0, 10.0
    ) == 110_000.0
    assert subject._desired_quota_value(
        "Nova requests per minute", 5.0, 10.0
    ) == 105.0
    assert subject._desired_quota_value(
        "Provisioned throughput model units", 0.0, 10.0
    ) == 1.0


def test_capacity_manager_uses_lambda_role_and_preserves_authority(monkeypatch) -> None:
    quota_inventory = {
        "us-east-1": {
            "quotas": [
                {
                    "QuotaName": "OpenAI tokens per day",
                    "QuotaCode": "L-TEST",
                    "Value": 100_000.0,
                    "Adjustable": True,
                    "GlobalQuota": False,
                }
            ],
            "errors": [],
        }
    }
    model_inventory = {
        "us-east-1": {
            "activeInferenceProfiles": ["global.amazon.nova-lite-v1:0"],
            "activeOnDemandModels": ["openai.gpt-oss-20b-1:0"],
            "errors": [],
        }
    }
    monkeypatch.setattr(
        subject,
        "_quota_inventory",
        lambda region: quota_inventory[region],
    )
    monkeypatch.setattr(
        subject,
        "_model_inventory",
        lambda region: model_inventory[region],
    )
    captured = {}

    def requests(inventories, *, multiplier, maximum):
        captured["inventories"] = inventories
        captured["multiplier"] = multiplier
        captured["maximum"] = maximum
        return [
            {
                "region": "us-east-1",
                "quotaName": "OpenAI tokens per day",
                "quotaCode": "L-TEST",
                "current": 100_000.0,
                "desired": 1_100_000.0,
                "status": "PENDING",
                "requestId": "request-1",
                "existingRequest": False,
            }
        ]

    monkeypatch.setattr(subject, "_request_quota_increases", requests)
    monkeypatch.setattr(
        subject,
        "_direct_runtime_smoke",
        lambda region, routes, maximum: {
            "ok": False,
            "region": region,
            "attempts": [],
            "selectedRoute": None,
        },
    )
    monkeypatch.setattr(
        subject.bedrock_smoke,
        "lambda_handler",
        lambda event, context: {
            "ok": True,
            "routeId": "mantle::us-east-1::fast-model",
            "region": "us-east-1",
            "modelId": "fast-model",
            "endpointFamily": "bedrock-mantle-responses",
            "responseNonEmpty": True,
            "configuredRegions": ["us-east-1"],
            "configuredModelCount": 16,
            "configuredRouteCatalogCount": 32,
            "smokeRouteLimit": 16,
            "mantleModelCount": 20,
            "runtimeModelCount": 12,
            "attemptedModelIds": ["mantle::us-east-1::fast-model"],
        },
    )

    result = subject.lambda_handler(
        {
            "regions": ["us-east-1"],
            "desiredMultiplier": 10,
            "maxQuotaRequests": 30,
            "maxRoutesPerRegion": 32,
        },
        None,
    )

    assert result["ok"] is True
    assert result["liveCapacityOk"] is True
    assert result["capacityManagerRoleUsed"] is True
    assert result["quotaIncreaseSubmittedCount"] == 1
    assert result["quotaIncreaseAcceptedOrPendingCount"] == 1
    assert result["successfulRegions"] == ["us-east-1"]
    assert result["mainLambdaMemoryMb"] == 10240
    assert result["productionRouteAttemptCeiling"] == 80
    assert result["productionAuthorityChanged"] is False
    assert result["automaticWagerAllowed"] is False
    assert result["secretExposed"] is False
    assert captured["multiplier"] == 10.0
    assert captured["maximum"] == 30


def test_no_live_capacity_remains_explicitly_waiting(monkeypatch) -> None:
    monkeypatch.setattr(
        subject,
        "_quota_inventory",
        lambda region: {"quotas": [], "errors": []},
    )
    monkeypatch.setattr(
        subject,
        "_model_inventory",
        lambda region: {
            "activeInferenceProfiles": [],
            "activeOnDemandModels": [],
            "errors": [],
        },
    )
    monkeypatch.setattr(subject, "_request_quota_increases", lambda *a, **k: [])
    monkeypatch.setattr(
        subject,
        "_direct_runtime_smoke",
        lambda region, routes, maximum: {
            "ok": False,
            "region": region,
            "attempts": [],
            "selectedRoute": None,
        },
    )
    monkeypatch.setattr(
        subject.bedrock_smoke,
        "lambda_handler",
        lambda event, context: {
            "ok": False,
            "attemptedModelIds": [],
            "errors": [{"errorCode": "NO_CAPACITY", "message": "none"}],
        },
    )

    result = subject.lambda_handler(
        {"regions": ["us-east-1"], "requestQuotaIncreases": False},
        None,
    )

    assert result["ok"] is False
    assert result["liveCapacityOk"] is False
    assert result["conclusion"] == "CAPACITY_REQUESTED_WAITING"
    assert result["productionAuthorityChanged"] is False


def test_redaction_removes_account_and_authorization_material() -> None:
    value = subject._redact(
        "User arn:aws:iam::123456789012:role/example authorization=secret-token"
    )
    assert "123456789012" not in value
    assert "secret-token" not in value
    assert "[REDACTED_ARN]" in value
