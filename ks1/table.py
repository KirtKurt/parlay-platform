"""One row per official game; final-box identities are audit labels, never features."""
from collections import defaultdict
from datetime import date as calendar_date, timedelta
import hashlib
import json
import statistics

import numpy as np
import pandas as pd
import pyarrow as pa

from ks1.features import Features, day, number, utc
from ks1.inventory import encode

VERSION = "KS1-game-table-v1"
