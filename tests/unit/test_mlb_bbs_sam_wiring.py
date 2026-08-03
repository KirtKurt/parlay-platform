from __future__ import annotations

from pathlib import Path

from scripts import verify_mlb_no_bbd_runtime as no_bbd


ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_mlb_runtime_is_provider_neutral() -> None:
    assert no_bbd.verify_files() == []


def test_canonical_template_has_no_retired_bbs_surface() -> None:
    template = (ROOT / "template.yaml").read_text(encoding="utf-8")

    for token in (
        "BbsApiKey",
        "BbsApiSecret",
        "BBS_API_KEY",
        "BBS_API_SECRET_ARN",
        "BBS_SHADOW_CAPTURE_ENABLED",
        "api.bigballsdata.com",
        "mlb/providers/bbs/",
    ):
        assert token not in template


def test_active_deploy_uses_no_bbd_verifier_not_legacy_wiring() -> None:
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    source_contract = (
        ROOT / ".github/workflows/mlb-production-source-contract.yml"
    ).read_text(encoding="utf-8")

    for workflow in (deploy, source_contract):
        assert "verify_mlb_no_bbd_runtime.py" in workflow
        assert "verify_mlb_bbs_sam_wiring.py" not in workflow
        assert "BbsApiKey" not in workflow
        assert "BBS_API_KEY" not in workflow
