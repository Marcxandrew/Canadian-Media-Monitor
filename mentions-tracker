"""
Persistent counter for MEI/IEDM media mentions.

Stores a JSON file (mentions_tracker.json) next to this module.
Resets automatically on January 1 of a new year.

Usage in main.py:
    from mentions_tracker import update_tracker
    ytd_count = update_tracker(len(mei_articles))
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

_TRACKER_PATH = os.path.join(os.path.dirname(__file__), "mentions_tracker.json")


def _load() -> dict:
    """Read tracker from disk. Returns fresh dict if file missing or year changed."""
    current_year = datetime.now().year
    if os.path.exists(_TRACKER_PATH):
        try:
            with open(_TRACKER_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("year") == current_year:
                return data
            # New year — reset count
            log.info("New year detected (%d → %d). Resetting MEI mention count.", data.get("year"), current_year)
        except Exception as exc:
            log.warning("Could not read mentions_tracker.json: %s", exc)
    return {"year": current_year, "count": 0}


def _save(data: dict) -> None:
    try:
        with open(_TRACKER_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        log.error("Could not write mentions_tracker.json: %s", exc)


def get_ytd_count() -> int:
    """Return the current year-to-date mention count without modifying it."""
    return _load().get("count", 0)


def update_tracker(new_mentions: int) -> int:
    """
    Add new_mentions to the running YTD total, save, and return the new total.
    Call this once per newsletter run with the count of today's MEI articles.
    """
    data = _load()
    data["count"] += new_mentions
    _save(data)
    log.info("MEI mention tracker: +%d today → %d YTD (%d)", new_mentions, data["count"], data["year"])
    return data["count"]
