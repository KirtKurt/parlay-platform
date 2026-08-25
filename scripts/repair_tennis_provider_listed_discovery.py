from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REPLACEMENTS = {
    "tennis_learning/live_pipeline.py": (
        (
            'sports = _get("/sports/", {"all": "false"})',
            'sports = _get("/sports/", {"all": "true"})',
        ),
        (
            '            and bool(sport.get("active", False))\n',
            '',
        ),
        (
            'no active non-outright tennis sport keys discovered',
            'no provider-listed non-outright tennis sport keys discovered',
        ),
        (
            'ALL_ACTIVE_NON_OUTRIGHT_MATCHES_ALL_H2H_BOOKMAKER_REGIONS',
            'ALL_PROVIDER_LISTED_NON_OUTRIGHT_MATCHES_ALL_H2H_BOOKMAKER_REGIONS',
        ),
    ),
    ".github/workflows/deploy-tennis-learning.yml": (
        (
            'ALL_ACTIVE_NON_OUTRIGHT_MATCHES_',
            'ALL_PROVIDER_LISTED_NON_OUTRIGHT_MATCHES_',
        ),
    ),
    ".github/workflows/publish-tennis-daily-card.yml": (
        (
            'ALL_ACTIVE_NON_OUTRIGHT_MATCHES_',
            'ALL_PROVIDER_LISTED_NON_OUTRIGHT_MATCHES_',
        ),
    ),
}

DISCOVERY_TEST = '''


def test_every_provider_listed_non_outright_tennis_key_is_inventoried():
    assert 'sports = _get("/sports/", {"all": "true"})' in PIPELINE
    assert 'and bool(sport.get("active", False))' not in PIPELINE
    assert (
        'ALL_PROVIDER_LISTED_NON_OUTRIGHT_MATCHES_'
        'ALL_H2H_BOOKMAKER_REGIONS'
    ) in PIPELINE
    assert 'f"/sports/{sport_key}/events"' in PIPELINE
'''


def _replace_exact(text: str, *, old: str, new: str, relative: str) -> str:
    old_count = text.count(old)
    new_count = text.count(new) if new else 0
    if old_count == 1:
        return text.replace(old, new, 1)
    if old_count == 0 and (not new or new_count >= 1):
        return text
    raise RuntimeError(
        f"unexpected Tennis discovery contract in {relative}: "
        f"old_count={old_count} new_count={new_count} old={old!r}"
    )


def apply(*, check_only: bool = False) -> dict[str, object]:
    materialized: dict[str, str] = {}
    changed: list[str] = []
    already_repaired: list[str] = []

    for relative, replacements in REPLACEMENTS.items():
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        updated = original
        for old, new in replacements:
            updated = _replace_exact(
                updated,
                old=old,
                new=new,
                relative=relative,
            )
        materialized[relative] = updated
        if updated != original:
            changed.append(relative)
            if not check_only:
                path.write_text(updated, encoding="utf-8")
        else:
            already_repaired.append(relative)

    test_relative = "tests/test_tennis_full_region_contract.py"
    test_path = ROOT / test_relative
    test_original = test_path.read_text(encoding="utf-8")
    if "test_every_provider_listed_non_outright_tennis_key_is_inventoried" in test_original:
        test_updated = test_original
        already_repaired.append(test_relative)
    else:
        test_updated = test_original.rstrip() + DISCOVERY_TEST + "\n"
        changed.append(test_relative)
        if not check_only:
            test_path.write_text(test_updated, encoding="utf-8")
    materialized[test_relative] = test_updated

    pipeline = materialized["tennis_learning/live_pipeline.py"]
    deploy = materialized[".github/workflows/deploy-tennis-learning.yml"]
    card = materialized[".github/workflows/publish-tennis-daily-card.yml"]
    tests = materialized[test_relative]

    required_by_file = {
        "pipeline": (
            pipeline,
            {
                'sports = _get("/sports/", {"all": "true"})',
                'ALL_PROVIDER_LISTED_NON_OUTRIGHT_MATCHES_'
                'ALL_H2H_BOOKMAKER_REGIONS',
                '"sport_keys_truncated": 0',
                'f"/sports/{sport_key}/events"',
            },
        ),
        "deploy": (
            deploy,
            {
                'ALL_PROVIDER_LISTED_NON_OUTRIGHT_MATCHES_',
                "status['authority'] == 'AUTHORITATIVE'",
            },
        ),
        "card": (
            card,
            {
                'ALL_PROVIDER_LISTED_NON_OUTRIGHT_MATCHES_',
                "scan_prefix('COVERAGE#')",
                "'full_slate_evaluated': full_slate_evaluated",
            },
        ),
        "tests": (
            tests,
            {
                "test_every_provider_listed_non_outright_tennis_key_is_inventoried",
                'sports = _get("/sports/", {"all": "true"})',
            },
        ),
    }
    missing: dict[str, list[str]] = {}
    for label, (text, markers) in required_by_file.items():
        absent = sorted(marker for marker in markers if marker not in text)
        if absent:
            missing[label] = absent

    forbidden = {
        'sports = _get("/sports/", {"all": "false"})',
        'and bool(sport.get("active", False))',
        'MAX_ACTIVE_KEYS',
        'ALL_ACTIVE_NON_OUTRIGHT_MATCHES_ALL_H2H_BOOKMAKER_REGIONS',
    }
    present_forbidden = sorted(
        marker for marker in forbidden if marker in pipeline
    )
    if missing or present_forbidden:
        raise RuntimeError(
            f"Tennis provider-listed discovery contract failed: "
            f"missing={missing} forbidden={present_forbidden}"
        )

    return {
        "ok": True,
        "repair": "TENNIS_PROVIDER_LISTED_NON_OUTRIGHT_KEY_DISCOVERY",
        "sports_discovery_all_parameter": True,
        "active_metadata_filter_removed": True,
        "event_inventory_authority": "THE_ODDS_API_EVENTS_ENDPOINT",
        "changed": sorted(set(changed)),
        "already_repaired": sorted(set(already_repaired)),
        "immutable_prediction_history_rewritten": False,
        "model_authority_changed": False,
        "human_winner_selection": False,
        "other_sport_changed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply(check_only=args.check), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
