"""Load the already retained MLB stores. This module has no provider client."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json

from ks1.historical_starters import load_active_historical_context, read_locked_predictions
from ks1.inventory import Reader, RESEARCH, RECONSTRUCTED, ROOT
from ks1.historical_feed import PREFIX as HISTORICAL_FEED_PREFIX, feed_identity

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
    prior_pointer = reader.read(RESEARCH + "prior-games.json")["artifact"]
    prior = reader.pointer(prior_pointer)
    prior_receipt = dict(reader.receipts[-1])
    retrieved = datetime.now(timezone.utc).isoformat()
    source_year = int(prior["priorYear"])+1 if prior.get("priorYear") else None
    prior_receipt.update(provider="MLB Stats API", retrieved_at=retrieved,
                         provider_requests=prior.get("receipt", {}).get("requests", []),
                         complete_years=[year for year, complete in (
                             (source_year, prior.get("currentYearCoverageComplete")),
                             (source_year-1 if source_year else None,
                              prior.get("priorYearCoverageComplete")))
                             if year is not None and complete is True])
    statcast = reader.pointer(reader.read(RESEARCH + "statcast.json")["artifact"])
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
    published_predictions = []
    try:
        published_predictions, inventory = read_locked_predictions(
            s3, bucket, datetime.now(timezone.utc).isoformat())
        optional_reads.append({"source": "mlb/ks1/predictions-v1/", "status": "read",
                               "rows": len(published_predictions),
                               "versions": inventory["versions_read"]})
        reader.receipts.extend({"bucket": source["bucket"], "key": source["key"],
                                "versionId": source.get("version_id"),
                                "sha256": source["sha256"]}
                               for source in inventory["sources"])
    except Exception as exc:
        # This optional bridge is atomic: a failed version read contributes no
        # starter identity and cannot fail the ordinary game-table build.
        published_predictions = []
        optional_reads.append({"source": "mlb/ks1/predictions-v1/", "status": "unavailable",
                               "error_code": getattr(exc, "response", {}).get(
                                   "Error", {}).get("Code", type(exc).__name__)})
    historical_pitcher_context = {}
    try:
        import boto3
        table = boto3.resource("dynamodb", region_name="us-east-1").Table(
            "parlay_platform_snapshots")
        historical_pitcher_context, status, receipt = load_active_historical_context(
            s3, table)
        optional_reads.append(status)
        reader.receipts.append(receipt)
    except Exception as exc:
        # V8 context is a shadow-only accelerator.  Missing or invalid pointer
        # evidence fails closed without weakening the base KS1 build.
        historical_pitcher_context = {}
        optional_reads.append({"source": "mlb/v8/historical-context/manifests/",
                               "status": "unavailable",
                               "error_code": getattr(exc, "response", {}).get(
                                   "Error", {}).get("Code", type(exc).__name__)})
    historical_feeds = []
    try:
        def read_feed(key):
            try:
                source = Reader(s3, bucket)
                value = source.read(key)
                entry = {**value, 'receipt': source.receipts[0]}
                return entry if feed_identity(entry) is not None else None
            except Exception:
                return None  # A corrupt optional key cannot poison other games.
        with ThreadPoolExecutor(max_workers=8) as pool:
            historical_feeds = [entry for entry in pool.map(read_feed, sorted(reader.keys(HISTORICAL_FEED_PREFIX)))
                                if entry is not None]
        reader.receipts.extend(entry['receipt'] for entry in historical_feeds)
        optional_reads.append({'source':HISTORICAL_FEED_PREFIX,'status':'read','rows':len(historical_feeds)})
    except Exception as exc:
        historical_feeds = []
        optional_reads.append({'source':HISTORICAL_FEED_PREFIX,'status':'unavailable',
                               'error_code':type(exc).__name__})
    return {"reconstructed": reconstructed, "research": research,
            "compact": compact, "full": prior["games"], "schedule": prior["schedule"],
            "current30_history_complete": prior.get("current30CoverageComplete") is True,
            "current_year_history_complete": prior.get("currentYearCoverageComplete") is True,
            "prior_year_history_complete": prior.get("priorYearCoverageComplete") is True,
            "statcast": statcast.get("rows", []),
            "statcast_coverage_complete": statcast.get("current30CoverageComplete",
                                                        statcast.get("coverageComplete")) is True,
            "prior_statcast_profiles": statcast.get("priorYearProfiles", {}),
            "prior_statcast_year": statcast.get("priorYear"),
            "schedule_observed_at": prior.get("receipt", {}).get("retrievedAtUtc"),
            "snapshots": snapshots, "finals": finals, "odds": odds,
            "published_predictions": published_predictions,
            "historical_pitcher_context": historical_pitcher_context,
            "official_history_source": prior_receipt,
            "historical_pregame_feeds": historical_feeds,
            "source_receipts": sorted(reader.receipts, key=lambda r: (r["bucket"], r["key"])),
            "optional_reads": optional_reads}
