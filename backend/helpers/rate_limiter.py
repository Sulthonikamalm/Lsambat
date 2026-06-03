"""
helpers/rate_limiter.py — Rate limiting & tier budget logic.
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from helpers.history import load_scrape_history

logger = logging.getLogger("demo_monitor")


def get_week_scrape_count(history_path: Path) -> int:
    """Hitung berapa kali scraping sudah dilakukan minggu ini."""
    history = load_scrape_history(history_path)
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=weekday
    )
    week_start_str = week_start.strftime("%Y-%m-%dT%H:%M:%SZ")

    count = 0
    for entry in history:
        if entry.get("timestamp", "") >= week_start_str:
            count += 1
    return count


def get_current_tier(settings: dict, history_path: Path) -> dict:
    """Tentukan tier budget berdasarkan jumlah scraping minggu ini."""
    week_count = get_week_scrape_count(history_path)
    rate_config = settings.get("rate_limit", {})
    max_per_week = rate_config.get("max_scrapes_per_week", 4)
    tiers = rate_config.get("budget_tiers", [])

    if week_count >= max_per_week:
        return {
            "allowed": False,
            "tier_number": week_count + 1,
            "reason": f"Batas penggunaan minggu ini tercapai ({week_count}/{max_per_week}).",
            "week_count": week_count,
            "max_per_week": max_per_week,
        }

    tier_index = min(week_count, len(tiers) - 1) if tiers else 0
    tier = tiers[tier_index] if tiers else {
        "posts_per_source": 10, "comments_per_post": 100
    }

    return {
        "allowed": True,
        "tier_number": week_count + 1,
        "posts_per_source": tier.get("posts_per_source", 10),
        "comments_per_post": tier.get("comments_per_post", 100),
        "week_count": week_count,
        "max_per_week": max_per_week,
    }
