#!/usr/bin/env python3
"""Apply the signal-policy persistence repair deterministically.

The live failure occurred because startup installed the signal policy, a later
wrapper replaced ``engine.predict_all``, and the stale module-level applied flag
prevented the policy from being restored. The final pre-lock writer then rejected
all rows as ``signal_policy_version_missing``.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"expected exactly one repair anchor in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    runtime = ROOT / "hello_world" / "mlb_ml_runtime_install_v3.py"
    protected = ROOT / "hello_world" / "mlb_manual_pull_protected.py"
    contract = ROOT / ".github" / "workflows" / "mlb-production-source-contract.yml"
    test_path = ROOT / "tests" / "unit" / "test_mlb_signal_policy_prelock_persistence.py"

    replace_once(
        runtime,
        '"MLB-ML-RUNTIME-INSTALL-v4.2-signal-policy-prelock-persistence-"',
        '"MLB-ML-RUNTIME-INSTALL-v4.3-signal-policy-public-wrapper-reassertion-"',
    )
    replace_once(
        runtime,
        "        import mlb_signal_policy_v12\n",
        "        import mlb_signal_policy_v12\n"
        "        import mlb_signal_policy_persistence_bridge_v1\n",
    )
    replace_once(
        runtime,
        "        mlb_slate_coverage_patch.install_public_authority(engine, mlb_slate_prediction_lock)\n"
        "        status[\"steps\"][\"canonicalProbabilityAndPersistedPrelockAuthority\"] = bool(\n",
        "        mlb_slate_coverage_patch.install_public_authority(engine, mlb_slate_prediction_lock)\n"
        "        # Startup customizers may have installed the signal policy and then\n"
        "        # replaced predict_all while leaving its applied flag behind. Reassert\n"
        "        # the current policy on the final public rows before the sole writer.\n"
        "        mlb_signal_policy_persistence_bridge_v1.apply(engine, mlb_signal_policy_v12)\n"
        "        status[\"steps\"][\"signalPolicyV13PersistenceBridge\"] = bool(\n"
        "            getattr(\n"
        "                engine,\n"
        "                \"_INQSI_MLB_SIGNAL_POLICY_PERSISTENCE_BRIDGE_V1_APPLIED\",\n"
        "                False,\n"
        "            )\n"
        "            and getattr(\n"
        "                engine,\n"
        "                \"MLB_SIGNAL_POLICY_PERSISTENCE_BRIDGE_VERSION\",\n"
        "                None,\n"
        "            )\n"
        "            == mlb_signal_policy_persistence_bridge_v1.VERSION\n"
        "        )\n"
        "        status[\"signalPolicyPersistenceBridgeVersion\"] = (\n"
        "            mlb_signal_policy_persistence_bridge_v1.VERSION\n"
        "        )\n"
        "        status[\"steps\"][\"canonicalProbabilityAndPersistedPrelockAuthority\"] = bool(\n",
    )
    replace_once(
        protected,
        '    "canonicalProbabilityAndPersistedPrelockAuthority",\n',
        '    "canonicalProbabilityAndPersistedPrelockAuthority",\n'
        '    "signalPolicyV13PersistenceBridge",\n',
    )
    replace_once(
        contract,
        "            tests/unit/test_mlb_signal_policy_prelock_persistence.py \\\n",
        "            tests/unit/test_mlb_signal_policy_prelock_persistence.py \\\n"
        "            tests/unit/test_mlb_signal_policy_persistence_bridge_v1.py \\\n",
    )
    replace_once(
        contract,
        "          python -m py_compile hello_world/mlb_probability_actionability_guard.py\n",
        "          python -m py_compile hello_world/mlb_probability_actionability_guard.py\n"
        "          python -m py_compile hello_world/mlb_signal_policy_persistence_bridge_v1.py\n",
    )
    replace_once(
        test_path,
        "import mlb_signal_policy_v12 as signal_policy\n",
        "import mlb_signal_policy_v12 as signal_policy\n"
        "import mlb_signal_policy_persistence_bridge_v1 as persistence_bridge\n",
    )
    replace_once(
        test_path,
        '    assert "MLB-ML-RUNTIME-INSTALL-v4.2-signal-policy-prelock-persistence" in source\n',
        '    bridge_install = source.index(\n'
        '        "mlb_signal_policy_persistence_bridge_v1.apply(engine, mlb_signal_policy_v12)"\n'
        '    )\n'
        '    assert signal_install < public_install < bridge_install < finalizer_install\n'
        '    assert "MLB-ML-RUNTIME-INSTALL-v4.3-signal-policy-public-wrapper-reassertion" in source\n',
    )

    print("MLB signal-policy persistence repair applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
