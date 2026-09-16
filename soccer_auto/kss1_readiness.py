"""Fail-closed proof of a fitted goals model and real recorded selections."""
from datetime import timedelta
import math

from .canonical import parse_utc


def verify_rollout(training, status, picks):
    """Validate the fresh trainer result and isolated API readback together.

    This proves fitted shadow output, never grants public T10 authority.
    A DC=12 filter of zero is not a failed model when another book published.
    """
    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    context = status.get("training_context") or {}
    model = context.get("model_digest")
    require(training.get("published_context") is True, "GOALS_CONTEXT_NOT_PUBLISHED")
    require(training.get("trained") is True and context.get("trained") is True,
            "GOALS_MODEL_NOT_TRAINED")
    require(isinstance(model, str) and len(model) == 64
            and model == training.get("model_digest") == context.get("candidate_model_digest"),
            "GOALS_MODEL_READBACK_MISMATCH")
    require(training.get("artifact_uri") == context.get("artifact_uri"),
            "GOALS_CONTEXT_READBACK_MISMATCH")
    require(context.get("candidate_passed_retrospective") is True,
            "GOALS_CANDIDATE_NOT_QUALIFIED")
    for value in (training, status, context, picks):
        require(value.get("automatic_prediction_allowed") is False,
                "GOALS_SHADOW_AUTHORITY_VIOLATION")
    readiness = context.get("readiness") or {}
    require(training.get("readiness") == readiness, "GOALS_READINESS_READBACK_MISMATCH")
    counts = readiness.get("split_counts") or {}
    require(all(int(counts.get(k, 0)) >= n for k, n in
                (("train", 500), ("validation", 100), ("holdout", 100))),
            "GOALS_CHRONOLOGICAL_SPLITS_NOT_READY")
    candidate, baseline = context.get("candidate_holdout") or {}, context.get("candidate_baseline") or {}
    require(candidate.get("event_manifest") and
            candidate["event_manifest"] == baseline.get("event_manifest") and
            candidate.get("count") == baseline.get("count") == counts["holdout"],
            "GOALS_HOLDOUT_COHORT_MISMATCH")
    metrics = [float(x.get(k, float("nan"))) for x in (candidate, baseline) for k in ("brier", "log_loss")]
    require(all(math.isfinite(x) and x >= 0 for x in metrics)
            and metrics[0] < metrics[2] and metrics[1] <= metrics[3],
            "GOALS_HOLDOUT_GATE_FAILED")
    require(picks.get("trained_only") is True and picks.get("truncated") is False,
            "GOALS_PICKS_PROOF_INCOMPLETE")
    rows = picks.get("picks") or []
    published = picks.get("published_counts") or {}
    published_any = int(published.get("any") or 0)
    if rows:
        require(picks.get("count") == len(rows), "GOALS_PICK_COUNT_MISMATCH")
        events = set()
        for row in rows:
            require(row.get("model_digest") == model and row.get("model_state") == "FITTED_SHADOW",
                    "GOALS_PICK_MODEL_MISMATCH")
            require(row.get("event_key") and row["event_key"] not in events,
                    "GOALS_DUPLICATE_OR_MISSING_PICK_IDENTITY")
            events.add(row["event_key"])
            markets = row.get("markets") or {}
            require(any(markets.get(key) not in (None, "ABSTAIN") for key in
                        ("1x2_published", "double_chance_published", "ou25_published", "btts_published")),
                    "GOALS_PICK_NOT_READY")
            require((row.get("input_coverage") or {}).get("team_strength_complete") is True,
                    "GOALS_PICK_NOT_READY")
            require(parse_utc(row["created_at"]) <= parse_utc(row["commence_time"]) - timedelta(minutes=60),
                    "GOALS_PICK_AFTER_T60")
            require(parse_utc(context["context_as_of"]) <= parse_utc(row["created_at"]),
                    "GOALS_PICK_PREDATES_CONTEXT")
        recorded = len(rows)
    else:
        # A deployment can have no publishable trained shadow book for the
        # current slate. That is safe while authority is SHADOW_LEARNING, but
        # it must be explicit and complete; an unexplained empty response
        # remains a readiness failure.
        safe_empty = (
            picks.get("reason") == "NO_MATCHING_RECORDED_PICKS"
            and published_any == 0
            and isinstance(picks.get("missing"), list)
            and bool(picks["missing"])
            and int(picks.get("count") or 0) == 0
            and all(
                row.get("reason") in {
                    "NO_RECORDED_T60_TRAINED_PICK",
                    "NO_RECORDED_T60_TRAINED_PUBLISHED_BOOK",
                }
                for row in picks["missing"]
            )
            and all(
                int(published.get(key) or 0) == 0
                for key in ("1x2", "double_chance", "ou25", "btts", "any")
            )
            and len(picks["missing"]) == int(picks.get("fixture_count") or 0)
        )
        require(safe_empty, "NO_RECORDED_TRAINED_PUBLISHED_BOOKS")
        recorded = 0
    return {"verified": True, "model_digest": model, "recorded_12_picks": recorded,
            "published_counts": published, "authority": "SHADOW_LEARNING",
            "automatic_prediction_allowed": False}
