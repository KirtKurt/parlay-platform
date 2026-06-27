from typing import Any, Dict


def _fix_result(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return row
    stored = row.get("stored") or {}
    if isinstance(stored, dict) and stored.get("sk") is None and row.get("gamesStored"):
        row["gamesSeen"] = row.get("gamesStored")
        row["gamesStored"] = 0
        row["deduped"] = True
        row["note"] = "Games were seen but the snapshot was skipped by the duplicate-pull guard."
    return row


def apply(odds_module: Any) -> None:
    if odds_module is None or getattr(odds_module, "_inqsi_pull_report_guard_installed", False):
        return
    original_pull_one = odds_module.pull_one
    original_pull_sport = odds_module.pull_sport
    original_pull_many = odds_module.pull_many

    def pull_one(app_sport: str, provider_sport_key: str):
        return _fix_result(original_pull_one(app_sport, provider_sport_key))

    def pull_sport(app_sport: str):
        row = original_pull_sport(app_sport)
        fixed = [_fix_result(provider) for provider in row.get("providerPulls") or []]
        row["providerPulls"] = fixed
        row["gamesSeen"] = sum(int(p.get("gamesSeen") or p.get("gamesStored") or 0) for p in fixed)
        row["gamesStored"] = sum(int(p.get("gamesStored") or 0) for p in fixed)
        row["deduped"] = any(bool(p.get("deduped")) for p in fixed)
        return row

    def pull_many(sports):
        report = original_pull_many(sports)
        results = []
        for row in report.get("results") or []:
            fixed = [_fix_result(provider) for provider in row.get("providerPulls") or []]
            row["providerPulls"] = fixed
            row["gamesSeen"] = sum(int(p.get("gamesSeen") or p.get("gamesStored") or 0) for p in fixed)
            row["gamesStored"] = sum(int(p.get("gamesStored") or 0) for p in fixed)
            row["deduped"] = any(bool(p.get("deduped")) for p in fixed)
            results.append(row)
        report["results"] = results
        report["sportsWithStoredGames"] = [r.get("appSport") for r in results if int(r.get("gamesStored") or 0) > 0]
        report["sportsWithSeenGames"] = [r.get("appSport") for r in results if int(r.get("gamesSeen") or r.get("gamesStored") or 0) > 0]
        return report

    odds_module.pull_one = pull_one
    odds_module.pull_sport = pull_sport
    odds_module.pull_many = pull_many
    odds_module._inqsi_pull_report_guard_installed = True
