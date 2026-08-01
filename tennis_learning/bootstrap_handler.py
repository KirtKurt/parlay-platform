from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Any, Mapping

import backfill
import handler

# Use GitHub's archive CDN directly. The previous github.com archive URLs returned
# 404 in Lambda even though the repositories themselves were available.
backfill.SOURCES = {
    "atp": "https://codeload.github.com/JeffSackmann/tennis_atp/zip/refs/heads/master",
    "wta": "https://codeload.github.com/JeffSackmann/tennis_wta/zip/refs/heads/master",
}


def _download_with_fallback(url: str) -> bytes:
    candidates = [url]
    if "codeload.github.com" in url:
        candidates.append(url.replace("/zip/refs/heads/master", "/legacy.zip/refs/heads/master"))
    errors: list[str] = []
    for candidate in candidates:
        for attempt in range(3):
            request = urllib.request.Request(
                candidate,
                headers={
                    "User-Agent": "inqis-tennis-learning/1.3",
                    "Accept": "application/zip,application/octet-stream;q=0.9,*/*;q=0.8",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = response.read()
                if len(payload) < 1024 or not payload.startswith(b"PK"):
                    raise RuntimeError(f"invalid zip payload ({len(payload)} bytes)")
                return payload
            except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                errors.append(f"{candidate} attempt {attempt + 1}: {exc}")
                time.sleep(2 ** attempt)
    raise RuntimeError("historical archive download failed: " + " | ".join(errors[-6:]))


backfill._download = _download_with_fallback


def _american(probability: float) -> float:
    p = max(0.01, min(0.99, float(probability)))
    return -100.0 * p / (1.0 - p) if p >= 0.5 else 100.0 * (1.0 - p) / p


def _settle_compatible(payload: Mapping[str, Any]):
    row = dict(payload)
    signals = dict(row["signals"])
    if "player_odds" not in signals or "opponent_odds" not in signals:
        fair = float(signals.pop("market_fair_prob", 0.5))
        signals["player_odds"] = _american(fair)
        signals["opponent_odds"] = _american(1.0 - fair)
    row["signals"] = signals
    return handler.settle(row)


backfill.settle = _settle_compatible


def lambda_handler(event, context):
    return backfill.lambda_handler(event, context)
