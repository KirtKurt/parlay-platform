from __future__ import annotations

from decimal import Decimal

import pytest

import mlb_v8_observational_audit_v1_3 as audit


def test_observational_pointer_converts_nested_floats_to_decimal():
    value = audit.to_ddb(
        {
            "accuracy": 0.625,
            "bands": [{"lower": 0.55, "upper": 0.60}],
            "flags": {"complete": False},
            "count": 8,
        }
    )

    assert value["accuracy"] == Decimal("0.625")
    assert value["bands"][0]["lower"] == Decimal("0.55")
    assert value["bands"][0]["upper"] == Decimal("0.6")
    assert value["flags"]["complete"] is False
    assert value["count"] == 8


def test_observational_pointer_rejects_non_finite_float():
    with pytest.raises(ValueError, match="non-finite float"):
        audit.to_ddb({"accuracy": float("nan")})
