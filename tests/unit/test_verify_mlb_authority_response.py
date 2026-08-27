from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_mlb_authority_response import (
    AUTHORITY_CONTRACT,
    verify_payload,
    verify_public_prediction_payload,
)


def no_champion_payload():
    return {
        "ok": False,
        "status": "NO_QUALIFIED_CHAMPION",
        "error": "NO_QUALIFIED_CHAMPION",
        "publicationClosed": True,
        "productionSelectionAllowed": False,
        "model_version": None,
        "primaryAlgorithm": None,
        "primaryAlgorithmActive": False,
        "soleProductionAlgorithm": None,
        "game_winner_model": None,
        "requestedAuthority": "AWS_ML_PROSPECTIVE_R7",
        "qualifiedChampionRequired": True,
        "qualifiedChampionPresent": False,
        "r7ChampionQualified": False,
        "r7DeploymentIdentity": None,
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "legacyRecommendationAuthority": False,
        "retiredAuthoritySuppressed": True,
        "retiredV15_10Eligible": False,
        "automaticWagerAllowed": False,
        "rowLevelAutomaticWagerAllowed": False,
        "authorityContractVersion": AUTHORITY_CONTRACT,
    }


def qualified_payload():
    return {
        "ok": True,
        "status": "QUALIFIED_CHAMPION",
        "publicationClosed": False,
        "productionSelectionAllowed": True,
        "model_version": "mlb-r7-2026-09-15-001",
        "primaryAlgorithm": "AWS-ML-PROSPECTIVE-R7",
        "primaryAlgorithmActive": True,
        "soleProductionAlgorithm": "AWS-ML-PROSPECTIVE-R7",
        "game_winner_model": "mlb-r7-2026-09-15-001",
        "requestedAuthority": "AWS_ML_PROSPECTIVE_R7",
        "qualifiedChampionRequired": True,
        "qualifiedChampionPresent": True,
        "r7ChampionQualified": True,
        "r7DeploymentIdentity": {
            "modelId": "mlb-r7-2026-09-15-001",
            "artifactDigest": "a" * 64,
        },
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "legacyRecommendationAuthority": False,
        "retiredAuthoritySuppressed": True,
        "retiredV15_10Eligible": False,
        "authorityContractVersion": AUTHORITY_CONTRACT,
    }


def test_explicit_no_champion_503_is_healthy_fail_closed_runtime():
    report = verify_payload(503, no_champion_payload())
    assert report["ok"] is True, report
    assert report["state"] == "NO_QUALIFIED_CHAMPION"


def test_legitimate_qualified_r7_200_is_healthy_runtime():
    report = verify_payload(200, qualified_payload())
    assert report["ok"] is True, report
    assert report["state"] == "QUALIFIED_R7_CHAMPION"


def test_no_champion_body_on_200_is_rejected():
    report = verify_payload(200, no_champion_payload())
    assert report["ok"] is False
    assert "http_and_body_do_not_match_an_allowed_authority_state" in report["errors"]


def test_retired_v15_10_marker_is_rejected_in_any_allowed_state():
    payload = no_champion_payload()
    payload["productionAuthoritySource"] = "mlb_ranked_winner_v15_10_active_ensemble"
    report = verify_payload(503, payload)
    assert report["ok"] is False
    assert "retired_v15_10_authority_marker_present" in report["errors"]


def test_qualified_state_requires_deployment_identity_and_r7_authority():
    payload = qualified_payload()
    payload["r7DeploymentIdentity"] = None
    payload["requestedAuthority"] = "LEGACY"
    report = verify_payload(200, payload)
    assert report["ok"] is False
    assert "r7DeploymentIdentity_missing_for_qualified_r7" in report["errors"]
    assert "qualified_authority_is_not_aws_ml_prospective_r7" in report["errors"]


def test_retired_suppression_fields_are_mandatory():
    payload = no_champion_payload()
    payload["retiredAuthoritySuppressed"] = False
    payload["retiredV15_10Eligible"] = True
    report = verify_payload(503, payload)
    assert report["ok"] is False
    assert "retiredAuthoritySuppressed_must_be_true" in report["errors"]
    assert "retiredV15_10Eligible_must_be_false" in report["errors"]


def test_predictions_route_accepts_only_atomic_zero_winner_no_champion_503():
    payload = no_champion_payload()
    payload.update({
        "sport": "mlb",
        "winner_predictions": [],
        "predictions": [],
        "count": 0,
    })

    report = verify_public_prediction_payload(503, payload)

    assert report["ok"] is True, report
    assert report["state"] == "NO_QUALIFIED_CHAMPION"
    assert report["publicationClosed"] is True
    assert report["retiredAuthoritySuppressed"] is True
    assert report["publicWinnerCount"] == 0


def test_predictions_route_rejects_arbitrary_503_or_published_fallback_winner():
    arbitrary = verify_public_prediction_payload(
        503,
        {
            "ok": False,
            "status": "Service Unavailable",
            "predictions": [],
            "winner_predictions": [],
            "count": 0,
        },
    )
    assert arbitrary["ok"] is False
    assert arbitrary["state"] == "INVALID"

    payload = no_champion_payload()
    payload.update({
        "sport": "mlb",
        "winner_predictions": [{"predictedWinner": "Retired fallback"}],
        "predictions": [{"predictedWinner": "Retired fallback"}],
        "count": 1,
    })
    leaked = verify_public_prediction_payload(503, payload)
    assert leaked["ok"] is False
    assert "winner_predictions_must_be_explicitly_empty" in leaked["errors"]
    assert "predictions_must_be_explicitly_empty" in leaked["errors"]
    assert "public_prediction_count_must_be_zero" in leaked["errors"]


def test_predictions_route_rejects_no_champion_body_returned_with_http_200():
    payload = no_champion_payload()
    payload.update({
        "sport": "mlb",
        "winner_predictions": [],
        "predictions": [],
        "count": 0,
    })

    report = verify_public_prediction_payload(200, payload)

    assert report["ok"] is False
    assert "http_and_body_do_not_match_an_allowed_authority_state" in report["errors"]
