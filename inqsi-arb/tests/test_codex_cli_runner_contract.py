from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "inqsi-arb" / "ops" / "codex_cli_runner.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "inqsi-arb-engineering-controller.yml"


def test_runner_is_arb_scoped_and_draft_only():
    text = RUNNER.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "inqsi-arb/*|.github/workflows/inqsi-arb-*" in text
    assert "gh pr create" in text
    assert "--draft" in text
    assert "gh pr merge" not in text
    assert "sam deploy" not in text
    assert "do not place wagers" in lowered
    assert "do not merge or deploy" in lowered
    assert "python -m pytest -q inqsi-arb/tests" in text


def test_controller_uses_existing_secret_and_codex_cli_runner():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.Inqsi_ARB_Autonomous_Coding_Agent" in text
    assert "npm install --global @openai/codex" in text
    assert "bash inqsi-arb/ops/codex_cli_runner.sh" in text
    assert "fetch-depth: 0" in text
