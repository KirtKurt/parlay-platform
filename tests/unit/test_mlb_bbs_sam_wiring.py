from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts import verify_mlb_bbs_sam_wiring as wiring


def test_checked_in_bbs_wiring_is_shadow_only_and_least_privilege() -> None:
    proof = wiring.verify()

    assert proof["ok"] is True
    assert proof["secretName"] == "BBS_API_KEY"
    assert proof["credentialConsumer"] == "MLBAuditedPullFunction"
    assert proof["shadowOnly"] is True


def test_rejects_bbs_secret_on_public_read_lambda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = wiring.TEMPLATE.read_text(encoding="utf-8")
    public_marker = "  MLBV3ReadFunction:\n"
    poisoned = template.replace(
        public_marker,
        public_marker
        + "    Metadata:\n"
        + "      BBS_API_SECRET_ARN: forbidden\n",
        1,
    )
    candidate = tmp_path / "template.yaml"
    candidate.write_text(poisoned, encoding="utf-8")
    monkeypatch.setattr(wiring, "TEMPLATE", candidate)

    with pytest.raises(wiring.ContractError, match="escaped|leaked"):
        wiring.verify()


def test_rejects_retired_activation_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "template.yaml").write_text(wiring.TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copy2(wiring.DEPLOY_WORKFLOW, root / ".github" / "workflows" / "deploy.yml")
    retired = root / wiring.RETIRED_ACTIVATION_PATHS[0]
    retired.write_text("name: forbidden\n", encoding="utf-8")
    monkeypatch.setattr(wiring, "ROOT", root)
    monkeypatch.setattr(wiring, "TEMPLATE", root / "template.yaml")
    monkeypatch.setattr(wiring, "DEPLOY_WORKFLOW", root / ".github" / "workflows" / "deploy.yml")

    with pytest.raises(wiring.ContractError, match="retired provider activation"):
        wiring.verify()
