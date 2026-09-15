from pathlib import Path
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "template.yaml").read_text(encoding="utf-8")
RUNTIME = (ROOT / "mlb_research" / "mlb_research_runtime_v1.py").read_text(encoding="utf-8")


def _research_function_block() -> str:
    match = re.search(
        r"(?ms)^  MLBResearchFunction:\n(?P<body>.*?)(?=^  [A-Za-z0-9]+:\n|\Z)",
        TEMPLATE,
    )
    assert match, "MLBResearchFunction is missing from template.yaml"
    return match.group("body")


def test_research_lambda_packages_every_runtime_import_dependency() -> None:
    """The deployed research Lambda must not import code outside its CodeUri."""
    block = _research_function_block()
    assert "CodeUri: mlb_research/" in block
    if "from ks1.features import Features, starter_matchup" not in RUNTIME:
        return

    vendored = ROOT / "mlb_research" / "ks1" / "features.py"
    vendored_events = ROOT / "mlb_research" / "ks1" / "statcast_events.py"
    assert vendored.exists(), "mlb_research Lambda is missing vendored ks1.features"
    assert vendored_events.exists(), "vendored ks1.features dependency statcast_events is missing"
    assert not (vendored.parent / "__init__.py").exists(), (
        "vendored KS1 must stay a namespace package so repository tests keep using authoritative root ks1"
    )
    assert vendored.read_bytes() == (ROOT / "ks1" / "features.py").read_bytes(), (
        "vendored ks1.features must remain byte-identical to the authoritative KS1 implementation"
    )
    assert vendored_events.read_bytes() == (ROOT / "ks1" / "statcast_events.py").read_bytes(), (
        "vendored ks1.statcast_events must remain byte-identical to the authoritative KS1 implementation"
    )


def test_research_lambda_imports_from_codeuri_only() -> None:
    """Reproduce Lambda sys.path: only mlb_research/ is importable as application code."""
    code_uri = ROOT / "mlb_research"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(code_uri)
    subprocess.run(
        [sys.executable, "-I", "-c", (
            "import sys; "
            f"sys.path.insert(0, {str(code_uri)!r}); "
            "import mlb_research_runtime_v1; "
            "from ks1.features import Features, starter_matchup; "
            "assert Features is not None and callable(starter_matchup)"
        )],
        cwd=code_uri,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_research_lambda_dependency_repair_does_not_widen_codeuri() -> None:
    """Do not repair this by packaging the repository root/shared sports."""
    block = _research_function_block()
    assert "CodeUri: mlb_research/" in block
