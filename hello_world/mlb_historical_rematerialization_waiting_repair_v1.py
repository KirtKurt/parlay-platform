"""Allow V7 rematerialization to reconcile slates while at the settled-horizon wait.

The settled-horizon phase is a healthy idle state, not a reason to skip a newly
appended completed slate whose feature pointer has not yet been reconciled.
"""
from __future__ import annotations

from typing import Any

VERSION = "MLB-HISTORICAL-REMATERIALIZATION-WAITING-REPAIR-v1"
WAITING_PHASE = "WAITING_FOR_SETTLED_HORIZON"


def install(rematerialization: Any) -> Any:
    if getattr(
        rematerialization,
        "_INQSI_REMATERIALIZATION_WAITING_REPAIR_INSTALLED",
        False,
    ):
        return rematerialization
    phases = set(getattr(rematerialization, "ELIGIBLE_PHASES", set()) or set())
    phases.add(WAITING_PHASE)
    rematerialization.ELIGIBLE_PHASES = phases
    rematerialization.REMATERIALIZATION_WAITING_REPAIR_VERSION = VERSION
    rematerialization._INQSI_REMATERIALIZATION_WAITING_REPAIR_INSTALLED = True
    return rematerialization
