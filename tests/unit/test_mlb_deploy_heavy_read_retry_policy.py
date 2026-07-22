from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / ".github/workflows/deploy.yml"
TEST_PATH = "tests/unit/test_mlb_deploy_heavy_read_retry_policy.py"


def _status_script() -> str:
    source = DEPLOY.read_text(encoding="utf-8")
    matches = list(
        re.finditer(
            r"(?ms)^      - name: Smoke test read-only MLB lock status\s*$\n"
            r"(?P<body>.*?)(?=^      - name: |\Z)",
            source,
        )
    )
    assert len(matches) == 1
    body = matches[0].group("body")
    start = body.index("          python - <<'PY'\n") + len("          python - <<'PY'\n")
    end = body.rindex("\n          PY")
    return textwrap.dedent(body[start:end])


def _retry_function():
    tree = ast.parse(_status_script())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "transient_retry_delay"
    ]
    assert len(functions) == 1
    namespace: dict[str, object] = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "<deploy>", "exec"), namespace)
    return namespace["transient_retry_delay"]


def test_heavy_read_probe_retries_structured_500_without_lambda_drain() -> None:
    retry = _retry_function()
    delay, drain = retry(
        RuntimeError("JSON probe attempt limit exhausted after 1 attempts: HTTP 500"),
        1200,
    )
    assert delay == 30
    assert drain is False


def test_heavy_read_probe_drains_ambiguous_or_gateway_timeouts() -> None:
    retry = _retry_function()
    for reason in ("HTTP 504", "TimeoutError", "URLError", "ConnectionError", "IncompleteRead"):
        delay, drain = retry(RuntimeError(f"probe failed: {reason}"), 1200)
        assert delay == 330
        assert drain is True
    assert retry(RuntimeError("probe failed: HTTP 504"), 17) == (17, True)


def test_status_and_predictions_keep_one_delivery_per_outer_attempt() -> None:
    script = _status_script()
    compile(script, "<deploy-heavy-read-smoke>", "exec")
    assert script.count("except TransientHttpProbeExhausted as exc:") == 2
    assert script.count("transient_retry_delay(\n            exc,\n            remaining,") == 2
    assert script.count("max_attempts=1,") == 2
    assert "Lock-status authority is transiently unavailable" in script
    assert "Predictions authority is transiently unavailable" in script


def test_retry_policy_is_mandatory_in_the_deploy_regression_gate() -> None:
    source = DEPLOY.read_text(encoding="utf-8")
    regression = re.search(
        r"(?ms)^          regression_tests=\(\s*$\n(?P<body>.*?)(?=^          \)\s*$)",
        source,
    )
    assert regression is not None
    assert regression.group("body").count(TEST_PATH) == 1
