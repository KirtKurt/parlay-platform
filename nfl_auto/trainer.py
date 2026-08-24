"""Autonomous target training, objective gates, and atomic historical promotion."""
from __future__ import annotations

from typing import Any, Mapping

from .canonical import digest, now_utc
from .config import Settings, TARGETS
from .llm_analyst import propose_trials
from .model import TrainingRow, adaptive_split, select_candidate
from .storage import NflStore


def _corpus_summary(split: Any) -> dict[str, Any]:
    # Audit outcomes are deliberately withheld from the LLM search analyst.
    return {
        "train_rows": len(split.train),
        "validation_rows": len(split.validation),
        "audit_rows_reserved": len(split.audit),
        "train_positive_rate": (
            sum(row.label for row in split.train) / len(split.train) if split.train else None
        ),
        "validation_positive_rate": (
            sum(row.label for row in split.validation) / len(split.validation)
            if split.validation
            else None
        ),
        "train_market_prior_mean": (
            sum(row.market_prior for row in split.train) / len(split.train)
            if split.train
            else None
        ),
        "validation_market_prior_mean": (
            sum(row.market_prior for row in split.validation) / len(split.validation)
            if split.validation
            else None
        ),
    }


def promotion_gate(
    report: Mapping[str, Any],
    settings: Settings,
    *,
    champion: Mapping[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    counts = report.get("split_counts") or {}
    if int(counts.get("train") or 0) < settings.min_training_rows:
        failures.append("INSUFFICIENT_TRAINING_ROWS")
    live_mode = report.get("split_mode") == "LIVE_EXPANDING_PROSPECTIVE_AUDIT"
    min_validation = settings.min_live_validation_rows if live_mode else settings.min_validation_rows
    min_audit = settings.min_live_audit_rows if live_mode else settings.min_audit_rows
    if int(counts.get("validation") or 0) < min_validation:
        failures.append("INSUFFICIENT_VALIDATION_ROWS")
    if int(counts.get("audit") or 0) < min_audit:
        failures.append("INSUFFICIENT_AUDIT_ROWS")
    validation_skill = float(((report.get("validation") or {}).get("log_loss_skill") or 0.0))
    audit_skill = float(((report.get("audit") or {}).get("log_loss_skill") or 0.0))
    if validation_skill <= 0.0:
        failures.append("VALIDATION_DOES_NOT_BEAT_MARKET")
    if audit_skill <= 0.0:
        failures.append("AUDIT_DOES_NOT_BEAT_MARKET")
    lower_bound = float(report.get("audit_market_skill_lower_bound_95") or float("-inf"))
    if lower_bound <= 0.0:
        failures.append("AUDIT_SKILL_LOWER_BOUND_NOT_POSITIVE")
    audit_ece = float((((report.get("audit") or {}).get("candidate") or {}).get("ece") or 1.0))
    if audit_ece > settings.max_audit_ece:
        failures.append("AUDIT_CALIBRATION_FAILED")
    if champion:
        champion_report = champion.get("report") or {}
        old_loss = float(
            ((((champion_report.get("audit") or {}).get("candidate") or {}).get("log_loss")) or float("inf"))
        )
        new_loss = float(
            ((((report.get("audit") or {}).get("candidate") or {}).get("log_loss")) or float("inf"))
        )
        if new_loss > old_loss + 1e-9:
            failures.append("CANDIDATE_WORSE_THAN_CHAMPION_AUDIT")
    return not failures, failures


def train_target(
    *,
    store: NflStore,
    settings: Settings,
    target: str,
    bedrock_client: Any = None,
) -> dict[str, Any]:
    raw_rows = store.feature_rows(target)
    rows: list[TrainingRow] = []
    rejected = 0
    for raw in raw_rows:
        try:
            row = TrainingRow.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        if row.target != target:
            rejected += 1
            continue
        rows.append(row)
    split, split_mode = adaptive_split(
        rows,
        min_live_rows=settings.min_live_rows_for_adaptation,
        live_validation_rows=settings.min_live_validation_rows,
        live_audit_rows=settings.min_live_audit_rows,
    )
    analyst = propose_trials(
        target=target,
        corpus_summary=_corpus_summary(split),
        model_id=settings.llm_model_id,
        region_name=settings.aws_region,
        bedrock_client=bedrock_client,
    )
    store.put_op(
        "LLM_ANALYSIS",
        f"{target}#{analyst.get('generated_at')}",
        {"target": target, **analyst},
    )
    try:
        model, report = select_candidate(
            split,
            target,
            search=analyst.get("trials") or None,
        )
    except ValueError as exc:
        result = {
            "ok": False,
            "target": target,
            "status": "TRAINING_DEFERRED",
            "reason": str(exc),
            "valid_rows": len(rows),
            "rejected_rows": rejected,
            "llm_status": analyst.get("status"),
            "at": now_utc(),
        }
        store.put_op("TRAINING_RUN", f"{target}#{result['at']}", result)
        return result
    report = {
        **report,
        "split_mode": split_mode,
        "llm_analysis": {
            "status": analyst.get("status"),
            "ok": analyst.get("ok"),
            "llm_trial_count": len(analyst.get("llm_trials") or []),
            "response_digest": analyst.get("response_digest"),
        },
        "valid_rows": len(rows),
        "rejected_rows": rejected,
        "report_digest": "pending",
    }
    report["report_digest"] = digest({k: v for k, v in report.items() if k != "report_digest"})
    artifact = model.to_dict()
    champion = store.champion(target)
    passed, failures = promotion_gate(report, settings, champion=champion)
    authority_state = "HISTORICAL_CHAMPION_CANDIDATE" if passed else "REJECTED_BY_OBJECTIVE_GATE"
    store.put_model_candidate(
        target=target,
        model=artifact,
        report={**report, "promotion_failures": failures},
        authority_state=authority_state,
    )
    artifact_location = store.put_artifact(
        logical_key=f"nfl/models/{target}",
        payload={"model": artifact, "report": report, "promotion_failures": failures},
    )
    if passed:
        store.promote_model(
            target=target,
            model=artifact,
            report={**report, "promotion_failures": [], "artifact": artifact_location},
        )
    result = {
        "ok": True,
        "target": target,
        "status": "PROMOTED_HISTORICAL_CHAMPION" if passed else "REJECTED_BY_GATE",
        "model_digest": artifact["model_digest"],
        "promotion_failures": failures,
        "artifact": artifact_location,
        "split_counts": report["split_counts"],
        "audit": report["audit"],
        "audit_market_skill_lower_bound_95": report["audit_market_skill_lower_bound_95"],
        "llm_status": analyst.get("status"),
        "at": now_utc(),
    }
    store.put_op("TRAINING_RUN", f"{target}#{result['at']}", result)
    return result


def train_all_targets(
    *,
    store: NflStore,
    settings: Settings,
    bedrock_client: Any = None,
) -> dict[str, Any]:
    if not store.acquire_lease("TRAIN_ALL", ttl_seconds=900):
        return {"ok": True, "status": "LEASE_HELD"}
    try:
        results = [
            train_target(
                store=store,
                settings=settings,
                target=target,
                bedrock_client=bedrock_client,
            )
            for target in TARGETS
        ]
        return {
            "ok": all(bool(row.get("ok")) for row in results),
            "status": "COMPLETE",
            "results": results,
            "at": now_utc(),
        }
    finally:
        store.release_lease("TRAIN_ALL")
