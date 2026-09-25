"""Execute the actual deployment smoke fixture against the local HTTP handler."""
import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "inqsi-arb" / "src"))
from app import lambda_handler


def smoke_source():
    workflow = (ROOT / ".github/workflows/inqsi-arb-deploy.yml").read_text()
    start = workflow.index("          now=time.strftime(")
    end = workflow.index("          report_event_id=", start)
    return textwrap.dedent(workflow[start:end])


def run_smoke(mutate=None):
    def request(path, method="GET", payload=None):
        response = lambda_handler({
            "httpMethod": method, "path": path, "body": json.dumps(payload),
        }, None)
        assert response["statusCode"] == 200, response
        report = json.loads(response["body"])
        if mutate:
            mutate(report)
        return 200, {}, json.dumps(report)

    scope = {"request": request, "json": json, "time": time}
    exec(compile(smoke_source(), "deployment-smoke", "exec"), scope)
    return scope["report"]


def test_deployment_fixture_matches_current_fail_closed_runtime():
    report = run_smoke()
    assert report["n_arbs"] == 0
    assert report["detected_unverified"][0]["arb"] is False


def test_deployment_smoke_rejects_qualified_unreviewed_opportunity():
    with pytest.raises(AssertionError):
        run_smoke(lambda report: report["detected_unverified"][0].update(arb=True))


def test_deployment_smoke_rejects_compatible_unreviewed_rules():
    with pytest.raises(AssertionError):
        run_smoke(lambda report: report["detected_unverified"][0]["validation"].update(
            rules_compatible=True))


def test_deployment_smoke_rejects_wagering():
    with pytest.raises(AssertionError):
        run_smoke(lambda report: report.update(places_bets=True))


def test_deployment_smoke_rejects_wrong_settlement_contract():
    with pytest.raises(AssertionError):
        run_smoke(lambda report: report["detected_unverified"][0]["validation"].update(
            settlement_reason="UNREVIEWED_OR_MISSING_RULE"))
