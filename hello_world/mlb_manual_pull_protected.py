from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

mlb_ml_runtime_install_v3 = None
try:
    import mlb_ml_runtime_install_v3

    _raw_runtime_status = mlb_ml_runtime_install_v3.install()
except Exception as exc:
    _raw_runtime_status = {
        "applied": False,
        "ok": False,
        "steps": {},
        "errors": [str(exc)],
    }


_REQUIRED_RUNTIME_STEPS = {
    "accuracyTargetsSeparated",
    "legacyReliabilityOverlaySafety",
    "sourceHonestFundamentals",
    "sourceHonestFundamentalsV2",
    "legacyV1ChampionRuntimeInstalledForShadowDiagnostics",
    "legacyV1AuthorityDisabled",
    "v2ShadowManualFirst",
    "officialSemanticsFinalized",
    "exactCleanCohortVectorPatch",
    "officialFreezeBridge",
    "immutableFeatureFreeze",
    "immutableLockedStorageAuthority",
    "canonicalLockedStorageFinalizer",
    "lastPrelockPromotionAuthority",
    "canonicalProbabilityAndPersistedPrelockAuthority",
    "providerNeutralCalibrationAndActionability",
    "legacyFinalGateDisabled",
}

_WINNER_LIFECYCLE_DEFECT_SCOPE_VERSION = (
    "MLB-WINNER-LIFECYCLE-DEFECT-SCOPE-v1-release-separated"
)
_WINNER_LIFECYCLE_CONVERGENCE_VERSION = (
    "MLB-WINNER-LIFECYCLE-CONVERGENCE-v1-exact-cutoff-read-only"
)

if isinstance(_raw_runtime_status, dict):
    ML_RUNTIME_INSTALL_STATUS = dict(_raw_runtime_status)
else:
    ML_RUNTIME_INSTALL_STATUS = {
        "applied": False,
        "ok": False,
        "steps": {},
        "errors": [
            "mlb_ml_runtime_install_v3.install() returned a non-dictionary status"
        ],
    }

_runtime_errors = ML_RUNTIME_INSTALL_STATUS.get("errors")
if not isinstance(_runtime_errors, list):
    _runtime_errors = [str(_runtime_errors)] if _runtime_errors else []
ML_RUNTIME_INSTALL_STATUS["errors"] = _runtime_errors

_runtime_steps = ML_RUNTIME_INSTALL_STATUS.get("steps")
if not isinstance(_runtime_steps, dict):
    _runtime_steps = {}
    ML_RUNTIME_INSTALL_STATUS["steps"] = _runtime_steps
    ML_RUNTIME_INSTALL_STATUS["errors"].append(
        "mlb_ml_runtime_install_v3.install() returned invalid step status"
    )

_missing_runtime_steps = sorted(
    name for name in _REQUIRED_RUNTIME_STEPS if _runtime_steps.get(name) is not True
)
_expected_runtime_version = getattr(mlb_ml_runtime_install_v3, "VERSION", None)
ML_RUNTIME_INSTALL_STATUS["expectedVersion"] = _expected_runtime_version
if ML_RUNTIME_INSTALL_STATUS.get("applied") is not True:
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if ML_RUNTIME_INSTALL_STATUS.get("ok") is not True:
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if ML_RUNTIME_INSTALL_STATUS.get("errors"):
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if (
    not _expected_runtime_version
    or ML_RUNTIME_INSTALL_STATUS.get("version") != _expected_runtime_version
):
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if _missing_runtime_steps:
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
    ML_RUNTIME_INSTALL_STATUS["missingRequiredSteps"] = _missing_runtime_steps

# Do not even import the HOT candidate writer until the exact runtime has been
# installed and attested. This makes correctness independent of usercustomize.
mlb_manual_pull = None
if ML_RUNTIME_INSTALL_STATUS.get("ok") is True:
    try:
        import mlb_manual_pull as _mlb_manual_pull
        import mlb_canonical_manifest_retry_binding_patch

        manifest_retry_patch = (
            mlb_canonical_manifest_retry_binding_patch.install(_mlb_manual_pull)
        )
        if manifest_retry_patch.get("ok") is not True:
            raise RuntimeError(
                "canonical manifest retry binding patch failed: "
                + json.dumps(manifest_retry_patch, default=str, sort_keys=True)
            )
        mlb_manual_pull = _mlb_manual_pull
        ML_RUNTIME_INSTALL_STATUS["steps"][
            "canonicalManifestRetryBinding"
        ] = True
        ML_RUNTIME_INSTALL_STATUS[
            "canonicalManifestRetryBindingPatch"
        ] = manifest_retry_patch
        ML_RUNTIME_INSTALL_STATUS["candidateWriterImported"] = True
    except Exception as exc:
        ML_RUNTIME_INSTALL_STATUS["ok"] = False
        ML_RUNTIME_INSTALL_STATUS["steps"][
            "canonicalManifestRetryBinding"
        ] = False
        ML_RUNTIME_INSTALL_STATUS["candidateWriterImported"] = False
        ML_RUNTIME_INSTALL_STATUS["errors"].append(
            f"mlb_manual_pull import failed after runtime installation: {exc}"
        )
else:
    ML_RUNTIME_INSTALL_STATUS["candidateWriterImported"] = False

ADMIN_TOKEN = os.environ.get("INQSI_ADMIN_API_TOKEN", "")


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(body)
    payload["mlRuntimeInstallation"] = ML_RUNTIME_INSTALL_STATUS
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token",
            "access-control-allow-methods": "POST,OPTIONS",
        },
        "body": json.dumps(payload),
    }


def _is_scheduled(event: Dict[str, Any]) -> bool:
    return not (event.get("httpMethod") or event.get("requestContext"))


def _is_exact_audited_scheduled_pull(event: Dict[str, Any]) -> bool:
    """Identify only the immutable EventBridge input for the natural HOT pull."""

    return bool(
        set(event) == {"sport", "t", "run", "days_ahead"}
        and type(event.get("sport")) is str
        and event.get("sport") == "mlb"
        and type(event.get("t")) is str
        and event.get("t") == "HOT"
        and type(event.get("run")) is str
        and event.get("run") == "hot_pull_audited"
        and type(event.get("days_ahead")) is int
        and event.get("days_ahead") == 0
    )


def _scoped_lifecycle_defects(
    result: Dict[str, Any],
) -> Optional[tuple[bool, bool]]:
    if not isinstance(result, dict):
        return None
    winner_defect = result.get("winnerLifecycleOperationalDefect")
    release_defect = result.get("releasePlayabilityOperationalDefect")
    expected_scopes = []
    if winner_defect is True:
        expected_scopes.append("WINNER_LIFECYCLE")
    if release_defect is True:
        expected_scopes.append("RELEASE_PLAYABILITY")
    if not (
        result.get("operationalDefectScopeVersion")
        == _WINNER_LIFECYCLE_DEFECT_SCOPE_VERSION
        and isinstance(winner_defect, bool)
        and isinstance(release_defect, bool)
        and result.get("operationalDefectScopes") == expected_scopes
        and (result.get("operationalDefect") is True) == bool(expected_scopes)
    ):
        return None
    return winner_defect, release_defect


def _canonical_locked_storage_failures(
    result: Dict[str, Any], game_date: str
) -> list[str]:
    """Validate canonical lock persistence independently of defect scoping."""

    suffix = game_date or "unknown"
    failures = []
    try:
        candidate_count = int(
            result.get("canonicalLockedStorageCandidateCount") or 0
        )
        if candidate_count < 0:
            raise ValueError("negative canonical candidate count")
    except Exception:
        candidate_count = -1
        failures.append(f"canonical_locked_storage_contract_invalid:{suffix}")

    storage_errors = result.get("canonicalLockedStorageErrors")
    if bool(storage_errors):
        failures.append(f"canonical_locked_storage_errors:{suffix}")

    if candidate_count > 0:
        if result.get("canonicalLockedStorageComplete") is not True:
            failures.append(f"canonical_locked_storage_incomplete:{suffix}")
        try:
            stored_count = int(result.get("canonicalLockedStoredCount") or 0)
        except Exception:
            stored_count = -1
        if stored_count != candidate_count:
            failures.append(f"canonical_locked_storage_count_mismatch:{suffix}")
    return failures


_DUE = "LOCK_DUE_CANONICAL_MISSING"
_OPEN = "OPEN_PRE_LOCK"
_MISSED = {"MISSED_LOCK", "MISSED_NOT_BACKFILLED"}
_GRACE = timedelta(minutes=5)
_EASTERN = ZoneInfo("America/New_York")
_PROVIDER_MANIFEST_VERSION = "INQSI-PROVIDER-SCHEDULE-MANIFEST-v1"
_OFFICIAL_SCHEDULE_AUTHORITY_VERSION = (
    "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
)
_CANONICAL_STORAGE_VERSION = (
    "MLB-LOCKED-PREDICTION-STORAGE-FINALIZER-v6-effective-schedule-lifecycle"
)
_CANONICAL_STORAGE_AUTHORITY = (
    "consistent-read verified immutable T-minus-45 stage"
)
_PUBLIC_PER_GAME_AUTHORITY_VERSION = (
    "MLB-LAST-PRELOCK-PROMOTION-AUTHORITY-v1-canonical-read-overlay"
)


def _exact_count(value: Any) -> Optional[int]:
    return value if type(value) is int and value >= 0 else None


def _nonempty_string(value: Any) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


def _sha256_string(value: Any) -> bool:
    return bool(
        _nonempty_string(value)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _date_string(value: Any) -> bool:
    if not _nonempty_string(value):
        return False
    try:
        return datetime.fromisoformat(value).date().isoformat() == value
    except Exception:
        return False


def _tags(row: Dict[str, Any]) -> Optional[set[str]]:
    values = row.get("tags")
    if (
        not isinstance(values, list)
        or any(not _nonempty_string(value) for value in values)
        or len(values) != len(set(values))
    ):
        return None
    return set(values)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: Any) -> Optional[datetime]:
    if not _nonempty_string(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_identity(value: Any) -> str:
    if not _nonempty_string(value):
        return ""
    if value.startswith(("provider:", "key:", "teams:")):
        return value
    return f"provider:{value}"


def _row_identity(row: Dict[str, Any]) -> str:
    # Bound identity fields are authoritative. providerEventId is only a
    # fallback because it may retain a different source-provider alias.
    bound = {
        _canonical_identity(row.get(field))
        for field in ("gameId", "game_id", "gameIdentity", "game_identity")
        if row.get(field) not in (None, "")
    }
    if len(bound) == 1 and "" not in bound:
        return next(iter(bound))
    if bound:
        return ""
    return _canonical_identity(
        row.get("providerEventId") or row.get("provider_event_id")
    )


def _manifest(result: Dict[str, Any]) -> Optional[tuple[str, ...]]:
    lock = result.get("slatePredictionLock")
    identities = lock.get("manifestGameIdentities") if isinstance(lock, dict) else None
    game_date = result.get("game_date_et")
    slate_date = result.get("slate_date")
    if (
        not isinstance(lock, dict)
        or not _date_string(game_date)
        or slate_date != game_date
        or not isinstance(identities, list)
        or not identities
        or any(
            not _nonempty_string(value)
            or not value.startswith(("provider:", "key:", "teams:"))
            for value in identities
        )
        or len(set(identities)) != len(identities)
    ):
        return None
    observed = _aware_utc(lock.get("providerManifestObservedAtUtc"))
    pull_id = lock.get("providerManifestPullId")
    expected_pk = f"PROVIDER_MANIFEST#mlb#{game_date}"
    expected_sk = (
        f"OBSERVED#{observed.isoformat()}#PULL#{pull_id}"
        if observed is not None and _nonempty_string(pull_id)
        else None
    )
    flags = (
        "providerManifestValidated",
        "providerManifestImmutable",
        "providerManifestFullProviderSchedule",
        "officialScheduleBacked",
        "officialScheduleAuthoritativeStartTimes",
        "durableRosterImmutableReadbackVerified",
    )
    return (
        tuple(identities)
        if lock.get("providerManifestVersion") == _PROVIDER_MANIFEST_VERSION
        and lock.get("providerManifestPk") == expected_pk
        and lock.get("providerManifestSk") == expected_sk
        and _sha256_string(lock.get("providerManifestFingerprint"))
        and lock.get("officialScheduleAuthorityVersion")
        == _OFFICIAL_SCHEDULE_AUTHORITY_VERSION
        and _sha256_string(lock.get("officialScheduleAuthorityFingerprint"))
        and _exact_count(lock.get("manifestGameCount")) == len(identities)
        and _exact_count(lock.get("verifiedFullSlateGameCount"))
        == len(identities)
        and _exact_count(lock.get("officialScheduleGameCount"))
        == len(identities)
        and all(lock.get(field) is True for field in flags)
        else None
    )


def _manifest_token(result: Dict[str, Any]) -> Optional[tuple[Any, ...]]:
    identities = _manifest(result)
    lock = result.get("slatePredictionLock")
    if identities is None or not isinstance(lock, dict):
        return None
    return (
        identities,
        lock.get("providerManifestVersion"),
        lock.get("providerManifestPk"),
        lock.get("providerManifestSk"),
        lock.get("providerManifestFingerprint"),
        lock.get("providerManifestObservedAtUtc"),
        lock.get("providerManifestPullId"),
        lock.get("officialScheduleAuthorityVersion"),
        lock.get("officialScheduleAuthorityFingerprint"),
    )


def _clean_lifecycle(
    result: Dict[str, Any], *, require_storage: bool = False
) -> bool:
    lock = result.get("slatePredictionLock")
    coverage = result.get("slateCoverage")
    if not isinstance(lock, dict) or not isinstance(coverage, dict):
        return False
    storage_clean = (
        result.get("preLockStorageErrors") == []
        and result.get("canonicalLockedStorageErrors") == {}
        if require_storage
        else True
    )
    checks = (
        result.get("invalidTerminalLifecycleRows") == {},
        result.get("invalidPlayabilityReleaseRows") == {},
        lock.get("canonicalReadError") is None,
        lock.get("invalidCanonicalRows") == {},
        lock.get("invalidLifecycleStatusRows") == {},
        lock.get("invalidTerminalLifecycleRows") == {},
        lock.get("invalidPlayabilityReleaseRows") == {},
        coverage.get("canonicalReadError") is None,
        coverage.get("invalidCanonicalRows") == {},
        coverage.get("invalidLifecycleStatusRows") == {},
        coverage.get("invalidPersistedPrelockRows") == {},
        coverage.get("ambiguousCurrentPredictionIdentities") == [],
        coverage.get("missingLifecycleDisplayGameIdentities") == [],
        coverage.get("extraCurrentPredictionIdentities") == [],
        coverage.get("missingGameIdentities") == [],
        coverage.get("missingWinnerPredictionGameIdentities") == [],
    )
    return storage_clean and all(checks)


def _due_contract(
    result: Dict[str, Any], *, require_storage: bool
) -> Optional[tuple[str, ...]]:
    if not isinstance(result, dict):
        return None
    required = [
        "allGamesPredicted",
        "displayStatusCoverageComplete",
        "lifecycleCoverageComplete",
    ]
    if require_storage:
        required += [
            "preLockStorageLifecycleAware",
            "preLockStorageComplete",
            "preLockStorageDispositionComplete",
        ]
    manifest = _manifest(result)
    count = _exact_count(result.get("gameCount"))
    slate = result.get("slate_date")
    declared_date = result.get("game_date_et")
    if (
        result.get("ok") is not True
        or _scoped_lifecycle_defects(result) != (True, False)
        or any(result.get(field) is not True for field in required)
        or not manifest
        or count != len(manifest)
        or not _date_string(slate)
        or declared_date != slate
        or not _clean_lifecycle(result, require_storage=require_storage)
    ):
        return None
    if require_storage:
        candidates = _exact_count(result.get("preLockStorageCandidateCount"))
        if (
            candidates is None
            or _exact_count(result.get("preLockStoredCount")) != candidates
            or _exact_count(result.get("preLockStorageDispositionCount")) != count
            or _exact_count(result.get("preLockStorageRowCount")) != count
            or _canonical_locked_storage_failures(
                result, str(result.get("game_date_et") or "")
            )
        ):
            return None
    lock = result["slatePredictionLock"]
    coverage = result["slateCoverage"]
    pending = lock.get("pendingCanonicalStatuses")
    if not isinstance(pending, dict) or not pending:
        return None
    if any(value not in {_DUE, _OPEN} for value in pending.values()):
        return None
    due = tuple(identity for identity, value in pending.items() if value == _DUE)
    if not due:
        return None
    canonical_count = len(manifest) - len(pending)
    checks = (
        lock.get("lockStatus") == _DUE,
        lock.get("canonicalReadOperational") is True,
        _exact_count(lock.get("lockMinutesBeforeEachGame")) == 45,
        _exact_count(lock.get("lockDueCanonicalMissingCount")) == len(due),
        _exact_count(lock.get("missedLockCount")) == 0,
        coverage.get("canonicalReadOperational") is True,
        coverage.get("pendingCanonicalStatuses") == pending,
        _exact_count(coverage.get("lockDueCanonicalMissingCount")) == len(due),
        _exact_count(coverage.get("missedLockCount")) == 0,
        set(pending).issubset(manifest),
        _exact_count(lock.get("canonicalLockedGameCount")) == canonical_count,
        _exact_count(coverage.get("canonicalLockedGameCount"))
        == canonical_count,
        _exact_count(lock.get("pendingCanonicalGameCount")) == len(pending),
        _exact_count(coverage.get("pendingCanonicalGameCount")) == len(pending),
        lock.get("canonicalCoverageComplete") is False,
        coverage.get("canonicalCoverageComplete") is False,
        lock.get("canonicalPredictionComplete") is False,
        coverage.get("canonicalPredictionComplete") is False,
        result.get("canonicalPredictionComplete") is False,
    )
    if not all(checks):
        return None
    if require_storage:
        open_count = sum(value == _OPEN for value in pending.values())
        stored_count = open_count + canonical_count
        class_counts = (
            _exact_count(result.get("preLockStorageCandidateCount"))
            == open_count,
            _exact_count(result.get("preLockStoredCount")) == open_count,
            _exact_count(result.get("preLockStorageLifecycleSkippedCount"))
            == len(due),
            result.get("preLockStorageLifecycleSkippedStatuses") == [_DUE],
            _exact_count(result.get("canonicalLockedStorageCandidateCount"))
            == canonical_count,
            _exact_count(result.get("canonicalLockedStoredCount"))
            == canonical_count,
            result.get("canonicalLockedStorageErrors") == {},
            result.get("canonicalLockedStorageComplete")
            is (canonical_count > 0),
            _exact_count(
                result.get("canonicalLockedStorageSuppressedUnauthorizedCount")
            )
            == 0,
            result.get("canonicalLockedStorageSuppressedEarlyWrites") is True,
            result.get("canonicalLockedStorageVersion")
            == _CANONICAL_STORAGE_VERSION,
            result.get("canonicalLockedStorageAuthority")
            == _CANONICAL_STORAGE_AUTHORITY,
            _exact_count(result.get("storedCount")) == stored_count,
            result.get("stored") is (stored_count > 0),
        )
        if not all(class_counts):
            return None
    return due


def _row_map(
    result: Dict[str, Any], field: str, manifest: tuple[str, ...]
) -> Optional[Dict[str, Dict[str, Any]]]:
    rows = result.get(field)
    if not isinstance(rows, list) or len(rows) != len(manifest):
        return None
    mapped: Dict[str, Dict[str, Any]] = {}
    provider_aliases: set[str] = set()
    for row in rows:
        identity = _row_identity(row) if isinstance(row, dict) else ""
        if not identity or identity in mapped:
            return None
        raw_aliases = [
            row.get(alias_field)
            for alias_field in ("providerEventId", "provider_event_id")
            if row.get(alias_field) not in (None, "")
        ]
        aliases = {_canonical_identity(value) for value in raw_aliases}
        if "" in aliases or len(aliases) > 1:
            return None
        if aliases:
            alias = next(iter(aliases))
            if (
                (alias in manifest and alias != identity)
                or alias in provider_aliases
            ):
                return None
            provider_aliases.add(alias)
        mapped[identity] = row
    return mapped if set(mapped) == set(manifest) else None


def _row_timing(
    result: Dict[str, Any], row: Dict[str, Any]
) -> Optional[tuple[datetime, datetime]]:
    per_game = row.get("perGameCanonicalLock")
    if not isinstance(per_game, dict):
        return None
    scheduled = _aware_utc(row.get("scheduledLockAtUtc"))
    canonical = _aware_utc(per_game.get("lockAtUtc"))
    start = _aware_utc(row.get("commenceTime"))
    slate = result.get("game_date_et")
    if (
        not _date_string(slate)
        or result.get("slate_date") != slate
        or scheduled is None
        or canonical != scheduled
        or start is None
        or start - timedelta(minutes=45) != scheduled
        or start.astimezone(_EASTERN).date().isoformat() != slate
    ):
        return None
    return scheduled, start


def _timing_map(
    result: Dict[str, Any],
) -> Optional[Dict[str, tuple[datetime, datetime]]]:
    manifest = _manifest(result)
    if not manifest:
        return None
    timings: Dict[str, tuple[datetime, datetime]] = {}
    for field in ("predictions", "perGameStatus"):
        rows = _row_map(result, field, manifest)
        if rows is None:
            return None
        for identity, row in rows.items():
            timing = _row_timing(result, row)
            if not timing or timings.get(identity, timing) != timing:
                return None
            timings[identity] = timing
    return timings if set(timings) == set(manifest) else None


def _open_row(
    result: Dict[str, Any],
    row: Dict[str, Any],
    now: datetime,
    *,
    display_card: bool = False,
) -> bool:
    timing = _row_timing(result, row)
    per_game = row.get("perGameCanonicalLock") or {}
    tags = _tags(row)
    conflicting_tags = {
        "FINAL_LOCKED",
        "OFFICIAL_PREDICTION",
        "OFFICIAL_LOCKED_PREDICTION",
        "CANONICAL_PER_GAME_LOCK",
        _DUE,
        "PER_GAME_CANONICAL_LOCK_MISSING",
        *_MISSED,
    }
    return bool(
        timing
        and tags is not None
        and row.get("lockStatus") == _OPEN
        and row.get("officialPredictionStatus")
        == "PRE_LOCK_PLATFORM_PREDICTION"
        and row.get("recommendationStatus") == "PRE_LOCK_PREDICTION"
        and per_game.get("status") == _OPEN
        and per_game.get("canonical") is False
        and row.get("locked") is False
        and row.get("lockedPrediction") is False
        and row.get("lockOutcomeRecorded") is False
        and row.get("canonical") is False
        and row.get("officialPrediction") is False
        and row.get("officialPick") is False
        and (
            "isOfficialDisplayPick" not in row
            if display_card
            else row.get("isOfficialDisplayPick") is False
        )
        and (
            "displayPrediction" not in row
            if display_card
            else row.get("displayPrediction") is True
        )
        and {"PRE_LOCK_PREDICTION", "PER_GAME_CANONICAL_LOCK_PENDING"}
        .issubset(tags)
        and not conflicting_tags.intersection(tags)
        and now < timing[0] < timing[1]
    )


def _due_row(
    result: Dict[str, Any],
    row: Dict[str, Any],
    *,
    display_card: bool = False,
) -> bool:
    per_game = row.get("perGameCanonicalLock") or {}
    tags = _tags(row)
    conflicting_tags = {
        "FINAL_LOCKED",
        "OFFICIAL_PREDICTION",
        "OFFICIAL_LOCKED_PREDICTION",
        "CANONICAL_PER_GAME_LOCK",
        _OPEN,
        *_MISSED,
    }
    return bool(
        _row_timing(result, row)
        and tags is not None
        and row.get("lockStatus") == _DUE
        and row.get("officialPredictionStatus") == _DUE
        and row.get("recommendationStatus") == _DUE
        and per_game.get("status") == _DUE
        and per_game.get("canonical") is False
        and row.get("locked") is False
        and row.get("lockedPrediction") is False
        and row.get("lockOutcomeRecorded") is False
        and row.get("canonical") is False
        and row.get("officialPrediction") is False
        and row.get("officialPick") is False
        and (
            "isOfficialDisplayPick" not in row
            if display_card
            else row.get("isOfficialDisplayPick") is False
        )
        and (
            "displayPrediction" not in row
            if display_card
            else row.get("displayPrediction") is True
        )
        and {_DUE, "PER_GAME_CANONICAL_LOCK_MISSING"}.issubset(tags)
        and not conflicting_tags.intersection(tags)
    )


def _canonical_row(
    result: Dict[str, Any],
    row: Dict[str, Any],
    *,
    display_card: bool = False,
) -> bool:
    per_game = row.get("perGameCanonicalLock") or {}
    tags = _tags(row)
    return bool(
        _row_timing(result, row)
        and tags is not None
        and row.get("lockStatus") == "LOCKED_CANONICAL"
        and row.get("officialPredictionStatus")
        == "OFFICIAL_LOCKED_PREDICTION"
        and per_game.get("status") == "OFFICIAL_LOCKED_PREDICTION"
        and per_game.get("canonical") is True
        and row.get("locked") is True
        and row.get("lockedPrediction") is True
        and row.get("lockOutcomeRecorded") is True
        and row.get("canonical") is True
        and row.get("officialPrediction") is True
        and row.get("officialPick") is True
        and (
            "isOfficialDisplayPick" not in row
            if display_card
            else row.get("isOfficialDisplayPick") is True
        )
        and {
            "FINAL_LOCKED",
            "OFFICIAL_PREDICTION",
            "OFFICIAL_LOCKED_PREDICTION",
            "CANONICAL_PER_GAME_LOCK",
        }.issubset(tags)
        and not (
            {_DUE, "PER_GAME_CANONICAL_LOCK_MISSING"} | _MISSED
        ).intersection(tags)
    )


def _due_cutoff(
    result: Dict[str, Any], due: tuple[str, ...], now: datetime
) -> Optional[datetime]:
    manifest = _manifest(result)
    if not manifest:
        return None
    pending = result["slatePredictionLock"].get("pendingCanonicalStatuses")
    if (
        not isinstance(pending, dict)
        or not set(pending).issubset(manifest)
        or any(value not in {_DUE, _OPEN} for value in pending.values())
    ):
        return None
    expected_due = set(due)
    if {key for key, value in pending.items() if value == _DUE} != expected_due:
        return None
    cutoff = None
    observed_timing: Dict[str, tuple[datetime, datetime]] = {}
    for field in ("predictions", "perGameStatus"):
        rows = _row_map(result, field, manifest)
        if rows is None:
            return None
        for identity, row in rows.items():
            timing = _row_timing(result, row)
            if not timing or observed_timing.get(identity, timing) != timing:
                return None
            observed_timing[identity] = timing
            expected_status = pending.get(identity)
            if expected_status is None:
                if (
                    not _canonical_row(
                        result,
                        row,
                        display_card=field == "perGameStatus",
                    )
                    or timing[0] > now
                ):
                    return None
                continue
            if expected_status == _OPEN:
                if not _open_row(
                    result,
                    row,
                    now,
                    display_card=field == "perGameStatus",
                ):
                    return None
                continue
            if not _due_row(
                result,
                row,
                display_card=field == "perGameStatus",
            ):
                return None
            scheduled, start = timing
            if (
                not scheduled <= now <= scheduled + _GRACE
                or start <= now
                or cutoff not in (None, scheduled)
            ):
                return None
            cutoff = scheduled
    return cutoff


def _current_observation(
    payload: Dict[str, Any],
    initial: Dict[str, Any],
    game_date: str,
    cutoff: datetime,
    now: datetime,
) -> Optional[datetime]:
    history = payload.get("canonical_pull_history")
    if (
        not isinstance(history, list)
        or len(history) != 1
        or not isinstance(history[0], dict)
        or history[0].get("game_date_et") != game_date
    ):
        return None
    row = history[0]
    observed = _aware_utc(payload.get("asof"))
    if observed is None:
        return None
    expected_pk = f"PULLS#mlb#{game_date}"
    count = _exact_count(initial.get("gameCount"))
    token = _manifest_token(initial)
    if count is None or token is None:
        return None
    (
        _,
        manifest_version,
        manifest_pk,
        manifest_sk,
        manifest_fingerprint,
        manifest_observed_at,
        manifest_pull_id,
        official_version,
        official_fingerprint,
    ) = token
    manifests = payload.get("provider_schedule_manifests")
    if (
        not isinstance(manifests, list)
        or len(manifests) != 1
        or not isinstance(manifests[0], dict)
        or manifests[0].get("game_date_et") != game_date
    ):
        return None
    manifest = manifests[0]
    checks = (
        _exact_count(payload.get("count")) == count,
        _exact_count(payload.get("days_ahead")) == 0,
        payload.get("providerScheduleManifestComplete") is True,
        manifest.get("ok") is True,
        manifest.get("immutable") is True,
        manifest.get("fullProviderSchedule") is True,
        manifest.get("boundToCanonicalPull") is True,
        manifest.get("officialScheduleBacked") is True,
        manifest.get("officialScheduleAuthorityBound") is True,
        _exact_count(manifest.get("gameCount")) == count,
        _exact_count(manifest.get("officialScheduleGameCount")) == count,
        manifest.get("version") == manifest_version,
        manifest.get("pk") == manifest_pk,
        manifest.get("sk") == manifest_sk,
        manifest.get("fingerprint") == manifest_fingerprint,
        manifest.get("officialScheduleAuthorityFingerprint")
        == official_fingerprint,
        manifest.get("officialScheduleAuthorityVersion") == official_version,
        row.get("ok") is True,
        row.get("error") is None,
        row.get("providerManifestBound") is True,
        row.get("providerManifestImmutable") is True,
        row.get("providerManifestFullSchedule") is True,
        row.get("providerManifestValidationErrors") == [],
        row.get("canonicalManifestValidatedAgainstPersistedPull") is True,
        row.get("retryReturnedExistingCanonicalPull") is False,
        row.get("sameSlotRetryAuthorityRebound") is False,
        row.get("officialScheduleAuthorityBound") is True,
        row.get("officialScheduleBacked") is True,
        row.get("providerManifestPk") == manifest_pk,
        row.get("providerManifestSk") == manifest_sk,
        row.get("providerManifestVersion") == manifest_version,
        row.get("providerManifestFingerprint") == manifest_fingerprint,
        row.get("officialScheduleAuthorityVersion") == official_version,
        row.get("officialScheduleAuthorityFingerprint") == official_fingerprint,
        _exact_count(row.get("games")) == count,
        _exact_count(row.get("providerManifestGameCount")) == count,
        _exact_count(row.get("officialScheduleGameCount")) == count,
        _aware_utc(row.get("canonicalSlotStartUtc")) == cutoff,
        row.get("canonicalPullPk") == expected_pk,
        row.get("pk") == expected_pk,
        row.get("canonicalPullSk") == f"PULL#SLOT#{cutoff.isoformat()}",
        _aware_utc(row.get("canonicalPulledAtUtc")) == observed,
        _aware_utc(manifest_observed_at) == observed,
        row.get("pull_id") == manifest_pull_id,
        row.get("canonicalPullId") == manifest_pull_id,
        manifest_sk
        == f"OBSERVED#{observed.isoformat()}#PULL#{manifest_pull_id}",
        cutoff <= observed <= now <= cutoff + _GRACE,
    )
    return observed if all(checks) else None


def _healthy_read(
    initial: Dict[str, Any],
    persisted: Any,
    due: tuple[str, ...],
    cutoff: datetime,
    now: datetime,
) -> bool:
    if not isinstance(persisted, dict):
        return False
    game_date = initial.get("game_date_et")
    manifest = _manifest(initial)
    lock = persisted.get("slatePredictionLock")
    coverage = persisted.get("slateCoverage")
    flags = (
        "allGamesPredicted",
        "displayStatusCoverageComplete",
        "lifecycleCoverageComplete",
    )
    initial_timing = _timing_map(initial)
    if (
        not _date_string(game_date)
        or persisted.get("ok") is not True
        or persisted.get("game_date_et") != game_date
        or persisted.get("slate_date") != game_date
        or _scoped_lifecycle_defects(persisted) != (False, False)
        or persisted.get("operationalDefect") is not False
        or any(persisted.get(field) is not True for field in flags)
        or not manifest
        or _manifest_token(persisted) != _manifest_token(initial)
        or not initial_timing
        or _timing_map(persisted) != initial_timing
        or _exact_count(persisted.get("gameCount")) != len(manifest or ())
        or not isinstance(lock, dict)
        or lock.get("canonicalReadOperational") is not True
        or lock.get("canonicalReadError") is not None
        or _exact_count(lock.get("lockDueCanonicalMissingCount")) != 0
        or _exact_count(lock.get("missedLockCount")) != 0
        or not isinstance(coverage, dict)
        or coverage.get("canonicalReadOperational") is not True
        or _exact_count(coverage.get("lockDueCanonicalMissingCount")) != 0
        or _exact_count(coverage.get("missedLockCount")) != 0
        or not _clean_lifecycle(persisted)
    ):
        return False
    pending = lock.get("pendingCanonicalStatuses")
    initial_pending = initial["slatePredictionLock"].get(
        "pendingCanonicalStatuses"
    )
    expected_open = {
        identity: _OPEN
        for identity, value in (initial_pending or {}).items()
        if value == _OPEN
    }
    if (
        not isinstance(initial_pending, dict)
        or not isinstance(pending, dict)
        or pending != expected_open
        or not set(pending).issubset(manifest)
    ):
        return False
    if coverage.get("pendingCanonicalStatuses") != pending:
        return False
    canonical_count = None
    observed_timing: Dict[str, tuple[datetime, datetime]] = {}
    for field in ("predictions", "perGameStatus"):
        rows = _row_map(persisted, field, manifest)
        if rows is None:
            return False
        field_canonical_count = 0
        for identity, row in rows.items():
            timing = _row_timing(persisted, row)
            if not timing or observed_timing.get(identity, timing) != timing:
                return False
            observed_timing[identity] = timing
            if identity in pending:
                if not _open_row(
                    persisted,
                    row,
                    now,
                    display_card=field == "perGameStatus",
                ):
                    return False
                continue
            field_canonical_count += 1
            if (
                not _canonical_row(
                    persisted,
                    row,
                    display_card=field == "perGameStatus",
                )
                or timing[0] > now
                or (
                    identity in due
                    and (
                        timing[0] != cutoff
                        or timing[1] != cutoff + timedelta(minutes=45)
                    )
                )
            ):
                return False
        if canonical_count not in (None, field_canonical_count):
            return False
        canonical_count = field_canonical_count
    if canonical_count is None or any(identity in pending for identity in due):
        return False
    complete = not pending
    expected_lock_status = (
        "COMPLETE_MANIFEST_ALL_CANONICAL"
        if complete
        else "PARTIAL_PER_GAME_CANONICAL"
    )
    count_checks = (
        _exact_count(lock.get("canonicalLockedGameCount")) == canonical_count,
        _exact_count(coverage.get("canonicalLockedGameCount")) == canonical_count,
        _exact_count(lock.get("pendingCanonicalGameCount")) == len(pending),
        _exact_count(coverage.get("pendingCanonicalGameCount")) == len(pending),
        lock.get("lockStatus") == expected_lock_status,
        lock.get("canonicalCoverageComplete") is complete,
        coverage.get("canonicalCoverageComplete") is complete,
        lock.get("canonicalPredictionComplete") is complete,
        coverage.get("canonicalPredictionComplete") is complete,
        persisted.get("canonicalPredictionComplete") is complete,
    )
    if not all(count_checks):
        return False
    return True


def _read_only_attestation(result: Any) -> bool:
    coverage = result.get("slateCoverage") if isinstance(result, dict) else None
    return bool(
        isinstance(coverage, dict)
        and result.get("readAuthority")
        == "persisted_prelock_and_canonical_locked_only"
        and _exact_count(coverage.get("canonicalReadAuthorityWriteCount")) == 0
        and coverage.get("prelockPredictionAuthority")
        == "validated_immutable_pregame_snapshot"
        and coverage.get("publicPrelockRecomputed") is False
        and coverage.get("storeRequested") is False
    )


_STORAGE_FIELDS = (
    "stored",
    "storedCount",
    "preLockStoredCount",
    "preLockStorageCandidateCount",
    "preLockStorageComplete",
    "preLockStorageErrors",
    "preLockStorageLifecycleAware",
    "preLockStorageLifecycleSkippedCount",
    "preLockStorageLifecycleSkippedStatuses",
    "preLockStorageDispositionCount",
    "preLockStorageRowCount",
    "preLockStorageDispositionComplete",
    "canonicalLockedStorageCandidateCount",
    "canonicalLockedStoredCount",
    "canonicalLockedStorageErrors",
    "canonicalLockedStorageVersion",
    "canonicalLockedStorageComplete",
    "canonicalLockedStorageSuppressedUnauthorizedCount",
    "canonicalLockedStorageAuthority",
    "canonicalLockedStorageSuppressedEarlyWrites",
)


def _current_cutoff_convergence_response(
    event: Dict[str, Any], response: Dict[str, Any], failures: list[str]
) -> Optional[Dict[str, Any]]:
    if not _is_exact_audited_scheduled_pull(event):
        return None
    try:
        body = response.get("body")
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        return None
    if (
        type(response.get("statusCode")) is not int
        or not 200 <= response["statusCode"] < 300
        or payload.get("ok") is not True
        or payload.get("live_pull_ok") is not True
        or payload.get("fallback_used") is not False
        or payload.get("sport") != "mlb"
        or payload.get("t") != "HOT"
        or payload.get("run") != "hot_pull_audited"
    ):
        return None
    results = payload.get("game_winner_predictions")
    if (
        not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], dict)
    ):
        return None
    initial = results[0]
    game_date = initial.get("game_date_et")
    if not _date_string(game_date):
        return None
    if failures != [f"winner_prediction_failed:{game_date}"]:
        return None
    now = _utc_now()
    if now.tzinfo is None or now.utcoffset() is None:
        return None
    now = now.astimezone(timezone.utc)
    due = _due_contract(initial, require_storage=True)
    cutoff = _due_cutoff(initial, due, now) if due else None
    observed = (
        _current_observation(payload, initial, game_date, cutoff, now)
        if cutoff
        else None
    )
    if cutoff is None or observed is None:
        return None

    engine = getattr(mlb_manual_pull, "mlb_game_winner_engine", None)
    reader = getattr(engine, "read_persisted_predictions", None)
    if (
        not callable(reader)
        or getattr(
            engine,
            "_INQSI_MLB_PERSISTED_PRELOCK_PUBLIC_AUTHORITY_ENABLED",
            False,
        )
        is not True
        or getattr(engine, "MLB_PUBLIC_PER_GAME_AUTHORITY_VERSION", None)
        != _PUBLIC_PER_GAME_AUTHORITY_VERSION
    ):
        return None
    try:
        persisted = reader(game_date, store=False, limit=500)
    except Exception:
        return None
    if not _read_only_attestation(persisted):
        return None
    now_after = _utc_now()
    if now_after.tzinfo is None or now_after.utcoffset() is None:
        return None
    now_after = now_after.astimezone(timezone.utc)
    if not observed <= now <= now_after <= cutoff + _GRACE:
        return None
    converged = _healthy_read(initial, persisted, due, cutoff, now_after)
    persisted_due = (
        _due_contract(persisted, require_storage=False)
        if not converged and isinstance(persisted, dict)
        else None
    )
    if not converged and (
        set(persisted_due or ()) != set(due)
        or _manifest_token(persisted) != _manifest_token(initial)
        or _timing_map(persisted) != _timing_map(initial)
        or persisted.get("game_date_et") != game_date
        or persisted.get("slate_date") != game_date
        or _due_cutoff(persisted, persisted_due, now_after) != cutoff
    ):
        return None

    evidence = {
        "version": _WINNER_LIFECYCLE_CONVERGENCE_VERSION,
        "evidenceScope": "SUPPLEMENTAL_PERSISTED_READ",
        "status": (
            "CONVERGED_BY_IMMEDIATE_PERSISTED_READ"
            if converged
            else "CONVERGENCE_PENDING_CURRENT_CUTOFF"
        ),
        "reason": _DUE,
        "currentCanonicalSlotUtc": cutoff.isoformat(),
        "payloadObservedAtUtc": observed.isoformat(),
        "wrapperCheckedAtUtc": now_after.isoformat(),
        "dueGameIdentities": list(due),
        "converged": converged,
        "convergencePending": not converged,
        "nonfatalForCurrentPull": not converged,
        "hardFailIfDueOnLaterSlot": True,
        "storageAndDispositionComplete": True,
        "initialWinnerLifecycleExecuted": True,
        "operationalDefectPreserved": not converged,
        "readOnly": True,
        "winnerWriterInvoked": False,
        "externalOddsFetched": False,
        "candidateWritten": False,
    }
    annotated = dict(initial)
    if converged:
        annotated.update(persisted)
        annotated.update(
            {field: initial[field] for field in _STORAGE_FIELDS if field in initial}
        )
        annotated["game_date_et"] = game_date
    annotated["winnerLifecycleConvergence"] = evidence
    payload = dict(payload)
    payload["game_winner_predictions"] = [annotated]
    payload["winnerLifecycleConvergence"] = evidence
    out = dict(response)
    out["body"] = json.dumps(payload, default=str)
    return _attach_runtime_status(out)




def _winner_lifecycle_health(payload: Dict[str, Any]) -> Dict[str, Any]:
    results = [
        result
        for result in (payload.get("game_winner_predictions") or [])
        if isinstance(result, dict)
    ]
    scoped = [
        (result, defects)
        for result in results
        if (defects := _scoped_lifecycle_defects(result)) is not None
    ]
    winner_defect_dates = sorted({
        str(result.get("game_date_et") or "unknown")
        for result, defects in scoped
        if defects[0]
    })
    release_defect_dates = sorted({
        str(result.get("game_date_et") or "unknown")
        for result, defects in scoped
        if defects[1]
    })
    scope_complete = len(scoped) == len(results)
    return {
        "version": _WINNER_LIFECYCLE_DEFECT_SCOPE_VERSION,
        "resultCount": len(results),
        "scopedResultCount": len(scoped),
        "scopeComplete": scope_complete,
        "winnerLifecycleHealthy": (
            not winner_defect_dates if scope_complete else None
        ),
        "winnerLifecycleOperationalDefectDates": winner_defect_dates,
        "releasePlayabilityHealthy": (
            not release_defect_dates if scope_complete else None
        ),
        "releasePlayabilityOperationalDefectDates": release_defect_dates,
        "releasePlayabilityFailClosed": True,
    }


def _runtime_failure(event: Dict[str, Any]) -> Dict[str, Any]:
    body = {
        "ok": False,
        "sport": "mlb",
        "error": "MLB_ML_PULL_RUNTIME_NOT_READY",
        "status": ML_RUNTIME_INSTALL_STATUS,
    }
    if _is_scheduled(event):
        raise RuntimeError(
            "MLB_SCHEDULED_PULL_PREREQUISITE_FAILED:"
            + json.dumps(body, default=str, sort_keys=True)
        )
    return _resp(500, body)


def _attach_runtime_status(response: Any) -> Any:
    if not isinstance(response, dict):
        return response
    out = dict(response)
    body = out.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        payload = {"rawBody": body}
    if isinstance(payload, dict):
        lifecycle_health = _winner_lifecycle_health(payload)
        payload = {
            "winnerLifecycleHealth": lifecycle_health,
            **{
                key: value
                for key, value in payload.items()
                if key != "winnerLifecycleHealth"
            },
        }
        payload["mlRuntimeInstallation"] = ML_RUNTIME_INSTALL_STATUS
        out["body"] = json.dumps(payload, default=str)
    return out


def _raise_scheduled_delegate_failure(event: Dict[str, Any], response: Any) -> None:
    """Make EventBridge observe a failed delegated HOT pull as a failed invocation.

    The storage contract is lifecycle-aware. Open pre-lock predictions must be
    durably written, while post-cutoff status rows such as ``MISSED_LOCK`` or
    ``LOCKED_NO_PREDICTION_DATA`` are evidence, not prediction candidates.
    """

    if not _is_scheduled(event) or not isinstance(response, dict):
        return
    body = response.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        payload = {"rawBody": body}
    try:
        status_code = int(response.get("statusCode") or 200)
    except Exception:
        status_code = 500
    candidate_failures = []
    try:
        provider_game_count = int(payload.get("count") or 0)
    except Exception:
        provider_game_count = 0
    if provider_game_count > 0:
        manifests = payload.get("provider_schedule_manifests")
        manifest_counts: Dict[str, int] = {}
        if not isinstance(manifests, list) or not manifests:
            candidate_failures.append("provider_schedule_manifest_missing")
        else:
            for manifest in manifests:
                if not isinstance(manifest, dict):
                    candidate_failures.append("provider_schedule_manifest_invalid")
                    continue
                game_date = str(manifest.get("game_date_et") or "")
                try:
                    manifest_count = int(manifest.get("gameCount") or 0)
                except Exception:
                    manifest_count = -1
                if not game_date or game_date in manifest_counts:
                    candidate_failures.append("provider_schedule_manifest_date_invalid_or_duplicate")
                else:
                    manifest_counts[game_date] = manifest_count
                if (
                    manifest.get("ok") is not True
                    or manifest.get("immutable") is not True
                    or manifest.get("fullProviderSchedule") is not True
                    or manifest.get("boundToCanonicalPull") is not True
                    or manifest_count <= 0
                    or not manifest.get("version")
                    or not manifest.get("fingerprint")
                    or not manifest.get("pk")
                    or not manifest.get("sk")
                ):
                    candidate_failures.append(
                        f"provider_schedule_manifest_authority_invalid:{game_date or 'unknown'}"
                    )
            if sum(count for count in manifest_counts.values() if count > 0) != provider_game_count:
                candidate_failures.append("provider_schedule_manifest_count_mismatch")
        if payload.get("providerScheduleManifestComplete") is not True:
            candidate_failures.append("provider_schedule_manifest_incomplete")
        winner_results = payload.get("game_winner_predictions")
        if not isinstance(winner_results, list) or not winner_results:
            candidate_failures.append("winner_prediction_results_missing")
        else:
            winner_dates = set()
            for result in winner_results:
                if not isinstance(result, dict):
                    candidate_failures.append("winner_prediction_result_invalid")
                    continue
                game_date = str(result.get("game_date_et") or "")
                winner_dates.add(game_date)
                lifecycle_aware = result.get("preLockStorageLifecycleAware") is True
                lifecycle_complete_for_result = (
                    result.get("displayStatusCoverageComplete") is True
                    and result.get("lifecycleCoverageComplete") is True
                    and result.get("preLockStorageComplete") is True
                    and result.get("preLockStorageDispositionComplete") is True
                )
                # A recommendation can be intentionally non-actionable (for example
                # NEGATIVE_EV_GUARD) without being an operational persistence failure.
                # Only fail the scheduled ingest when the result is explicitly an
                # operational defect or its lifecycle/storage contract is incomplete.
                scoped_defects = _scoped_lifecycle_defects(result)
                scoped_winner_defect = (
                    scoped_defects[0] if scoped_defects is not None else None
                )
                # Release/playability assessment failures keep wagering blocked,
                # but they do not mean that the canonical winner or its durable
                # pre-lock storage failed.  A scoped delegate result lets the
                # ingest alarm distinguish those lanes.  Unscoped/older results
                # retain the conservative legacy behavior and fail closed.
                operational_winner_failure = (
                    scoped_winner_defect is True
                    if scoped_winner_defect is not None
                    else result.get("operationalDefect") is True
                )
                canonical_storage_failures = _canonical_locked_storage_failures(
                    result, game_date
                )
                candidate_failures.extend(canonical_storage_failures)
                hard_winner_failure = bool(
                    operational_winner_failure
                    or canonical_storage_failures
                    or (
                        result.get("ok") is not True
                        and not lifecycle_complete_for_result
                    )
                )
                if hard_winner_failure:
                    candidate_failures.append(
                        f"winner_prediction_failed:{game_date or 'unknown'}"
                    )

                if lifecycle_aware:
                    lifecycle_complete = (
                        result.get("displayStatusCoverageComplete") is True
                        and result.get("lifecycleCoverageComplete") is True
                    )
                    if result.get("allGamesPredicted") is not True and not lifecycle_complete:
                        candidate_failures.append(
                            f"winner_prediction_coverage_incomplete:{game_date or 'unknown'}"
                        )
                    if result.get("preLockStorageComplete") is not True:
                        candidate_failures.append(
                            f"prelock_storage_incomplete:{game_date or 'unknown'}"
                        )
                    try:
                        candidate_count = int(result.get("preLockStorageCandidateCount") or 0)
                        stored_count = int(result.get("preLockStoredCount") or 0)
                        game_count = int(result.get("gameCount") or 0)
                        disposition_count = int(result.get("preLockStorageDispositionCount") or 0)
                    except Exception:
                        candidate_count = stored_count = game_count = disposition_count = -1
                    if candidate_count != stored_count:
                        candidate_failures.append(
                            f"prelock_candidate_count_mismatch:{game_date or 'unknown'}"
                        )
                    if (
                        result.get("preLockStorageDispositionComplete") is not True
                        or disposition_count != game_count
                    ):
                        candidate_failures.append(
                            f"prelock_storage_disposition_incomplete:{game_date or 'unknown'}"
                        )
                else:
                    # Compatibility path for an older delegate result. It keeps
                    # the original strict all-games candidate contract until the
                    # lifecycle-aware finalizer is installed in the same artifact.
                    if result.get("allGamesPredicted") is not True:
                        candidate_failures.append(
                            f"winner_prediction_coverage_incomplete:{game_date or 'unknown'}"
                        )
                    if result.get("preLockStorageComplete") is False:
                        candidate_failures.append(
                            f"prelock_storage_incomplete:{game_date or 'unknown'}"
                        )
                    try:
                        candidate_count = int(result.get("preLockStorageCandidateCount") or 0)
                        stored_count = int(result.get("preLockStoredCount") or 0)
                        game_count = int(result.get("gameCount") or 0)
                    except Exception:
                        candidate_count = stored_count = game_count = -1
                    if candidate_count <= 0 or candidate_count != stored_count or candidate_count != game_count:
                        candidate_failures.append(
                            f"prelock_candidate_count_mismatch:{game_date or 'unknown'}"
                        )

                if game_date not in manifest_counts or game_count != manifest_counts.get(game_date):
                    candidate_failures.append(
                        f"winner_prediction_manifest_count_mismatch:{game_date or 'unknown'}"
                    )
            if winner_dates != set(manifest_counts):
                candidate_failures.append("winner_prediction_manifest_date_mismatch")
    if status_code >= 400 or payload.get("ok") is False or candidate_failures:
        if candidate_failures:
            payload = dict(payload)
            payload["candidatePersistenceFailures"] = candidate_failures
        raise RuntimeError(
            "MLB_SCHEDULED_PULL_FAILED:"
            + json.dumps(payload, default=str, sort_keys=True)
        )


def _header(event: Dict[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value or "")
    return ""


def _auth_error(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # EventBridge scheduled invocations have no HTTP request context and remain allowed.
    if not (event.get("httpMethod") or event.get("requestContext")):
        return None
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return None
    if not ADMIN_TOKEN:
        return _resp(500, {"ok": False, "sport": "mlb", "error": "INQSI_ADMIN_API_TOKEN_NOT_CONFIGURED"})
    token = _header(event, "x-inqsi-admin-token").strip()
    auth = _header(event, "authorization").strip()
    if auth.lower().startswith("bearer "):
        auth = auth.split(" ", 1)[1].strip()
    if token == ADMIN_TOKEN or auth == ADMIN_TOKEN:
        return None
    return _resp(401, {"ok": False, "sport": "mlb", "error": "ADMIN_TOKEN_REQUIRED"})


def lambda_handler(event, context):
    event = event or {}
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return _resp(200, {"ok": True})
    if ML_RUNTIME_INSTALL_STATUS.get("ok") is not True or mlb_manual_pull is None:
        return _runtime_failure(event)
    auth_error = _auth_error(event)
    if auth_error is not None:
        return auth_error
    response = _attach_runtime_status(mlb_manual_pull.lambda_handler(event, context))
    try:
        _raise_scheduled_delegate_failure(event, response)
    except RuntimeError as exc:
        message = str(exc)
        prefix = "MLB_SCHEDULED_PULL_FAILED:"
        failure_payload = {}
        if message.startswith(prefix):
            try:
                parsed = json.loads(message[len(prefix):])
                failure_payload = parsed if isinstance(parsed, dict) else {}
            except Exception:
                failure_payload = {}
        raw_failures = failure_payload.get("candidatePersistenceFailures")
        failures = (
            [str(failure) for failure in raw_failures]
            if isinstance(raw_failures, list)
            else []
        )
        manifest_only_failure = bool(failures) and all(
            failure == "provider_schedule_manifest_incomplete"
            or failure == "provider_schedule_manifest_missing"
            or failure.startswith("provider_schedule_manifest_authority_invalid:")
            for failure in failures
        )
        if _is_scheduled(event):
            converged_response = _current_cutoff_convergence_response(
                event, response, failures
            )
            if converged_response is not None:
                return converged_response
        if not _is_scheduled(event) or not manifest_only_failure:
            raise
        # The canonical writer is intrinsically idempotent by 15-minute slot.
        # Re-running the same scheduled event once allows a transient authority-
        # binding race to converge without creating a second canonical observation.
        # Mixed failures are never retried here; they remain fail-closed.
        retry_event = dict(event)
        retry_event["force"] = True
        retry_event["manifest_binding_retry"] = True
        response = _attach_runtime_status(
            mlb_manual_pull.lambda_handler(retry_event, context)
        )
        _raise_scheduled_delegate_failure(retry_event, response)
    return response
