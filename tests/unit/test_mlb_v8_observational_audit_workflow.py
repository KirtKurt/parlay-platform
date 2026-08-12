from pathlib import Path


def test_observational_runtime_installs_dependencies_before_grading():
    source = Path(
        ".github/workflows/mlb-v8-observational-audit.yml"
    ).read_text()
    runtime = source.split("  observational-audit:\n", 1)[1]

    setup = runtime.index("Setup Python 3.11")
    install = runtime.index("Install observational runtime dependencies")
    grade = runtime.index("Freeze and grade independent observational challenger")

    assert setup < install < grade
    assert "'boto3>=1.34,<2'" in runtime
    assert "'numpy>=1.26,<3'" in runtime
    assert "import boto3" in runtime
    assert "import botocore" in runtime
    assert "import numpy" in runtime
