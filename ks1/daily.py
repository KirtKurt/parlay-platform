"""KS1 daily inference: frozen LightGBM winner and dual-Poisson run models."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import statistics
import sys
import tempfile
import zipfile

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from urllib.error import HTTPError

from ks1.features import Features
from ks1.live_inputs import PREFIX, SCHEMA, ProviderFailure, day, team_identity, utc
from ks1.platt import american

# truncated for tool - full content required
