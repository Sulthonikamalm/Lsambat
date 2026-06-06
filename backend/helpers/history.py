"""
helpers/history.py — Scrape history persistence & rate limiting.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("demo_monitor")


def load_scrape_history(path: Path) -> list:
    """Load scrape history dari file JSON."""
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def save_scrape_history(history: list, path: Path):
    """Persist scrape history ke file JSON. Auto-pruning > 4 minggu."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(weeks=4)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        history = [e for e in history if e.get("timestamp", "") >= cutoff]

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.warning(f"Gagal simpan scrape history: {e}")


def record_scrape(path: Path, trigger: str, summary: dict,
                  api_calls_session: int = 0, estimated_cost_session: float = 0.0):
    """Catat satu sesi scraping ke history termasuk biaya."""
    history = load_scrape_history(path)
    _new = summary.get("total_new_comments", 0)
    _baseline = summary.get("total_baseline_comments", 0)
    history.append({
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trigger": trigger,
        "posts_discovered": summary.get("total_posts_discovered", 0),
        "new_posts": summary.get("total_new_posts", 0),
        "new_comments": _new,
        "baseline_comments": _baseline,
        "saved_comments": _new + _baseline,
        "skipped_low_relevance": summary.get("skipped_low_relevance", 0),
        "skipped_zero_comments": summary.get("skipped_zero_comments", 0),
        "api_calls_session": api_calls_session,
        "estimated_cost_session": round(estimated_cost_session, 4),
    })
    save_scrape_history(history, path)
