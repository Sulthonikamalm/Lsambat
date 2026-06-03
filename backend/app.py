"""
app.py — Flask + SocketIO server untuk SurabayaSambat v2.

Entry point: python backend/app.py
Dashboard: http://localhost:5000

Slim entry point — semua logic dipecah ke:
- routes/ (API endpoints)
- services/ (business logic)
- helpers/ (rate limiter, history)
"""

import sys
import logging
import threading
from pathlib import Path

from flask import Flask, send_from_directory
from flask_socketio import SocketIO

# Ensure backend/ is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_settings, get_apify_tokens, get_paths
from scraper import ApifyScraper

# ── Setup ─────────────────────────────────────────────────────

app = Flask(
    __name__,
    static_folder=str(Path(__file__).resolve().parent.parent / "frontend"),
    static_url_path="",
)
app.config["SECRET_KEY"] = "surabayasambat-demo-2026"

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("demo_monitor")

# ── Load config & init objects ────────────────────────────────

settings = load_settings()
tokens = get_apify_tokens(settings)
paths = get_paths(settings)

usage_log_path = str(
    Path(__file__).resolve().parent.parent
    / settings.get("output", {}).get("api_usage_log", "data/api_usage_log.json")
)

scraper = ApifyScraper(
    tokens=tokens,
    actor_ids=settings["apify"]["actor_ids"],
    timeout=settings["apify"]["request_timeout_seconds"],
    cost_per_call=settings["apify"].get("estimated_cost_per_call", 0.032),
    usage_log_path=usage_log_path,
)

# ── Shared application state ─────────────────────────────────

app_state = {
    "system_active": False,
    "last_auto_scrape_time": None,
    "scrape_lock": threading.Lock(),
    "scrape_stop_event": threading.Event(),
    "history_path": Path(__file__).resolve().parent.parent / settings.get(
        "output", {}
    ).get("scrape_history", "data/scrape_history.json"),
}

# ── Register Blueprints ──────────────────────────────────────

from routes.scrape_routes import scrape_bp, init_scrape_routes
from routes.source_routes import source_bp, init_source_routes
from routes.data_routes import data_bp, init_data_routes
from routes.system_routes import system_bp, init_system_routes

init_scrape_routes(scraper, settings, paths, app_state, socketio)
init_source_routes(paths, socketio)
init_data_routes(paths)
init_system_routes(scraper, settings, paths, app_state, socketio)

app.register_blueprint(scrape_bp)
app.register_blueprint(source_bp)
app.register_blueprint(data_bp)
app.register_blueprint(system_bp)

# ── Static files ──────────────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ── Start scheduler daemon ────────────────────────────────────

from services.scheduler_service import start_auto_scrape_loop

start_auto_scrape_loop(app_state, scraper, settings, paths, socketio)

# ── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("=" * 60)
    print(f"  {settings['project']['name']}")
    print(f"  Token aktif: {len(tokens)}")
    print("=" * 60)
    print(f"  Dashboard: http://localhost:5000")
    print("=" * 60)

    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
