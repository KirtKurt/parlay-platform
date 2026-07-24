from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "mlb_historical_optimizer" / "template.yaml"
HANDLER = ROOT / "hello_world" / "mlb_historical_optimizer_handler.py"


def test_historical_optimizer_lambda_memory_respects_deployment_ceiling():
    """Prevent a repeat of the production CREATE_FAILED at 4,096 MB."""

    text = TEMPLATE.read_text(encoding="utf-8")
    memory_values = re.findall(r"^\s+MemorySize:\s*(\d+)\s*$", text, re.MULTILINE)

    assert memory_values == ["3008"]
    assert int(memory_values[0]) <= 3008


def test_historical_optimizer_keeps_maximum_execution_window():
    """The lower memory ceiling must not silently shorten the long search window."""

    text = TEMPLATE.read_text(encoding="utf-8")
    timeout_values = re.findall(r"^\s+Timeout:\s*(\d+)\s*$", text, re.MULTILINE)

    assert timeout_values == ["900"]


def test_historical_optimizer_does_not_consume_reserved_concurrency():
    """This account must retain AWS's minimum ten unreserved executions."""

    template = TEMPLATE.read_text(encoding="utf-8")
    handler = HANDLER.read_text(encoding="utf-8")

    assert "ReservedConcurrentExecutions" not in template
    assert "MLB_HISTORICAL_LEASE_SECONDS" in template
    assert "acquire_lease" in handler or "_acquire_lease" in handler
