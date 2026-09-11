from __future__ import annotations

import re
from typing import Any

_MAX_BOUNDED_ATTEMPTS = 6
_EXHAUSTION = re.compile(
    r"RuntimeError: no acceptable planner task after ([1-6]) bounded attempts \(requested=([0-9]+)\)"
)

_FORBIDDEN_AUTHORITY_FIELDS = (
    "taskPublished",
    "productionAuthorityChanged",
    "directProductionDeployAllowed",
    "mainBranchWriteAllowed",
    "modelPromotionAllowed",
    "secretMutationAllowed",
    "otherSportChangeAllowed",
)


def _validated_rejected_attempts(attempts: list[dict[str, Any]], expected: int) -> None:
    if len(attempts) != expected or not 1 <= len(attempts) <= _MAX_BOUNDED_ATTEMPTS:
        raise ValueError("bounded exhaustion attempt ledger length mismatch")
    for index, attempt in enumerate(attempts, 1):
        if attempt.get("attempt") != index:
            raise ValueError("bounded exhaustion attempt ledger is not sequential")
        if attempt.get("attemptBudget") != expected:
            raise ValueError("bounded exhaustion attempt budget mismatch")
        if attempt.get("accepted") is True or not str(attempt.get("rejected") or "").strip():
            raise ValueError("bounded exhaustion ledger contains a non-rejected attempt")


def build_no_action_receipt(
    *,
    runtime_log: str,
    attempts: list[dict[str, Any]],
    task_published: bool,
    response_published: bool,
) -> dict[str, Any]:
    """Convert only proven bounded planner exhaustion into a read-only no-op outcome.

    This never changes ``run()`` semantics: the planner runtime still fails closed and
    writes only its rejection ledger. The scheduled wrapper may use this receipt to
    distinguish a healthy, bounded "nothing eligible to publish" cycle from an
    infrastructure/runtime failure. Any other exception remains a hard failure.
    """
    match = _EXHAUSTION.search(runtime_log)
    if match is None:
        raise ValueError("planner did not fail by bounded task exhaustion")
    expected = int(match.group(1))
    requested = int(match.group(2))
    _validated_rejected_attempts(attempts, expected)
    if task_published or response_published:
        raise ValueError("bounded exhaustion must not publish a task or accepted response")
    return {
        "ok": True,
        "status": "NO_ACTION",
        "reason": "BOUNDED_PLANNER_EXHAUSTED",
        "readOnly": True,
        "taskPublished": False,
        "productionAuthorityChanged": False,
        "directProductionDeployAllowed": False,
        "mainBranchWriteAllowed": False,
        "modelPromotionAllowed": False,
        "secretMutationAllowed": False,
        "otherSportChangeAllowed": False,
        "attemptCount": len(attempts),
        "attemptBudget": expected,
        "requestedAttemptBudget": requested,
        "waitFor": "NEW_OPERATIONAL_EVIDENCE_OR_DISTINCT_VALID_TASK",
        "rejectedTitles": [str(attempt.get("title") or "<unparsed>") for attempt in attempts],
    }


def validate_no_action_receipt(
    receipt: dict[str, Any], attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    if receipt.get("ok") is not True or receipt.get("status") != "NO_ACTION":
        raise ValueError("invalid NO_ACTION outcome")
    if receipt.get("reason") != "BOUNDED_PLANNER_EXHAUSTED" or receipt.get("readOnly") is not True:
        raise ValueError("NO_ACTION receipt does not preserve fail-closed semantics")
    if any(receipt.get(key) is not False for key in _FORBIDDEN_AUTHORITY_FIELDS):
        raise ValueError("NO_ACTION receipt grants forbidden authority")
    expected = receipt.get("attemptCount")
    if not isinstance(expected, int):
        raise ValueError("NO_ACTION attempt count missing")
    _validated_rejected_attempts(attempts, expected)
    if receipt.get("attemptBudget") != expected:
        raise ValueError("NO_ACTION receipt attempt budget mismatch")
    if receipt.get("waitFor") != "NEW_OPERATIONAL_EVIDENCE_OR_DISTINCT_VALID_TASK":
        raise ValueError("NO_ACTION wait condition is invalid")
    if receipt.get("rejectedTitles") != [
        str(attempt.get("title") or "<unparsed>") for attempt in attempts
    ]:
        raise ValueError("NO_ACTION rejected-title receipt mismatch")
    return receipt
