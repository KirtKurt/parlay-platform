from __future__ import annotations

import shutil
from pathlib import Path

from scripts import verify_mlb_sportsdataio_sam_wiring as wiring


ROOT = Path(__file__).resolve().parents[2]


def _copy_contract(tmp_path: Path) -> Path:
    for relative in ("template.yaml", ".github/workflows/deploy.yml"):
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return tmp_path


def test_repository_uses_resource_scoped_sportsdataio_wiring() -> None:
    assert wiring.verify_repository(ROOT) == []


def test_rejects_sportsdataio_secret_leak_to_trainer(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    template = root / "template.yaml"
    text = template.read_text(encoding="utf-8")
    anchor = "          MLB_ML_ARTIFACTS_BUCKET: !Ref MLBMLArtifactsBucket\n"
    template.write_text(
        text.replace(
            anchor,
            anchor + "          SPORTSDATAIO_API_KEY: !Ref SportsDataIoApiKey\n",
            1,
        ),
        encoding="utf-8",
    )

    assert (
        "sportsdataio_environment_leaked:MLBMLTrainingFunction:SPORTSDATAIO_API_KEY"
        in wiring.verify_repository(root)
    )


def test_rejects_sportsdataio_secret_leak_to_read_api(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    template = root / "template.yaml"
    text = template.read_text(encoding="utf-8")
    anchor = "  MLBV3ReadFunction:\n    Type: AWS::Serverless::Function\n    Properties:\n"
    replacement = anchor + (
        "      Environment:\n"
        "        Variables:\n"
        "          SPORTSDATAIO_API_KEY: !Ref SportsDataIoApiKey\n"
    )
    template.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")

    assert (
        "sportsdataio_environment_leaked:MLBV3ReadFunction:SPORTSDATAIO_API_KEY"
        in wiring.verify_repository(root)
    )


def test_rejects_required_or_missing_deploy_secret_override(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    text = text.replace(
        '              SportsDataIoApiKey="${SPORTSDATAIO_API_KEY_VALUE}" \\\n',
        "",
        1,
    )
    text += '\n# test -n "${SPORTSDATAIO_API_KEY_VALUE:-}"\n'
    deploy.write_text(text, encoding="utf-8")

    errors = wiring.verify_repository(root)
    assert "deploy_must_pass_sportsdataio_parameter_exactly_once" in errors
    assert "deploy_must_not_require_or_print_optional_sportsdataio_secret" in errors
