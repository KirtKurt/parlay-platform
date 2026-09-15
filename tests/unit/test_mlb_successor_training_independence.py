from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRAINER = ROOT / "hello_world" / "mlb_ml_aws_training_v1.py"


def test_successor_development_refreshes_before_r8_continuity_wait_returns() -> None:
    """The separate successor must not go stale behind an R8 continuity wait.

    Fresh AWS evidence shows successor capture is healthy while successor
    training has remained stale since Sep 13.  The trainer currently invokes
    ``successor.develop`` only after R8 continuity early-return gates.  That
    couples the independent successor heartbeat to an unrelated R8 wait state.

    Keep R8's fail-closed return exactly as-is, but refresh the successor's own
    development/status from the already source-admitted rows before returning.
    This must not freeze, qualify, promote, activate, or publish a successor.
    """
    source = TRAINER.read_text(encoding="utf-8")
    successor_call = source.index("successor.develop(")
    continuity_wait = source.index("if continuity_missing_dates:")
    continuity_health_wait = source.index(
        'if isinstance(slate_continuity, dict) and slate_continuity.get("ok") is not True:'
    )
    assert successor_call < continuity_wait
    assert successor_call < continuity_health_wait


def test_successor_development_remains_before_r8_challenger_fit() -> None:
    source = TRAINER.read_text(encoding="utf-8")
    assert source.index("successor.develop(") < source.index(
        "challenger: Optional[Dict[str, Any]] = None"
    )
