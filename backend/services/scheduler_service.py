"""
services/scheduler_service.py — Background auto-scrape scheduler.

MODE DEMO: scraping otomatis setiap 2 menit saat sistem aktif.
"""

import logging
import time
import threading
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("demo_monitor")

# Interval demo: 2 menit
DEMO_INTERVAL_MINUTES = 2
CHECK_INTERVAL_SECONDS = 10


def calculate_next_scrape_time(system_active, last_auto_scrape_time):
    """Hitung waktu scraping otomatis berikutnya (Mode Demo 2 Menit)."""
    if not system_active or not last_auto_scrape_time:
        return None
    next_scrape_utc = last_auto_scrape_time + timedelta(minutes=DEMO_INTERVAL_MINUTES)
    return next_scrape_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def start_auto_scrape_loop(app_state, scraper, settings, paths, socketio):
    """
    Jalankan background daemon thread untuk auto-scrape.

    Args:
        app_state: dict with keys: system_active, last_auto_scrape_time,
                   scrape_lock, scrape_stop_event, history_path
        scraper: ApifyScraper instance
        settings: dict dari settings.yaml
        paths: dict dari get_paths()
        socketio: SocketIO instance
    """
    from helpers.rate_limiter import get_current_tier
    from services.scrape_service import run_unified_scrape

    def _emit_log(message, level="info"):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "level": level,
        }
        socketio.emit("log", entry)
        logger.info(f"[Dashboard] {message}")

    def _get_status_data():
        from helpers.rate_limiter import get_current_tier
        tier = get_current_tier(settings, app_state["history_path"])
        schedule_cfg = settings.get("schedule", {})
        return {
            "system_active": app_state["system_active"],
            "is_scraping": app_state["scrape_lock"].locked(),
            "auto_scrape_day": schedule_cfg.get("auto_scrape_day", "monday"),
            "auto_scrape_hour": schedule_cfg.get("auto_scrape_hour", 8),
            "next_auto_scrape_time": calculate_next_scrape_time(
                app_state["system_active"],
                app_state["last_auto_scrape_time"]
            ),
            "tier_info": tier,
        }

    def _loop():
        while True:
            try:
                if (app_state["system_active"]
                        and not app_state["scrape_lock"].locked()
                        and app_state["last_auto_scrape_time"]):

                    now = datetime.now(timezone.utc)
                    next_scrape = app_state["last_auto_scrape_time"] + timedelta(
                        minutes=DEMO_INTERVAL_MINUTES
                    )

                    if now >= next_scrape:
                        app_state["last_auto_scrape_time"] = now

                        tier = get_current_tier(settings, app_state["history_path"])
                        if tier["allowed"] and app_state["scrape_lock"].acquire(blocking=False):
                            logger.info("[Scheduler] Memulai scraping otomatis (Demo 2 menit)!")
                            _emit_log("🤖 Memulai scraping otomatis (Mode Demo 2 Menit).", "info")
                            app_state["scrape_stop_event"].clear()
                            socketio.emit("scrape_started", {"tier": tier["tier_number"]})
                            try:
                                run_unified_scrape(
                                    scraper, settings, paths,
                                    app_state["history_path"],
                                    app_state["scrape_stop_event"],
                                    _emit_log, socketio,
                                    trigger="auto"
                                )
                            finally:
                                app_state["scrape_lock"].release()
                                socketio.emit("status_change", _get_status_data())
            except Exception as e:
                logger.error(f"[Scheduler] Error: {e}", exc_info=True)

            time.sleep(CHECK_INTERVAL_SECONDS)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread
