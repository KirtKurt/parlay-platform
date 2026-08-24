from pathlib import Path


def test_template_and_package_are_nfl_isolated() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "nfl-auto-template.yaml").read_text().lower()
    assert "nfl_auto" in template
    assert "parlay-platform-nfl-auto" not in template  # stack name belongs in workflow, not resource names
    for forbidden in ("soccer_auto", "mlb_auto", "inqis_tennis", "parlay_platform_"):
        assert forbidden not in template
    package_files = list((root / "nfl_auto").glob("*.py"))
    assert package_files
    for path in package_files:
        text = path.read_text().lower()
        assert "soccer_auto" not in text
        assert "mlb_auto" not in text
