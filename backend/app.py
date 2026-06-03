"""
app.py — Flask + SocketIO server untuk SurabayaSambat v2.

Dashboard monitoring keluhan masyarakat Surabaya.
Unified scraping flow: Discover → Queue → Comments dalam satu alur.

Entry point: python backend/app.py
Dashboard: http://localhost:5000
"""

import sys
import json
import logging
import threading
import time
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, request, send_from_directory, send_file
from flask_socketio import SocketIO

# Ensure backend/ is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_settings, get_apify_tokens, get_paths
from scraper import ApifyScraper
from discover_posts import (
    run_post_discovery,
    load_source_registry,
    get_active_sources,
)
from process_queue import process_pending_queue, get_queue_rows, get_posts_rows
from stage4_utils import load_csv_if_exists, safe_write_csv
import pandas as pd

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

# Usage log path
usage_log_path = str(
    Path(__file__).resolve().parent.parent
    / settings.get("output", {}).get("api_usage_log", "data/api_usage_log.json")
)

scraper = ApifyScraper(
    tokens=tokens,
    actor_id=settings["apify"]["actor_id"],
    timeout=settings["apify"]["request_timeout_seconds"],
    cost_per_call=settings["apify"].get("estimated_cost_per_call", 0.032),
    usage_log_path=usage_log_path,
)

# Scrape history path (for rate limiting)
_scrape_history_path = Path(__file__).resolve().parent.parent / settings.get(
    "output", {}
).get("scrape_history", "data/scrape_history.json")

# Thread-safe state
_scrape_lock = threading.Lock()
_scrape_stop_event = threading.Event()
_system_active = False  # ON/OFF toggle state
_scrape_thread = None
_last_auto_scrape_time = None  # Waktu terakhir mulai timer otomatis (untuk mode Demo 2-menit)


# ── Helper: emit log ke dashboard ─────────────────────────────


def emit_log(message: str, level: str = "info"):
    """Kirim log message ke dashboard via WebSocket."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "level": level,
    }
    socketio.emit("log", entry)
    logger.info(f"[Dashboard] {message}")


# ── Scrape History (for rate limiting) ────────────────────────


def _load_scrape_history() -> list:
    """Load scrape history dari file JSON."""
    if _scrape_history_path.exists():
        try:
            with open(_scrape_history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def _save_scrape_history(history: list):
    """Persist scrape history ke file JSON. Auto-pruning > 4 minggu."""
    try:
        # Pruning: hapus entry > 4 minggu
        cutoff = (datetime.now(timezone.utc) - timedelta(weeks=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        history = [e for e in history if e.get("timestamp", "") >= cutoff]

        _scrape_history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_scrape_history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.warning(f"Gagal simpan scrape history: {e}")


def _record_scrape(trigger: str, summary: dict, api_calls_session: int = 0, estimated_cost_session: float = 0.0):
    """Catat satu sesi scraping ke history termasuk biaya."""
    history = _load_scrape_history()
    history.append({
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trigger": trigger,  # "auto" or "manual"
        "posts_discovered": summary.get("total_posts_discovered", 0),
        "new_posts": summary.get("total_new_posts", 0),
        "new_comments": summary.get("total_new_comments", 0),
        "skipped_low_relevance": summary.get("skipped_low_relevance", 0),
        "api_calls_session": api_calls_session,
        "estimated_cost_session": round(estimated_cost_session, 4),
    })
    _save_scrape_history(history)


def _get_week_scrape_count() -> int:
    """Hitung berapa kali scraping sudah dilakukan minggu ini."""
    history = _load_scrape_history()
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


def _get_current_tier() -> dict:
    """Tentukan tier budget berdasarkan jumlah scraping minggu ini."""
    week_count = _get_week_scrape_count()
    rate_config = settings.get("rate_limit", {})
    max_per_week = rate_config.get("max_scrapes_per_week", 4)
    tiers = rate_config.get("budget_tiers", [])

    if week_count >= max_per_week:
        return {"allowed": False, "tier_number": week_count + 1, "reason": 
                f"Batas scraping minggu ini tercapai ({week_count}/{max_per_week})."}

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


# ── Routes: Static files ──────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ── API: Status ───────────────────────────────────────────────


@app.route("/api/status")
def api_status():
    tier_info = _get_current_tier()
    schedule_cfg = settings.get("schedule", {})

    return jsonify({
        "system_active": _system_active,
        "is_scraping": _scrape_lock.locked(),
        "auto_scrape_day": schedule_cfg.get("auto_scrape_day", "monday"),
        "auto_scrape_hour": schedule_cfg.get("auto_scrape_hour", 8),
        "next_auto_scrape_time": _calculate_next_scrape_time(),
        "tier_info": tier_info,
    })


# ══════════════════════════════════════════════════════════════
# UNIFIED SCRAPE: satu tombol = discover + queue processing
# ══════════════════════════════════════════════════════════════


def _run_unified_scrape(trigger: str = "manual"):
    """
    Unified scraping flow:
    1. Discover postingan baru dari semua akun sumber
    2. Proses antrean → ambil komentar dari postingan baru
    
    Args:
        trigger: "manual" atau "auto"
    """
    try:
        # Phase 1: Discover
        emit_log("Langkah 1/2: Mencari postingan terbaru dari semua akun...", "info")
        
        # Get tier limits
        tier = _get_current_tier()
        if not tier["allowed"]:
            emit_log(f"⚠️ {tier['reason']}", "warning")
            socketio.emit("scrape_complete", {"success": False, "error": tier["reason"]})
            return

        # Override settings with tier limits
        scrape_settings = dict(settings)
        scrape_settings["post_discovery"] = dict(settings.get("post_discovery", {}))
        scrape_settings["post_discovery"]["max_posts_per_source"] = tier["posts_per_source"]
        scrape_settings["post_discovery"]["comments_per_post"] = tier["comments_per_post"]

        # Snapshot API call count BEFORE scraping
        api_calls_before = scraper.total_api_calls

        discovery_summary = run_post_discovery(
            scraper, scrape_settings, paths, stop_event=_scrape_stop_event
        )

        if _scrape_stop_event.is_set():
            emit_log("⏹ Proses dihentikan oleh pengguna.", "warning")
            socketio.emit("scrape_complete", {"success": False, "error": "Dihentikan"})
            return

        if "error" in discovery_summary:
            emit_log(f"❌ {discovery_summary['error']}", "error")
            socketio.emit("scrape_complete", {
                "success": False, "error": discovery_summary["error"]
            })
            return

        total_new = discovery_summary.get("total_new_posts", 0)
        total_found = discovery_summary.get("total_posts_discovered", 0)
        total_changed = discovery_summary.get("total_comment_changed", 0)

        emit_log(
            f"✅ Ditemukan {total_found} postingan, {total_new} baru, "
            f"{total_changed} ada komentar baru.",
            "success",
        )

        # Emit discovered posts to UI
        for post in discovery_summary.get("new_posts", []):
            socketio.emit("new_post", {
                "post_url": post.get("post_url", ""),
                "source_account": post.get("source_account", ""),
                "post_shortcode": post.get("post_shortcode", ""),
                "caption_raw": (post.get("caption_raw", "") or "")[:200],
                "post_relevance": post.get("post_relevance", ""),
                "relevance_score": post.get("relevance_score", ""),
                "relevance_reasons": post.get("relevance_reasons", ""),
                "comment_count_last_seen": post.get("comment_count_last_seen", ""),
                "discovered_at": post.get("discovered_at", ""),
            })

        queued = discovery_summary.get("total_queued", 0)
        if queued == 0:
            emit_log("Tidak ada postingan baru yang perlu di-scrape komentarnya.", "info")
            api_calls_after = scraper.total_api_calls
            session_calls = api_calls_after - api_calls_before
            session_cost = session_calls * settings["apify"].get("estimated_cost_per_call", 0.032)
            _record_scrape(trigger, discovery_summary, api_calls_session=session_calls, estimated_cost_session=session_cost)
            socketio.emit("scrape_complete", {
                "success": True,
                "posts_found": total_found,
                "new_posts": total_new,
                "new_comments": 0,
            })
            return

        # Phase 2: Process Queue (ambil komentar)
        emit_log(
            f"Langkah 2/2: Mengambil komentar dari {queued} postingan...",
            "info",
        )

        def _on_queue_item(item):
            if item["status"] == "completed":
                emit_log(
                    f"✅ {item.get('source_account', '')} — "
                    f"{item['new_comments']} komentar baru",
                    "success",
                )
            else:
                error_msg = item.get("error_message", "")
                if "kuota" in error_msg.lower() or "quota" in error_msg.lower() or "limit" in error_msg.lower():
                    emit_log(f"⚠️ Kuota API habis. Proses dihentikan otomatis.", "warning")
                else:
                    emit_log(f"❌ Gagal: {error_msg}", "error")
            socketio.emit("queue_item_done", item)

        queue_summary = process_pending_queue(
            scraper, scrape_settings, paths,
            on_item=_on_queue_item,
            stop_event=_scrape_stop_event,
        )

        total_comments = queue_summary.get("total_new_comments", 0)
        total_completed = queue_summary.get("total_completed", 0)
        total_failed = queue_summary.get("total_failed", 0)
        total_skipped = queue_summary.get("total_skipped", 0)

        # Combine summaries for record with cost tracking
        api_calls_after = scraper.total_api_calls
        session_calls = api_calls_after - api_calls_before
        session_cost = session_calls * settings["apify"].get("estimated_cost_per_call", 0.032)

        combined_summary = {
            "total_posts_discovered": total_found,
            "total_new_posts": total_new,
            "total_new_comments": total_comments,
            "skipped_low_relevance": discovery_summary.get("skipped_low_relevance", 0),
        }
        _record_scrape(trigger, combined_summary, api_calls_session=session_calls, estimated_cost_session=session_cost)

        # Final message
        if queue_summary.get("stopped_early"):
            stop_reason = queue_summary.get("stop_reason", "")
            emit_log(
                f"⚠️ Proses berhenti lebih awal: {stop_reason}",
                "warning",
            )
            emit_log(
                f"Data yang sudah diambil ({total_comments} komentar) tetap tersimpan.",
                "info",
            )
        else:
            emit_log(
                f"✅ Selesai! {total_comments} komentar baru dari "
                f"{total_completed} postingan.",
                "success",
            )

        if total_failed > 0:
            emit_log(
                f"⚠️ {total_failed} postingan gagal diproses.",
                "warning",
            )
        if total_skipped > 0:
            emit_log(
                f"ℹ️ {total_skipped} postingan dilewati (kuota habis).",
                "info",
            )

        socketio.emit("scrape_complete", {
            "success": True,
            "posts_found": total_found,
            "new_posts": total_new,
            "new_comments": total_comments,
            "completed": total_completed,
            "failed": total_failed,
            "skipped": total_skipped,
            "stopped_early": queue_summary.get("stopped_early", False),
        })

    except Exception as e:
        logger.error(f"Unified scrape error: {e}", exc_info=True)
        emit_log(f"❌ Terjadi kesalahan: {str(e)[:200]}", "error")
        socketio.emit("scrape_complete", {"success": False, "error": str(e)[:200]})


# ── API: Scrape Sekarang ──────────────────────────────────────


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """Unified scrape: discover + process queue dalam satu alur."""
    global _scrape_thread

    if not _scrape_lock.acquire(blocking=False):
        return jsonify({
            "success": False,
            "error": "Proses scraping sedang berjalan. Tunggu hingga selesai atau tekan Stop.",
        }), 409

    # Check rate limit
    tier = _get_current_tier()
    if not tier["allowed"]:
        _scrape_lock.release()
        return jsonify({"success": False, "error": tier["reason"]}), 429

    # Reset stop event
    _scrape_stop_event.clear()

    tier_num = tier["tier_number"]
    max_week = tier["max_per_week"]

    emit_log(
        f"🔄 Memulai scraping (scraping ke-{tier_num} minggu ini, maks {max_week})...",
        "info",
    )
    if tier_num > 1:
        emit_log(
            f"ℹ️ Limit tier {tier_num}: maks {tier['posts_per_source']} postingan/akun, "
            f"{tier['comments_per_post']} komentar/postingan.",
            "info",
        )

    socketio.emit("scrape_started", {"tier": tier_num})

    # Run in background thread
    def _scrape_job():
        try:
            _run_unified_scrape(trigger="manual")
        finally:
            _scrape_lock.release()
            socketio.emit("status_change", _get_status_data())

    _scrape_thread = threading.Thread(target=_scrape_job, daemon=True)
    _scrape_thread.start()

    return jsonify({"success": True, "tier": tier_num})


# ── API: Stop Scraping ────────────────────────────────────────


@app.route("/api/stop-scrape", methods=["POST"])
def api_stop_scrape():
    """Hentikan proses scraping yang sedang berjalan."""
    _scrape_stop_event.set()
    emit_log("⏹ Menghentikan proses...", "warning")
    return jsonify({"success": True})


# ── API: Toggle System ON/OFF ─────────────────────────────────


@app.route("/api/toggle-system", methods=["POST"])
def api_toggle_system():
    """Toggle sistem ON/OFF. Saat ON, scraping otomatis terjadwal."""
    global _system_active, _last_auto_scrape_time

    _system_active = not _system_active
    status = "aktif" if _system_active else "nonaktif"

    if _system_active:
        _last_auto_scrape_time = datetime.now(timezone.utc)
        emit_log(
            f"✅ Sistem diaktifkan. [MODE DEMO] Scraping otomatis setiap 2 menit.",
            "success",
        )
    else:
        _last_auto_scrape_time = None
        emit_log("⏹ Sistem dinonaktifkan. Scraping otomatis dihentikan.", "warning")

    socketio.emit("system_toggle", {"active": _system_active})
    socketio.emit("status_change", _get_status_data())

    return jsonify({"success": True, "system_active": _system_active})


# ── API: Usage & Cost ─────────────────────────────────────────


@app.route("/api/usage")
def api_usage():
    """Statistik penggunaan API dan biaya."""
    usage = scraper.get_usage_summary()
    tier = _get_current_tier()
    usage["rate_limit"] = tier
    usage["scrape_history"] = _load_scrape_history()[-10:]  # last 10
    return jsonify(usage)


# ── API: Download Dataset ─────────────────────────────────────


@app.route("/api/download")
def api_download():
    """Download semua komentar sebagai CSV."""
    csv_path = paths["raw_comments_csv"]
    if not csv_path.exists():
        return jsonify({"error": "Belum ada data komentar"}), 404
    return send_file(
        csv_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="surabayasambat_komentar.csv",
    )


# ── API: Reset Data ───────────────────────────────────────────


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset semua data scraping."""
    global _system_active
    _scrape_stop_event.set()
    _system_active = False

    # Delete data files
    data_files = [
        paths.get("raw_posts_csv"),
        paths.get("post_queue_csv"),
        paths.get("raw_comments_csv"),
    ]
    for f in data_files:
        if f and f.exists():
            f.unlink()
            logger.info(f"Deleted {f}")

    emit_log("🗑️ Semua data direset.", "warning")
    socketio.emit("status_change", _get_status_data())
    return jsonify({"success": True})


# ══════════════════════════════════════════════════════════════
# SOURCE MANAGEMENT: Tambah/Hapus akun sumber dari dashboard
# ══════════════════════════════════════════════════════════════


@app.route("/api/sources")
def api_sources():
    """Daftar semua akun sumber."""
    df = load_source_registry(paths["source_registry"])
    if df.empty:
        return jsonify({"sources": [], "active_count": 0})

    active_ids = set()
    df_active = get_active_sources(df)
    if not df_active.empty:
        active_ids = set(df_active["source_id"].tolist())

    sources = []
    for _, row in df.fillna("").iterrows():
        sid = row.get("source_id", "")
        sources.append({
            "source_id": sid,
            "source_account": row.get("source_account", ""),
            "platform": row.get("platform", ""),
            "priority_level": row.get("priority_level", ""),
            "monitoring_window_days": row.get("monitoring_window_days", ""),
            "status": row.get("status", ""),
            "notes": row.get("notes", ""),
            "is_active": sid in active_ids,
        })

    return jsonify({"sources": sources, "active_count": len(active_ids)})


@app.route("/api/sources", methods=["POST"])
def api_add_source():
    """Tambah akun sumber baru. User cukup paste link profil IG."""
    data = request.get_json()
    profile_url = data.get("profile_url", "").strip()
    priority = str(data.get("priority_level", "2")).strip()
    window_days = str(data.get("monitoring_window_days", "30")).strip()

    if not profile_url:
        return jsonify({"success": False, "error": "Link profil harus diisi"}), 400

    # Extract username dari URL
    username = _extract_ig_username(profile_url)
    if not username:
        return jsonify({
            "success": False,
            "error": "Link tidak valid. Gunakan format: https://www.instagram.com/username/",
        }), 400

    # Load existing
    registry_path = paths["source_registry"]
    df = pd.DataFrame()
    if registry_path.exists():
        df = pd.read_csv(registry_path, dtype=str).fillna("")

    # Check duplicate
    if not df.empty and f"@{username}" in df["source_account"].values:
        return jsonify({
            "success": False,
            "error": f"Akun @{username} sudah ada di daftar",
        }), 409

    # Generate new source_id
    existing_ids = set(df["source_id"].tolist()) if not df.empty else set()
    new_num = 1
    while f"SRC{new_num:03d}" in existing_ids:
        new_num += 1
    new_id = f"SRC{new_num:03d}"

    # Add new row
    new_row = pd.DataFrame([{
        "source_id": new_id,
        "source_account": f"@{username}",
        "platform": "instagram",
        "priority_level": priority,
        "monitoring_window_days": window_days,
        "status": "active",
        "notes": f"Ditambahkan via dashboard",
    }])

    if df.empty:
        df = new_row
    else:
        df = pd.concat([df, new_row], ignore_index=True)

    safe_write_csv(df, registry_path, f"Tambah akun @{username}", logger)

    emit_log(f"✅ Akun @{username} ditambahkan ke daftar pantauan.", "success")
    socketio.emit("source_added", {"source_account": f"@{username}", "source_id": new_id})

    return jsonify({"success": True, "source_id": new_id, "username": username})


@app.route("/api/sources/<source_id>", methods=["DELETE"])
def api_delete_source(source_id):
    """Hapus akun sumber berdasarkan source_id."""
    registry_path = paths["source_registry"]
    if not registry_path.exists():
        return jsonify({"success": False, "error": "Data tidak ditemukan"}), 404

    df = pd.read_csv(registry_path, dtype=str).fillna("")

    mask = df["source_id"] == source_id
    if not mask.any():
        return jsonify({"success": False, "error": f"Akun {source_id} tidak ditemukan"}), 404

    account_name = df.loc[mask, "source_account"].values[0]
    df = df[~mask]
    safe_write_csv(df, registry_path, f"Hapus akun {account_name}", logger)

    emit_log(f"🗑️ Akun {account_name} dihapus dari daftar pantauan.", "warning")
    socketio.emit("source_deleted", {"source_id": source_id})

    return jsonify({"success": True})


def _extract_ig_username(url_or_username: str) -> str:
    """Extract username dari URL Instagram atau username langsung."""
    url_or_username = url_or_username.strip().rstrip("/")

    # Jika sudah @username
    if url_or_username.startswith("@"):
        return url_or_username.lstrip("@")

    # Parse URL
    match = re.search(r"instagram\.com/([a-zA-Z0-9_.]+)", url_or_username)
    if match:
        username = match.group(1)
        # Filter built-in pages
        if username in ("p", "reel", "explore", "stories", "accounts", "tv"):
            return ""
        return username

    # Plain username (no URL)
    if re.match(r"^[a-zA-Z0-9_.]+$", url_or_username):
        return url_or_username

    return ""


# ── API: Posts & Queue (read-only) ────────────────────────────


@app.route("/api/posts")
def api_posts():
    return jsonify(get_posts_rows(paths))


@app.route("/api/queue")
def api_queue():
    return jsonify(get_queue_rows(paths))


@app.route("/api/comments")
def api_comments():
    """Daftar komentar yang sudah di-scrape (dengan paginasi)."""
    df = load_csv_if_exists(paths["raw_comments_csv"])
    if df.empty:
        return jsonify({"comments": [], "total": 0, "page": 1, "per_page": 50})

    total_rows = len(df)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = min(per_page, 200)  # maks 200 per request

    df = df.sort_values("scraped_at", ascending=False)
    offset = (page - 1) * per_page
    df_page = df.iloc[offset:offset + per_page]

    return jsonify({
        "comments": df_page.fillna("").to_dict(orient="records"),
        "total": total_rows,
        "page": page,
        "per_page": per_page,
    })


# ── API: Dashboard Init (gabungan untuk mengurangi request) ───


@app.route("/api/dashboard-init")
def api_dashboard_init():
    """Endpoint gabungan: status + usage + sources + stats dalam 1 request."""
    # Status
    tier_info = _get_current_tier()
    schedule_cfg = settings.get("schedule", {})
    status_data = {
        "system_active": _system_active,
        "is_scraping": _scrape_lock.locked(),
        "auto_scrape_day": schedule_cfg.get("auto_scrape_day", "monday"),
        "auto_scrape_hour": schedule_cfg.get("auto_scrape_hour", 8),
        "next_auto_scrape_time": _calculate_next_scrape_time(),
        "tier_info": tier_info,
    }

    # Usage
    usage_data = scraper.get_usage_summary()
    usage_data["rate_limit"] = tier_info
    usage_data["scrape_history"] = _load_scrape_history()[-10:]

    # Sources
    df = load_source_registry(paths["source_registry"])
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

    # Stats
    posts = get_posts_rows(paths)
    comments_df = load_csv_if_exists(paths["raw_comments_csv"])
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


# ── Scheduler: Auto Scrape (MODE DEMO 2 MENIT) ────────────────


def _calculate_next_scrape_time() -> str:
    """Hitung waktu scraping otomatis berikutnya (Mode Demo 2 Menit)."""
    if not _system_active or not _last_auto_scrape_time:
        return None

    # Tambahkan 2 menit dari waktu _last_auto_scrape_time
    next_scrape_utc = _last_auto_scrape_time + timedelta(minutes=2)
    return next_scrape_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _auto_scrape_loop():
    """Background loop untuk mengecek jadwal auto-scrape (Mode Demo 2 Menit)."""
    global _last_auto_scrape_time
    while True:
        try:
            if _system_active and not _scrape_lock.locked() and _last_auto_scrape_time:
                now = datetime.now(timezone.utc)
                next_scrape = _last_auto_scrape_time + timedelta(minutes=2)

                # Jika sudah waktunya (2 menit lewat)
                if now >= next_scrape:
                    # Reset timer untuk 2 menit berikutnya
                    _last_auto_scrape_time = now
                    
                    tier = _get_current_tier()
                    if tier["allowed"]:
                        logger.info("[Scheduler] Memulai scraping otomatis (Demo 2 menit)!")
                        emit_log("🤖 Memulai scraping otomatis (Mode Demo 2 Menit).", "info")
                        if _scrape_lock.acquire(blocking=False):
                            _scrape_stop_event.clear()
                            socketio.emit("scrape_started", {"tier": tier["tier_number"]})
                            try:
                                _run_unified_scrape(trigger="auto")
                            finally:
                                _scrape_lock.release()
                                socketio.emit("status_change", _get_status_data())
        except Exception as e:
            logger.error(f"[Scheduler] Error: {e}", exc_info=True)
            
        time.sleep(10) # Cek setiap 10 detik agar responsif di UI

# Jalankan scheduler daemon
_scheduler_thread = threading.Thread(target=_auto_scrape_loop, daemon=True)
_scheduler_thread.start()


# ── Helper: status data ──────────────────────────────────────


def _get_status_data() -> dict:
    tier = _get_current_tier()
    schedule_cfg = settings.get("schedule", {})
    return {
        "system_active": _system_active,
        "is_scraping": _scrape_lock.locked(),
        "auto_scrape_day": schedule_cfg.get("auto_scrape_day", "monday"),
        "auto_scrape_hour": schedule_cfg.get("auto_scrape_hour", 8),
        "next_auto_scrape_time": _calculate_next_scrape_time(),
        "tier_info": tier,
    }


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
    print(f"  Token aktif: {len(tokens)}")
    print("=" * 60)
    print(f"  Dashboard: http://localhost:5000")
    print("=" * 60)

    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
