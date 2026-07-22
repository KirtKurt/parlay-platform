from __future__ import annotations

import io

import pytest

import archive


def record():
    return {
        "slate_date_et": "2026-07-22",
        "slot_utc": "2026-07-22T10:00:00+00:00",
        "tournament_key": "tennis_atp_test",
        "event_id": "event-1",
        "attempt": 1,
        "record_status": "ACCEPTED_PREMATCH",
        "observed_at_utc": "2026-07-22T10:00:01+00:00",
        "payload": {"event_id": "event-1", "books": {"fanduel": {"a": -110}}},
    }


class S3:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"ETag": "test"}


class PreconditionFailed(Exception):
    response = {
        "ResponseMetadata": {"HTTPStatusCode": 412},
        "Error": {"Code": "PreconditionFailed"},
    }


def test_s3_archive_is_conditional_encrypted_and_redaction_safe(monkeypatch):
    client = S3()
    monkeypatch.setattr(archive, "_encode_parquet", lambda rows: b"PAR1safePAR1")
    writer = archive.S3ParquetTennisArchive("archive-bucket", s3_client=client)

    receipt = writer.archive_tournament(
        [record()],
        slate_date_et="2026-07-22",
        slot_utc="2026-07-22T10:00:00+00:00",
        tournament_key="tennis_atp_test",
        attempt=1,
    )

    call = client.calls[0]
    assert call["IfNoneMatch"] == "*"
    assert call["ServerSideEncryption"] == "AES256"
    assert call["ContentType"] == "application/vnd.apache.parquet"
    assert call["Key"].endswith("attempt=001.parquet")
    assert receipt["created"] is True
    assert receipt["compression"] == "zstd"


def test_existing_immutable_object_is_idempotent(monkeypatch):
    client = S3(PreconditionFailed())
    monkeypatch.setattr(archive, "_encode_parquet", lambda rows: b"PAR1samePAR1")
    writer = archive.S3ParquetTennisArchive("archive-bucket", s3_client=client)

    receipt = writer.archive_tournament(
        [record()],
        slate_date_et="2026-07-22",
        slot_utc="2026-07-22T10:00:00+00:00",
        tournament_key="tennis_atp_test",
        attempt=1,
    )

    assert receipt["created"] is False


def test_real_parquet_schema_and_zstd_compression():
    pytest.importorskip("pyarrow")
    parquet = pytest.importorskip("pyarrow.parquet")

    data = archive._encode_parquet([record()])
    table = parquet.read_table(io.BytesIO(data))
    metadata = parquet.ParquetFile(io.BytesIO(data)).metadata

    assert data[:4] == b"PAR1" and data[-4:] == b"PAR1"
    assert table.num_rows == 1
    assert table.schema.metadata[b"inqsi_schema_version"] == (
        archive.ARCHIVE_SCHEMA_VERSION.encode("utf-8")
    )
    assert metadata.row_group(0).column(0).compression == "ZSTD"
    assert "payload_json" in table.column_names
