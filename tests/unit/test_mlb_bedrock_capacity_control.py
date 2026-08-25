from mlb_auto_llm import capacity_control as control


def test_quota_matching_is_limited_to_authorized_model_families():
    assert control.quota_is_relevant(
        "Cross-Region InvokeModel tokens per minute for OpenAI GPT OSS 20B"
    )
    assert control.quota_is_relevant(
        "On-demand model invocation requests per minute for Amazon Nova Lite"
    )
    assert not control.quota_is_relevant(
        "Cross-Region InvokeModel tokens per minute for Anthropic Claude Sonnet"
    )
    assert not control.quota_is_relevant("Knowledge bases per account")


def test_model_family_aliases_cover_gpt_oss_and_nova():
    assert control.matched_model_families(
        "Model invocation max tokens per day for openai.gpt-oss-120b"
    ) == ["openai_gpt_oss_120b"]
    assert "amazon_nova_2_lite" in control.matched_model_families(
        "Cross-region tokens per minute for Amazon Nova 2 Lite"
    )


def test_quota_targets_raise_daily_tpm_and_rpm_capacity():
    assert control.desired_quota_value(
        "Model invocation max tokens per day for GPT OSS 20B", 1_000_000
    ) == 100_000_000
    assert control.desired_quota_value(
        "Cross-Region InvokeModel tokens per minute for GPT OSS 20B", 20_000
    ) == 2_000_000
    assert control.desired_quota_value(
        "On-demand model invocation requests per minute for Nova Lite", 50
    ) == 2_000


def test_dedicated_capacity_is_one_unit_and_no_commitment():
    assert control.PROVISIONED_REGION == "us-east-1"
    assert control.PROVISIONED_MODEL_ID == "amazon.nova-lite-v1:0:24k"
    assert control.PROVISIONED_MODEL_UNITS == 1


def test_control_plane_explicitly_does_not_add_circuit_breaker_behavior():
    assert "no-circuit-breaker" in control.VERSION
