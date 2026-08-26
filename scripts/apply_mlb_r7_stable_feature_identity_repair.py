#!/usr/bin/env python3
"""Install the MLB-only movement identity and trainer transport repairs."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANUAL_PULL = ROOT / "hello_world/mlb_manual_pull.py"
INVOKER = ROOT / "scripts/invoke_mlb_trainer_with_retry.py"

IMPORT_ANCHOR = '''try:
    from mlb_signal_api import _delta_for_game, _game_index
except Exception:
    _delta_for_game = None
    _game_index = None
'''
IMPORT_REPLACEMENT = IMPORT_ANCHOR + '''
try:
    import mlb_movement_feature_identity_v2 as movement_identity_v2
except Exception:
    movement_identity_v2 = None
'''

MOVEMENT_BLOCK = r'''def _all_hot_snapshots_for_game_date(game_date: str) -> List[Dict[str, Any]]:
    if snapshots_tbl is None:
        return []
    pk = f"SPORT#mlb#DATE#{game_date}"
    expression = Key("PK").eq(pk) & Key("SK").begins_with(
        f"HOT#GAME_DATE#{game_date}"
    )
    rows: List[Dict[str, Any]] = []
    start_key = None
    while True:
        kwargs: Dict[str, Any] = {
            "KeyConditionExpression": expression,
            "ScanIndexForward": True,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        response = snapshots_tbl.query(**kwargs)
        rows.extend(response.get("Items") or [])
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            break
    return sorted(rows, key=lambda row: str(row.get("asof") or ""))


def _latest_two_hot_snapshots_for_game_date(
    game_date: str, limit: int = 12
) -> List[Dict[str, Any]]:
    # Backward-compatible helper retained for diagnostics. Production movement
    # reconstruction uses the complete immutable pregame history below.
    rows = _all_hot_snapshots_for_game_date(game_date)
    return rows[-2:]


def _movement_strength(delta: float, agreeing_books: int, disagreeing_books: int) -> str:
    abs_delta = abs(float(delta or 0))
    if abs_delta >= 0.018 and agreeing_books >= 2 and disagreeing_books == 0:
        return "HIGH"
    if abs_delta >= 0.006 and agreeing_books >= 2:
        return "MEDIUM"
    if abs_delta > 0:
        return "LOW"
    return "FLAT"


def _store_hot_movement_features(*, game_date: str, asof: str, run: str) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        return {"ok": False, "stored": 0, "error": "SIGNAL_LEDGER_TABLE not configured"}
    if _delta_for_game is None or movement_identity_v2 is None:
        return {
            "ok": False,
            "stored": 0,
            "error": "identity_stable_movement_helpers_unavailable",
        }

    snapshots = _all_hot_snapshots_for_game_date(game_date)
    derived = movement_identity_v2.derive_latest_features(
        snapshots,
        delta_for_game=_delta_for_game,
        movement_strength=_movement_strength,
    )
    stored = 0
    errors: List[str] = []
    samples: List[Dict[str, Any]] = []
    for row in derived:
        identity = str(row.get("stable_identity") or "")
        latest_asof = str(row.get("latest_asof") or "")
        if not identity or not latest_asof:
            errors.append("derived_feature_missing_stable_identity_or_asof")
            continue
        safe_identity = identity.replace("#", "_")
        feature = {
            "PK": f"ML_FEATURE#mlb#{game_date}",
            "SK": f"HOT_DELTA#{latest_asof}#IDENTITY#{safe_identity}",
            "entity_type": "HOT_PULL_MOVEMENT_FEATURE",
            "sport": "mlb",
            "platform_version": PLATFORM_VERSION,
            "game_date_et": game_date,
            "feature_version": movement_identity_v2.VERSION,
            "created_at": _now_iso(),
            "run": run,
            "date_isolated": True,
            "hot_only": True,
            "label_status": "PENDING_RESULT",
            **row,
        }
        try:
            signal_ledger_tbl.put_item(Item=_ddb_safe(feature))
            stored += 1
            samples.append(
                {
                    "stable_identity": identity,
                    "official_game_pk": row.get("official_game_pk"),
                    "hot_team": row.get("hot_team"),
                    "hot_delta": round(float(row.get("hot_delta") or 0.0), 6),
                    "movement_strength": row.get("movement_strength"),
                    "previous_asof": row.get("previous_asof"),
                    "latest_asof": row.get("latest_asof"),
                }
            )
        except Exception as exc:
            errors.append(f"{identity}: {type(exc).__name__}")

    return {
        "ok": len(errors) == 0,
        "stored": stored,
        "derived": len(derived),
        "snapshotCount": len(snapshots),
        "feature_version": movement_identity_v2.VERSION,
        "immutablePregameOnly": True,
        "outcomeDataUsed": False,
        "postStartObservationUsed": False,
        "predictionsWritten": 0,
        "locksWritten": 0,
        "labelsWritten": 0,
        "errors": errors,
        "sample": samples[:20],
    }


'''

MODE_ANCHOR = '''        payload = _event_payload(event)
        gate = _scheduled_start_gate(event, payload)
'''
MODE_REPLACEMENT = '''        payload = _event_payload(event)
        if str(payload.get("mode") or "") == "movement_identity_rebuild":
            game_date = str(
                payload.get("game_date_et")
                or payload.get("slate_date_et")
                or ""
            )
            try:
                datetime.strptime(game_date, "%Y-%m-%d")
            except Exception:
                return _resp(400, {
                    "ok": False,
                    "sport": "mlb",
                    "mode": "movement_identity_rebuild",
                    "error": "valid_game_date_et_required",
                })
            rebuilt = _store_hot_movement_features(
                game_date=game_date,
                asof=_now_iso(),
                run=str(payload.get("run") or "movement_identity_rebuild"),
            )
            return _resp(200 if rebuilt.get("ok") else 503, {
                "sport": "mlb",
                "mode": "movement_identity_rebuild",
                "game_date_et": game_date,
                "sharedRootStackDeployed": False,
                "otherSportChanged": False,
                "immutablePredictionHistoryRewritten": False,
                "postStartPredictionCreated": False,
                "directPredictionWrite": False,
                "directLockWrite": False,
                "directLabelWrite": False,
                **rebuilt,
            })
        gate = _scheduled_start_gate(event, payload)
'''


def patch_manual_pull(source: str) -> str:
    out = source
    if "import mlb_movement_feature_identity_v2 as movement_identity_v2" not in out:
        if out.count(IMPORT_ANCHOR) != 1:
            raise RuntimeError("manual-pull movement import anchor drifted")
        out = out.replace(IMPORT_ANCHOR, IMPORT_REPLACEMENT, 1)

    start_marker = "def _latest_two_hot_snapshots_for_game_date"
    end_marker = "def _record_snapshot_audit_safe"
    start = out.find(start_marker)
    end = out.find(end_marker, start)
    if start < 0 or end < 0:
        raise RuntimeError("manual-pull movement block anchors drifted")
    if "identity_stable_movement_helpers_unavailable" not in out[start:end]:
        out = out[:start] + MOVEMENT_BLOCK + out[end:]

    if '== "movement_identity_rebuild"' not in out:
        if out.count(MODE_ANCHOR) != 1:
            raise RuntimeError("manual-pull rebuild-mode anchor drifted")
        out = out.replace(MODE_ANCHOR, MODE_REPLACEMENT, 1)
    return out


def patch_invoker(source: str) -> str:
    out = source
    old_import = "from botocore.exceptions import ClientError\n"
    new_import = (
        "from botocore.exceptions import (\n"
        "    ClientError,\n"
        "    ConnectionClosedError,\n"
        "    EndpointConnectionError,\n"
        "    ReadTimeoutError,\n"
        ")\n"
    )
    if "ConnectionClosedError" not in out:
        if out.count(old_import) != 1:
            raise RuntimeError("trainer invoker exception import drifted")
        out = out.replace(old_import, new_import, 1)

    old_counters = '''    invocation_attempts = 0
    pre_admission_failures = 0
    lease_contention_failures = 0
    lease_deadline: Optional[float] = None
'''
    new_counters = '''    invocation_attempts = 0
    pre_admission_failures = 0
    lease_contention_failures = 0
    transport_failures = 0
    lease_deadline: Optional[float] = None
'''
    if "transport_failures = 0" not in out:
        if out.count(old_counters) != 1:
            raise RuntimeError("trainer invoker counter anchor drifted")
        out = out.replace(old_counters, new_counters, 1)

    client_error_anchor = '''        except ClientError as exc:
            capacity_kind = _pre_admission_capacity_kind(exc)
'''
    transport_block = '''        except (ConnectionClosedError, EndpointConnectionError, ReadTimeoutError) as exc:
            # The exact trainer execution lease prevents overlap if AWS admitted
            # the request before the response channel closed. Repeating the same
            # immutable-evidence run is idempotent and cannot promote authority.
            if not (retry_execution_lease or mode == STATUS_MODE):
                raise
            transport_failures += 1
            if transport_failures >= 3:
                raise DeployInvokeError(
                    "lambda_transport_retry_exhausted"
                ) from exc
            delay = _backoff_seconds(transport_failures)
            print(
                "AWS MLB trainer response transport closed; retrying through "
                f"the exact execution lease {transport_failures}/3 in {delay}s",
                file=sys.stderr,
            )
            sleep(delay)
            continue
        except ClientError as exc:
            capacity_kind = _pre_admission_capacity_kind(exc)
'''
    if "lambda_transport_retry_exhausted" not in out:
        if out.count(client_error_anchor) != 1:
            raise RuntimeError("trainer invoker exception anchor drifted")
        out = out.replace(client_error_anchor, transport_block, 1)

    config_anchor = '''                read_timeout=1000,
                retries={"total_max_attempts": 1, "mode": "standard"},
'''
    config_replacement = '''                read_timeout=1000,
                tcp_keepalive=True,
                retries={"total_max_attempts": 1, "mode": "standard"},
'''
    if "tcp_keepalive=True" not in out:
        if out.count(config_anchor) != 1:
            raise RuntimeError("trainer invoker client config anchor drifted")
        out = out.replace(config_anchor, config_replacement, 1)
    return out


def main() -> int:
    manual_source = MANUAL_PULL.read_text(encoding="utf-8")
    invoker_source = INVOKER.read_text(encoding="utf-8")
    manual_patched = patch_manual_pull(manual_source)
    invoker_patched = patch_invoker(invoker_source)
    MANUAL_PULL.write_text(manual_patched, encoding="utf-8")
    INVOKER.write_text(invoker_patched, encoding="utf-8")
    print(
        "MLB R7 stable feature identity repair applied: "
        f"manualPullChanged={manual_patched != manual_source}, "
        f"invokerChanged={invoker_patched != invoker_source}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
