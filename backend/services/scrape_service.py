"""
services/scrape_service.py — Unified scrape orchestration.

Menjalankan flow: Discover → Queue → Comments dalam satu alur.
"""

import logging

from helpers.rate_limiter import get_current_tier
from helpers.history import record_scrape
from discover_posts import run_post_discovery
from process_queue import process_pending_queue

logger = logging.getLogger("demo_monitor")


def run_unified_scrape(scraper, settings, paths, history_path,
                       stop_event, emit_log, socketio, trigger="manual"):
    """
    Unified scraping flow:
    1. Discover postingan baru dari semua akun sumber
    2. Proses antrean → ambil komentar dari postingan baru

    Args:
        scraper: ApifyScraper instance
        settings: dict dari settings.yaml
        paths: dict dari get_paths()
        history_path: Path ke scrape_history.json
        stop_event: threading.Event
        emit_log: callable(message, level)
        socketio: SocketIO instance
        trigger: "manual" atau "auto"
    """
    try:
        # Phase 1: Discover
        emit_log("Langkah 1/2: Mencari postingan terbaru dari semua akun...", "info")

        tier = get_current_tier(settings, history_path)
        if not tier["allowed"]:
            emit_log(f"⚠️ {tier['reason']}", "warning")
            socketio.emit("scrape_complete", {"success": False, "error": tier["reason"]})
            return

        # Override settings with tier limits
        scrape_settings = dict(settings)
        scrape_settings["post_discovery"] = dict(settings.get("post_discovery", {}))
        scrape_settings["post_discovery"]["max_posts_per_source"] = tier["posts_per_source"]
        scrape_settings["post_discovery"]["comments_per_post"] = tier["comments_per_post"]

        api_calls_before = scraper.total_api_calls

        discovery_summary = run_post_discovery(
            scraper, scrape_settings, paths, stop_event=stop_event
        )

        if stop_event.is_set():
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

        _emit_discovered_posts(socketio, discovery_summary)

        queued = discovery_summary.get("total_queued", 0)
        if queued == 0:
            emit_log("Tidak ada postingan baru yang perlu di-scrape komentarnya.", "info")
            _record_and_emit(
                scraper, settings, history_path, trigger, discovery_summary,
                api_calls_before, 0, total_found, total_new, socketio
            )
            return

        # Phase 2: Process Queue
        emit_log(f"Langkah 2/2: Mengambil komentar dari {queued} postingan...", "info")

        queue_summary = _process_queue(
            scraper, scrape_settings, paths, stop_event, emit_log, socketio
        )

        total_comments = queue_summary.get("total_new_comments", 0)

        combined_summary = {
            "total_posts_discovered": total_found,
            "total_new_posts": total_new,
            "total_new_comments": total_comments,
            "skipped_low_relevance": discovery_summary.get("skipped_low_relevance", 0),
        }
        _record_and_emit(
            scraper, settings, history_path, trigger, combined_summary,
            api_calls_before, total_comments, total_found, total_new, socketio,
            queue_summary=queue_summary
        )

    except Exception as e:
        logger.error(f"Unified scrape error: {e}", exc_info=True)
        emit_log(f"❌ Terjadi kesalahan: {str(e)[:200]}", "error")
        socketio.emit("scrape_complete", {"success": False, "error": str(e)[:200]})


def _emit_discovered_posts(socketio, discovery_summary):
    """Emit setiap postingan baru ke UI."""
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


def _process_queue(scraper, settings, paths, stop_event, emit_log, socketio):
    """Proses antrean queue dan emit progress per item."""
    def on_queue_item(item):
        if item["status"] == "completed":
            emit_log(
                f"✅ {item.get('source_account', '')} — "
                f"{item['new_comments']} komentar baru",
                "success",
            )
        else:
            error_msg = item.get("error_message", "")
            if any(kw in error_msg.lower() for kw in ["kuota", "quota", "limit"]):
                emit_log("⚠️ Kuota API habis. Proses dihentikan otomatis.", "warning")
            else:
                emit_log(f"❌ Gagal: {error_msg}", "error")
        socketio.emit("queue_item_done", item)

    return process_pending_queue(
        scraper, settings, paths,
        on_item=on_queue_item,
        stop_event=stop_event,
    )


def _record_and_emit(scraper, settings, history_path, trigger, summary,
                     api_calls_before, total_comments, total_found, total_new,
                     socketio, queue_summary=None):
    """Record history dan emit scrape_complete."""
    api_calls_after = scraper.total_api_calls
    session_calls = api_calls_after - api_calls_before
    session_cost = session_calls * settings["apify"].get("estimated_cost_per_call", 0.032)
    record_scrape(history_path, trigger, summary,
                  api_calls_session=session_calls,
                  estimated_cost_session=session_cost)

    result = {
        "success": True,
        "posts_found": total_found,
        "new_posts": total_new,
        "new_comments": total_comments,
    }

    if queue_summary:
        result["completed"] = queue_summary.get("total_completed", 0)
        result["failed"] = queue_summary.get("total_failed", 0)
        result["skipped"] = queue_summary.get("total_skipped", 0)
        result["stopped_early"] = queue_summary.get("stopped_early", False)

    socketio.emit("scrape_complete", result)
