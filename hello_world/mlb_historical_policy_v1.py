from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

VERSION = "MLB-HISTORICAL-POLICY-v15.11.1"
CUTOVER_MODE = "HISTORICAL_DAILY_OPTIMIZER_ONLY"
INCUMBENT_MODE = "V15_10_RANKED_WINNER"
FAIL_CLOSED_MODE = "FAIL_CLOSED"


@dataclass(frozen=True)
class HistoricalPolicy:
    sport_key: str = "baseball_mlb"
    timezone: str = "America/New_York"
    pull_start_local: str = "01:00"
    snapshot_minutes: int = 15
    lock_minutes_before_commence: int = 45
    min_train_games: int = 1000
    min_validation_games: int = 200
    min_audit_games: int = 200
    min_validation_days: int = 14
    min_audit_days: int = 14
    min_daily_accuracy: float = 0.80
    target_daily_accuracy: float = 0.90
    required_coverage: float = 1.0
    max_train_validation_gap: float = 0.08
    max_validation_audit_gap: float = 0.05
    max_brier_regression: float = 0.0
    max_log_loss_regression: float = 0.0

    def validate(self) -> None:
        if self.min_train_games < 1000:
            raise ValueError("min_train_games must be at least 1000")
        if self.min_validation_games < 200 or self.min_audit_games < 200:
            raise ValueError("validation and audit each require at least 200 games")
        if self.min_validation_days < 14 or self.min_audit_days < 14:
            raise ValueError("validation and audit each require at least 14 dates")
        if self.snapshot_minutes != 15:
            raise ValueError("historical cadence is fixed at 15 minutes")
        if self.pull_start_local != "01:00":
            raise ValueError("game-day historical collection must start at 01:00 ET")
        if self.lock_minutes_before_commence != 45:
            raise ValueError("features must be frozen at T-minus-45")
        if not 0.80 <= self.min_daily_accuracy <= self.target_daily_accuracy <= 1.0:
            raise ValueError("daily accuracy targets are invalid")
        if self.required_coverage != 1.0:
            raise ValueError("complete official-slate coverage is mandatory")


@dataclass(frozen=True)
class ChronologicalSplit:
    train_dates: Tuple[str, ...]
    validation_dates: Tuple[str, ...]
    audit_dates: Tuple[str, ...]
    train_games: int
    validation_games: int
    audit_games: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DailySlateResult:
    slate_date: str
    official_games: int
    predicted_games: int
    correct_games: int
    missing_game_ids: Tuple[str, ...]
    extra_game_ids: Tuple[str, ...]
    duplicate_game_ids: Tuple[str, ...]
    coverage: float
    accuracy: float
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GateDecision:
    approved: bool
    decision: str
    blockers: Tuple[str, ...]
    policy_version: str
    cutover_mode: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _iso_date(value: Any) -> str:
    text = str(value or "").strip()
    parsed = date.fromisoformat(text)
    return parsed.isoformat()


def _game_counts_by_date(rows: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    seen: set[Tuple[str, str]] = set()
    for row in rows:
        slate_date = _iso_date(row.get("slate_date"))
        game_id = str(row.get("game_id") or "").strip()
        if not game_id:
            raise ValueError("every row requires game_id")
        key = (slate_date, game_id)
        if key in seen:
            raise ValueError(f"duplicate game row: {slate_date}:{game_id}")
        seen.add(key)
        counts[slate_date] = counts.get(slate_date, 0) + 1
    return counts


def chronological_split(
    rows: Iterable[Mapping[str, Any]],
    policy: HistoricalPolicy | None = None,
) -> ChronologicalSplit:
    policy = policy or HistoricalPolicy()
    policy.validate()
    counts = _game_counts_by_date(rows)
    dates = sorted(counts)
    if not dates:
        raise ValueError("no settled games supplied")

    train: List[str] = []
    validation: List[str] = []
    audit: List[str] = []
    train_games = validation_games = audit_games = 0
    phase = "train"

    for slate_date in dates:
        count = counts[slate_date]
        if phase == "train":
            train.append(slate_date)
            train_games += count
            if train_games >= policy.min_train_games:
                phase = "validation"
        elif phase == "validation":
            validation.append(slate_date)
            validation_games += count
            if (
                validation_games >= policy.min_validation_games
                and len(validation) >= policy.min_validation_days
            ):
                phase = "audit"
        else:
            audit.append(slate_date)
            audit_games += count

    if train_games < policy.min_train_games:
        raise ValueError("fewer than 1000 chronological training games")
    if validation_games < policy.min_validation_games or len(validation) < policy.min_validation_days:
        raise ValueError("insufficient later walk-forward validation evidence")
    if audit_games < policy.min_audit_games or len(audit) < policy.min_audit_days:
        raise ValueError("insufficient strictly later untouched audit evidence")
    if not (max(train) < min(validation) and max(validation) < min(audit)):
        raise ValueError("date partitions overlap or are not chronological")

    return ChronologicalSplit(
        train_dates=tuple(train),
        validation_dates=tuple(validation),
        audit_dates=tuple(audit),
        train_games=train_games,
        validation_games=validation_games,
        audit_games=audit_games,
    )


def score_daily_slates(
    predictions: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
    policy: HistoricalPolicy | None = None,
) -> List[DailySlateResult]:
    policy = policy or HistoricalPolicy()
    policy.validate()

    official: Dict[Tuple[str, str], str] = {}
    by_date: Dict[str, set[str]] = {}
    for row in outcomes:
        slate_date = _iso_date(row.get("slate_date"))
        game_id = str(row.get("game_id") or "").strip()
        winner = str(row.get("winner") or "").strip()
        if not game_id or not winner:
            raise ValueError("settled outcomes require game_id and winner")
        key = (slate_date, game_id)
        if key in official:
            raise ValueError(f"duplicate official outcome: {slate_date}:{game_id}")
        official[key] = winner
        by_date.setdefault(slate_date, set()).add(game_id)

    prediction_rows: Dict[Tuple[str, str], List[str]] = {}
    for row in predictions:
        slate_date = _iso_date(row.get("slate_date"))
        game_id = str(row.get("game_id") or "").strip()
        pick = str(row.get("pick") or "").strip()
        if not game_id or not pick:
            raise ValueError("predictions require game_id and pick")
        prediction_rows.setdefault((slate_date, game_id), []).append(pick)

    results: List[DailySlateResult] = []
    for slate_date in sorted(by_date):
        official_ids = by_date[slate_date]
        predicted_ids = {
            game_id for date_value, game_id in prediction_rows if date_value == slate_date
        }
        missing = sorted(official_ids - predicted_ids)
        extra = sorted(predicted_ids - official_ids)
        duplicates = sorted(
            game_id
            for game_id in predicted_ids
            if len(prediction_rows[(slate_date, game_id)]) != 1
        )
        correct = 0
        for game_id in official_ids:
            picks = prediction_rows.get((slate_date, game_id)) or []
            if len(picks) == 1 and picks[0] == official[(slate_date, game_id)]:
                correct += 1
        official_count = len(official_ids)
        predicted_count = len(predicted_ids)
        coverage = predicted_count / official_count if official_count else 0.0
        accuracy = correct / official_count if official_count else 0.0
        passed = bool(
            coverage == policy.required_coverage
            and not missing
            and not extra
            and not duplicates
            and accuracy >= policy.min_daily_accuracy
        )
        results.append(
            DailySlateResult(
                slate_date=slate_date,
                official_games=official_count,
                predicted_games=predicted_count,
                correct_games=correct,
                missing_game_ids=tuple(missing),
                extra_game_ids=tuple(extra),
                duplicate_game_ids=tuple(duplicates),
                coverage=coverage,
                accuracy=accuracy,
                passed=passed,
            )
        )
    return results


def _daily_blockers(prefix: str, rows: Sequence[Mapping[str, Any]], threshold: float) -> List[str]:
    blockers: List[str] = []
    if not rows:
        return [f"{prefix}_daily_results_missing"]
    for row in rows:
        slate_date = str(row.get("slate_date") or "unknown")
        if float(row.get("coverage", 0.0)) != 1.0:
            blockers.append(f"{prefix}_{slate_date}_incomplete_coverage")
        if row.get("missing_game_ids") or row.get("extra_game_ids") or row.get("duplicate_game_ids"):
            blockers.append(f"{prefix}_{slate_date}_slate_identity_mismatch")
        if float(row.get("accuracy", 0.0)) < threshold:
            blockers.append(f"{prefix}_{slate_date}_below_{threshold:.2f}")
    return blockers


def evaluate_promotion_gate(
    report: Mapping[str, Any],
    policy: HistoricalPolicy | None = None,
) -> GateDecision:
    policy = policy or HistoricalPolicy()
    policy.validate()
    blockers: List[str] = []

    counts = report.get("sample_counts") or {}
    if int(counts.get("train", 0)) < policy.min_train_games:
        blockers.append("training_games_below_1000")
    if int(counts.get("validation", 0)) < policy.min_validation_games:
        blockers.append("validation_games_below_200")
    if int(counts.get("audit", 0)) < policy.min_audit_games:
        blockers.append("audit_games_below_200")
    if int(counts.get("validation_days", 0)) < policy.min_validation_days:
        blockers.append("validation_days_below_14")
    if int(counts.get("audit_days", 0)) < policy.min_audit_days:
        blockers.append("audit_days_below_14")

    chronology = report.get("chronology") or {}
    if chronology.get("whole_date_partitions") is not True:
        blockers.append("whole_date_partition_proof_missing")
    if chronology.get("strictly_ordered") is not True:
        blockers.append("chronological_order_proof_missing")
    if chronology.get("audit_opened_after_selection") is not True:
        blockers.append("untouched_audit_proof_missing")

    provenance = report.get("provenance") or {}
    required_provenance = {
        "starts_at_0100_et": "historical_collection_not_0100_et",
        "cadence_15_minutes": "historical_cadence_not_15_minutes",
        "t_minus_45_clipped": "t_minus_45_leakage_guard_missing",
        "settled_official_labels": "official_settlement_proof_missing",
        "no_future_features": "future_information_leakage_detected",
    }
    for key, blocker in required_provenance.items():
        if provenance.get(key) is not True:
            blockers.append(blocker)

    blockers.extend(
        _daily_blockers(
            "validation",
            report.get("validation_daily") or [],
            policy.min_daily_accuracy,
        )
    )
    blockers.extend(
        _daily_blockers(
            "audit",
            report.get("audit_daily") or [],
            policy.min_daily_accuracy,
        )
    )

    metrics = report.get("metrics") or {}
    train_accuracy = float(metrics.get("train_accuracy", 0.0))
    validation_accuracy = float(metrics.get("validation_accuracy", 0.0))
    audit_accuracy = float(metrics.get("audit_accuracy", 0.0))
    if train_accuracy - validation_accuracy > policy.max_train_validation_gap:
        blockers.append("train_validation_divergence_exceeds_limit")
    if validation_accuracy - audit_accuracy > policy.max_validation_audit_gap:
        blockers.append("validation_audit_divergence_exceeds_limit")
    if float(metrics.get("validation_brier", 99.0)) > float(metrics.get("market_validation_brier", -99.0)) + policy.max_brier_regression:
        blockers.append("validation_brier_regressed_vs_market")
    if float(metrics.get("audit_brier", 99.0)) > float(metrics.get("market_audit_brier", -99.0)) + policy.max_brier_regression:
        blockers.append("audit_brier_regressed_vs_market")
    if float(metrics.get("validation_log_loss", 99.0)) > float(metrics.get("market_validation_log_loss", -99.0)) + policy.max_log_loss_regression:
        blockers.append("validation_log_loss_regressed_vs_market")
    if float(metrics.get("audit_log_loss", 99.0)) > float(metrics.get("market_audit_log_loss", -99.0)) + policy.max_log_loss_regression:
        blockers.append("audit_log_loss_regressed_vs_market")

    artifact = report.get("artifact") or {}
    if artifact.get("sha256_validated") is not True:
        blockers.append("artifact_digest_not_validated")
    if artifact.get("immutable") is not True:
        blockers.append("artifact_not_immutable")
    if not str(artifact.get("sha256") or ""):
        blockers.append("artifact_sha256_missing")

    approved = not blockers
    return GateDecision(
        approved=approved,
        decision="PROMOTE" if approved else "BLOCK",
        blockers=tuple(sorted(set(blockers))),
        policy_version=VERSION,
        cutover_mode=CUTOVER_MODE,
    )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: Mapping[str, Any]) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def resolve_production_authority(
    *,
    cutover_record: Mapping[str, Any] | None,
    champion_record: Mapping[str, Any] | None,
    artifact_sha256: str | None,
) -> str:
    if not cutover_record:
        return INCUMBENT_MODE
    if cutover_record.get("mode") != CUTOVER_MODE:
        return FAIL_CLOSED_MODE
    if cutover_record.get("legacy_fallback_allowed") is not False:
        return FAIL_CLOSED_MODE
    if not champion_record or champion_record.get("approved") is not True:
        return FAIL_CLOSED_MODE
    expected = str(champion_record.get("artifact_sha256") or "")
    if not expected or expected != str(artifact_sha256 or ""):
        return FAIL_CLOSED_MODE
    return CUTOVER_MODE


def build_cutover_records(
    *,
    experiment_id: str,
    artifact_sha256: str,
    gate_report_sha256: str,
    approved_at_utc: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    approved_at_utc = approved_at_utc or datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    champion = {
        "record_type": "mlb_historical_daily_optimizer_champion",
        "version": VERSION,
        "experiment_id": experiment_id,
        "approved": True,
        "artifact_sha256": artifact_sha256,
        "gate_report_sha256": gate_report_sha256,
        "approved_at_utc": approved_at_utc,
        "automatic_wager_allowed": False,
    }
    cutover = {
        "record_type": "mlb_production_algorithm_cutover",
        "version": VERSION,
        "mode": CUTOVER_MODE,
        "write_once": True,
        "legacy_selection_authority": False,
        "legacy_fallback_allowed": False,
        "automatic_legacy_restore_allowed": False,
        "automatic_wager_allowed": False,
        "experiment_id": experiment_id,
        "artifact_sha256": artifact_sha256,
        "gate_report_sha256": gate_report_sha256,
        "approved_at_utc": approved_at_utc,
    }
    return {"champion": champion, "cutover": cutover}
