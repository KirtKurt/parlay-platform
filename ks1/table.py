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
from ks1.refresh import pregame_status
from ks1.starter_identity import published_starter_index

VERSION = "KS1-game-table-v1"
