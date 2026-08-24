"""Isolated autonomous NFL historical-training and regular-season inference package."""

from .config import (
    HISTORICAL_SEASONS,
    LIVE_COLLECTION_START_UTC,
    SPORT_KEY,
    TARGETS,
)

__all__ = [
    "HISTORICAL_SEASONS",
    "LIVE_COLLECTION_START_UTC",
    "SPORT_KEY",
    "TARGETS",
]
