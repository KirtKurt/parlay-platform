"""Deterministic AWS SDK defaults for offline unit-test collection.

Production Lambda receives its region and credentials from AWS. Unit tests import
several modules that construct boto3 resources at module import time, so CI must
provide non-network defaults before those imports occur.
"""
from __future__ import annotations

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
