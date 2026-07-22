from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


ARCHIVE_SCHEMA_VERSION = "INQSI-TENNIS-PREMATCH-ARCHIVE-v1"
_SAFE_PART = re.compile(r"[^A-Za-z0-9_.=-]+")


def _safe_part(value: Any) -> str:
    text = _SAFE_PART.sub("_", str(value or "unknown").strip())
    return text[:180] or "unknown"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _encode_parquet(records: List[Dict[str, Any]]) -> bytes:
    """Encode a compact, stable training envelope with ZSTD compression.

    PyArrow is deliberately imported lazily so configuration/health probes do
    not pay its cold-start cost. The Lambda deployment package pins the wheel.
    """

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised by deploy cold start
        raise RuntimeError("tennis_parquet_runtime_missing") from exc

    fields = {
        "schema_version": [],
        "sport": [],
        "slate_date_et": [],
        "slot_utc": [],
        "tournament_key": [],
        "event_id": [],
        "attempt": [],
        "record_status": [],
        "observed_at_utc": [],
        "payload_json": [],
    }
    for record in records:
        payload = dict(record.get("payload") or {})
        fields["schema_version"].append(ARCHIVE_SCHEMA_VERSION)
        fields["sport"].append("tennis")
        fields["slate_date_et"].append(str(record.get("slate_date_et") or ""))
        fields["slot_utc"].append(str(record.get("slot_utc") or ""))
        fields["tournament_key"].append(str(record.get("tournament_key") or ""))
        fields["event_id"].append(str(record.get("event_id") or ""))
        fields["attempt"].append(int(record.get("attempt") or 1))
        fields["record_status"].append(str(record.get("record_status") or "UNKNOWN"))
        fields["observed_at_utc"].append(str(record.get("observed_at_utc") or ""))
        fields["payload_json"].append(_canonical_json(payload))

    schema = pa.schema(
        [
            pa.field("schema_version", pa.string(), nullable=False),
            pa.field("sport", pa.string(), nullable=False),
            pa.field("slate_date_et", pa.string(), nullable=False),
            pa.field("slot_utc", pa.string(), nullable=False),
            pa.field("tournament_key", pa.string(), nullable=False),
            pa.field("event_id", pa.string(), nullable=False),
            pa.field("attempt", pa.int32(), nullable=False),
            pa.field("record_status", pa.string(), nullable=False),
            pa.field("observed_at_utc", pa.string(), nullable=False),
            pa.field("payload_json", pa.string(), nullable=False),
        ],
        metadata={b"inqsi_schema_version": ARCHIVE_SCHEMA_VERSION.encode("utf-8")},
    )
    table = pa.Table.from_pydict(fields, schema=schema)
    output = io.BytesIO()
    pq.write_table(
        table,
        output,
        compression="zstd",
        compression_level=6,
        use_dictionary=True,
        write_statistics=True,
        version="2.6",
    )
    return output.getvalue()


@dataclass(frozen=True)
class ArchiveReceipt:
    bucket: str
    key: str
    sha256: str
    byte_count: int
    record_count: int
    created: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": ARCHIVE_SCHEMA_VERSION,
            "bucket": self.bucket,
            "key": self.key,
            "sha256": self.sha256,
            "byteCount": self.byte_count,
            "recordCount": self.record_count,
            "created": self.created,
            "format": "parquet",
            "compression": "zstd",
        }


class S3ParquetTennisArchive:
    """Conditional-create archive; the runtime role intentionally has no delete."""

    def __init__(self, bucket: str, *, s3_client: Any = None):
        if not str(bucket or "").strip():
            raise RuntimeError("TENNIS_ARCHIVE_BUCKET is required")
        if s3_client is None:
            import boto3

            s3_client = boto3.client("s3")
        self.bucket = str(bucket).strip()
        self.s3 = s3_client

    @staticmethod
    def _key(
        *, slate_date_et: str, slot_utc: str, tournament_key: str, attempt: int
    ) -> str:
        return (
            f"prematch/{ARCHIVE_SCHEMA_VERSION}/"
            f"slate_date_et={_safe_part(slate_date_et)}/"
            f"slot_utc={_safe_part(slot_utc)}/"
            f"tournament_key={_safe_part(tournament_key)}/"
            f"attempt={max(int(attempt), 1):03d}.parquet"
        )

    def archive_tournament(
        self,
        records: Iterable[Dict[str, Any]],
        *,
        slate_date_et: str,
        slot_utc: str,
        tournament_key: str,
        attempt: int,
    ) -> Dict[str, Any]:
        rows = [dict(record) for record in records]
        if not rows:
            raise RuntimeError("tennis_archive_requires_at_least_one_record")
        body = _encode_parquet(rows)
        digest = hashlib.sha256(body).hexdigest()
        key = self._key(
            slate_date_et=slate_date_et,
            slot_utc=slot_utc,
            tournament_key=tournament_key,
            attempt=attempt,
        )
        created = True
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType="application/vnd.apache.parquet",
                ServerSideEncryption="AES256",
                Metadata={
                    "schema-version": ARCHIVE_SCHEMA_VERSION,
                    "sha256": digest,
                    "record-count": str(len(rows)),
                },
                IfNoneMatch="*",
            )
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            code = str((response.get("Error") or {}).get("Code") or "")
            if status == 412 or code in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
            }:
                created = False
            else:
                raise RuntimeError("tennis_archive_write_failed") from None
        return ArchiveReceipt(
            bucket=self.bucket,
            key=key,
            sha256=digest,
            byte_count=len(body),
            record_count=len(rows),
            created=created,
        ).as_dict()


class InMemoryTennisArchive:
    """Deterministic acknowledgement sink for unit tests."""

    def __init__(self):
        self.rows: Dict[tuple[str, str, str, int], List[Dict[str, Any]]] = {}

    def archive_tournament(
        self,
        records: Iterable[Dict[str, Any]],
        *,
        slate_date_et: str,
        slot_utc: str,
        tournament_key: str,
        attempt: int,
    ) -> Dict[str, Any]:
        values = [dict(record) for record in records]
        if not values:
            raise RuntimeError("tennis_archive_requires_at_least_one_record")
        key = (slate_date_et, slot_utc, tournament_key, int(attempt))
        created = key not in self.rows
        self.rows.setdefault(key, values)
        digest = hashlib.sha256(_canonical_json(values).encode("utf-8")).hexdigest()
        return {
            "schemaVersion": ARCHIVE_SCHEMA_VERSION,
            "bucket": "memory",
            "key": "/".join(map(str, key)),
            "sha256": digest,
            "byteCount": len(_canonical_json(values).encode("utf-8")),
            "recordCount": len(values),
            "created": created,
            "format": "parquet-test-double",
            "compression": "zstd",
        }
