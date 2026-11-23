"""Deterministic N-way arbitrage engine for Inqsi.

The engine has no network or AWS dependencies. It evaluates normalized quote
sets and explicitly separates mathematical detection from settlement-verified
arbitrage qualification. Mathematical opportunity detection is never enough to
label an opportunity a verified arb: settlement rules must be COMPATIBLE.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from itertools import product
from math import ceil, floor, isfinite
from typing import Any, Dict, Iterable, List, Mapping, Optional

from settlement_matrix import SettlementProofError, prove_quoted_market
from stake_rounding import MAX_ENUMERATED_LEGS, StakeRoundingError, optimize_rounding_neighborhood

MAX_SCAN_PLAN_WORK = 20000


class ArbValidationError(ValueError):
    pass
