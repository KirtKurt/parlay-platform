from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


# Operational snapshots from these reports describe the fundamentals scoring /
# provenance implementation that existed when the report was observed. Once a
# newer reviewed fundamentals repair lands on main, an older snapshot must not
# continue to authorize new engineering work as though the repair never happened.
_FUNDAMENTALS_RUNTIME_REPORT_TOKENS = (
    "mlb_scoring_guard_status",
    "mlb_scoring_fix_post_deploy",
    "mlb_fundamentals_provenance_diagnostic",
)

# Require both an action verb and an MLB-fundamentals subject token. This avoids
# treating planner/evidence documentation commits as proof that an operational
# defect was repaired.
_REPAIR_ACTION_TOKENS = (
    " repair",
    " fix",
    " align",
    " bind",
    " credit",
    " restore",
    " resolve",
    " enable",
)
_FUNDAMENTALS_REPAIR_SUBJECT_TOKENS = (
    "fundamentals",
    "provenance",
    "post-persistence",
    "post persistence",
    "batting order",
    "lineup",
    "scoring guard",
)


def _parse_epoch(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (parsed >= 0.0 and parsed < float("inf")):
        return None
    return parsed


def _parse_commit_epoch(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _is_fundamentals_repair_subject(subject: str) -> bool:
    lowered = f" {subject.lower()}"
    return bool(
        "mlb" in lowered
        and any(token in lowered for token in _REPAIR_ACTION_TOKENS)
        and any(token in lowered for token in _FUNDAMENTALS_REPAIR_SUBJECT_TOKENS)
    )


def recent_fundamentals_repair_cutoff(evidence: str) -> float | None:
    """Return the newest relevant main-history repair timestamp in a JSONL packet."""
    latest: float | None = None
    for line in evidence.splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        if item.get("kind") != "recent_main_history":
            continue
        content = str(item.get("content") or "")
        for commit_line in content.splitlines():
            parts = commit_line.split(" ", 2)
            if len(parts) != 3:
                continue
            _sha, timestamp, subject = parts
            if not _is_fundamentals_repair_subject(subject):
                continue
            epoch = _parse_commit_epoch(timestamp)
            if epoch is not None and (latest is None or epoch > latest):
                latest = epoch
    return latest


def _is_fundamentals_runtime_path(path: Any) -> bool:
    lowered = str(path or "").lower()
    return any(token in lowered for token in _FUNDAMENTALS_RUNTIME_REPORT_TOKENS)


def _hit_is_superseded(hit: Any, cutoff: float) -> bool:
    if not isinstance(hit, dict) or not _is_fundamentals_runtime_path(hit.get("path")):
        return False
    observed = _parse_epoch(hit.get("observedEpoch"))
    return observed is not None and observed <= cutoff


def filter_superseded_fundamentals_evidence(evidence: str) -> str:
    """Remove only operational evidence made stale by a newer main repair.

    The filter is fail-closed for task authority: an old runtime snapshot can no
    longer authorize work after a relevant repair landed. A newer observation
    after that repair remains visible and can prove the defect persists. This
    function never changes source reports, predictions, ledgers, locks, models,
    or production authority.
    """
    cutoff = recent_fundamentals_repair_cutoff(evidence)
    if cutoff is None:
        return evidence

    kept: list[str] = []
    removed_reports = 0
    removed_hits = 0
    for line in evidence.splitlines():
        try:
            item = json.loads(line)
        except Exception:
            kept.append(line)
            continue

        kind = str(item.get("kind") or "")
        if kind == "current_runtime_report" and _is_fundamentals_runtime_path(item.get("path")):
            observed = _parse_epoch(item.get("observedEpoch"))
            if observed is not None and observed <= cutoff:
                removed_reports += 1
                continue

        if kind == "planner_feature_pipeline_evidence_summary":
            hits = item.get("hits")
            if isinstance(hits, list):
                current = [hit for hit in hits if not _hit_is_superseded(hit, cutoff)]
                removed_hits += len(hits) - len(current)
                if not current:
                    continue
                item = dict(item)
                item["hits"] = current
                kept.append(json.dumps(item, ensure_ascii=False))
                continue

        if kind == "planner_focus_evidence_summary":
            hits = item.get("hits")
            if isinstance(hits, list):
                current = [
                    hit
                    for hit in hits
                    if not (
                        isinstance(hit, dict)
                        and str(hit.get("domain") or "") == "data_capture"
                        and _hit_is_superseded(hit, cutoff)
                    )
                ]
                removed_hits += len(hits) - len(current)
                item = dict(item)
                item["hits"] = current
                kept.append(json.dumps(item, ensure_ascii=False))
                continue

        kept.append(line)

    if removed_reports or removed_hits:
        kept.append(
            json.dumps(
                {
                    "kind": "evidence_supersession_receipt",
                    "topic": "mlb_fundamentals_runtime",
                    "repairCutoffEpoch": cutoff,
                    "removedRuntimeReportCount": removed_reports,
                    "removedDerivedHitCount": removed_hits,
                    "rule": "OLDER_THAN_OR_EQUAL_TO_NEWER_REVIEWED_MAIN_REPAIR",
                    "readOnly": True,
                    "productionAuthorityChanged": False,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(kept) + ("\n" if kept else "")
