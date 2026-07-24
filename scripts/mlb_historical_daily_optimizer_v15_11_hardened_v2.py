#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

BASE_PATH = Path(__file__).with_name("mlb_historical_daily_optimizer_v15_11_hardened.py")
SPEC = importlib.util.spec_from_file_location("mlb_historical_hardened_v1", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load hardened MLB historical optimizer")
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)

from mlb_historical_evidence_v2 import (  # noqa: E402
    assert_evidence_chain,
    build_evidence_chain,
    evidence_blockers,
)

BASE.assert_evidence_chain = assert_evidence_chain
BASE.build_evidence_chain = build_evidence_chain
BASE.evidence_blockers = evidence_blockers
BASE.VERSION = "MLB-HISTORICAL-HARDENED-CLI-v15.11.1.1"

parser = BASE.parser
main = BASE.main

if __name__ == "__main__":
    raise SystemExit(main())
