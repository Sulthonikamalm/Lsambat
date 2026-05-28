"""
app.py — Flask + SocketIO server untuk Live Comment Monitor Demo.

Entry point: python backend/app.py
Dashboard: http://localhost:5000
"""

import sys
import logging
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, send_from_directory, send_file
from flask_socketio import SocketIO

# Ensure backend/ is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_settings, get_apify_tokens, get_paths
from scraper import ApifyScraper
from monitor import CommentMonitor, MODE_MONITORING

# ── Setup ─────────────────────────────────────────────────────

app = Flask(
    __name__,
    static_folder=str(Path(__file__).resolve().parent.parent / "frontend"),
    static_url_path="",
)
app.config["SECRET_KEY"] = "surabayasambat-demo-2026"

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Logging
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

scraper = ApifyScraper(
    tokens=tokens,
    actor_id=settings["apify"]["actor_id"],
    timeout=settings["apify"]["request_timeout_seconds"],
)

monitor = CommentMonitor(
    scraper=scraper,
    post_url=settings["target"]["post_url"],
    paths=paths,
)

# Monitoring scheduler state
_monitoring_thread = None
_monitoring_stop_event = threading.Event()

# ── Helper: emit log ke dashboard ─────────────────────────────


def emit_log(message: str, level: str = "info"):
    """Kirim log message ke dashboard via WebSocket."""
    from datetime import datetime, timezone

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "level": level,
    }
    socketio.emit("log", entry)
    logger.info(f"[Dashboard] {message}")


# ── Routes: Static files ──────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ── API: Status ───────────────────────────────────────────────


@app.route("/api/status")
def api_status():
    status = monitor.get_status()
    status["interval_seconds"] = settings["monitoring"]["interval_seconds"]
    return jsonify(status)


# ── API: Kumpulkan Data Awal ─────────────────────────────────


@app.route("/api/collect", methods=["POST"])
def api_collect():
    emit_log("Mengumpulkan semua komentar yang ada...", "info")
    emit_log(
        f"Scraping dengan limit {settings['apify']['baseline_results_limit']}...",
        "info",
    )

    result = monitor.collect_initial(
        results_limit=settings["apify"]["baseline_results_limit"]
    )

    if result.get("success"):
        count = result["collected_count"]
        total = result["total_scraped"]

        if count > 0:
            emit_log(
                f"Berhasil mengumpulkan {count} komentar "
                f"(dari {total} yang di-scrape)",
                "success",
            )
            emit_log(
                "Semua komentar telah masuk ke dataset CSV.",
                "success",
            )
            # Emit each comment to dashboard table
            for comment in result.get("new_comments", []):
                socketio.emit("new_comment", comment)
        else:
            emit_log(
                f"Tidak ada komentar ditemukan ({total} di-scrape). "
                f"Postingan mungkin belum memiliki komentar.",
                "info",
            )

        emit_log(
            'Siap untuk memulai monitoring. Klik "Mulai Monitoring".',
            "info",
        )
        socketio.emit("status_change", monitor.get_status())
    else:
        emit_log(f"Gagal: {result.get('error', 'Unknown')}", "error")

    return jsonify(result)


# ── API: Start Monitoring ────────────────────────────────────


@app.route("/api/start", methods=["POST"])
def api_start():
    global _monitoring_thread, _monitoring_stop_event

    result = monitor.start_monitoring()

    if not result.get("success"):
        emit_log(f"Gagal start: {result.get('error')}", "error")
        return jsonify(result)

    emit_log("LIVE MONITORING AKTIF", "success")
    emit_log(
        "Komentar baru yang terdeteksi akan otomatis masuk dataset.",
        "success",
    )

    interval = settings["monitoring"]["interval_seconds"]
    emit_log(
        f"Pemeriksaan otomatis setiap {interval} detik.",
        "info",
    )

    socketio.emit("status_change", monitor.get_status())

    # Start background scheduler
    _monitoring_stop_event.clear()
    _monitoring_thread = threading.Thread(
        target=_monitoring_loop,
        args=(interval,),
        daemon=True,
    )
    _monitoring_thread.start()

    return jsonify(result)


def _monitoring_loop(interval_seconds: int):
    """Background loop: jalankan monitoring cycle setiap N detik."""

    while not _monitoring_stop_event.is_set():
        # Countdown
        socketio.emit("countdown_start", {"seconds": interval_seconds})

        # Wait with early exit check
        for remaining in range(interval_seconds, 0, -1):
            if _monitoring_stop_event.is_set():
                return
            time.sleep(1)

        if _monitoring_stop_event.is_set():
            return

        # Run cycle
        _run_single_cycle()


def _run_single_cycle():
    """Execute satu monitoring cycle dan emit hasilnya."""
    cycle = monitor.cycle_number + 1
    emit_log(f"Siklus #{cycle}: Memeriksa komentar baru...", "info")

    result = monitor.run_monitoring_cycle(
        results_limit=settings["apify"]["monitoring_results_limit"]
    )

    if result.get("success"):
        new_found = result["new_found"]
        dups = result["duplicates_prevented"]

        if new_found > 0:
            emit_log(
                f"Siklus #{result['cycle']}: "
                f"{new_found} KOMENTAR BARU DITEMUKAN!",
                "new_comment",
            )
            for comment in result.get("new_comments", []):
                socketio.emit("new_comment", comment)
        else:
            emit_log(
                f"Siklus #{result['cycle']}: "
                f"0 komentar baru, {dups} duplikat dicegah.",
                "info",
            )

        socketio.emit("cycle_complete", {
            "cycle": result["cycle"],
            "new_found": new_found,
            "duplicates_prevented": dups,
            "comments_scraped": result["comments_scraped"],
        })
        socketio.emit("status_change", monitor.get_status())
    else:
        emit_log(
            f"Siklus #{cycle} gagal: {result.get('error', 'Unknown')}",
            "error",
        )


# ── API: Run Once ─────────────────────────────────────────────


@app.route("/api/run-once", methods=["POST"])
def api_run_once():
    mode = monitor.mode

    if mode == MODE_MONITORING:
        cycle = monitor.cycle_number + 1
        emit_log(f"Siklus #{cycle}: Memeriksa komentar baru...", "info")

        result = monitor.run_once(
            results_limit=settings["apify"]["monitoring_results_limit"]
        )

        if result.get("success"):
            new_found = result.get("new_found", 0)
            if new_found > 0:
                emit_log(
                    f"{new_found} KOMENTAR BARU DITEMUKAN!",
                    "new_comment",
                )
                for comment in result.get("new_comments", []):
                    socketio.emit("new_comment", comment)
            else:
                emit_log(
                    f"0 komentar baru, "
                    f"{result.get('duplicates_prevented', 0)} duplikat dicegah.",
                    "info",
                )
            socketio.emit("cycle_complete", {
                "cycle": result.get("cycle", cycle),
                "new_found": new_found,
                "duplicates_prevented": result.get("duplicates_prevented", 0),
                "comments_scraped": result.get("comments_scraped", 0),
            })
            socketio.emit("status_change", monitor.get_status())
        else:
            emit_log(
                f"Gagal: {result.get('error', 'Unknown')}",
                "error",
            )
    else:
        result = {
            "success": False,
            "error": f"Run Once tidak tersedia di mode {mode}",
        }
        emit_log(result["error"], "error")

    return jsonify(result)


# ── API: Stop ─────────────────────────────────────────────────


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _monitoring_stop_event

    _monitoring_stop_event.set()
    result = monitor.stop()

    emit_log("Monitoring dihentikan.", "warning")
    emit_log("Dataset tersedia untuk download.", "info")
    socketio.emit("status_change", monitor.get_status())

    return jsonify(result)


# ── API: Reset ────────────────────────────────────────────────


@app.route("/api/reset", methods=["POST"])
def api_reset():
    global _monitoring_stop_event

    _monitoring_stop_event.set()
    result = monitor.reset()

    emit_log("Semua data direset.", "warning")
    emit_log("Kembali ke mode Idle.", "info")
    socketio.emit("status_change", monitor.get_status())

    return jsonify(result)


# ── API: New comments list ────────────────────────────────────


@app.route("/api/new-comments")
def api_new_comments():
    comments = monitor.get_new_comments()
    return jsonify(comments)


# ── API: Download CSV ─────────────────────────────────────────


@app.route("/api/download")
def api_download():
    csv_path = paths["new_comments_csv"]
    if not csv_path.exists():
        return jsonify({"error": "Belum ada data komentar"}), 404
    return send_file(
        csv_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="surabayasambat_comments.csv",
    )


# ── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # Reconfigure stdout for Windows UTF-8
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("=" * 60)
    print(f"  {settings['project']['name']}")
    print(f"  Post URL: {settings['target']['post_url']}")
    print(f"  Active tokens: {len(tokens)}")
    print(f"  Monitoring interval: {settings['monitoring']['interval_seconds']} detik")
    print(f"  Current mode: {monitor.mode}")
    print("=" * 60)
    print(f"  Dashboard: http://localhost:5000")
    print("=" * 60)

    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
