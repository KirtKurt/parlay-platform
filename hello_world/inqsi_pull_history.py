import copy, os, uuid, json, hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

try:
    import mlb_official_schedule_authority as official_schedule
except Exception:
    official_schedule = None

DDB = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
PULLS = DDB.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
BOOKS = ["fanatics", "draftkings", "fanduel", "betmgm", "caesars", "betrivers", "bovada", "lowvig"]
MIN_PARLAY_PULLS = int(os.environ.get("INQSI_MIN_PARLAY_PULLS", "12"))
DEFAULT_AUTO_PARLAY_SPORTS = ["nfl", "cfb", "mlb", "nba", "wnba", "ncaam", "nhl", "tennis", "soccer"]
SUPPORTED = {
    "nfl": {"label": "NFL", "level": "pro", "gender": "men"},
    "cfb": {"label": "College Football", "level": "college", "gender": "men", "aliases": ["ncaaf", "college_football", "college_football_men"]},
    "college_football_men": {"label": "College Football - Men", "level": "college", "gender": "men", "aliases": ["cfb", "ncaaf"]},
    "college_football_women": {"label": "College Football - Women", "level": "college", "gender": "women", "provider_status": "manual_or_future_provider"},
    "mlb": {"label": "MLB", "level": "pro", "gender": "men"},
    "college_baseball_men": {"label": "College Baseball - Men", "level": "college", "gender": "men", "aliases": ["college_baseball", "ncaa_baseball"]},
    "college_baseball_women": {"label": "College Baseball - Women", "level": "college", "gender": "women", "provider_status": "manual_or_future_provider"},
    "college_softball_women": {"label": "College Softball - Women", "level": "college", "gender": "women", "provider_status": "manual_or_future_provider"},
    "nba": {"label": "NBA", "level": "pro", "gender": "men"},
    "wnba": {"label": "WNBA", "level": "pro", "gender": "women"},
    "ncaam": {"label": "College Basketball - Men", "level": "college", "gender": "men", "aliases": ["college_basketball_men"]},
    "ncaaw": {"label": "College Basketball - Women", "level": "college", "gender": "women", "aliases": ["ncaawb", "college_basketball_women"]},
    "nhl": {"label": "NHL", "level": "pro", "gender": "men"},
    "tennis": {"label": "Tennis", "level": "mixed"},
    "soccer": {"label": "Soccer", "level": "mixed"},
}
ALIASES = {k: k for k in SUPPORTED}
for k, v in SUPPORTED.items():
    for a in v.get("aliases", []):
        ALIASES[a] = k

MLB_EXCLUDED_EXHIBITION_MATCHUPS = {
    frozenset({"american league", "national league"}),
}
MLB_EXHIBITION_MARKERS = ("all-star", "all star")
PROVIDER_MANIFEST_VERSION = "INQSI-PROVIDER-SCHEDULE-MANIFEST-v1"
PROVIDER_MANIFEST_RECORD_TYPE = "provider_schedule_manifest"
CANONICAL_PAYLOAD_FINGERPRINT_VERSION = "INQSI-DDB-READ-EXACT-TYPED-JSON-SHA256-v1"
PULL_SLOT_MINUTES = 15
PULL_SLOT_VERSION = "INQSI-CANONICAL-PULL-SLOT-v1-earliest-integrity-valid"
PULL_HISTORY_INTEGRITY_VERSION = "INQSI-PULL-HISTORY-INTEGRITY-v1-canonical-quarter-hour"
INTRINSIC_PULL_IDEMPOTENCY_VERSION = "INQSI-PULL-STORE-v3-intrinsic-quarter-hour-idempotency"
_INQSI_INTRINSIC_PULL_SLOT_IDEMPOTENCY = True


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    tz = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
    return datetime.now(timezone.utc).astimezone(tz).date().isoformat()


def sport_key(s: Optional[str]) -> str:
    raw = (s or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(raw, raw)


def _ddb_safe(x: Any, *, preserve_nulls: bool = False) -> Any:
    if isinstance(x, float):
        return Decimal(str(x))
    if isinstance(x, list):
        return [_ddb_safe(i, preserve_nulls=preserve_nulls) for i in x]
    if isinstance(x, dict):
        # A fundamentals-v2 snapshot is commonly nested inside a prediction or
        # frozen vector.  Null is part of that snapshot's signed schema (it
        # proves an unavailable source value was not fabricated), so switch the
        # policy at the nested snapshot boundary as well as at the root.
        preserve_nested_nulls = bool(
            preserve_nulls
            or x.get("version")
            == "MLB-FUNDAMENTALS-SNAPSHOT-v2-immutable-source-provenance"
        )
        out = {
            k: _ddb_safe(v, preserve_nulls=preserve_nested_nulls)
            for k, v in x.items()
            if preserve_nested_nulls or v is not None
        }
        # DynamoDB supports explicit NULL values. Preserve the two target slots
        # only for the canonical pregame ML vector so readback can prove that
        # neither outcome was available at lock. Other None fields remain omitted.
        if (
            str(x.get("version") or "").startswith("MLB-ML-FROZEN-FEATURE-SNAPSHOT-")
            and isinstance(x.get("labels"), dict)
        ):
            labels = dict(out.get("labels") or {})
            for target in ("homeWon", "pickCorrect"):
                if target in x.get("labels", {}) and x["labels"].get(target) is None:
                    labels[target] = None
            out["labels"] = labels
        return out
    return x


def ddb_safe(x: Any) -> Any:
    preserve_nulls = bool(
        isinstance(x, dict)
        and x.get("version")
        == "MLB-FUNDAMENTALS-SNAPSHOT-v2-immutable-source-provenance"
    )
    return _ddb_safe(x, preserve_nulls=preserve_nulls)


def _exact_decimal_text(value: Decimal) -> str:
    """Return an exact, context-independent spelling for a numeric value."""
    if not value.is_finite():
        return str(value)
    sign, raw_digits, exponent = value.as_tuple()
    digits = list(raw_digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if not digits:
        return "0"
    coefficient = "".join(str(digit) for digit in digits)
    return f"{'-' if sign else ''}{coefficient}e{exponent}"


def _canonical_payload_value(x: Any) -> Any:
    """Encode a DynamoDB value as an exact, type-tagged JSON tree."""
    if x is None:
        return ["null"]
    if isinstance(x, bool):
        return ["boolean", x]
    if isinstance(x, Decimal):
        return ["number", _exact_decimal_text(x)]
    if isinstance(x, int):
        return ["number", _exact_decimal_text(Decimal(x))]
    if isinstance(x, float):
        return ["number", _exact_decimal_text(Decimal(str(x)))]
    if isinstance(x, str):
        return ["string", x]
    if isinstance(x, list):
        return ["list", [_canonical_payload_value(item) for item in x]]
    if isinstance(x, dict):
        entries = sorted(
            ((str(key), _canonical_payload_value(value)) for key, value in x.items()),
            key=lambda entry: entry[0],
        )
        return ["object", entries]
    return ["other", f"{type(x).__module__}.{type(x).__qualname__}", str(x)]


def _legacy_payload_value(x: Any) -> Any:
    if isinstance(x, Decimal):
        return int(x) if x.is_finite() and x == x.to_integral_value() else float(x)
    if isinstance(x, list):
        return [_legacy_payload_value(item) for item in x]
    if isinstance(x, dict):
        return {str(key): _legacy_payload_value(value) for key, value in x.items()}
    return x


def canonical_payload_fingerprint(value: Any) -> str:
    payload = json.dumps(
        _canonical_payload_value(value),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def legacy_payload_fingerprint(value: Any) -> str:
    """Verify unversioned snapshots written before the exact typed contract."""
    payload = json.dumps(
        _legacy_payload_value(value),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def slate_date(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))).date().isoformat()
    except Exception:
        return today()


def _parse_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def pull_slot_start(value: Any) -> Optional[datetime]:
    parsed = value if isinstance(value, datetime) else _parse_utc(value)
    if parsed is None:
        return None
    parsed = parsed.astimezone(timezone.utc)
    minute = (parsed.minute // PULL_SLOT_MINUTES) * PULL_SLOT_MINUTES
    return parsed.replace(minute=minute, second=0, microsecond=0)


def _pull_slot_key(sport: str, slate: str, slot: datetime) -> Dict[str, str]:
    return {
        "PK": f"PULLS#{sport}#{slate}",
        "SK": f"PULL#SLOT#{slot.astimezone(timezone.utc).isoformat()}",
    }


def _pull_fingerprint_payload(pull: Dict[str, Any]) -> Dict[str, Any]:
    payload = copy.deepcopy(pull or {})
    payload.pop("canonicalPullSlot", None)
    payload.pop("canonicalPullStorage", None)
    payload.pop("_canonicalSlotRawPulls", None)
    return payload


def pull_payload_fingerprint(pull: Dict[str, Any]) -> str:
    return canonical_payload_fingerprint(_pull_fingerprint_payload(pull))


def _pull_integrity_errors(
    pull: Dict[str, Any],
    *,
    sport: Optional[str] = None,
    slate: Optional[str] = None,
    require_manifest: bool = False,
) -> List[str]:
    errors: List[str] = []
    if not isinstance(pull, dict):
        return ["pull_not_object"]
    if not str(pull.get("pull_id") or ""):
        errors.append("pull_id_missing")
    if pull_slot_start(pull.get("pulled_at")) is None:
        errors.append("pulled_at_invalid")
    if not isinstance(pull.get("games"), list):
        errors.append("games_not_list")
    if sport and sport_key(pull.get("sport")) != sport_key(sport):
        errors.append("sport_mismatch")
    if slate and str(pull.get("slate_date") or "") != str(slate):
        errors.append("slate_mismatch")
    has_manifest = isinstance(pull.get("provider_schedule_manifest"), dict)
    has_binding = isinstance(pull.get("provider_manifest_binding"), dict)
    if require_manifest and (not has_manifest or not has_binding):
        errors.append("provider_manifest_missing")
    elif has_manifest or has_binding:
        errors.extend(validate_provider_schedule_manifest(pull, slate))
    return sorted(set(errors))


def _slot_input_metadata(pull: Dict[str, Any]) -> Dict[str, Any]:
    metadata = pull.get("canonicalPullSlot") or {}
    if metadata.get("version") != PULL_SLOT_VERSION:
        fingerprint = pull_payload_fingerprint(pull)
        return {
            "rawPullCount": 1,
            "validPullCount": 1,
            "invalidPullCount": 0,
            "rawPullIds": [str(pull.get("pull_id") or "")],
            "rawPullFingerprints": [fingerprint],
        }
    return {
        "rawPullCount": max(int(metadata.get("rawPullCount") or 1), 1),
        "validPullCount": max(int(metadata.get("validPullCount") or 1), 0),
        "invalidPullCount": max(int(metadata.get("invalidPullCount") or 0), 0),
        "rawPullIds": [str(value) for value in (metadata.get("rawPullIds") or [])],
        "rawPullFingerprints": [
            str(value) for value in (metadata.get("rawPullFingerprints") or [])
        ],
    }


def canonicalize_pull_slots(
    pulls: Iterable[Dict[str, Any]],
    *,
    sport: Optional[str] = None,
    slate: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return one deterministic integrity-valid pull per UTC quarter-hour.

    The earliest valid row wins. Raw rows remain immutable in DynamoDB; the
    attached metadata makes any historical duplicate contamination explicit to
    scorers and the T-minus-45 training gate.
    """
    grouped: Dict[
        str,
        List[Tuple[datetime, int, str, str, Dict[str, Any], Dict[str, Any]]],
    ] = {}
    invalid_by_slot: Dict[str, int] = {}
    raw_count_by_slot: Dict[str, int] = {}
    raw_ids_by_slot: Dict[str, List[str]] = {}
    raw_fingerprints_by_slot: Dict[str, List[str]] = {}
    raw_variants_by_slot: Dict[str, List[Dict[str, Any]]] = {}
    for input_index, raw in enumerate(pulls or []):
        if not isinstance(raw, dict):
            continue
        pulled_at = _parse_utc(raw.get("pulled_at"))
        slot = pull_slot_start(pulled_at)
        if pulled_at is None or slot is None:
            continue
        slot_text = slot.isoformat()
        inherited = _slot_input_metadata(raw)
        raw_count_by_slot[slot_text] = raw_count_by_slot.get(slot_text, 0) + int(
            inherited["rawPullCount"]
        )
        raw_ids_by_slot.setdefault(slot_text, []).extend(inherited["rawPullIds"])
        raw_fingerprints_by_slot.setdefault(slot_text, []).extend(
            inherited["rawPullFingerprints"]
        )
        inherited_variants = raw.get("_canonicalSlotRawPulls")
        variants = (
            inherited_variants
            if isinstance(inherited_variants, list) and inherited_variants
            else [raw]
        )
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            raw_variant = copy.deepcopy(variant)
            raw_variant.pop("canonicalPullSlot", None)
            raw_variant.pop("_canonicalSlotRawPulls", None)
            raw_variants_by_slot.setdefault(slot_text, []).append(raw_variant)
        errors = _pull_integrity_errors(raw, sport=sport, slate=slate)
        if errors:
            invalid_by_slot[slot_text] = invalid_by_slot.get(slot_text, 0) + max(
                int(inherited["invalidPullCount"]), 1
            )
            continue
        fingerprint = pull_payload_fingerprint(raw)
        grouped.setdefault(slot_text, []).append(
            (
                pulled_at,
                input_index,
                str(raw.get("pull_id") or ""),
                fingerprint,
                copy.deepcopy(raw),
                inherited,
            )
        )

    selected: List[Dict[str, Any]] = []
    for slot_text in sorted(grouped):
        # Exact timestamp ties keep immutable query/input order. In DynamoDB a
        # fixed slot key prevents such ties for new writes; retaining order is
        # the only truthful migration rule for legacy/fake histories because
        # a lexical pull ID is not evidence that one observation came first.
        candidates = sorted(grouped[slot_text], key=lambda value: value[:2])
        pulled_at, _, pull_id, fingerprint, canonical, _ = candidates[0]
        valid_count = sum(max(int(item[5]["validPullCount"]), 1) for item in candidates)
        raw_count = max(raw_count_by_slot.get(slot_text, valid_count), valid_count)
        invalid_count = invalid_by_slot.get(slot_text, 0)
        duplicate_count = max(raw_count - 1, 0)
        raw_ids = sorted(set(value for value in raw_ids_by_slot.get(slot_text, []) if value))
        raw_fingerprints = sorted(
            set(value for value in raw_fingerprints_by_slot.get(slot_text, []) if value)
        )
        canonical["canonicalPullSlot"] = {
            "version": PULL_SLOT_VERSION,
            "slotMinutes": PULL_SLOT_MINUTES,
            "slotStartUtc": slot_text,
            "canonical": True,
            "selectionPolicy": "earliest_integrity_valid_pull_in_utc_quarter_hour",
            "canonicalPullId": pull_id,
            "canonicalPulledAtUtc": pulled_at.isoformat(),
            "canonicalPullFingerprint": fingerprint,
            "rawPullCount": raw_count,
            "validPullCount": valid_count,
            "invalidPullCount": invalid_count,
            "duplicatePullCount": duplicate_count,
            "contaminated": duplicate_count > 0 or invalid_count > 0,
            "rawPullIds": raw_ids,
            "rawPullFingerprints": raw_fingerprints,
        }
        canonical["_canonicalSlotRawPulls"] = sorted(
            raw_variants_by_slot.get(slot_text, []),
            key=lambda pull: (
                _parse_utc(pull.get("pulled_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
                str(pull.get("pull_id") or ""),
                pull_payload_fingerprint(pull),
            ),
        )
        selected.append(canonical)
    return selected


def pull_history_integrity(pulls: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    canonical = canonicalize_pull_slots(list(pulls or []))
    slots = [copy.deepcopy(pull.get("canonicalPullSlot") or {}) for pull in canonical]
    raw_count = sum(int(slot.get("rawPullCount") or 0) for slot in slots)
    invalid_count = sum(int(slot.get("invalidPullCount") or 0) for slot in slots)
    duplicate_count = sum(int(slot.get("duplicatePullCount") or 0) for slot in slots)
    fingerprint_payload = [
        {
            "slotStartUtc": slot.get("slotStartUtc"),
            "canonicalPullId": slot.get("canonicalPullId"),
            "canonicalPulledAtUtc": slot.get("canonicalPulledAtUtc"),
            "canonicalPullFingerprint": slot.get("canonicalPullFingerprint"),
            "rawPullCount": slot.get("rawPullCount"),
            "invalidPullCount": slot.get("invalidPullCount"),
        }
        for slot in slots
    ]
    return {
        "version": PULL_HISTORY_INTEGRITY_VERSION,
        "canonicalizationVersion": PULL_SLOT_VERSION,
        "slotMinutes": PULL_SLOT_MINUTES,
        "rawPullCount": raw_count,
        "uniqueSlotCount": len(slots),
        "duplicatePullCount": duplicate_count,
        "invalidPullCount": invalid_count,
        "contaminatedSlotCount": sum(1 for slot in slots if slot.get("contaminated") is True),
        "duplicateContaminated": duplicate_count > 0 or invalid_count > 0,
        "slotStartsUtc": [slot.get("slotStartUtc") for slot in slots],
        "canonicalSlotFingerprint": canonical_payload_fingerprint(fingerprint_payload),
    }


def team_key(name: Optional[str]) -> str:
    return " ".join((name or "").lower().strip().split())


def game_key(sport: str, g: Dict[str, Any]) -> str:
    return str(g.get("game_key") or g.get("game_id") or g.get("id") or f"{sport}|{team_key(g.get('away_team') or g.get('away'))}|{team_key(g.get('home_team') or g.get('home'))}")


def provider_game_identity(sport: str, game: Dict[str, Any]) -> str:
    """Stable schedule identity, independent of whether odds are available."""
    return str(
        game.get("game_id")
        or game.get("id")
        or game.get("game_key")
        or f"{sport}|{team_key(game.get('away_team') or game.get('away'))}|{team_key(game.get('home_team') or game.get('home'))}|{game.get('commence_time') or game.get('start_time') or game.get('startTime') or ''}"
    )


def _provider_manifest_game(sport: str, game: Dict[str, Any]) -> Dict[str, Any]:
    identity = provider_game_identity(sport, game)
    row = {
        "game_id": identity,
        "id": str(game.get("id") or identity),
        "game_key": str(game.get("game_key") or game_key(sport, game)),
        "home_team": game.get("home_team") or game.get("home") or game.get("homeTeam"),
        "away_team": game.get("away_team") or game.get("away") or game.get("awayTeam"),
        "commence_time": game.get("commence_time") or game.get("start_time") or game.get("startTime"),
        "league": game.get("league") or SUPPORTED[sport]["label"],
        "level": game.get("level") or SUPPORTED[sport].get("level"),
        "gender": game.get("gender") or SUPPORTED[sport].get("gender"),
        "provider_sport_key": game.get("provider_sport_key"),
    }
    official_fields = (
        "official_game_pk",
        "official_game_id",
        "official_commence_time",
        "official_game_type",
        "official_game_number",
        "official_double_header",
        "official_status",
        "provider_event_id",
        "provider_commence_time",
        "provider_start_drift_seconds",
        "canonical_start_time_source",
        "schedule_authority",
        "schedule_authority_version",
    )
    # Preserve the exact legacy v1 fingerprint material for old manifests.
    # Official fields are added only to Stats-API-backed MLB rows.
    if game.get("official_game_pk") not in (None, ""):
        row.update({
            field: _legacy_payload_value(game.get(field))
            for field in official_fields
            if game.get(field) is not None
        })
    return row


def _manifest_sort_key(game: Dict[str, Any]) -> tuple:
    return (
        str(game.get("commence_time") or ""),
        str(game.get("game_id") or game.get("id") or ""),
        team_key(game.get("away_team")),
        team_key(game.get("home_team")),
    )


def _provider_manifest_material(manifest: Dict[str, Any]) -> Dict[str, Any]:
    games = sorted(
        [_provider_manifest_game(str(manifest.get("sport") or "mlb"), game) for game in manifest.get("games") or []],
        key=_manifest_sort_key,
    )
    material = {
        "version": str(manifest.get("version") or ""),
        "recordType": str(manifest.get("recordType") or ""),
        "sport": str(manifest.get("sport") or ""),
        "slateDate": str(manifest.get("slateDate") or ""),
        "pullId": str(manifest.get("pullId") or ""),
        "observedAtUtc": str(manifest.get("observedAtUtc") or ""),
        "source": str(manifest.get("source") or ""),
        "gameCount": len(games),
        "gameIdentities": [provider_game_identity(str(manifest.get("sport") or "mlb"), game) for game in games],
        "games": games,
    }
    # New official proof is bound into the immutable manifest fingerprint;
    # manifests written before this field retain their original fingerprint.
    if "scheduleAuthority" in manifest:
        material["scheduleAuthority"] = _legacy_payload_value(manifest.get("scheduleAuthority"))
    return material


def provider_manifest_fingerprint(manifest: Dict[str, Any]) -> str:
    payload = json.dumps(_provider_manifest_material(manifest), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_provider_schedule_manifest(
    *,
    sport: str,
    slate: str,
    pulled_at: str,
    pull_id: str,
    source: str,
    games: List[Dict[str, Any]],
    schedule_authority: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    schedule_games = sorted([_provider_manifest_game(sport, game) for game in games], key=_manifest_sort_key)
    manifest: Dict[str, Any] = {
        "version": PROVIDER_MANIFEST_VERSION,
        "recordType": PROVIDER_MANIFEST_RECORD_TYPE,
        "sport": sport,
        "slateDate": slate,
        "pullId": pull_id,
        "observedAtUtc": pulled_at,
        "source": source,
        "gameCount": len(schedule_games),
        "gameIdentities": [provider_game_identity(sport, game) for game in schedule_games],
        "games": schedule_games,
    }
    if schedule_authority is not None:
        manifest["scheduleAuthority"] = schedule_authority
    manifest["fingerprint"] = provider_manifest_fingerprint(manifest)
    return manifest


def _provider_manifest_key(manifest: Dict[str, Any]) -> Dict[str, str]:
    return {
        "PK": f"PROVIDER_MANIFEST#{manifest['sport']}#{manifest['slateDate']}",
        "SK": f"OBSERVED#{manifest['observedAtUtc']}#PULL#{manifest['pullId']}",
    }


def validate_provider_schedule_manifest(
    pull: Dict[str, Any],
    expected_slate: Optional[str] = None,
    *,
    verify_immutable_storage: bool = False,
) -> List[str]:
    """Validate the full schedule proof and, for lock reads, its write-once copy."""
    errors: List[str] = []
    manifest = pull.get("provider_schedule_manifest")
    binding = pull.get("provider_manifest_binding")
    if not isinstance(manifest, dict):
        return ["provider_schedule_manifest_missing"]
    if not isinstance(binding, dict):
        return ["provider_manifest_binding_missing"]
    if manifest.get("version") != PROVIDER_MANIFEST_VERSION:
        errors.append("provider_manifest_version_mismatch")
    if manifest.get("recordType") != PROVIDER_MANIFEST_RECORD_TYPE:
        errors.append("provider_manifest_record_type_mismatch")
    if expected_slate and str(manifest.get("slateDate") or "") != str(expected_slate):
        errors.append("provider_manifest_slate_mismatch")
    fingerprint = provider_manifest_fingerprint(manifest)
    if str(manifest.get("fingerprint") or "") != fingerprint:
        errors.append("provider_manifest_fingerprint_mismatch")
    schedule_games = sorted(list(manifest.get("games") or []), key=_manifest_sort_key)
    identities = [provider_game_identity(str(manifest.get("sport") or "mlb"), game) for game in schedule_games]
    pull_games = sorted(list(pull.get("games") or []), key=_manifest_sort_key)
    pull_identities = [provider_game_identity(str(pull.get("sport") or "mlb"), game) for game in pull_games]
    try:
        declared_count = int(manifest.get("gameCount"))
    except Exception:
        declared_count = -1
    if declared_count != len(schedule_games):
        errors.append("provider_manifest_game_count_mismatch")
    if list(manifest.get("gameIdentities") or []) != identities:
        errors.append("provider_manifest_identity_proof_mismatch")
    if len(set(identities)) != len(identities):
        errors.append("provider_manifest_duplicate_game_identity")
    if pull_identities != identities:
        errors.append("provider_manifest_pull_membership_mismatch")
    schedule_authority = manifest.get("scheduleAuthority")
    authority_claimed = bool(
        schedule_authority is not None
        or binding.get("officialScheduleBacked") is True
        or binding.get("officialScheduleAuthorityFingerprint")
        or any(game.get("official_game_pk") not in (None, "") for game in schedule_games)
    )
    if schedule_authority is not None:
        if not isinstance(schedule_authority, dict):
            errors.append("official_schedule_authority_proof_invalid")
        else:
            if official_schedule is None:
                errors.append("official_schedule_authority_validator_unavailable")
            else:
                errors.extend(
                    official_schedule.validate_authority_proof(
                        schedule_authority,
                        schedule_games,
                    )
                )
            if str(schedule_authority.get("slateDate") or "") != str(manifest.get("slateDate") or ""):
                errors.append("official_schedule_authority_slate_mismatch")
            if str(schedule_authority.get("observedAtUtc") or "") != str(manifest.get("observedAtUtc") or ""):
                errors.append("official_schedule_authority_observed_at_mismatch")
            if (
                binding.get("officialScheduleBacked") is not True
                or str(binding.get("officialScheduleAuthorityVersion") or "")
                != str(schedule_authority.get("version") or "")
                or str(binding.get("officialScheduleAuthorityFingerprint") or "")
                != str(schedule_authority.get("fingerprint") or "")
            ):
                errors.append("official_schedule_authority_binding_mismatch")
    elif authority_claimed:
        errors.append("official_schedule_authority_proof_missing")
    expected_key = _provider_manifest_key(manifest)
    try:
        bound_count = int(binding.get("gameCount"))
    except Exception:
        bound_count = -1
    if (
        binding.get("version") != PROVIDER_MANIFEST_VERSION
        or str(binding.get("fingerprint") or "") != fingerprint
        or bound_count != len(schedule_games)
        or binding.get("pk") != expected_key["PK"]
        or binding.get("sk") != expected_key["SK"]
        or binding.get("immutable") is not True
        or binding.get("fullProviderSchedule") is not True
    ):
        errors.append("provider_manifest_binding_mismatch")
    if verify_immutable_storage:
        if PULLS is None:
            errors.append("provider_manifest_storage_unavailable")
        else:
            stored = PULLS.get_item(Key=expected_key, ConsistentRead=True).get("Item")
            stored_manifest = (stored or {}).get("data") if isinstance(stored, dict) else None
            if not isinstance(stored_manifest, dict):
                errors.append("immutable_provider_manifest_missing")
            elif (
                stored.get("record_type") != PROVIDER_MANIFEST_RECORD_TYPE
                or str(stored.get("manifest_fingerprint") or "") != fingerprint
                or str(stored_manifest.get("fingerprint") or "") != fingerprint
                or provider_manifest_fingerprint(stored_manifest) != fingerprint
            ):
                errors.append("immutable_provider_manifest_readback_mismatch")
    return errors


def provider_manifest_games_for_lock(pull: Dict[str, Any], slate: str) -> List[Dict[str, Any]]:
    errors = validate_provider_schedule_manifest(pull, slate, verify_immutable_storage=True)
    if errors:
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:" + ",".join(errors))
    return sorted(list((pull.get("provider_schedule_manifest") or {}).get("games") or []), key=_manifest_sort_key)


def _manifest_observed_at(pull: Dict[str, Any]) -> Optional[datetime]:
    value = (
        (pull.get("provider_schedule_manifest") or {}).get("observedAtUtc")
        or pull.get("pulled_at")
        or pull.get("asof")
        or pull.get("created_at")
    )
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _manifest_game_start(game: Dict[str, Any]) -> Optional[datetime]:
    value = game.get("commence_time") or game.get("commenceTime")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _event_roster_backed(pull: Dict[str, Any]) -> bool:
    """Return whether this pull was built from the provider's events roster.

    Older v1 pulls used the odds response itself as the schedule.  Keep them as
    a migration fallback until the first events-backed pull is stored, but do
    not let them outrank the new schedule authority afterward.
    """
    roster = (pull.get("meta") or {}).get("provider_roster") or {}
    return bool(
        roster.get("source") in {
            "the_odds_api_events_exact_id_merge",
            "mlb_stats_api_exact_date_with_the_odds_api_event_crosswalk",
        }
        and roster.get("exactProviderIdMerge") is True
    )


def _official_schedule_backed(pull: Dict[str, Any]) -> bool:
    authority = (pull.get("provider_schedule_manifest") or {}).get("scheduleAuthority")
    return bool(
        isinstance(authority, dict)
        and official_schedule is not None
        and authority.get("version") == official_schedule.VERSION
        and authority.get("source") == official_schedule.SOURCE
        and authority.get("verified") is True
        and authority.get("authoritativeRoster") is True
        and authority.get("authoritativeStartTimes") is True
    )


def _schedule_comparison_game(sport: str, game: Dict[str, Any]) -> Dict[str, Any]:
    """Compare schedule facts without treating mutable status as roster drift."""
    row = _provider_manifest_game(sport, game)
    return {
        field: row.get(field)
        for field in (
            "game_id",
            "game_key",
            "home_team",
            "away_team",
            "commence_time",
            "league",
            "level",
            "gender",
            "provider_sport_key",
            "official_game_pk",
            "official_game_id",
            "official_commence_time",
        )
        if field in row
    }


def _official_membership_by_pk(
    games: Iterable[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Index one official roster without letting mutable provider IDs define it."""
    by_pk: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    for game in games or []:
        official_pk = str(game.get("official_game_pk") or "").strip()
        if not official_pk:
            errors.append("official_game_pk_missing")
            continue
        if official_pk in by_pk:
            errors.append(f"duplicate_official_game_pk:{official_pk}")
            continue
        by_pk[official_pk] = game
    return by_pk, sorted(set(errors))


def _official_ordered_teams(game: Dict[str, Any]) -> Tuple[str, str]:
    normalizer = (
        official_schedule.normalize_team
        if official_schedule is not None
        else team_key
    )
    return (
        normalizer(game.get("away_team") or game.get("awayTeam")),
        normalizer(game.get("home_team") or game.get("homeTeam")),
    )


def _compatible_official_schedule_revision(
    membership_games: Iterable[Dict[str, Any]],
    revision_games: Iterable[Dict[str, Any]],
) -> Tuple[bool, List[str]]:
    """Require an exact official-PK/team roster before accepting new times."""
    membership_by_pk, membership_errors = _official_membership_by_pk(
        membership_games
    )
    revision_by_pk, revision_errors = _official_membership_by_pk(revision_games)
    errors = [*membership_errors, *revision_errors]
    if set(membership_by_pk) != set(revision_by_pk):
        errors.append("official_game_pk_membership_changed")
    for official_pk in sorted(set(membership_by_pk) & set(revision_by_pk)):
        if _official_ordered_teams(membership_by_pk[official_pk]) != (
            _official_ordered_teams(revision_by_pk[official_pk])
        ):
            errors.append(f"ordered_teams_changed:{official_pk}")
    return not errors, sorted(set(errors))


def _overlay_official_schedule_revision(
    membership_games: Iterable[Dict[str, Any]],
    revision_games: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply official schedule facts while preserving roster/provider identity."""
    revision_by_pk, errors = _official_membership_by_pk(revision_games)
    if errors:
        raise RuntimeError(
            "MLB_OFFICIAL_SCHEDULE_REVISION_INVALID:" + ",".join(errors)
        )
    schedule_fields = (
        "commence_time",
        "official_commence_time",
        "official_game_type",
        "official_game_number",
        "official_double_header",
        "official_status",
        "canonical_start_time_source",
        "schedule_authority",
        "schedule_authority_version",
    )
    effective: List[Dict[str, Any]] = []
    for membership_game in membership_games or []:
        official_pk = str(membership_game.get("official_game_pk") or "")
        revision = revision_by_pk[official_pk]
        row = copy.deepcopy(membership_game)
        for field in schedule_fields:
            if field in revision:
                row[field] = copy.deepcopy(revision.get(field))
        effective.append(row)
    return sorted(effective, key=_manifest_sort_key)


def verified_full_slate_manifest(
    pulls: List[Dict[str, Any]],
    slate: str,
) -> Dict[str, Any]:
    """Resolve a durable pre-start roster across official and legacy feeds.

    Maximum-cardinality prestart proof protects same-day migration from a
    contracted feed. At equal coverage, an MLB Stats API exact-date manifest
    outranks the provider events roster, which outranks legacy odds-derived
    membership. Later anomalies cannot erase the selected durable roster.
    """
    candidates: List[Tuple[int, datetime, Dict[str, Any], bool, bool]] = []
    missing_provider_proofs: List[Tuple[Optional[datetime], str]] = []
    invalid_provider_proofs: List[Dict[str, Any]] = []
    for pull in pulls or []:
        if str(pull.get("source") or "").strip().lower() != "the_odds_api":
            continue
        manifest = pull.get("provider_schedule_manifest")
        binding = pull.get("provider_manifest_binding")
        if manifest is None and binding is None:
            missing_provider_proofs.append(
                (_manifest_observed_at(pull), str(pull.get("pull_id") or "unknown"))
            )
            continue
        errors = validate_provider_schedule_manifest(
            pull,
            slate,
            verify_immutable_storage=False,
        )
        if errors:
            invalid_provider_proofs.append({
                "pullId": str(pull.get("pull_id") or "unknown"),
                "errors": sorted(set(errors)),
            })
            continue
        observed_at = _manifest_observed_at(pull)
        if observed_at is None:
            raise RuntimeError(
                "MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:observed_at_invalid"
            )
        try:
            count = int((manifest or {}).get("gameCount"))
        except Exception:
            count = -1
        candidates.append(
            (
                count,
                observed_at,
                pull,
                _event_roster_backed(pull),
                _official_schedule_backed(pull),
            )
        )

    if not candidates:
        if invalid_provider_proofs:
            errors = sorted({
                error
                for proof in invalid_provider_proofs
                for error in (proof.get("errors") or [])
            })
            raise RuntimeError(
                "MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:" + ",".join(errors)
            )
        if missing_provider_proofs:
            missing_ids = sorted(pull_id for _observed_at, pull_id in missing_provider_proofs)
            raise RuntimeError(
                "MLB_PROVIDER_SCHEDULE_MANIFEST_MISSING:provider_schedule_manifest_missing:"
                + ",".join(missing_ids)
            )
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_MISSING:NO_PROVIDER_PULLS")

    # Before an official schedule exists, migration must not let the first
    # post-deploy events response redefine a larger prestart legacy roster.
    # Once an exact-date MLB schedule is present it owns roster membership even
    # if a mutable legacy/provider feed previously contained more rows.
    first_proven_at = min(item[1] for item in candidates)
    unproved_current = sorted(
        pull_id
        for observed_at, pull_id in missing_provider_proofs
        if observed_at is None or observed_at >= first_proven_at
    )
    # Once a valid write-once roster exists, missing or invalid later mutable
    # pulls are anomalies. They cannot globally invalidate that roster.

    anchor_candidates: List[Tuple[int, datetime, Dict[str, Any], bool, bool]] = []
    for candidate in candidates:
        _count, observed_at, pull, _event_backed, _official_backed = candidate
        starts = [
            _manifest_game_start(game)
            for game in ((pull.get("provider_schedule_manifest") or {}).get("games") or [])
        ]
        starts = [value for value in starts if value is not None]
        if starts and observed_at < min(starts):
            anchor_candidates.append(candidate)
    if not anchor_candidates:
        raise RuntimeError("MLB_PROVIDER_FULL_SLATE_PRESTART_AUTHORITY_MISSING")

    official_anchor_candidates = [item for item in anchor_candidates if item[4]]
    authority_candidates = official_anchor_candidates or anchor_candidates
    full_count = max(item[0] for item in authority_candidates)
    full_pool = [item for item in authority_candidates if item[0] == full_count]
    preferred_pool = (
        [item for item in full_pool if item[4]]
        or [item for item in full_pool if item[3]]
        or full_pool
    )
    _full_count, _full_at, full_pull, full_event_backed, full_official_backed = min(
        preferred_pool,
        key=lambda item: item[1],
    )
    _latest_count, latest_at, latest_pull, latest_event_backed, latest_official_backed = max(
        candidates,
        key=lambda item: item[1],
    )
    authority_mode = (
        "MLB_STATS_API_EXACT_DATE"
        if full_official_backed
        else "EVENTS_ROSTER_EXACT_PROVIDER_ID"
        if full_event_backed
        else "LEGACY_ODDS_MANIFEST_MIGRATION_FALLBACK"
    )

    full_games = provider_manifest_games_for_lock(full_pull, slate)
    latest_games = (
        full_games
        if latest_pull is full_pull
        else provider_manifest_games_for_lock(latest_pull, slate)
    )
    if full_count != len(full_games):
        raise RuntimeError("MLB_PROVIDER_FULL_SLATE_MANIFEST_COUNT_MISMATCH")

    full_by_id = {provider_game_identity("mlb", game): game for game in full_games}
    latest_by_id = {provider_game_identity("mlb", game): game for game in latest_games}
    if len(full_by_id) != len(full_games) or len(latest_by_id) != len(latest_games):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_DUPLICATE_IDENTITY")

    unknown = sorted(set(latest_by_id) - set(full_by_id))

    changed = sorted(
        identity
        for identity, latest_game in latest_by_id.items()
        if identity in full_by_id
        if _schedule_comparison_game("mlb", latest_game)
        != _schedule_comparison_game("mlb", full_by_id[identity])
    )
    prematurely_omitted = sorted(
        identity
        for identity, game in full_by_id.items()
        if identity not in latest_by_id
        and (
            _manifest_game_start(game) is None
            or _manifest_game_start(game) > latest_at
        )
    )
    schedule_pull = full_pull
    schedule_games = full_games
    rejected_schedule_revisions: List[Dict[str, Any]] = []
    if full_official_backed:
        compatible_revisions: List[
            Tuple[int, datetime, Dict[str, Any], bool, bool]
        ] = []
        for candidate in candidates:
            count, _observed_at, candidate_pull, _event_backed, official_backed = candidate
            if not official_backed or count != full_count:
                continue
            # Every candidate manifest was fingerprint-validated in memory
            # above. Use that immutable payload for compatibility screening;
            # only the selected membership/schedule records need additional
            # strongly consistent DynamoDB readback.
            candidate_games = list(
                (candidate_pull.get("provider_schedule_manifest") or {}).get("games")
                or []
            )
            compatible, revision_errors = _compatible_official_schedule_revision(
                full_games,
                candidate_games,
            )
            if compatible:
                compatible_revisions.append(candidate)
            elif _observed_at > _full_at:
                rejected_schedule_revisions.append({
                    "type": "OFFICIAL_SCHEDULE_REVISION_MEMBERSHIP_REJECTED",
                    "pullId": str(candidate_pull.get("pull_id") or "unknown"),
                    "errors": revision_errors,
                })
        if compatible_revisions:
            (
                _schedule_count,
                _schedule_at,
                schedule_pull,
                _schedule_event_backed,
                _schedule_official_backed,
            ) = max(compatible_revisions, key=lambda item: item[1])
            schedule_games = (
                full_games
                if schedule_pull is full_pull
                else latest_games
                if schedule_pull is latest_pull
                else provider_manifest_games_for_lock(schedule_pull, slate)
            )

    effective_games = (
        _overlay_official_schedule_revision(full_games, schedule_games)
        if schedule_pull is not full_pull
        else list(full_games)
    )

    anomalies: List[Dict[str, Any]] = [
        *invalid_provider_proofs,
        *rejected_schedule_revisions,
    ]
    if unproved_current:
        anomalies.append({
            "type": "CURRENT_PROVIDER_PULL_MANIFEST_MISSING",
            "pullIds": unproved_current,
        })
    if unknown:
        anomalies.append({
            "type": "LATEST_FEED_UNKNOWN_GAME_IDENTITY",
            "gameIdentities": unknown,
        })
    if changed:
        anomalies.append({
            "type": "LATEST_FEED_SCHEDULE_CHANGED",
            "gameIdentities": changed,
        })
    if prematurely_omitted:
        anomalies.append({
            "type": "LATEST_FEED_FUTURE_GAME_OMITTED",
            "gameIdentities": prematurely_omitted,
        })

    full_manifest = full_pull.get("provider_schedule_manifest") or {}
    schedule_manifest = schedule_pull.get("provider_schedule_manifest") or {}
    latest_manifest = latest_pull.get("provider_schedule_manifest") or {}
    full_schedule_authority = full_manifest.get("scheduleAuthority") or {}
    schedule_revision_authority = schedule_manifest.get("scheduleAuthority") or {}
    latest_schedule_authority = latest_manifest.get("scheduleAuthority") or {}
    return {
        "version": "MLB-VERIFIED-FULL-SLATE-ROSTER-v4-membership-and-schedule-revision-authority",
        "slateDate": slate,
        "games": list(effective_games),
        "fullSlateGameCount": len(effective_games),
        "latestFeedGameCount": len(latest_games),
        "latestFeedContracted": len(latest_games) < len(full_games),
        "rosterAuthorityMode": authority_mode,
        "officialScheduleBacked": bool(full_official_backed),
        "latestFeedOfficialScheduleBacked": bool(latest_official_backed),
        "officialScheduleAuthorityVersion": schedule_revision_authority.get("version"),
        "officialScheduleAuthoritySource": schedule_revision_authority.get("source"),
        "officialScheduleAuthorityFingerprint": schedule_revision_authority.get("fingerprint"),
        "officialScheduleGameCount": schedule_revision_authority.get("officialGameCount"),
        "officialScheduleAuthoritativeStartTimes": schedule_revision_authority.get("authoritativeStartTimes") is True,
        "officialScheduleMissingProviderEventGameIds": list(schedule_revision_authority.get("missingProviderEventOfficialGameIds") or []),
        "latestOfficialScheduleAuthorityFingerprint": latest_schedule_authority.get("fingerprint"),
        "eventRosterBacked": bool(full_event_backed),
        "latestFeedEventRosterBacked": bool(latest_event_backed),
        "legacyMigrationFallback": not bool(full_official_backed or full_event_backed),
        "latestFeedAnomalies": anomalies,
        "latestFeedAnomalyCount": len(anomalies),
        "durableRosterPreservedDespiteLatestFeedAnomaly": bool(anomalies),
        "fullAuthorityPull": full_pull,
        "membershipAuthorityPull": full_pull,
        "membershipAuthorityFingerprint": full_manifest.get("fingerprint"),
        "membershipAuthorityPullId": full_manifest.get("pullId"),
        "membershipOfficialScheduleAuthorityFingerprint": full_schedule_authority.get("fingerprint"),
        "scheduleAuthorityPull": schedule_pull,
        "scheduleAuthorityFingerprint": schedule_manifest.get("fingerprint"),
        "scheduleAuthorityPullId": schedule_manifest.get("pullId"),
        "scheduleAuthorityObservedAtUtc": schedule_manifest.get("observedAtUtc"),
        "scheduleRevisionApplied": schedule_pull is not full_pull,
        "latestFeedPull": latest_pull,
        "fullAuthorityFingerprint": full_manifest.get("fingerprint"),
        "fullAuthorityPullId": full_manifest.get("pullId"),
        "fullAuthorityObservedAtUtc": full_manifest.get("observedAtUtc"),
        "latestFeedFingerprint": latest_manifest.get("fingerprint"),
        "latestFeedPullId": latest_manifest.get("pullId"),
        "latestFeedObservedAtUtc": latest_manifest.get("observedAtUtc"),
        "immutableReadbackVerified": True,
    }


def provider_manifest_authority_for_lock(
    pull: Dict[str, Any],
    slate: str,
    expected_games: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return the exact immutable full-schedule proof used by a lock stage.

    This is intentionally a pointer-and-fingerprint proof rather than a copy of
    a caller supplied flag.  The manifest has already been independently
    written, and this function performs the strongly consistent readback before
    a per-game stage may bind it.
    """
    errors = validate_provider_schedule_manifest(
        pull,
        slate,
        verify_immutable_storage=True,
    )
    manifest = pull.get("provider_schedule_manifest") or {}
    binding = pull.get("provider_manifest_binding") or {}
    manifest_games = sorted(list(manifest.get("games") or []), key=_manifest_sort_key)
    manifest_identities = [
        provider_game_identity(str(manifest.get("sport") or "mlb"), game)
        for game in manifest_games
    ]
    if expected_games is not None:
        expected = sorted(
            [_provider_manifest_game("mlb", game) for game in expected_games],
            key=_manifest_sort_key,
        )
        expected_identities = [provider_game_identity("mlb", game) for game in expected]
        if expected_identities != manifest_identities:
            errors.append("provider_manifest_lock_slate_membership_mismatch")
    if errors:
        raise RuntimeError(
            "MLB_PROVIDER_SCHEDULE_MANIFEST_AUTHORITY_INVALID:"
            + ",".join(sorted(set(errors)))
        )
    schedule_authority = manifest.get("scheduleAuthority") or {}
    return {
        "version": PROVIDER_MANIFEST_VERSION,
        "recordType": PROVIDER_MANIFEST_RECORD_TYPE,
        "pk": binding.get("pk"),
        "sk": binding.get("sk"),
        "fingerprint": manifest.get("fingerprint"),
        "slateDate": manifest.get("slateDate"),
        "observedAtUtc": manifest.get("observedAtUtc"),
        "pullId": manifest.get("pullId"),
        "gameCount": len(manifest_games),
        "gameIdentities": manifest_identities,
        "immutable": True,
        "writeOnce": True,
        "fullProviderSchedule": True,
        "consistentReadVerified": True,
        "officialScheduleBacked": bool(schedule_authority),
        "officialScheduleAuthorityVersion": schedule_authority.get("version"),
        "officialScheduleAuthoritySource": schedule_authority.get("source"),
        "officialScheduleAuthorityFingerprint": schedule_authority.get("fingerprint"),
        "officialScheduleGameCount": schedule_authority.get("officialGameCount"),
        "officialScheduleAuthoritativeRoster": schedule_authority.get("authoritativeRoster") is True,
        "officialScheduleAuthoritativeStartTimes": schedule_authority.get("authoritativeStartTimes") is True,
        "officialScheduleMissingProviderEventGameIds": list(schedule_authority.get("missingProviderEventOfficialGameIds") or []),
    }


def mlb_model_eligible_game(game: Dict[str, Any]) -> bool:
    """Return False for special MLB exhibitions that must not enter accuracy or ML learning."""
    home = team_key(game.get("home_team") or game.get("home") or game.get("homeTeam"))
    away = team_key(game.get("away_team") or game.get("away") or game.get("awayTeam"))
    if frozenset({home, away}) in MLB_EXCLUDED_EXHIBITION_MATCHUPS:
        return False
    descriptor = " ".join(
        str(game.get(field) or "")
        for field in ("league", "name", "title", "description", "event_name", "eventName")
    ).lower()
    return not any(marker in descriptor for marker in MLB_EXHIBITION_MARKERS)


def _filter_mlb_model_pull(pull: Dict[str, Any]) -> Dict[str, Any]:
    games = list(pull.get("games") or [])
    eligible = [game for game in games if mlb_model_eligible_game(game)]
    if len(eligible) == len(games):
        return pull
    filtered = dict(pull)
    filtered["games"] = eligible
    meta = dict(filtered.get("meta") or {})
    meta["excludedNonModelGameCount"] = len(games) - len(eligible)
    meta["excludedNonModelGamePolicy"] = "MLB_ALL_STAR_EXHIBITION_EXCLUDED_FROM_PREDICTION_ACCURACY_AND_LEARNING"
    filtered["meta"] = meta
    return filtered


def american_prob(v: Any) -> Optional[float]:
    try:
        a = int(v)
    except Exception:
        return None
    if a == 0:
        return None
    return abs(a) / (abs(a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def vig(home: Any, away: Any) -> Optional[tuple]:
    hp, ap = american_prob(home), american_prob(away)
    if hp is None or ap is None or hp + ap <= 0:
        return None
    return hp / (hp + ap), ap / (hp + ap)


def supported_sports() -> Dict[str, Any]:
    return {
        "ok": True,
        "architecture": "15_min_pull_history",
        "minimumParlayPulls": MIN_PARLAY_PULLS,
        "minimumParlayHistoryMinutes": MIN_PARLAY_PULLS * 15,
        "sports": [{"key": k, **v} for k, v in SUPPORTED.items()],
        "collegeCoverage": {"football": ["college_football_men", "college_football_women"], "baseball": ["college_baseball_men", "college_baseball_women", "college_softball_women"], "basketball": ["ncaam", "ncaaw"]},
        "note": "Three-leg parlays are refused until the 12th 15-minute pull for the sport/slate.",
    }


def normalize_pull(body: Dict[str, Any]) -> Dict[str, Any]:
    sport = sport_key(body.get("sport") or body.get("sport_key"))
    if sport not in SUPPORTED:
        return {"ok": False, "error": "unsupported_sport", "sport": sport, "supportedSports": [x["key"] for x in supported_sports()["sports"]]}
    pulled_at = body.get("pulled_at") or body.get("asof") or now()
    raw_games = body.get("games") or body.get("events") or []
    pull_id = str(body.get("pull_id") or f"pull_{uuid.uuid4().hex[:16]}")
    slate = str(body.get("slate_date") or slate_date(pulled_at))
    source = str(body.get("source") or "manual_or_provider_payload")
    meta = dict(body.get("meta") or {})
    schedule_authority = meta.get("official_schedule_authority")
    games = []
    for raw in raw_games if isinstance(raw_games, list) else []:
        if not isinstance(raw, dict):
            continue
        home = raw.get("home_team") or raw.get("home") or raw.get("homeTeam")
        away = raw.get("away_team") or raw.get("away") or raw.get("awayTeam")
        incoming = raw.get("books") or raw.get("bookmakers") or {}
        if isinstance(incoming, list):
            incoming = {str(b.get("key") or b.get("book") or b.get("title") or "").lower(): b for b in incoming if isinstance(b, dict)}
        books = {}
        for book, data in (incoming or {}).items():
            if not isinstance(data, dict):
                continue
            ml = data.get("ml") or data.get("moneyline") or data.get("h2h") or {}
            hp = ml.get("home") or ml.get("home_price") or ml.get("homePrice")
            ap = ml.get("away") or ml.get("away_price") or ml.get("awayPrice")
            if hp is None or ap is None:
                continue
            key = str(book).lower().strip().replace(" ", "_")
            books[key] = {"ml": {"home": int(hp), "away": int(ap)}}
            if "spread" in data:
                books[key]["spread"] = data["spread"]
            if "total" in data:
                books[key]["total"] = data["total"]
        # The provider schedule is authoritative even when no supported book has
        # posted a moneyline yet.  Empty books must remain visible so prediction
        # coverage cannot silently shrink to only the games that were scoreable.
        if home and away:
            games.append({
                "game_id": str(raw.get("game_id") or raw.get("id") or game_key(sport, raw)),
                "game_key": game_key(sport, raw),
                "home_team": home,
                "away_team": away,
                "commence_time": raw.get("commence_time") or raw.get("start_time") or raw.get("startTime"),
                "league": raw.get("league") or SUPPORTED[sport]["label"],
                "level": raw.get("level") or SUPPORTED[sport].get("level"),
                "gender": raw.get("gender") or SUPPORTED[sport].get("gender"),
                "provider_sport_key": raw.get("provider_sport_key"),
                "official_game_pk": raw.get("official_game_pk"),
                "official_game_id": raw.get("official_game_id"),
                "official_commence_time": raw.get("official_commence_time"),
                "official_game_type": raw.get("official_game_type"),
                "official_game_number": raw.get("official_game_number"),
                "official_double_header": raw.get("official_double_header"),
                "official_status": raw.get("official_status") or {},
                "provider_event_id": raw.get("provider_event_id"),
                "provider_commence_time": raw.get("provider_commence_time"),
                "provider_start_drift_seconds": raw.get("provider_start_drift_seconds"),
                "canonical_start_time_source": raw.get("canonical_start_time_source"),
                "schedule_authority": raw.get("schedule_authority"),
                "schedule_authority_version": raw.get("schedule_authority_version"),
                "books": books,
                "odds_available": bool(books),
                "moneyline_available": any((payload or {}).get("ml") for payload in books.values()),
            })
    if not games:
        return {"ok": False, "error": "games_required", "message": "Provide at least one provider game with home and away teams."}
    games = sorted(games, key=_manifest_sort_key)
    if schedule_authority is not None:
        if sport != "mlb":
            return {"ok": False, "error": "official_schedule_authority_only_supported_for_mlb"}
        if not isinstance(schedule_authority, dict):
            return {"ok": False, "error": "official_schedule_authority_invalid", "errors": ["official_schedule_authority_missing"]}
        if official_schedule is None:
            return {"ok": False, "error": "official_schedule_authority_validator_unavailable"}
        authority_errors = official_schedule.validate_authority_proof(
            schedule_authority,
            games,
        )
        if str((schedule_authority or {}).get("slateDate") or "") != slate:
            authority_errors.append("official_schedule_authority_slate_mismatch")
        if str((schedule_authority or {}).get("observedAtUtc") or "") != str(pulled_at):
            authority_errors.append("official_schedule_authority_observed_at_mismatch")
        if authority_errors:
            return {
                "ok": False,
                "error": "official_schedule_authority_invalid",
                "errors": sorted(set(authority_errors)),
            }
    manifest = _build_provider_schedule_manifest(
        sport=sport,
        slate=slate,
        pulled_at=str(pulled_at),
        pull_id=pull_id,
        source=source,
        games=games,
        schedule_authority=schedule_authority,
    )
    manifest_key = _provider_manifest_key(manifest)
    meta.update({
        "oddsApiOperational": source == "the_odds_api",
        "architecture": "15_min_pull_history",
        "providerManifestVersion": PROVIDER_MANIFEST_VERSION,
        "providerManifestFingerprint": manifest["fingerprint"],
        "providerManifestGameCount": len(games),
        "officialScheduleBacked": bool(schedule_authority),
        "officialScheduleAuthorityVersion": (schedule_authority or {}).get("version"),
        "officialScheduleAuthorityFingerprint": (schedule_authority or {}).get("fingerprint"),
    })
    return {"ok": True, "pull": {
        "pull_id": pull_id,
        "sport": sport,
        "pulled_at": pulled_at,
        "slate_date": slate,
        "source": source,
        "interval_minutes": int(body.get("interval_minutes") or 15),
        "games": games,
        "provider_schedule_manifest": manifest,
        "provider_manifest_binding": {
            "version": PROVIDER_MANIFEST_VERSION,
            "fingerprint": manifest["fingerprint"],
            "gameCount": len(games),
            "pk": manifest_key["PK"],
            "sk": manifest_key["SK"],
            "immutable": True,
            "fullProviderSchedule": True,
            "officialScheduleBacked": bool(schedule_authority),
            "officialScheduleAuthorityVersion": (schedule_authority or {}).get("version"),
            "officialScheduleAuthorityFingerprint": (schedule_authority or {}).get("fingerprint"),
        },
        "meta": meta,
    }}


def _conditional_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    return str((response.get("Error") or {}).get("Code") or "") == "ConditionalCheckFailedException"


def _query_pull_items(sport: str, slate: str, limit: int = 500) -> List[Dict[str, Any]]:
    if PULLS is None:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    out: List[Dict[str, Any]] = []
    start_key = None
    size = min(max(int(limit), 1), 500)
    while len(out) < size:
        args: Dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(f"PULLS#{sport}#{slate}"),
            "ScanIndexForward": True,
            "Limit": size - len(out),
            "ConsistentRead": True,
        }
        if start_key:
            args["ExclusiveStartKey"] = start_key
        response = PULLS.query(**args)
        for raw_item in response.get("Items", []):
            if not isinstance(raw_item, dict) or raw_item.get("record_type") != "pull_run":
                continue
            item = copy.deepcopy(raw_item)
            pull = item.get("data")
            if not isinstance(pull, dict):
                continue
            pull["canonicalPullStorage"] = {
                "pk": item.get("PK"),
                "sk": item.get("SK"),
                "recordType": item.get("record_type"),
            }
            out.append(item)
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            break
    return out[:size]


def _provider_manifest_store_response(pull: Dict[str, Any], *, created: bool) -> Dict[str, Any]:
    manifest = pull.get("provider_schedule_manifest") or {}
    manifest_key = _provider_manifest_key(manifest)
    schedule_authority = manifest.get("scheduleAuthority") or {}
    return {
        "pk": manifest_key["PK"],
        "sk": manifest_key["SK"],
        "version": PROVIDER_MANIFEST_VERSION,
        "fingerprint": manifest.get("fingerprint"),
        "game_count": len(pull.get("games") or []),
        "immutable": True,
        "full_provider_schedule": True,
        "created": created,
        "official_schedule_backed": bool(schedule_authority),
        "official_schedule_authority_version": schedule_authority.get("version"),
        "official_schedule_authority_fingerprint": schedule_authority.get("fingerprint"),
        "official_schedule_game_count": schedule_authority.get("officialGameCount"),
    }


def _stored_pull_result(
    pull: Dict[str, Any],
    storage_key: Dict[str, Any],
    *,
    manifest_created: bool,
    deduped: bool,
) -> Dict[str, Any]:
    result = {
        "ok": True,
        "stored": {
            "pk": storage_key.get("PK") or storage_key.get("pk"),
            "sk": storage_key.get("SK") or storage_key.get("sk"),
            "pull_id": pull.get("pull_id"),
            "game_count": len(pull.get("games") or []),
            "provider_manifest": _provider_manifest_store_response(
                pull,
                created=manifest_created,
            ),
        },
        "pull": copy.deepcopy(pull),
        "deduped": bool(deduped),
        "dedupeVersion": INTRINSIC_PULL_IDEMPOTENCY_VERSION,
    }
    slot = pull_slot_start(pull.get("pulled_at"))
    result["canonicalSlot"] = {
        "version": PULL_SLOT_VERSION,
        "slotStartUtc": slot.isoformat() if slot else None,
        "canonicalPullId": pull.get("pull_id"),
        "canonicalPulledAtUtc": pull.get("pulled_at"),
        "retryReturnedExistingCanonicalPull": bool(deduped),
    }
    return result


def _existing_canonical_pull_for_slot(
    sport: str,
    slate: str,
    slot: datetime,
) -> Optional[Tuple[Dict[str, Any], Dict[str, str]]]:
    try:
        items = _query_pull_items(sport, slate, 500)
    except Exception:
        return None
    candidates: List[Tuple[datetime, str, str, Dict[str, Any], Dict[str, str]]] = []
    for item in items:
        pull = copy.deepcopy(item.get("data") or {})
        if pull_slot_start(pull.get("pulled_at")) != slot:
            continue
        if _pull_integrity_errors(
            pull,
            sport=sport,
            slate=slate,
            require_manifest=True,
        ):
            continue
        pulled_at = _parse_utc(pull.get("pulled_at"))
        if pulled_at is None:
            continue
        pull.pop("canonicalPullStorage", None)
        candidates.append((
            pulled_at,
            str(pull.get("pull_id") or ""),
            pull_payload_fingerprint(pull),
            pull,
            {"PK": str(item.get("PK") or ""), "SK": str(item.get("SK") or "")},
        ))
    if not candidates:
        return None
    _, _, _, pull, key = min(candidates, key=lambda value: value[:3])
    return pull, key


def store_pull(body: Dict[str, Any]) -> Dict[str, Any]:
    if PULLS is None:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    n = normalize_pull(body)
    if not n.get("ok"):
        return n
    p = n["pull"]
    slot = pull_slot_start(p.get("pulled_at"))
    if slot is None:
        return {"ok": False, "error": "pulled_at_invalid_for_canonical_slot"}

    # Migration-safe: if this quarter-hour already contains a legacy
    # timestamp-keyed pull, return that exact earliest valid row. New writes use
    # the deterministic slot key below, whose conditional put handles races.
    existing_slot = _existing_canonical_pull_for_slot(
        p["sport"],
        p["slate_date"],
        slot,
    )
    if existing_slot:
        existing_pull, existing_key = existing_slot
        return _stored_pull_result(
            existing_pull,
            existing_key,
            manifest_created=False,
            deduped=True,
        )

    manifest = p["provider_schedule_manifest"]
    manifest_key = _provider_manifest_key(manifest)
    manifest_item = {
        **manifest_key,
        "record_type": PROVIDER_MANIFEST_RECORD_TYPE,
        "sport": p["sport"],
        "slate_date": p["slate_date"],
        "pulled_at": p["pulled_at"],
        "pull_id": p["pull_id"],
        "manifest_version": PROVIDER_MANIFEST_VERSION,
        "manifest_fingerprint": manifest["fingerprint"],
        "write_once": True,
        "data": ddb_safe(manifest),
        "created_at": now(),
    }
    manifest_created = True
    try:
        PULLS.put_item(
            Item=manifest_item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except Exception as exc:
        if not _conditional_failure(exc):
            raise
        existing = PULLS.get_item(Key=manifest_key, ConsistentRead=True).get("Item")
        if (
            not isinstance(existing, dict)
            or existing.get("record_type") != PROVIDER_MANIFEST_RECORD_TYPE
            or str(existing.get("manifest_fingerprint") or "") != str(manifest["fingerprint"])
            or provider_manifest_fingerprint(existing.get("data") or {}) != manifest["fingerprint"]
        ):
            raise RuntimeError("IMMUTABLE_PROVIDER_MANIFEST_COLLISION") from exc
        manifest_created = False
    slot_key = _pull_slot_key(p["sport"], p["slate_date"], slot)
    item = {
        **slot_key,
        "record_type": "pull_run",
        "pull_store_version": INTRINSIC_PULL_IDEMPOTENCY_VERSION,
        "canonical_slot_version": PULL_SLOT_VERSION,
        "slot_start_utc": slot.isoformat(),
        "sport": p["sport"],
        "slate_date": p["slate_date"],
        "pulled_at": p["pulled_at"],
        "pull_id": p["pull_id"],
        "data": ddb_safe(p),
        "created_at": now(),
    }
    try:
        PULLS.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except Exception as exc:
        if not _conditional_failure(exc):
            raise
        existing_item = PULLS.get_item(Key=slot_key, ConsistentRead=True).get("Item")
        existing_pull = (existing_item or {}).get("data")
        if (
            not isinstance(existing_item, dict)
            or existing_item.get("record_type") != "pull_run"
            or not isinstance(existing_pull, dict)
            or _pull_integrity_errors(
                existing_pull,
                sport=p["sport"],
                slate=p["slate_date"],
                require_manifest=True,
            )
            or pull_slot_start(existing_pull.get("pulled_at")) != slot
        ):
            raise RuntimeError("CANONICAL_PULL_SLOT_COLLISION_INVALID_READBACK") from exc
        authority_errors = validate_provider_schedule_manifest(
            existing_pull,
            p["slate_date"],
            verify_immutable_storage=True,
        )
        if authority_errors:
            raise RuntimeError(
                "CANONICAL_PULL_SLOT_EXISTING_MANIFEST_INVALID:"
                + ",".join(authority_errors)
            ) from exc
        return _stored_pull_result(
            existing_pull,
            slot_key,
            manifest_created=False,
            deduped=True,
        )
    authority_errors = validate_provider_schedule_manifest(
        p,
        p["slate_date"],
        verify_immutable_storage=True,
    )
    if authority_errors:
        raise RuntimeError(
            "PROVIDER_SCHEDULE_MANIFEST_READBACK_INVALID:"
            + ",".join(authority_errors)
        )
    return _stored_pull_result(
        p,
        slot_key,
        manifest_created=manifest_created,
        deduped=False,
    )


def query_pulls_raw(sport: str, date: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    if PULLS is None:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    sport = sport_key(sport)
    date = date or today()
    limit = min(max(int(limit), 1), 500)
    rows = [copy.deepcopy(item.get("data") or {}) for item in _query_pull_items(sport, date, limit)]
    if sport == "mlb":
        rows = [_filter_mlb_model_pull(row) for row in rows]
    return sorted(
        rows,
        key=lambda pull: (
            _parse_utc(pull.get("pulled_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(pull.get("pull_id") or ""),
            pull_payload_fingerprint(pull),
        ),
    )


def query_pulls(sport: str, date: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    """Read canonical scoring pulls; raw duplicates require query_pulls_raw."""
    requested_sport = sport_key(sport)
    requested_date = date or today()
    size = min(max(int(limit), 1), 500)
    raw = query_pulls_raw(requested_sport, requested_date, 500)
    canonical = canonicalize_pull_slots(
        raw,
        sport=requested_sport,
        slate=requested_date,
    )
    return canonical[:size]


def book_probs(game: Dict[str, Any]) -> Dict[str, Any]:
    hv, av, bp = [], [], {}
    books = game.get("books") or {}
    for b in [x for x in BOOKS if x in books] + [x for x in books if x not in BOOKS]:
        pair = vig((books.get(b) or {}).get("ml", {}).get("home"), (books.get(b) or {}).get("ml", {}).get("away"))
        if pair:
            hp, ap = pair
            hv.append(hp); av.append(ap); bp[b] = {"home": hp, "away": ap}
    if not hv:
        return {}
    return {"home": sum(hv)/len(hv), "away": sum(av)/len(av), "book_count": len(hv), "book_divergence": (max(hv)-min(hv)) if len(hv) > 1 else 0, "book_probs": bp}


def mins(a: str, b: str) -> float:
    try:
        x = datetime.fromisoformat(str(a).replace("Z", "+00:00")); y = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        return max((y-x).total_seconds()/60.0, 1.0)
    except Exception:
        return 15.0


def side_signal(series: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
    vals = [float(x["probs"][side]) for x in series]
    pull_count, start, latest = len(vals), vals[0], vals[-1]
    delta = latest - start
    dur = mins(series[0].get("pulled_at"), series[-1].get("pulled_at")) if pull_count > 1 else 0
    velocity = (delta*100.0)/max(dur/60.0, .25) if pull_count > 1 else 0
    mid = max(1, pull_count//2)
    first = (vals[mid-1]-vals[0])/max(mid-1, 1) if pull_count > 2 else 0
    second = (vals[-1]-vals[mid-1])/max(pull_count-mid, 1) if pull_count > 2 else 0
    accel = second - first
    latest_gap = abs(float(series[-1]["probs"]["home"])-float(series[-1]["probs"]["away"]))
    div = float(series[-1]["probs"].get("book_divergence") or 0)
    reversals = 0
    if pull_count >= 3:
        signs = [1 if vals[i]-vals[i-1] > .0005 else -1 if vals[i]-vals[i-1] < -.0005 else 0 for i in range(1, pull_count)]
        reversals = sum(1 for i in range(1, len(signs)) if signs[i] and signs[i-1] and signs[i] != signs[i-1])
    tags = []
    if pull_count < 3: tags.append("LOW_PULL_DEPTH")
    if delta >= .018: tags.append("STEAM")
    if delta <= -.018: tags.append("RESISTANCE")
    if velocity >= 1.75: tags.append("MOMENTUM")
    if accel >= .004: tags.append("ACCELERATION")
    if accel <= -.004: tags.append("DECELERATION")
    if reversals: tags.append("REVERSAL")
    if latest_gap < .05: tags.append("COMPRESSED_MARKET")
    if div >= .035: tags.append("BOOK_DIVERGENCE")
    if reversals >= 2 or div >= .06: tags.append("CHAOS")
    if latest >= .56 and delta >= .012 and div < .035: tags.append("CERTAINTY_ANCHOR")
    if delta > 0 and latest < .50: tags.append("PUBLIC_FADE_CANDIDATE")
    if pull_count < 3: grade = "INSUFFICIENT_HISTORY"
    elif "CHAOS" in tags or ("REVERSAL" in tags and "BOOK_DIVERGENCE" in tags): grade = "FRAGILE"
    elif latest_gap < .05 or div >= .035: grade = "COIN_FLIP"
    elif latest >= .56 and delta >= .018 and div < .025: grade = "STRONG_SOLID"
    elif latest >= .525 and delta >= .008: grade = "SOLID"
    else: grade = "COIN_FLIP" if latest_gap < .08 else "FRAGILE"
    score = round(max(0, min(100, 50 + delta*700 + (latest-.5)*80 - div*300 - reversals*8)), 2)
    return {"side": side, "probStart": round(start, 5), "probLatest": round(latest, 5), "delta": round(delta, 5), "velocityPpHr": round(velocity, 3), "acceleration": round(accel, 5), "pullCount": pull_count, "durationMinutes": round(dur, 2), "latestGap": round(latest_gap, 5), "bookCount": int(series[-1]["probs"].get("book_count") or 0), "bookDivergence": round(div, 5), "reversals": reversals, "tags": sorted(set(tags)), "grade": grade, "score": score}


def signals(params: Dict[str, Any]) -> Dict[str, Any]:
    sport = sport_key(params.get("sport") or params.get("sport_key"))
    pulls = query_pulls(sport, params.get("slate_date"), params.get("limit") or 500)
    if len(pulls) < 2:
        return {"ok": True, "sport": sport, "pullCount": len(pulls), "signals": [], "message": "Need at least two 15-minute pulls before signal calculation."}
    out = []
    latest_games = pulls[-1].get("games", []) or []
    for game in latest_games:
        key = game.get("game_key") or game.get("game_id")
        series = []
        for p in pulls:
            for g in p.get("games", []) or []:
                if g.get("game_key") == key or g.get("game_id") == key:
                    pr = book_probs(g)
                    if pr: series.append({"pulled_at": p.get("pulled_at"), "game": g, "probs": pr})
                    break
        if not series: continue
        hs, aws = side_signal(series, "home"), side_signal(series, "away")
        best = hs if hs["score"] >= aws["score"] else aws
        out.append({"gameId": game.get("game_id"), "gameKey": key, "sport": sport, "homeTeam": game.get("home_team"), "awayTeam": game.get("away_team"), "commenceTime": game.get("commence_time"), "level": game.get("level"), "gender": game.get("gender"), "providerSportKey": game.get("provider_sport_key"), "selection": game.get("home_team") if best["side"] == "home" else game.get("away_team"), "selectedSide": best["side"], "grade": best["grade"], "score": best["score"], "tags": best["tags"], "homeSignal": hs, "awaySignal": aws})
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {"ok": True, "sport": sport, "slate_date": pulls[-1].get("slate_date"), "pullCount": len(pulls), "minimumParlayPulls": MIN_PARLAY_PULLS, "architecture": "15_min_pull_history", "signals": out}


def readiness(p: Dict[str, Any]) -> Dict[str, Any]:
    r = signals(p); ss = r.get("signals", [])
    elig = [s for s in ss if s.get("grade") in {"STRONG_SOLID", "SOLID", "COIN_FLIP"}]
    strong = [s for s in elig if s.get("grade") == "STRONG_SOLID"]
    pull_count = int(r.get("pullCount") or 0)
    if pull_count < MIN_PARLAY_PULLS:
        status = "WAITING_FOR_12TH_PULL"
    elif len(elig) >= 3 and strong:
        status = "READY"
    else:
        status = "BUILDING_ELIGIBLE_SIGNAL_DEPTH"
    return {"ok": True, "sport": r.get("sport"), "slate_date": r.get("slate_date"), "status": status, "pullCount": pull_count, "eligibleSignals": len(elig), "strongSignals": len(strong), "minimumParlayPulls": MIN_PARLAY_PULLS, "minimumParlayHistoryMinutes": MIN_PARLAY_PULLS * 15, "parlayEligible": status == "READY", "notes": ["Uses many timestamped pulls, not fixed T1-T3 snapshots.", "Three-leg parlays are refused until the 12th 15-minute pull for this sport/slate."]}


# Backward-compatible API route contract used by inqsi_api.
from inqsi_pull_history_routes import handle_pull_history_route
