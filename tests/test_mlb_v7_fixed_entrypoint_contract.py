from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "hello_world" / "mlb_historical_optimizer_v7_fixed_entrypoint.py"
LEGACY_SUPERVISED = ROOT / "hello_world" / "mlb_historical_supervised_v9.py"


def test_fixed_entrypoint_installs_supervised_runtime():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert "import mlb_historical_supervised_v9 as supervised" in source
    assert "supervised.install(" in source
    assert "base_entrypoint.optimizer_handler.optimizer" in source
    assert "base_entrypoint.optimizer_handler.policy_runtime" in source


def test_fixed_entrypoint_replaces_permissive_example_builder():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert "supervised._examples = _strict_examples" in source
    assert "MLB_SUPERVISED_HOME_WON_LABEL_MISSING" in source
    assert "MLB_SUPERVISED_HOME_WON_LABEL_INVALID" in source


def test_legacy_missing_label_coercion_is_explicitly_guarded_by_wrapper():
    legacy = LEGACY_SUPERVISED.read_text(encoding="utf-8")
    fixed = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'int(row.get("homeWon") or 0)' in legacy
    assert 'if "homeWon" not in row' in fixed
    assert "isinstance(value, int) and value in (0, 1)" in fixed


def test_fixed_entrypoint_delegates_to_canonical_historical_handler():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert "return base_entrypoint.lambda_handler(event, context)" in source
