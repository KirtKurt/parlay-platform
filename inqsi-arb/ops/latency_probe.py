from __future__ import annotations

import argparse
import concurrent.futures
import http.client
import json
import math
import statistics
import threading
import time
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit

USER_AGENT = "inqsi-arb-latency-probe/2.0"


def percentile(values: List[float], pct: float) -> float:
    if not values:
        raise ValueError("values required")
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[rank]


class PersistentClient:
    """One HTTPS connection owned by one worker thread.

    The acceptance test targets steady-state API latency, so TLS/DNS connection
    establishment is warmed once per concurrent client and excluded. Every
    measured request is still a client-observed HTTPS round trip.
    """

    def __init__(self, url: str, timeout: float):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("latency probe requires an https URL")
        self.host = parsed.hostname
        self.port = parsed.port or 443
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.timeout = timeout
        self.conn: http.client.HTTPSConnection | None = None

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None

    def _connect(self) -> http.client.HTTPSConnection:
        if self.conn is None:
            self.conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout)
        return self.conn

    def once(self) -> Tuple[bool, float, int | None]:
        started = time.perf_counter()
        status: int | None = None
        ok = False
        try:
            conn = self._connect()
            conn.request("GET", self.path, headers={"accept": "application/json", "user-agent": USER_AGENT})
            response = conn.getresponse()
            response.read()
            status = int(response.status)
            ok = status == 200
            if response.getheader("connection", "").lower() == "close":
                self.close()
        except Exception:
            self.close()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ok, elapsed_ms, status


def _worker(url: str, timeout: float, measured_requests: int) -> Dict[str, Any]:
    client = PersistentClient(url, timeout)
    try:
        warm = client.once()
        if not warm[0]:
            return {"warmup": warm, "rows": [], "error": "WARMUP_FAILED"}
        rows = [client.once() for _ in range(measured_requests)]
        return {"warmup": warm, "rows": rows, "error": None}
    finally:
        client.close()


def run_probe(url: str, *, requests_count: int, concurrency: int, timeout: float) -> Dict[str, Any]:
    count = max(1, int(requests_count))
    workers = max(1, min(int(concurrency), count, 100))

    # Divide the measured sample across persistent concurrent clients. Each
    # client performs one unmeasured warm-up so DNS/TLS establishment is not
    # confused with steady-state application/API latency.
    base, remainder = divmod(count, workers)
    work = [base + (1 if i < remainder else 0) for i in range(workers)]

    started = time.perf_counter()
    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, url, timeout, n) for n in work if n > 0]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    wall_seconds = time.perf_counter() - started

    warmups = [result["warmup"] for result in results]
    warm_failures = sum(1 for row in warmups if not row[0])
    rows: List[Tuple[bool, float, int | None]] = []
    for result in results:
        rows.extend(result["rows"])

    if warm_failures:
        raise RuntimeError(f"{warm_failures} persistent client warmups failed")
    if len(rows) != count:
        raise RuntimeError(f"measured sample incomplete expected={count} actual={len(rows)}")

    latencies = [row[1] for row in rows]
    successes = sum(1 for row in rows if row[0])
    failures = count - successes
    status_counts: Dict[str, int] = {}
    for _, _, status in rows:
        key = str(status) if status is not None else "ERROR"
        status_counts[key] = status_counts.get(key, 0) + 1

    warmup_latencies = [row[1] for row in warmups]
    return {
        "ok": failures == 0,
        "url": url,
        "sample_size": count,
        "concurrency": workers,
        "timeout_seconds": timeout,
        "warmup_requests_excluded": len(warmups),
        "warmup_latency_ms": {
            "min": round(min(warmup_latencies), 3),
            "median": round(statistics.median(warmup_latencies), 3),
            "max": round(max(warmup_latencies), 3),
        },
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
        "measurement_scope": "steady-state client-observed HTTPS round trip to cached/read-only endpoint with one DNS/TLS warmup excluded per persistent concurrent client; upstream quote age excluded",
        "connection_model": "one persistent HTTPS connection per concurrent client",
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
