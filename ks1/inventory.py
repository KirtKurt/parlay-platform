"""Read existing AWS datasets; no provider downloads or AWS mutations."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = "mlb/development-data/research-v1/"
RECONSTRUCTED = "mlb/development-data/reconstructed-v1/"
BBS_SECRET_NAMES = (
    "BIG_BALLS_DATA_API_KEY", "BBD_API_KEY", "BIGBALLS_DATA_API_KEY",
    "BIG_BALLS_API_KEY", "BIGBALLS_API_KEY", "BIGBALLSDATA_API_KEY",
    "BIG_BALLS_DATA_KEY", "BBD_API_TOKEN", "BIGBALLS_DATA_KEY",
    "BIGBALLSDATA_KEY", "BIG_BALLS_KEY", "BBD_KEY",
)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class Reader:
    """Small read-only adapter. Content hashes bind all exported source objects."""
    def __init__(self, s3, bucket):
        self.s3, self.bucket, self.receipts = s3, bucket, []

    def read(self, key, *, bucket=None, version=None, sha=None):
        kwargs = {"Bucket": bucket or self.bucket, "Key": key}
        if version:
            kwargs["VersionId"] = version
        response = self.s3.get_object(**kwargs)
        body = response["Body"].read()
        actual = hashlib.sha256(body).hexdigest()
        expected = sha or response.get("Metadata", {}).get("sha256")
        if expected and actual != expected:
            raise ValueError("stored source checksum mismatch")
        self.receipts.append({"bucket": kwargs["Bucket"], "key": key,
                              "versionId": response.get("VersionId"), "sha256": actual})
        return json.loads(body)

    def pointer(self, pointer):
        if not pointer.get("versionId") or not pointer.get("sha256"):
            raise ValueError("unbound stored artifact")
        return self.read(pointer.get("key") or RESEARCH + pointer["name"],
                         bucket=pointer.get("bucket"), version=pointer["versionId"], sha=pointer["sha256"])

    def keys(self, prefix, *, bucket=None):
        return [o["Key"] for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket or self.bucket, Prefix=prefix) for o in page.get("Contents", [])]


def describe(path, grain, rows, date_field, columns=None):
    dates = sorted({str(r[date_field])[:10] for r in rows if r.get(date_field)})
    return {"path": path, "grain": grain, "rows": len(rows),
            "dateRange": [dates[0], dates[-1]] if dates else None,
            "columns": columns or sorted({k for r in rows for k in r})}


def legacy_inventory(cf, lam, s3):
    """Inspect retained stores referenced by repo templates; never activate them."""
    definitions = (
        ("parlay-platform-mlb-v8-fundamentals-shadow", "FundamentalsArtifactsBucketName",
         "FundamentalsCollectorFunctionName", ("mlb/v8/fundamentals/", "mlb/v8/historical-bbs/manifests/")),
        ("parlay-platform-mlb-odds-v8-shadow", "ShadowArtifactsBucketName", None, ("mlb/odds-v8-shadow/",)),
        ("parlay-platform-mlb-historical-optimizer", "HistoricalArtifactsBucketName",
         "HistoricalOptimizerFunctionName", ("mlb/v8/historical-bbs/manifests/",)),
    )
    results = []
    for stack, bucket_output, function_output, prefixes in definitions:
        item = {"stack": stack, "awsReadOnly": True, "datasets": []}
        try:
            response = cf.describe_stacks(StackName=stack)
            outputs = {r["OutputKey"]: r["OutputValue"] for r in response["Stacks"][0].get("Outputs", [])}
            bucket = outputs.get(bucket_output)
            item["bucket"] = bucket
            if function_output and outputs.get(function_output):
                values = lam.get_function_configuration(FunctionName=outputs[function_output]).get("Environment", {}).get("Variables", {})
                item["configuredProviderCredentialNames"] = sorted(k for k in ("ODDS_API_KEY", *BBS_SECRET_NAMES) if values.get(k))
            if bucket:
                for prefix in prefixes:
                    objects = [o for p in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
                               for o in p.get("Contents", [])]
                    data = {"path": f"s3://{bucket}/{prefix}", "objects": len(objects)}
                    if objects:
                        latest = max(objects, key=lambda o: o["LastModified"])
                        body = s3.get_object(Bucket=bucket, Key=latest["Key"])["Body"].read()
                        value = json.loads(body)
                        rows = value.get("records", value.get("games", value.get("featuredEvents", [])))
                        data.update(latestSampleKey=latest["Key"], columns=sorted(value),
                                    sampleRows=len(rows), rowColumns=sorted({k for r in rows if isinstance(r, dict) for k in r}),
                                    sampleSha256=hashlib.sha256(body).hexdigest())
                        dates = sorted({str(r.get("slateDateEt") or r.get("gameDate") or r.get("date"))[:10]
                                        for r in rows if isinstance(r, dict) and any(r.get(k) for k in ("slateDateEt", "gameDate", "date"))})
                        data["sampleRowDateRange"] = [dates[0], dates[-1]] if dates else None
                        key_dates = sorted({m.group(0) for o in objects for m in re.finditer(r"20\d{2}-\d{2}-\d{2}", o["Key"])})
                        data["objectKeyDateRange"] = [key_dates[0], key_dates[-1]] if key_dates else None
                        data["scope"] = "all object keys listed; latest object schema sampled; no availability or training eligibility claim"
                    item["datasets"].append(data)
            item["status"] = "INSPECTED" if bucket else "BUCKET_OUTPUT_NOT_PRESENT"
        except Exception as exc:
            item.update(status="NOT_VERIFIED", errorCode=getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__))
        results.append(item)
    return results


def inventory(output, *, region="us-east-1", stack="parlay-platform-dev"):
    import boto3
    from botocore.config import Config
    config = Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}, max_pool_connections=12)
    cf, lam, s3 = (boto3.client(n, region_name=region, config=config) for n in ("cloudformation", "lambda", "s3"))
    def physical(logical):
        return cf.describe_stack_resource(StackName=stack, LogicalResourceId=logical)["StackResourceDetail"]["PhysicalResourceId"]
    function = physical("MLBMLTrainingFunction")
    env = lam.get_function_configuration(FunctionName=function)["Environment"]["Variables"]
    bucket = env["MLB_ML_ARTIFACTS_BUCKET"]
    reader = Reader(s3, bucket)
    report = json.loads((ROOT / "runtime_reports/mlb_data_admission_latest.json").read_text())
    pointer = report["historicalDevelopment"]["artifact"]
    if pointer["bucket"] != bucket or not pointer["key"].startswith(RECONSTRUCTED):
        raise ValueError("historical source pointer outside discovered development scope")
    reconstructed = reader.pointer(pointer)
    dataset_pointer = reader.read(RESEARCH + "dataset.json")
    research = reader.pointer(dataset_pointer["artifact"])
    prior = reader.pointer(reader.read(RESEARCH + "prior-games.json")["artifact"])
    statcast = reader.pointer(reader.read(RESEARCH + "statcast.json")["artifact"])
    source_keys = sorted(reader.keys(RECONSTRUCTED + "source-games/"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        compact = list(pool.map(reader.read, source_keys))
    snapshot_keys = sorted(reader.keys(RESEARCH + "snapshots/"))
    snapshots = [{**reader.read(key), "ks1SourceKey": key} for key in snapshot_keys]
    historical_artifacts = {json.dumps(r["sourceArtifact"], sort_keys=True) for r in reconstructed["rows"]}
    first_archive_pointer = json.loads(sorted(historical_artifacts)[0])
    first_archive = reader.pointer(first_archive_pointer)
    datasets = [
        describe(f"s3://{bucket}/{pointer['key']}", "game; reconstructed pregame features and separate label",
                 reconstructed["rows"], "slateDateEt"),
        describe(f"s3://{bucket}/{RESEARCH}dataset.json", "game; whole-slate research inputs and final scores",
                 research["rows"], "slateDateEt"),
        describe(f"s3://{bucket}/{RECONSTRUCTED}source-games/", "completed game; team batting, starter-group pitching, relief usage",
                 compact, "startAtUtc"),
        describe(f"s3://{bucket}/{RESEARCH}prior-games.json", "completed game; full team/player boxes",
                 prior["games"], "startAtUtc"),
        describe(f"s3://{bucket}/{RESEARCH}prior-games.json#schedule", "official scheduled game", prior["schedule"], "gameDate"),
        describe(f"s3://{bucket}/{RESEARCH}statcast.json", "pitch", statcast["rows"], "game_date"),
        describe(f"s3://{bucket}/{RESEARCH}snapshots/", "original pregame game/checkpoint", snapshots, "slateDateEt"),
        {"path": f"s3://{first_archive_pointer['bucket']}/{first_archive_pointer['key']}",
         "grain": "archived slate; records are games, snapshotAudit records market observations",
         "referencedSlateArtifacts": len(historical_artifacts),
         "dateRange": [min(r['slateDateEt'] for r in reconstructed['rows']), max(r['slateDateEt'] for r in reconstructed['rows'])],
         "columns": sorted(first_archive), "gameColumns": sorted(first_archive.get("records", [{}])[0]),
         "scope": "date range covers references; one source archive inspected"},
    ]
    credentials = {name: {"configuredInJob": bool(os.environ.get(name)), "configuredInTrainer": bool(env.get(name))}
                   for name in ("ODDS_API_KEY", *BBS_SECRET_NAMES)}
    result = {"system": "KS1", "phase": 1, "createdAtUtc": datetime.now(timezone.utc).isoformat(),
              "awsReadOnly": True, "providerCalls": 0, "stack": stack, "bucket": bucket,
              "credentialNames": credentials, "datasets": datasets,
              "existingModelLocation": f"s3://{bucket}/mlb/experiments/",
              "rawArchiveBucketConfiguredInTrainer": bool(env.get("RAW_ARCHIVE_BUCKET")),
              "sourcePointers": sorted(reader.receipts, key=lambda r: (r["bucket"], r["key"])),
              "legacyStores": legacy_inventory(cf, lam, s3),
              "providerCapabilities": {"bbsFieldsAssumed": [], "newArchiveDownload": False}}
    bundle = {"reconstructed": reconstructed["rows"], "research": research["rows"],
              "compact": compact, "prior": prior, "statcast": statcast, "snapshots": snapshots,
              "inventory": result}
    output.mkdir(parents=True, exist_ok=True)
    (output / "inventory.json").write_bytes(encode(result) + b"\n")
    with gzip.GzipFile(filename=str(output / "existing-inputs.json.gz"), mode="wb", mtime=0) as handle:
        handle.write(encode(bundle))
    print(json.dumps({k: v for k, v in result.items() if k != "sourcePointers"}, indent=2))
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory(args.output)
