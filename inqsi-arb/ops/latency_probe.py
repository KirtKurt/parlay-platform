from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import statistics
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

USER_AGENT = "inqsi-arb-latency-probe/1.0"


def percentile(values: List[float], pct: float) -> float:
    if not values:
        raise ValueError("values required")
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[rank]


def once(url: str, timeout: float) -> Tuple[bool, float, int | None]:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            status = int(response.status)
            ok = status == 200
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        ok = False
    except Exception:
        status = None
        ok = False
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return ok, elapsed_ms, status


def run_probe(url: str, *, requests_count: int, concurrency: int, timeout: float) -> Dict[str, Any]:
    count = max(1, int(requests_count))
    workers = max(1, min(int(concurrency), count, 100))
    # Warm-up is excluded from the measured sample by design.
    warm_ok, warm_ms, warm_status = once(url, timeout)
    if not warm_ok:
        raise RuntimeError(f"warmup failed status={warm_status} latency_ms={warm_ms:.2f}")

    started = time.perf_counter()
    rows: List[Tuple[bool, float, int | None]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(once, url, timeout) for _ in range(count)]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
    wall_seconds = time.perf_counter() - started

    latencies = [row[1] for row in rows]
    successes = sum(1 for row in rows if row[0])
    failures = count - successes
    status_counts: Dict[str, int] = {}
    for _, _, status in rows:
        key = str(status) if status is not None else "ERROR"
        status_counts[key] = status_counts.get(key, 0) + 1

    return {
        "ok": failures == 0,
        "url": url,
        "sample_size": count,
        "concurrency": workers,
        "timeout_seconds": timeout,
        "warmup_latency_ms": round(warm_ms, 3),
        "wall_seconds": round(wall_seconds, 3),
        "throughput_requests_per_second": round(count / wall_seconds, 3) if wall_seconds > 0 else None,
        "successes": successes,
        "failures": failures,
        "status_counts": status_counts,
        "latency_ms": {
            "min": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "p90": round(percentile(latencies, 90), 3),
            "p95": round(percentile(latencies, 95), 3),
            "p99": round(percentile(latencies, 99), 3),
            "max": round(max(latencies), 3),
            "mean": round(statistics.fmean(latencies), 3),
        },
        "measurement_scope": "client-observed HTTPS round trip to cached/read-only endpoint; upstream quote age excluded",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--p95-target-ms", type=float, default=500.0)
    parser.add_argument("--output", default="arb-latency-proof.json")
    args = parser.parse_args()
    report = run_probe(args.url, requests_count=args.requests, concurrency=args.concurrency, timeout=args.timeout)
    report["p95_target_ms"] = args.p95_target_ms
    report["p95_target_pass"] = report["ok"] and report["latency_ms"]["p95"] <= args.p95_target_ms
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["p95_target_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
