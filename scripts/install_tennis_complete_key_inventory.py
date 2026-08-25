from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_SCOPE = "ALL_ACTIVE_NON_OUTRIGHT_MATCHES_ALL_H2H_BOOKMAKER_REGIONS"
NEW_SCOPE = "ALL_TENNIS_KEYS_ALL_H2H_BOOKMAKER_REGIONS"


def _replace_discovery(source: str) -> str:
    pattern = re.compile(
        r"def _discover_tennis_keys\(\) -> list\[str\]:\n.*?\n\n\ndef _parallel_map",
        re.DOTALL,
    )
    replacement = '''def _discover_tennis_keys() -> list[str]:
    """Query every provider Tennis key; event rows, not metadata, determine scope."""
    configured = [
        x.strip()
        for x in os.getenv("TENNIS_ODDS_SPORT_KEYS", "").split(",")
        if x.strip() and x.strip() != "tennis"
    ]
    sports = _get("/sports/", {"all": "true"})
    if not isinstance(sports, list):
        raise RuntimeError("unexpected sports discovery response")

    # Configured keys are additive only. Never let an environment override narrow
    # the provider's complete Tennis catalog.
    keys: set[str] = set(configured)
    for sport in sports:
        key = str(sport.get("key") or "").strip()
        group = str(sport.get("group") or "").strip().lower()
        if group == "tennis" and key:
            keys.add(key)
    return sorted(keys)


def _parallel_map'''
    updated, count = pattern.subn(replacement, source, count=1)
    if count != 1:
        raise RuntimeError(f"expected one Tennis discovery function, replaced {count}")
    return updated


def _append_contract_test(test_path: Path) -> None:
    text = test_path.read_text()
    marker = "test_every_provider_tennis_key_is_discovered_from_actual_events"
    if marker in text:
        return
    addition = '''\n\ndef test_every_provider_tennis_key_is_discovered_from_actual_events():
    source = Path("tennis_learning/live_pipeline.py").read_text()
    block = source.split("def _discover_tennis_keys", 1)[1].split("def _parallel_map", 1)[0]
    assert 'sports = _get("/sports/", {"all": "true"})' in block
    assert 'keys: set[str] = set(configured)' in block
    assert 'group == "tennis" and key' in block
    assert 'sport.get("active"' not in block
    assert 'has_outrights' not in block
    assert 'return configured' not in block
'''
    test_path.write_text(text.rstrip() + addition + "\n")


def main() -> None:
    changed: list[str] = []
    live_path = ROOT / "tennis_learning/live_pipeline.py"
    source = live_path.read_text()
    source = _replace_discovery(source)
    if OLD_SCOPE not in source and NEW_SCOPE not in source:
        raise RuntimeError("Tennis coverage scope marker is missing")
    source = source.replace(OLD_SCOPE, NEW_SCOPE)
    live_path.write_text(source)
    changed.append(str(live_path.relative_to(ROOT)))

    related = [
        ROOT / "tests/test_tennis_live_coverage_contract.py",
        ROOT / ".github/workflows/deploy-tennis-learning.yml",
        ROOT / ".github/workflows/publish-tennis-daily-card.yml",
        ROOT / ".github/workflows/repair-tennis-all-matches-all-regions.yml",
    ]
    for path in related:
        if not path.exists():
            continue
        text = path.read_text().replace(OLD_SCOPE, NEW_SCOPE)
        path.write_text(text)
        changed.append(str(path.relative_to(ROOT)))

    test_path = ROOT / "tests/test_tennis_live_coverage_contract.py"
    _append_contract_test(test_path)

    final = live_path.read_text()
    discovery = final.split("def _discover_tennis_keys", 1)[1].split("def _parallel_map", 1)[0]
    assertions = {
        "all_true_discovery": 'sports = _get("/sports/", {"all": "true"})' in discovery,
        "configured_additive": 'keys: set[str] = set(configured)' in discovery,
        "active_filter_removed": 'sport.get("active"' not in discovery,
        "outright_filter_removed": "has_outrights" not in discovery,
        "scope_updated": NEW_SCOPE in final,
        "old_scope_removed": OLD_SCOPE not in final,
    }
    if not all(assertions.values()):
        raise RuntimeError(f"Tennis complete-key contract failed: {assertions}")

    hashes = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in sorted(set(changed))
    }
    print(json.dumps({"ok": True, "assertions": assertions, "sha256": hashes}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
