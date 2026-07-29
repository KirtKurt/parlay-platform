from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise SystemExit(f"{label} marker missing")


base = Path("hello_world/mlb_supervised_selection_guard_v2_3.py")
text = base.read_text()
text = replace_once(
    text,
    'BASELINE_GROUP = "market_baseline"\n',
    'BASELINE_GROUP = "market_baseline"\nREGULARIZATION_GRID = (0.02, 0.20)\n',
    "base regularization constant",
)
text = replace_once(
    text,
    '        l2_values = (0.02, 0.20)\n',
    '        l2_values = tuple(REGULARIZATION_GRID)\n',
    "selector l2 grid",
)
text = replace_once(
    text,
    '            for l2_index, l2 in enumerate(l2_values):\n',
    '            for l2 in l2_values:\n',
    "selector l2 loop",
)
text = replace_once(
    text,
    '                        seed=seed + group_index * 1000 + l2_index * 100 + fold_index,\n',
    '                        seed=seed + group_index * 1000 + fold_index,\n',
    "seed alignment",
)
base.write_text(text)

objective = Path("hello_world/mlb_supervised_daily_objective_v2_1.py")
text = objective.read_text()
text = text.replace("V2.5 keeps calibration", "V2.6 keeps calibration", 1)
text = replace_once(
    text,
    "    import mlb_supervised_selection_guard_v2_5 as selection_guard\n",
    "    import mlb_supervised_selection_guard_v2_6 as selection_guard\n",
    "objective direct import",
)
text = replace_once(
    text,
    "    from . import mlb_supervised_selection_guard_v2_5 as selection_guard\n",
    "    from . import mlb_supervised_selection_guard_v2_6 as selection_guard\n",
    "objective package import",
)
text = replace_once(
    text,
    'VERSION = "MLB-SUPERVISED-SHADOW-v2.5-provider-horizon-evaluable-folds"\n',
    'VERSION = "MLB-SUPERVISED-SHADOW-v2.6-seed-aligned-regularization-grid"\n',
    "objective version",
)
text = replace_once(
    text,
    '    if getattr(model_module, "_INQSI_MLB_DAILY_OBJECTIVE_V2_5_INSTALLED", False):\n',
    '    if getattr(model_module, "_INQSI_MLB_DAILY_OBJECTIVE_V2_6_INSTALLED", False):\n',
    "objective installed guard",
)
text = replace_once(
    text,
    '    model_module._INQSI_MLB_DAILY_OBJECTIVE_V2_5_INSTALLED = True\n',
    '    model_module._INQSI_MLB_DAILY_OBJECTIVE_V2_6_INSTALLED = True\n',
    "objective installed marker",
)
objective.write_text(text)

workflow = Path(".github/workflows/mlb-supervised-shadow-v2-recurring.yml")
text = workflow.read_text()
for old, new, label in (
    (
        "      - 'hello_world/mlb_supervised_selection_guard_v2_5.py'\n",
        "      - 'hello_world/mlb_supervised_selection_guard_v2_5.py'\n"
        "      - 'hello_world/mlb_supervised_selection_guard_v2_6.py'\n",
        "workflow v26 code path",
    ),
    (
        "      - 'tests/unit/test_mlb_supervised_selection_guard_v2_5.py'\n",
        "      - 'tests/unit/test_mlb_supervised_selection_guard_v2_5.py'\n"
        "      - 'tests/unit/test_mlb_supervised_selection_guard_v2_6.py'\n",
        "workflow v26 test path",
    ),
    (
        "            hello_world/mlb_supervised_selection_guard_v2_5.py \\\n",
        "            hello_world/mlb_supervised_selection_guard_v2_5.py \\\n"
        "            hello_world/mlb_supervised_selection_guard_v2_6.py \\\n",
        "workflow v26 compile",
    ),
    (
        "            tests/unit/test_mlb_supervised_selection_guard_v2_5.py \\\n",
        "            tests/unit/test_mlb_supervised_selection_guard_v2_5.py \\\n"
        "            tests/unit/test_mlb_supervised_selection_guard_v2_6.py \\\n",
        "workflow v26 pytest",
    ),
):
    text = replace_once(text, old, new, label)

text = text.replace(
    "MLB-SUPERVISED-SELECTION-GUARD-v2.5-provider-horizon-evaluable-folds",
    "MLB-SUPERVISED-SELECTION-GUARD-v2.6-seed-aligned-regularization-grid",
)
needle = "          assert thresholds.get('providerHorizonUnsupportedFoldsCountAsPassing') is False, thresholds\n"
addition = (
    needle
    + "          assert thresholds.get('regularizationComparisonSeedAligned') is True, thresholds\n"
    + "          assert thresholds.get('regularizationGridBounded') is True, thresholds\n"
    + "          assert len(thresholds.get('regularizationGrid') or []) == 8, thresholds\n"
)
if "regularizationComparisonSeedAligned" not in text:
    if needle not in text:
        raise SystemExit("workflow regularization assertion marker missing")
    text = text.replace(needle, addition, 2)
workflow.write_text(text)
