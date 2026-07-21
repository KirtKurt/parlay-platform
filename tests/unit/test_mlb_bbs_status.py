from __future__ import annotations

import json

from hello_world import mlb_advanced_context
from hello_world import mlb_date_signal_api


def test_provider_status_is_shadow_only_and_does_not_claim_field_coverage():
    status = mlb_advanced_context.advanced_context_status()
    provider = status["supplemental_provider_policy"]

    assert provider == {
        "provider": "Big Balls Sports Data",
        "mode": "SHADOW_ONLY",
        "credentialConsumer": "MLBAuditedPullFunction",
        "publicReadCredentialAccess": False,
        "predictionAuthority": False,
        "trainingEligibility": False,
        "completenessCredit": False,
        "captureCoverage": "PARTIAL_SINGLE_UTC_DATE_PROBE",
        "completeSlateCoverageClaimed": False,
        "reviewMilestoneDefined": False,
        "officialIdentityCredit": False,
        "providerIdentityGateSatisfied": False,
    }
    assert status["source_status"]["fip_xfip"] == "NOT_CONNECTED_SOURCE_REQUIRED"
    assert status["source_status"]["confirmed_lineups"] == "NOT_CONNECTED_SOURCE_REQUIRED"
    assert status["source_status"]["bullpen_fatigue"] == "NOT_CONNECTED_SOURCE_REQUIRED"


def test_generic_fundamentals_status_alias_is_read_only(monkeypatch):
    expected = {"ok": True, "supplemental_provider_policy": {"mode": "SHADOW_ONLY"}}
    monkeypatch.setattr(mlb_date_signal_api, "source_status", lambda: expected)

    response = mlb_date_signal_api.lambda_handler(
        {
            "httpMethod": "GET",
            "path": "/v1/mlb/fundamentals/status",
            "queryStringParameters": {},
        },
        None,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == expected
