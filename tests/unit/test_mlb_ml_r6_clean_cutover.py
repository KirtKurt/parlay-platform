from pathlib import Path

NEW_EXPERIMENT = "mlb-v2-2026-07-30-future-prospective-r6"
NEW_CUTOFF = "2026-07-30T04:00:00+00:00"
OLD_EXPERIMENT = "mlb-v2-2026-07-29-future-prospective-" + "r5"


def test_template_uses_forward_only_july30_r6_cutover():
    text = Path("template.yaml").read_text()
    assert f"MLB_ML_EXPERIMENT_ID: '{NEW_EXPERIMENT}'" in text
    assert f"MLB_ML_RELEASE_CONTRACT_ID: '{NEW_EXPERIMENT}'" in text
    assert f"MLB_ML_RELEASE_CUTOFF_UTC: '{NEW_CUTOFF}'" in text


def test_active_source_no_longer_pins_r5_experiment():
    roots = [Path("template.yaml"), Path("hello_world"), Path("scripts"), Path("tests"), Path(".github/workflows")]
    offenders = []
    for root in roots:
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml"}:
                continue
            if path == Path(__file__):
                continue
            if OLD_EXPERIMENT in path.read_text(errors="replace"):
                offenders.append(str(path))
    assert offenders == []
