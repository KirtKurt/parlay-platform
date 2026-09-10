"""Load the already retained MLB stores. This module has no provider client."""
from concurrent.futures import ThreadPoolExecutor
import json

from ks1.inventory import Reader, RESEARCH, RECONSTRUCTED, ROOT

FINALS = "mlb/historical-daily-v1/official-finals/"
ODDS = "mlb/odds-v8-shadow/"


def aws_clients(region, stack):
    import boto3
    from botocore.config import Config
    config = Config(connect_timeout=10, read_timeout=60,
                    retries={"max_attempts": 3}, max_pool_connections=12)
    cf, lam, s3 = [boto3.client(n, region_name=region, config=config)
                   for n in ("cloudformation", "lambda", "s3")]
    trainer = cf.describe_stack_resource(StackName=stack, LogicalResourceId="MLBMLTrainingFunction")
    env = lam.get_function_configuration(
        FunctionName=trainer["StackResourceDetail"]["PhysicalResourceId"])["Environment"]["Variables"]
    return cf, s3, env["MLB_ML_ARTIFACTS_BUCKET"]


def load_existing(cf, s3, bucket):
    reader = Reader(s3, bucket)
    pointer = json.loads((ROOT / "runtime_reports/mlb_data_admission_latest.json").read_text())["historicalDevelopment"]["artifact"]
    if pointer["bucket"] != bucket or not pointer["key"].startswith(RECONSTRUCTED):
        raise ValueError("historical pointer is outside discovered source scope")
    reconstructed = reader.pointer(pointer)["rows"]
    research = reader.pointer(reader.read(RESEARCH + "dataset.json")["artifact"])["rows"]
    prior = reader.pointer(reader.read(RESEARCH + "prior-games.json")["artifact"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        compact = list(pool.map(reader.read, sorted(reader.keys(RECONSTRUCTED + "source-games/"))))
    snapshots = [{**reader.read(key), "source_key": key}
                 for key in sorted(reader.keys(RESEARCH + "snapshots/"))]
    # The archive bucket is an observed immutable pointer, never a guessed name.
    archives = {r["sourceArtifact"]["bucket"] for r in reconstructed}
    finals = []
    target_dates = {r["slateDateEt"] for r in reconstructed}
    for archive in sorted(archives):
        keys = [key for key in reader.keys(FINALS, bucket=archive)
                if key.removeprefix(FINALS).removesuffix(".json") in target_dates]
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda key: reader.read(key, bucket=archive), sorted(keys)))
        for key, value in zip(sorted(keys), values):
            finals.extend({**game, "source_key": f"s3://{archive}/{key}"} for game in value["games"])
    odds, optional_reads = [], []
    try:
        response = cf.describe_stacks(StackName="parlay-platform-mlb-odds-v8-shadow")
        outputs = {r["OutputKey"]: r["OutputValue"] for r in response["Stacks"][0].get("Outputs", [])}
        odds_bucket = outputs["ShadowArtifactsBucketName"]
        keys = sorted(reader.keys(ODDS, bucket=odds_bucket))
        # Legacy metadata.sha256 can mean a semantic fingerprint. Bind the actual
        # object bytes separately instead of treating that metadata as a byte hash.
        def legacy(key):
            import hashlib
            result = s3.get_object(Bucket=odds_bucket, Key=key)
            body = result["Body"].read()
            reader.receipts.append({"bucket": odds_bucket, "key": key,
                                    "versionId": result.get("VersionId"),
                                    "sha256": hashlib.sha256(body).hexdigest()})
            return {**json.loads(body), "source_key": f"s3://{odds_bucket}/{key}"}
        with ThreadPoolExecutor(max_workers=8) as pool:
            odds = list(pool.map(legacy, keys))
        optional_reads.append({"source": ODDS, "status": "read", "objects": len(keys)})
    except Exception as exc:
        # An unavailable optional market store cannot invalidate the game table.
        optional_reads.append({"source": ODDS, "status": "unavailable",
                               "error_code": getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)})
    return {"reconstructed": reconstructed, "research": research,
            "compact": compact, "full": prior["games"], "schedule": prior["schedule"],
            "schedule_observed_at": prior.get("receipt", {}).get("retrievedAtUtc"),
            "snapshots": snapshots, "finals": finals, "odds": odds,
            "source_receipts": sorted(reader.receipts, key=lambda r: (r["bucket"], r["key"])),
            "optional_reads": optional_reads}
