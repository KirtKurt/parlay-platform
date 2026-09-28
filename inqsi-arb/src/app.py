from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

from arb_engine import ArbValidationError, scan_all
from audit_store import enabled as audit_enabled, recent as audit_recent, record as audit_record
from opportunity_alerts import publish_scan_opportunities
from constraints import apply_book_constraints, optimize_equal_payout
from lifecycle import outcome_pnl, recommend_two_leg_completion, record_leg
from margin_engine import analyze_events, analyze_snapshots
from market_catalog import MARKET_FAMILY_KEYS, expand_market_families
from market_discovery import discover_event_market_keys, discover_events, fetch_all_discovered_markets
from position_store import get as get_position, list_for_user, put as put_position
from provider import MARKET_FAMILIES, list_sports, scan_sport_payload
from quote_store import get_checkpoint, get_snapshot, list_snapshot_sports
from provider_books import catalog_summary, regions_for_books
from rules import registry_rows, registry_size
from state_packs import list_packs, pack_summary
from ui_page import HTML
from validation import validate_events

VERSION = "INQSI-ARB-v3"
DEFAULT_MARKETS = "h2h,spreads,totals"
