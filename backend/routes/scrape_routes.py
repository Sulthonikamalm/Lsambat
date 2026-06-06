"""
routes/scrape_routes.py — API endpoints untuk scraping.
"""

import threading
import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify

logger = logging.getLogger("demo_monitor")

scrape_bp = Blueprint("scrape", __name__)

# These will be injected by app.py via init_scrape_routes()
_deps = {}


def init_scrape_routes(scraper, settings, paths, app_state, socketio):
    """Inject dependencies yang dibutuhkan oleh route handlers."""
    _deps["scraper"] = scraper
    _deps["settings"] = settings
    _deps["paths"] = paths
    _deps["app_state"] = app_state
    _deps["socketio"] = socketio


def _emit_log(message, level="info"):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "level": level,
    }
    _deps["socketio"].emit("log", entry)
    logger.info(f"[Dashboard] {message}")


@scrape_bp.route("/api/scrape", methods=["POST"])
def api_scrape():
    """Unified scrape: discover + process queue dalam satu alur."""
    from helpers.rate_limiter import get_current_tier
    from services.scrape_service import run_unified_scrape
    from services.scheduler_service import calculate_next_scrape_time

    state = _deps["app_state"]

    if not state["scrape_lock"].acquire(blocking=False):
        return jsonify({
            "success": False,
            "error": "Proses sedang berjalan. Tunggu hingga selesai atau tekan Stop.",
        }), 409

    tier = get_current_tier(_deps["settings"], state["history_path"])
    if not tier["allowed"]:
        state["scrape_lock"].release()
        return jsonify({"success": False, "error": tier["reason"]}), 429

    state["scrape_stop_event"].clear()

    tier_num = tier["tier_number"]
    max_week = tier["max_per_week"]

    _emit_log(
        f"🔄 Memulai pengambilan data (ke-{tier_num} minggu ini, maks {max_week})...",
        "info",
    )
    if tier_num > 1:
        _emit_log(
            f"ℹ️ Pengambilan ke-{tier_num}: maks {tier['posts_per_source']} postingan/akun, "
            f"{tier['comments_per_post']} komentar/postingan.",
            "info",
        )

    _deps["socketio"].emit("scrape_started", {"tier": tier_num})

    def _scrape_job():
        try:
            run_unified_scrape(
                _deps["scraper"], _deps["settings"], _deps["paths"],
                state["history_path"], state["scrape_stop_event"],
                _emit_log, _deps["socketio"], trigger="manual"
            )
        finally:
            state["scrape_lock"].release()
            _deps["socketio"].emit("status_change", _get_status_data())

    thread = threading.Thread(target=_scrape_job, daemon=True)
    thread.start()
    return jsonify({"success": True, "tier": tier_num})


@scrape_bp.route("/api/stop-scrape", methods=["POST"])
def api_stop_scrape():
    """Hentikan proses scraping yang sedang berjalan."""
    _deps["app_state"]["scrape_stop_event"].set()
    _emit_log("⏹ Menghentikan proses...", "warning")
    return jsonify({"success": True})


def _get_status_data():
    from helpers.rate_limiter import get_current_tier
    from services.scheduler_service import (
        calculate_next_scrape_time, DEMO_INTERVAL_MINUTES, format_interval,
    )

    state = _deps["app_state"]
    tier = get_current_tier(_deps["settings"], state["history_path"])
    schedule_cfg = _deps["settings"].get("schedule", {})
    return {
        "system_active": state["system_active"],
        "is_scraping": state["scrape_lock"].locked(),
        "auto_scrape_day": schedule_cfg.get("auto_scrape_day", "monday"),
        "auto_scrape_hour": schedule_cfg.get("auto_scrape_hour", 8),
        "auto_scrape_interval_minutes": DEMO_INTERVAL_MINUTES,
        "auto_scrape_interval_label": format_interval(DEMO_INTERVAL_MINUTES),
        "next_auto_scrape_time": calculate_next_scrape_time(
            state["system_active"], state["last_auto_scrape_time"]
        ),
        "tier_info": tier,
    }
