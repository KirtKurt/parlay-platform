from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Tuple

from contracts import (
    FEATURE_SCHEMA_VERSION,
    RUN_MANIFEST_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    WINDOW_SCHEMA_VERSION,
    ddb_safe,
    from_ddb,
)


MAX_ITEM_BYTES = 360_000


def _conditional_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    return code == "ConditionalCheckFailedException"


def _assert_item_size(item: Dict[str, Any]) -> None:
    size = len(
        json.dumps(item, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    )
    if size > MAX_ITEM_BYTES:
        raise RuntimeError(f"tennis_item_too_large:{size}")


def match_key(event_id: str, slot_utc: str) -> Dict[str, str]:
    return {
        "PK": f"TENNIS#MATCH#{event_id}",
        "SK": f"PULL#SLOT#{slot_utc}",
    }


def signal_key(event_id: str, slot_utc: str) -> Dict[str, str]:
    return {
        "PK": f"TENNIS#MATCH#{event_id}",
        "SK": f"FEATURE#SLOT#{slot_utc}",
    }


def run_key(slate_date_et: str, slot_utc: str) -> Dict[str, str]:
    return {
        "PK": f"TENNIS#RUN#{slate_date_et}",
        "SK": f"SLOT#{slot_utc}",
    }


def window_key(slate_date_et: str) -> Dict[str, str]:
    return {"PK": f"TENNIS#WINDOW#{slate_date_et}", "SK": "STATE"}


def event_state_key(event_id: str) -> Dict[str, str]:
    return {"PK": f"TENNIS#MATCH#{event_id}", "SK": "STATE#PREMATCH_CUTOFF"}


def tournament_checkpoint_key(
    slate_date_et: str, slot_utc: str, tournament_key: str
) -> Dict[str, str]:
    return {
        "PK": f"TENNIS#RUN#{slate_date_et}",
        "SK": f"SLOT#{slot_utc}#TOURNAMENT#{tournament_key}",
    }


class DynamoTennisStore:
    def __init__(self, snapshots_table: str, signals_table: str):
        import boto3

        dynamodb = boto3.resource("dynamodb")
        self.snapshots = dynamodb.Table(snapshots_table)
        self.signals = dynamodb.Table(signals_table)

    def get_window_state(self, slate_date_et: str) -> Optional[Dict[str, Any]]:
        response = self.snapshots.get_item(
            Key=window_key(slate_date_et), ConsistentRead=True
        )
        item = response.get("Item")
        return from_ddb(item) if isinstance(item, dict) else None

    def open_window(
        self,
        slate_date_et: str,
        *,
        first_match_at_utc: str,
        gate_open_at_utc: str,
        opened_at_utc: str,
        latest_first_match_at_utc: str,
    ) -> Dict[str, Any]:
        response = self.snapshots.update_item(
            Key=window_key(slate_date_et),
            UpdateExpression=(
                "SET schema_version=:version, record_type=:record_type, sport=:sport, "
                "#window_state=:active, slate_date_et=:slate, "
                "first_match_at_utc=if_not_exists(first_match_at_utc,:first), "
                "gate_open_at_utc=if_not_exists(gate_open_at_utc,:gate), "
                "opened_at_utc=if_not_exists(opened_at_utc,:opened), "
                "latest_first_match_at_utc=:latest, updated_at_utc=:updated"
            ),
            ExpressionAttributeNames={"#window_state": "window_state"},
            ExpressionAttributeValues=ddb_safe(
                {
                    ":version": WINDOW_SCHEMA_VERSION,
                    ":record_type": "tennis_window_state",
                    ":sport": "tennis",
                    ":active": "ACTIVE",
                    ":slate": slate_date_et,
                    ":first": first_match_at_utc,
                    ":gate": gate_open_at_utc,
                    ":opened": opened_at_utc,
                    ":latest": latest_first_match_at_utc,
                    ":updated": opened_at_utc,
                }
            ),
            ReturnValues="ALL_NEW",
        )
        return from_ddb(response.get("Attributes") or {})

    def complete_window(self, slate_date_et: str, completed_at_utc: str) -> None:
        state = self.get_window_state(slate_date_et)
        if not state or not state.get("opened_at_utc"):
            return
        self.snapshots.update_item(
            Key=window_key(slate_date_et),
            UpdateExpression=(
                "SET #window_state=:complete, completed_at_utc=:completed, "
                "updated_at_utc=:completed"
            ),
            ExpressionAttributeNames={"#window_state": "window_state"},
            ExpressionAttributeValues={
                ":complete": "COMPLETE",
                ":completed": completed_at_utc,
            },
        )

    def latch_event_cutoff(
        self, event_id: str, candidate_commence_at_utc: str, observed_at_utc: str
    ) -> str:
        """Persist the earliest start ever observed; later moves never relax it."""

        key = event_state_key(str(event_id))
        values = {
            ":candidate": candidate_commence_at_utc,
            ":observed": observed_at_utc,
            ":record_type": "tennis_prematch_cutoff_state",
            ":sport": "tennis",
            ":event_id": str(event_id),
        }
        try:
            response = self.snapshots.update_item(
                Key=key,
                UpdateExpression=(
                    "SET record_type=:record_type, sport=:sport, event_id=:event_id, "
                    "earliest_commence_at_utc=:candidate, updated_at_utc=:observed"
                ),
                ConditionExpression=(
                    "attribute_not_exists(earliest_commence_at_utc) OR "
                    "earliest_commence_at_utc > :candidate"
                ),
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
            return str(
                (response.get("Attributes") or {}).get(
                    "earliest_commence_at_utc", candidate_commence_at_utc
                )
            )
        except Exception as exc:
            if not _conditional_failure(exc):
                raise
        response = self.snapshots.get_item(Key=key, ConsistentRead=True)
        item = from_ddb(response.get("Item") or {})
        value = str(item.get("earliest_commence_at_utc") or "")
        if not value:
            raise RuntimeError("tennis_event_cutoff_state_missing")
        return value

    def store_event_snapshot(
        self,
        event: Dict[str, Any],
        *,
        slot_utc: str,
        observed_at_utc: str,
        slate_date_et: str,
    ) -> bool:
        event_id = str(event["event_id"])
        item = {
            **match_key(event_id, slot_utc),
            "record_type": "tennis_match_snapshot",
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "sport": "tennis",
            "event_id": event_id,
            "slate_date_et": slate_date_et,
            "slot_utc": slot_utc,
            "observed_at_utc": observed_at_utc,
            "data": copy.deepcopy(event),
        }
        _assert_item_size(item)
        try:
            self.snapshots.put_item(
                Item=ddb_safe(item),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
            return True
        except Exception as exc:
            if _conditional_failure(exc):
                return False
            raise

    def query_match_snapshots(
        self, event_id: str, *, limit: int = 500
    ) -> List[Dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        response = self.snapshots.query(
            KeyConditionExpression=Key("PK").eq(f"TENNIS#MATCH#{event_id}")
            & Key("SK").begins_with("PULL#SLOT#"),
            ScanIndexForward=True,
            ConsistentRead=True,
            Limit=min(max(int(limit), 1), 500),
        )
        return [from_ddb(item) for item in response.get("Items") or []]

    def store_signal(self, feature: Dict[str, Any], *, slot_utc: str) -> bool:
        event_id = str(feature["event_id"])
        item = {
            **signal_key(event_id, slot_utc),
            "record_type": "tennis_ml_feature",
            "schema_version": FEATURE_SCHEMA_VERSION,
            "sport": "tennis",
            "event_id": event_id,
            "slate_date_et": feature.get("slate_date_et"),
            "slot_utc": slot_utc,
            "data": copy.deepcopy(feature),
        }
        _assert_item_size(item)
        try:
            self.signals.put_item(
                Item=ddb_safe(item),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
            return True
        except Exception as exc:
            if _conditional_failure(exc):
                return False
            raise

    def store_run_manifest(self, manifest: Dict[str, Any], *, slot_utc: str) -> bool:
        item = {
            **run_key(str(manifest["slate_date_et"]), slot_utc),
            "record_type": "tennis_pull_run",
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "sport": "tennis",
            "slot_utc": slot_utc,
            "data": copy.deepcopy(manifest),
        }
        _assert_item_size(item)
        try:
            self.snapshots.put_item(
                Item=ddb_safe(item),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
            return True
        except Exception as exc:
            if _conditional_failure(exc):
                return False
            raise

    def upsert_run_manifest(self, manifest: Dict[str, Any], *, slot_utc: str) -> None:
        item = {
            **run_key(str(manifest["slate_date_et"]), slot_utc),
            "record_type": "tennis_pull_run",
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "sport": "tennis",
            "slot_utc": slot_utc,
            "complete": bool(manifest.get("complete")),
            "data": copy.deepcopy(manifest),
        }
        _assert_item_size(item)
        self.snapshots.put_item(Item=ddb_safe(item))

    def get_run_manifest(
        self, slate_date_et: str, *, slot_utc: str
    ) -> Optional[Dict[str, Any]]:
        response = self.snapshots.get_item(
            Key=run_key(slate_date_et, slot_utc), ConsistentRead=True
        )
        item = from_ddb(response.get("Item") or {})
        data = item.get("data")
        return data if isinstance(data, dict) else None

    def has_run_manifest(self, slate_date_et: str, *, slot_utc: str) -> bool:
        manifest = self.get_run_manifest(slate_date_et, slot_utc=slot_utc)
        return bool(manifest and manifest.get("complete") is True)

    def checkpoint_tournament(
        self,
        slate_date_et: str,
        *,
        slot_utc: str,
        tournament_key: str,
        observed_at_utc: str,
        archive_receipt: Dict[str, Any],
    ) -> bool:
        item = {
            **tournament_checkpoint_key(slate_date_et, slot_utc, tournament_key),
            "record_type": "tennis_tournament_slot_checkpoint",
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "sport": "tennis",
            "slate_date_et": slate_date_et,
            "slot_utc": slot_utc,
            "tournament_key": tournament_key,
            "status": "COMPLETE",
            "observed_at_utc": observed_at_utc,
            "archive_receipt": copy.deepcopy(archive_receipt),
        }
        _assert_item_size(item)
        try:
            self.snapshots.put_item(
                Item=ddb_safe(item),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
            return True
        except Exception as exc:
            if _conditional_failure(exc):
                return False
            raise

    def completed_tournament_keys(
        self, slate_date_et: str, *, slot_utc: str
    ) -> List[str]:
        from boto3.dynamodb.conditions import Key

        response = self.snapshots.query(
            KeyConditionExpression=Key("PK").eq(f"TENNIS#RUN#{slate_date_et}")
            & Key("SK").begins_with(f"SLOT#{slot_utc}#TOURNAMENT#"),
            ConsistentRead=True,
        )
        return sorted(
            str(item.get("tournament_key"))
            for item in (response.get("Items") or [])
            if item.get("status") == "COMPLETE" and item.get("tournament_key")
        )


class InMemoryTennisStore:
    """Deterministic store used by unit tests and local architecture checks."""

    def __init__(self):
        self.windows: Dict[str, Dict[str, Any]] = {}
        self.snapshots: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.features: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.runs: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.event_states: Dict[str, Dict[str, Any]] = {}
        self.tournament_checkpoints: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def get_window_state(self, slate_date_et: str) -> Optional[Dict[str, Any]]:
        row = self.windows.get(slate_date_et)
        return copy.deepcopy(row) if row else None

    def open_window(
        self,
        slate_date_et: str,
        *,
        first_match_at_utc: str,
        gate_open_at_utc: str,
        opened_at_utc: str,
        latest_first_match_at_utc: str,
    ) -> Dict[str, Any]:
        row = self.windows.setdefault(
            slate_date_et,
            {
                "schema_version": WINDOW_SCHEMA_VERSION,
                "record_type": "tennis_window_state",
                "sport": "tennis",
                "slate_date_et": slate_date_et,
                "window_state": "ACTIVE",
                "first_match_at_utc": first_match_at_utc,
                "gate_open_at_utc": gate_open_at_utc,
                "opened_at_utc": opened_at_utc,
            },
        )
        row["latest_first_match_at_utc"] = latest_first_match_at_utc
        row["updated_at_utc"] = opened_at_utc
        return copy.deepcopy(row)

    def complete_window(self, slate_date_et: str, completed_at_utc: str) -> None:
        if slate_date_et in self.windows:
            self.windows[slate_date_et]["window_state"] = "COMPLETE"
            self.windows[slate_date_et]["completed_at_utc"] = completed_at_utc

    def latch_event_cutoff(
        self, event_id: str, candidate_commence_at_utc: str, observed_at_utc: str
    ) -> str:
        key = str(event_id)
        state = self.event_states.get(key)
        if state is None or candidate_commence_at_utc < str(
            state.get("earliest_commence_at_utc") or "~"
        ):
            self.event_states[key] = {
                "record_type": "tennis_prematch_cutoff_state",
                "sport": "tennis",
                "event_id": key,
                "earliest_commence_at_utc": candidate_commence_at_utc,
                "updated_at_utc": observed_at_utc,
            }
        return str(self.event_states[key]["earliest_commence_at_utc"])

    def store_event_snapshot(
        self,
        event: Dict[str, Any],
        *,
        slot_utc: str,
        observed_at_utc: str,
        slate_date_et: str,
    ) -> bool:
        key = (str(event["event_id"]), slot_utc)
        if key in self.snapshots:
            return False
        self.snapshots[key] = {
            **match_key(key[0], slot_utc),
            "record_type": "tennis_match_snapshot",
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "sport": "tennis",
            "event_id": key[0],
            "slate_date_et": slate_date_et,
            "slot_utc": slot_utc,
            "observed_at_utc": observed_at_utc,
            "data": copy.deepcopy(event),
        }
        return True

    def query_match_snapshots(
        self, event_id: str, *, limit: int = 500
    ) -> List[Dict[str, Any]]:
        rows = [
            copy.deepcopy(item)
            for (stored_event_id, _), item in self.snapshots.items()
            if stored_event_id == str(event_id)
        ]
        return sorted(rows, key=lambda row: row["slot_utc"])[:limit]

    def store_signal(self, feature: Dict[str, Any], *, slot_utc: str) -> bool:
        key = (str(feature["event_id"]), slot_utc)
        if key in self.features:
            return False
        self.features[key] = copy.deepcopy(feature)
        return True

    def store_run_manifest(self, manifest: Dict[str, Any], *, slot_utc: str) -> bool:
        key = (str(manifest["slate_date_et"]), slot_utc)
        if key in self.runs:
            return False
        self.runs[key] = copy.deepcopy(manifest)
        return True

    def upsert_run_manifest(self, manifest: Dict[str, Any], *, slot_utc: str) -> None:
        key = (str(manifest["slate_date_et"]), slot_utc)
        self.runs[key] = copy.deepcopy(manifest)

    def get_run_manifest(
        self, slate_date_et: str, *, slot_utc: str
    ) -> Optional[Dict[str, Any]]:
        value = self.runs.get((str(slate_date_et), slot_utc))
        return copy.deepcopy(value) if value is not None else None

    def has_run_manifest(self, slate_date_et: str, *, slot_utc: str) -> bool:
        manifest = self.get_run_manifest(slate_date_et, slot_utc=slot_utc)
        return bool(manifest and manifest.get("complete") is True)

    def checkpoint_tournament(
        self,
        slate_date_et: str,
        *,
        slot_utc: str,
        tournament_key: str,
        observed_at_utc: str,
        archive_receipt: Dict[str, Any],
    ) -> bool:
        key = (str(slate_date_et), slot_utc, str(tournament_key))
        if key in self.tournament_checkpoints:
            return False
        self.tournament_checkpoints[key] = {
            "status": "COMPLETE",
            "observed_at_utc": observed_at_utc,
            "archive_receipt": copy.deepcopy(archive_receipt),
        }
        return True

    def completed_tournament_keys(
        self, slate_date_et: str, *, slot_utc: str
    ) -> List[str]:
        return sorted(
            tournament
            for (
                slate,
                stored_slot,
                tournament,
            ), item in self.tournament_checkpoints.items()
            if slate == str(slate_date_et)
            and stored_slot == slot_utc
            and item.get("status") == "COMPLETE"
        )
