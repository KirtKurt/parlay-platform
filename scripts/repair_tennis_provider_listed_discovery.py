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


def apply(*, check_only: bool = False) -> dict[str, object]:
    changed: list[str] = []
    already_repaired: list[str] = []
    for relative, replacements in REPLACEMENTS.items():
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        updated = original
        path_already_repaired = True
        for old, new in replacements:
            old_count = updated.count(old)
            new_count = updated.count(new) if new else 0
            if old_count == 1:
                updated = updated.replace(old, new, 1)
                path_already_repaired = False
                continue
            if old_count == 0 and (not new or new_count >= 1):
                continue
            raise RuntimeError(
                f"unexpected Tennis discovery contract in {relative}: "
                f"old_count={old_count} new_count={new_count} old={old!r}"
            )

        if updated != original:
            changed.append(relative)
            if not check_only:
                path.write_text(updated, encoding="utf-8")
        elif path_already_repaired:
            already_repaired.append(relative)

    pipeline = (
        ROOT / "tennis_learning/live_pipeline.py"
    ).read_text(encoding="utf-8") if check_only else (
        ROOT / "tennis_learning/live_pipeline.py"
    ).read_text(encoding="utf-8")
    if not check_only and changed:
        pipeline = (ROOT / "tennis_learning/live_pipeline.py").read_text(
            encoding="utf-8"
        )

    required = {
        'sports = _get("/sports/", {"all": "true"})',
        'ALL_PROVIDER_LISTED_NON_OUTRIGHT_MATCHES_ALL_H2H_BOOKMAKER_REGIONS',
        '"sport_keys_truncated": 0',
    }
    missing = sorted(marker for marker in required if marker not in pipeline)
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
        "changed": changed,
        "already_repaired": already_repaired,
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
