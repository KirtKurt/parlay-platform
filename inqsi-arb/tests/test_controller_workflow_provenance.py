from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "inqsi-arb-controller.yml"


def test_production_controller_executes_trigger_revision_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")
    checkout = text.split("- uses: actions/checkout@v4", 1)[1].split(
        "- uses: actions/setup-python@v5", 1
    )[0]

    assert "ref: ${{ github.sha }}" in checkout
    assert "ref: main" not in checkout
    assert 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' in checkout
    assert text.index("Verify immutable controller revision") < text.index(
        "aws-actions/configure-aws-credentials@v4"
    )
