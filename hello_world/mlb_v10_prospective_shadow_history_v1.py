"""Append-only prospective-shadow evidence for MLB V10.

The active prospective window is intentionally recalculated when the frozen
registry advances. This module preserves prior non-empty windows as compact,
deduplicated observations so a freeze rollover cannot erase evidence.

All accuracy values exposed here are observed retrospective shadow outcome
rates. They are not validated predictive probabilities and confer no
production or pick-publication authority.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

VERSION = "MLB-V10-PROSPECTIVE-SHADOW-HISTORY-v1-append-only-deduplicated"
DEFAULT_REPORT_PATH = Path("runtime_reports/mlb_v10_autonomous_signal_discovery_latest.json")
MAX_GIT_HISTORY_COMMITS = 24
OBSERVED_RATE_SEMANTICS = (
    "Observed retrospective shadow outcome rate; not a validated predictive probability."
)


def _i(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _game_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    portfolio = snapshot.get("portfolio") or {}
    raw_rows = portfolio.get("predictions") or []
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        slate_date = str(raw.get("slateDateEt") or "")
        game_id = str(raw.get("gameId") or raw.get("gameIdentity") or "")
        if not slate_date or not game_id:
            continue
        key = (slate_date, game_id)
        if key in seen:
            continue
        seen.add(key)
        correct = raw.get("correct")
        rows.append({
            "slateDateEt": slate_date,
            "gameId": game_id,
            "selectedSide": str(raw.get("selectedSide") or "") or None,
            "correct": bool(correct) if correct in (True, False, 0, 1) else None,
        })
    rows.sort(key=lambda row: (str(row["slateDateEt"]), str(row["gameId"])))
    return rows


def _snapshot_identity(
    snapshot: Mapping[str, Any],
    games: Sequence[Mapping[str, Any]],
) -> str:
    portfolio = snapshot.get("portfolio") or {}
    material = {
        "registryFingerprint": snapshot.get("registryFingerprint"),
        "frozenThroughDate": snapshot.get("frozenThroughDate"),
        "futureCanonicalGameCount": _i(snapshot.get("futureCanonicalGameCount")),
        "futureSlateCount": _i(snapshot.get("futureSlateCount")),
        "pickCount": _i(portfolio.get("pickCount")),
        "correct": _i(portfolio.get("correct")),
        "losses": _i(portfolio.get("losses")),
        "games": list(games),
    }
    encoded = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_entry(snapshot: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, Mapping):
        return None
    portfolio = snapshot.get("portfolio") or {}
    future_game_count = _i(snapshot.get("futureCanonicalGameCount"))
    pick_count = _i(portfolio.get("pickCount"))
    if future_game_count <= 0 and pick_count <= 0:
        return None

    games = _game_rows(snapshot)
    slate_dates = sorted({
        str(row.get("slateDateEt") or "")
        for row in games
        if str(row.get("slateDateEt") or "")
    })
    if not slate_dates:
        daily = portfolio.get("daily") or []
        slate_dates = sorted({
            str(row.get("slateDateEt") or "")
            for row in daily
            if isinstance(row, Mapping) and str(row.get("slateDateEt") or "")
        })

    correct = _i(portfolio.get("correct"))
    losses = _i(portfolio.get("losses"))
    observed_accuracy = _f(portfolio.get("accuracy"))
    entry = {
        "version": VERSION,
        "snapshotFingerprint": "",
        "registryVersion": snapshot.get("registryVersion"),
        "registryFingerprint": snapshot.get("registryFingerprint"),
        "frozenThroughDate": snapshot.get("frozenThroughDate"),
        "evaluatedThroughDate": max(slate_dates, default=None),
        "futureCanonicalGameCount": future_game_count,
        "futureSlateCount": _i(snapshot.get("futureSlateCount")),
        "observedPickCount": pick_count,
        "correct": correct,
        "losses": losses,
        "observedAccuracy": observed_accuracy,
        "observedAccuracySemantics": OBSERVED_RATE_SEMANTICS,
        "policyChangedDuringEvaluation": snapshot.get("policyChangedDuringEvaluation") is True,
        "selectionUsesFutureLabels": snapshot.get("selectionUsesFutureLabels") is True,
        "productionAuthority": False,
        "shadowOnly": True,
        "games": games,
    }
    entry["snapshotFingerprint"] = _snapshot_identity(snapshot, games)
    return entry


def _history_entries(report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(report, Mapping):
        return []
    prospective = report.get("prospectiveShadow") or {}
    raw = prospective.get("history")
    if not isinstance(raw, list):
        raw = report.get("prospectiveShadowHistory")
    entries: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping) and item.get("snapshotFingerprint"):
                value = dict(item)
                value["productionAuthority"] = False
                value["shadowOnly"] = True
                value.setdefault("observedAccuracySemantics", OBSERVED_RATE_SEMANTICS)
                entries.append(value)
    active = snapshot_entry(prospective)
    if active is not None:
        entries.append(active)
    return entries


def merge_history(
    previous_report: Mapping[str, Any] | None,
    current_snapshot: Mapping[str, Any] | None,
    *,
    historical_reports: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(entry: Mapping[str, Any]) -> None:
        fingerprint = str(entry.get("snapshotFingerprint") or "")
        if not fingerprint or fingerprint in seen:
            return
        seen.add(fingerprint)
        value = dict(entry)
        value["productionAuthority"] = False
        value["shadowOnly"] = True
        value.setdefault("observedAccuracySemantics", OBSERVED_RATE_SEMANTICS)
        ordered.append(value)

    for report in historical_reports:
        for entry in _history_entries(report):
            add(entry)
    for entry in _history_entries(previous_report):
        add(entry)
    current = snapshot_entry(current_snapshot)
    if current is not None:
        add(current)

    ordered.sort(key=lambda row: (
        str(row.get("evaluatedThroughDate") or ""),
        str(row.get("frozenThroughDate") or ""),
        str(row.get("snapshotFingerprint") or ""),
    ))
    return ordered


def summarize_history(history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    unique_games: dict[tuple[str, str], Mapping[str, Any]] = {}
    slate_dates: set[str] = set()
    for entry in history:
        for raw in entry.get("games") or []:
            if not isinstance(raw, Mapping):
                continue
            slate_date = str(raw.get("slateDateEt") or "")
            game_id = str(raw.get("gameId") or "")
            if not slate_date or not game_id:
                continue
            slate_dates.add(slate_date)
            unique_games.setdefault((slate_date, game_id), raw)

    observed = [
        row
        for row in unique_games.values()
        if row.get("correct") in (True, False, 0, 1)
    ]
    correct = sum(bool(row.get("correct")) for row in observed)
    pick_count = len(observed)
    return {
        "version": VERSION,
        "status": "OBSERVED_SHADOW_HISTORY_AVAILABLE" if history else "AWAITING_FIRST_OBSERVED_SHADOW_WINDOW",
        "snapshotCount": len(history),
        "uniqueObservedPickCount": pick_count,
        "correct": correct,
        "losses": pick_count - correct,
        "observedAccuracy": (correct / pick_count) if pick_count else None,
        "observedAccuracySemantics": OBSERVED_RATE_SEMANTICS,
        "uniqueSlateDateCount": len(slate_dates),
        "firstObservedSlateDate": min(slate_dates, default=None),
        "lastObservedSlateDate": max(slate_dates, default=None),
        "productionAuthority": False,
        "mayWriteChampion": False,
        "mayPublishPicks": False,
        "shadowOnly": True,
    }


def _git(args: Sequence[str], *, cwd: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def load_repository_reports(
    *,
    repo_root: Path | None = None,
    report_path: Path = DEFAULT_REPORT_PATH,
    max_commits: int = MAX_GIT_HISTORY_COMMITS,
) -> list[dict[str, Any]]:
    root = repo_root or Path(__file__).resolve().parents[1]
    relative = report_path.as_posix()
    commits: list[str] = []
    for regex in ('"futureCanonicalGameCount": [1-9]', '"status": "EVALUATED"'):
        output = _git(
            [
                "log",
                f"--max-count={max(1, int(max_commits))}",
                "--format=%H",
                "-G",
                regex,
                "--",
                relative,
            ],
            cwd=root,
        )
        for sha in output.splitlines():
            sha = sha.strip()
            if sha and sha not in commits:
                commits.append(sha)
    reports: list[dict[str, Any]] = []
    for sha in reversed(commits[: max(1, int(max_commits))]):
        raw = _git(["show", f"{sha}:{relative}"], cwd=root)
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            reports.append(value)
    return reports


def enrich_snapshot(
    current_snapshot: Mapping[str, Any],
    previous_report: Mapping[str, Any] | None,
    *,
    historical_reports: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    current = dict(current_snapshot)
    reports = (
        load_repository_reports()
        if historical_reports is None
        else list(historical_reports)
    )
    history = merge_history(
        previous_report,
        current,
        historical_reports=reports,
    )
    current["historyVersion"] = VERSION
    current["historyAppendOnly"] = True
    current["historyDeduplicated"] = True
    current["history"] = history
    current["cumulativeObservedShadow"] = summarize_history(history)
    current["probabilitySemantics"] = OBSERVED_RATE_SEMANTICS
    current["productionAuthority"] = False
    current["shadowOnly"] = True
    return current
