"""Publish only KS1 date partitions, through the existing GitHub pipeline."""
import gzip
import hashlib
import io
import json
import os
from datetime import date as calendar_date

import pyarrow.compute as pc
import pyarrow.parquet as pq

from ks1.inventory import encode
from ks1.table import VERSION

PREFIX = "mlb/ks1/game-table-v1/"


def parquet_bytes(table):
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd", version="2.6")
    return buffer.getvalue()


def publish(s3, bucket, table, source_receipts, *, require_pipeline=True):
    if require_pipeline and not (os.environ.get("GITHUB_ACTIONS") == "true"
                                 and os.environ.get("GITHUB_REPOSITORY") == "KirtKurt/parlay-platform"
                                 and os.environ.get("GITHUB_REF") == "refs/heads/main"
                                 and os.environ.get("GITHUB_EVENT_NAME") in ("push", "workflow_dispatch")):
        raise ValueError("AWS publication is restricted to the repository main GitHub deploy job")
    frame = table.to_pandas()
    receipts, writes = [], []
    sources = gzip.compress(encode(source_receipts), mtime=0)
    sources_sha = hashlib.sha256(sources).hexdigest()
    for date in sorted(set(frame.date)):
        if calendar_date.fromisoformat(date).isoformat() != date:
            raise ValueError("invalid partition date")
        prefix = PREFIX + f"date={date}/"
        part = table.filter(pc.equal(table["date"], date))
        body = parquet_bytes(part)
        sha = hashlib.sha256(body).hexdigest()
        manifest_key = prefix + "manifest.json"
        existing = None
        try:
            existing = json.loads(s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read())
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                raise
        if existing and existing.get("parquet_sha256") == sha:
            manifest = existing
        else:
            data_key = prefix + f"games-{sha}.parquet"
            source_key = prefix + f"sources-{sources_sha}.json.gz"
            result = s3.put_object(Bucket=bucket, Key=data_key, Body=body,
                                   ContentType="application/vnd.apache.parquet", Metadata={"sha256": sha, "system": "KS1"})
            source_result = s3.put_object(Bucket=bucket, Key=source_key, Body=sources,
                                         ContentType="application/gzip", Metadata={"sha256": sources_sha, "system": "KS1"})
            manifest = {"system": "KS1", "phase": 1, "date": date, "table_version": VERSION,
                        "rows": part.num_rows, "parquet_key": data_key, "parquet_sha256": sha,
                        "parquet_version_id": result.get("VersionId"),
                        "source_receipts_key": source_key, "source_receipts_sha256": sources_sha,
                        "source_receipts_version_id": source_result.get("VersionId"),
                        "deployment_git_sha": os.environ.get("GITHUB_SHA")}
            # Publish the pointer last. Readers never observe a partial parquet.
            s3.put_object(Bucket=bucket, Key=manifest_key, Body=encode(manifest), ContentType="application/json")
            writes.extend([data_key, source_key, manifest_key])
        if manifest.get("date") != date or manifest.get("system") != "KS1" or not manifest["parquet_key"].startswith(prefix):
            raise ValueError("partition manifest escaped KS1 date scope")
        args = {"Bucket": bucket, "Key": manifest["parquet_key"]}
        if manifest.get("parquet_version_id"):
            args["VersionId"] = manifest["parquet_version_id"]
        result = s3.get_object(**args)
        stored = result["Body"].read()
        if hashlib.sha256(stored).hexdigest() != sha or not pq.read_table(io.BytesIO(stored)).equals(part):
            raise ValueError("published parquet failed readback")
        receipts.append({"date": date, "rows": part.num_rows, "manifest_key": manifest_key,
                         "parquet_sha256": sha, "version_id": result.get("VersionId"), "readback_verified": True})
    return {"bucket": bucket, "prefix": PREFIX, "partitions": receipts, "write_keys": writes}
