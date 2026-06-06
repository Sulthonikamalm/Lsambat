"""
routes/system_routes.py — API endpoints untuk status, toggle, reset, dashboard-init.
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from helpers.rate_limiter import get_current_tier
from helpers.history import load_scrape_history
from discover_posts import load_source_registry, get_active_sources
from stage4_utils import load_csv_if_exists
from process_queue import get_posts_rows

logger = logging.getLogger("demo_monitor")

system_bp = Blueprint("system", __name__)

_deps = {}


def init_system_routes(scraper, settings, paths, app_state, socketio):
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


def _get_status_data():
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


@system_bp.route("/api/status")
def api_status():
    return jsonify(_get_status_data())


@system_bp.route("/api/toggle-system", methods=["POST"])
def api_toggle_system():
    """Toggle sistem ON/OFF. Saat ON, scraping otomatis terjadwal."""
    state = _deps["app_state"]

    state["system_active"] = not state["system_active"]

    if state["system_active"]:
        # Cek apakah ada akun aktif (IF-THEN #10)
        df = load_source_registry(_deps["paths"]["source_registry"])
        df_active = get_active_sources(df) if not df.empty else df
        if df_active.empty:
            state["system_active"] = False
            _emit_log(
                "⚠️ Tidak bisa mengaktifkan sistem: belum ada akun yang dipantau. "
                "Tambahkan akun terlebih dahulu.",
                "warning"
            )
            return jsonify({"success": False, "error": "Belum ada akun aktif"})

        state["last_auto_scrape_time"] = datetime.now(timezone.utc)
        from services.scheduler_service import DEMO_INTERVAL_MINUTES, format_interval
        _emit_log(
            f"✅ Sistem diaktifkan. Scraping otomatis setiap {format_interval(DEMO_INTERVAL_MINUTES)}.",
            "success",
        )
    else:
        state["last_auto_scrape_time"] = None
        _emit_log("⏹ Sistem dinonaktifkan. Scraping otomatis dihentikan.", "warning")

    _deps["socketio"].emit("system_toggle", {"active": state["system_active"]})
    _deps["socketio"].emit("status_change", _get_status_data())

    return jsonify({"success": True, "system_active": state["system_active"]})


@system_bp.route("/api/usage")
def api_usage():
    """Statistik penggunaan API dan biaya."""
    usage = _deps["scraper"].get_usage_summary()
    tier = get_current_tier(_deps["settings"], _deps["app_state"]["history_path"])
    usage["rate_limit"] = tier
    usage["scrape_history"] = load_scrape_history(_deps["app_state"]["history_path"])[-10:]
    return jsonify(usage)


@system_bp.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset semua data scraping."""
    state = _deps["app_state"]
    state["scrape_stop_event"].set()
    state["system_active"] = False

    data_files = [
        _deps["paths"].get("raw_posts_csv"),
        _deps["paths"].get("post_queue_csv"),
        _deps["paths"].get("raw_comments_csv"),
    ]
    for f in data_files:
        if f and f.exists():
            f.unlink()
            logger.info(f"Deleted {f}")

    _emit_log("🗑️ Semua data direset.", "warning")
    _deps["socketio"].emit("status_change", _get_status_data())
    return jsonify({"success": True})


@system_bp.route("/api/dashboard-init")
def api_dashboard_init():
    """Endpoint gabungan: status + usage + sources + stats dalam 1 request."""
    status_data = _get_status_data()

    usage_data = _deps["scraper"].get_usage_summary()
    usage_data["rate_limit"] = status_data["tier_info"]
    usage_data["scrape_history"] = load_scrape_history(
        _deps["app_state"]["history_path"]
    )[-10:]

    df = load_source_registry(_deps["paths"]["source_registry"])
    sources_list = []
    active_count = 0
    if not df.empty:
        df_active = get_active_sources(df)
        active_ids = set(df_active["source_id"].tolist()) if not df_active.empty else set()
        active_count = len(active_ids)
        for _, row in df.fillna("").iterrows():
            sid = row.get("source_id", "")
            sources_list.append({
                "source_id": sid,
                "source_account": row.get("source_account", ""),
                "platform": row.get("platform", ""),
                "priority_level": row.get("priority_level", ""),
                "monitoring_window_days": row.get("monitoring_window_days", ""),
                "status": row.get("status", ""),
                "notes": row.get("notes", ""),
                "is_active": sid in active_ids,
            })

    posts = get_posts_rows(_deps["paths"])
    comments_df = load_csv_if_exists(_deps["paths"]["raw_comments_csv"])
    total_comments = len(comments_df) if not comments_df.empty else 0

    return jsonify({
        "status": status_data,
        "usage": usage_data,
        "sources": {"sources": sources_list, "active_count": active_count},
        "stats": {
            "total_posts": len(posts),
            "total_comments": total_comments,
        },
    })
