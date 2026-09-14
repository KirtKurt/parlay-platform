import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml


@pytest.mark.parametrize("key", ["", "test token with spaces"])
def test_sam_receives_optional_bbd_as_one_nonempty_argument(key):
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/deploy-soccer-auto.yml").read_text())
    step = next(s for s in workflow["jobs"]["verify-and-deploy"]["steps"] if s.get("name") == "Deploy isolated soccer_auto stack")
    script = re.sub(r"\$\{\{.*?\}\}", "test-value", step["run"])
    result = subprocess.run(["bash", "-c", "sam() { printf '%s\\n' \"$@\"; };\n" + script], env={**os.environ, "SOCCER_BBD_KEY": key}, capture_output=True, text=True, check=True)
    arguments = result.stdout.splitlines()
    assert "deploy" == arguments[0]
    assert "BbdApiKey=" not in arguments
    assert [a for a in arguments if a.startswith("BbdApiKey=")] == (["BbdApiKey=" + key] if key else [])
