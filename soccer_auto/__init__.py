"""Isolated autonomous soccer market-learning service.

This package intentionally has no imports from the legacy MLB, tennis, or soccer
pipelines. Its AWS resources are declared in ``soccer-auto-template.yaml``.
"""
from __future__ import annotations

import os

SYSTEM_NAME = "soccer_auto"
SCHEMA_VERSION = "soccer-auto-v1"

# Soccer fans out many bookmaker/market requests while sharing one provider key
# with separately deployed sports. A short eight-second distributed-lease wait
# caused ordinary cross-Lambda contention to surface as worker errors and enter
# SQS redrive. Enforce a bounded one-minute lease horizon package-wide so every
# soccer provider caller serializes safely behind the 3-RPS distributed limiter.
# The full worker repair also converts contention into delayed application-level
# retries; these defaults are a defense-in-depth backstop for every Lambda.
os.environ["SOCCER_AUTO_ODDS_RATE_LIMIT_MAX_WAIT_SECONDS"] = str(
    max(60, int(os.getenv("SOCCER_AUTO_ODDS_RATE_LIMIT_MAX_WAIT_SECONDS", "60")))
)
os.environ["SOCCER_AUTO_ODDS_RATE_LIMIT_MAX_CONTENTION_ATTEMPTS"] = str(
    max(2048, int(os.getenv("SOCCER_AUTO_ODDS_RATE_LIMIT_MAX_CONTENTION_ATTEMPTS", "2048")))
)
