"""
process_queue.py — Stage 4: proses antrean (post_queue) → ambil komentar.

Untuk tiap entry pending: scrape komentar dari post_url (resultsType=comments),
cocokkan komentar ke post via SHORTCODE (tahan beda /p/ vs /reel/), dedup,
simpan ke raw_comments.csv, update status queue (running → completed/failed).
"""

import logging

import pandas as pd

from stage4_utils import (
    now_utc,
    make_sha256,
    extract_value_by_candidates,
    load_csv_if_exists,
    safe_write_csv,
    url_shortcode,
)
from post_queue import (
    load_post_queue,
    save_post_queue,
    get_pending_queue,
    update_queue_status,
)

logger = logging.getLogger("demo_monitor")

COMMENT_COLUMNS = [
    "comment_id_hash", "comment_id", "post_id_hash", "post_url",
    "post_shortcode", "source_account", "comment_text", "comment_created_at",
    "scraped_at", "queue_id", "scraping_status",
]

COMMENT_TEXT_CANDIDATES = ["text", "comment", "commentText", "comment_text", "body", "content"]
COMMENT_ID_CANDIDATES = ["id", "cid", "commentId", "comment_id", "pk"]
COMMENT_TS_CANDIDATES = ["timestamp", "createdAt", "created_at", "takenAt", "date"]
COMMENT_URL_CANDIDATES = ["postUrl", "post_url", "url", "permalink"]
COMMENT_SHORTCODE_CANDIDATES = ["shortCode", "shortcode", "code"]


def _comment_shortcode(item: dict) -> str:
    """Ekstrak shortcode komentar dari URL atau field shortcode (jika ada)."""
    sc = extract_value_by_candidates(item, COMMENT_SHORTCODE_CANDIDATES)
    if sc:
        return str(sc).strip()
    url = extract_value_by_candidates(item, COMMENT_URL_CANDIDATES)
    if url:
        return url_shortcode(str(url))
    return ""


def _normalize_comment(item: dict, post_row: dict, queue_id: str) -> dict:
    comment_id = extract_value_by_candidates(item, COMMENT_ID_CANDIDATES)
    comment_id = str(comment_id) if comment_id is not None else ""

    text = extract_value_by_candidates(item, COMMENT_TEXT_CANDIDATES)
    text = str(text) if text is not None else ""

    created_at = extract_value_by_candidates(item, COMMENT_TS_CANDIDATES)
    created_at = str(created_at) if created_at is not None else ""

    post_url = post_row.get("post_url", "")

    if comment_id:
        comment_id_hash = make_sha256(f"instagram|{comment_id}")
    else:
        comment_id_hash = make_sha256(
            f"instagram|{post_url}|{text}|{created_at}"
        )

    return {
        "comment_id_hash": comment_id_hash,
        "comment_id": comment_id,
        "post_id_hash": post_row.get("post_id_hash", ""),
        "post_url": post_url,
        "post_shortcode": post_row.get("post_shortcode", "") or url_shortcode(post_url),
        "source_account": post_row.get("source_account", ""),
        "comment_text": text,
        "comment_created_at": created_at,
        "scraped_at": now_utc(),
        "queue_id": queue_id,
        "scraping_status": "new",
    }


def _load_existing_comment_hashes(comments_csv_path) -> set:
    df = load_csv_if_exists(comments_csv_path)
    if df.empty or "comment_id_hash" not in df.columns:
        return set()
    return set(df["comment_id_hash"].tolist())


def _append_comments(new_rows: list, comments_csv_path):
    if not new_rows:
        return
    df_new = pd.DataFrame(new_rows).reindex(columns=COMMENT_COLUMNS)
    existing = load_csv_if_exists(comments_csv_path)
    if not existing.empty:
        existing = existing.reindex(columns=COMMENT_COLUMNS)
        df_final = pd.concat([existing, df_new], ignore_index=True)
    else:
        df_final = df_new
    safe_write_csv(df_final, comments_csv_path, f"{len(df_new)} new comments", logger)


def process_one_queue_item(
    scraper, queue_row, paths, comments_limit, existing_hashes
) -> dict:
    """
    Proses satu entry queue. Return dict ringkasan untuk entry ini.
    Tidak meng-update CSV queue (dikelola pemanggil).
    """
    queue_id = queue_row.get("queue_id", "")
    post_url = str(queue_row.get("post_url", "")).strip()
    post_id_hash = queue_row.get("post_id_hash", "")
    source_account = queue_row.get("source_account", "")

    out = {
        "queue_id": queue_id,
        "post_url": post_url,
        "source_account": source_account,
        "status": "failed",
        "error_message": "",
        "new_comments": 0,
        "duplicates": 0,
        "found": 0,
    }

    if not post_url:
        out["error_message"] = "post_url kosong"
        logger.warning(f"[{queue_id}] invalid: post_url kosong")
        return out

    result = scraper.scrape_comments(post_url, limit=comments_limit)

    if result["api_status"] != "success":
        out["error_message"] = result.get("error_message", "api error")
        logger.error(f"[{queue_id}] Apify gagal: {out['error_message']}")
        return out

    comments = result["comments"]
    out["found"] = len(comments)

    target_sc = url_shortcode(post_url)
    post_row = {
        "post_id_hash": post_id_hash,
        "post_url": post_url,
        "post_shortcode": target_sc,
        "source_account": source_account,
    }

    new_rows = []
    for item in comments:
        # Cocokkan ke post via shortcode (tahan /p/ vs /reel/). Jika komentar tidak
        # membawa info shortcode/url, anggap milik post yang sedang di-scrape.
        item_sc = _comment_shortcode(item)
        if target_sc and item_sc and item_sc != target_sc:
            continue

        normalized = _normalize_comment(item, post_row, queue_id)
        h = normalized["comment_id_hash"]
        if h in existing_hashes:
            out["duplicates"] += 1
            continue
        existing_hashes.add(h)
        new_rows.append(normalized)

    out["new_comments"] = len(new_rows)
    _append_comments(new_rows, paths["raw_comments_csv"])

    out["status"] = "completed"
    logger.info(
        f"[{queue_id}] found={out['found']} new={out['new_comments']} "
        f"dup={out['duplicates']}"
    )
    return out


def process_pending_queue(scraper, settings, paths, on_item=None, stop_event=None) -> dict:
    """
    Proses semua entry pending di post_queue.

    Args:
        scraper: ApifyScraper
        settings, paths: config demo
        on_item: optional callback(item_result_dict) untuk emit realtime
        stop_event: threading.Event, jika set() maka proses berhenti
    """
    comments_limit = settings.get("post_discovery", {}).get("comments_per_post", 100)

    df_queue = load_post_queue(paths["post_queue_csv"])
    pending = get_pending_queue(df_queue)

    summary = {
        "total_pending": len(pending),
        "total_completed": 0,
        "total_failed": 0,
        "total_skipped": 0,
        "total_new_comments": 0,
        "items": [],
        "stopped_early": False,
        "stop_reason": "",
    }

    if pending.empty:
        logger.info("Tidak ada entry pending di post_queue")
        return summary

    existing_hashes = _load_existing_comment_hashes(paths["raw_comments_csv"])
    processed_count = 0

    for _, queue_row in pending.iterrows():
        queue_id = queue_row.get("queue_id", "")

        # Cek apakah diminta berhenti oleh user
        if stop_event and stop_event.is_set():
            remaining = len(pending) - processed_count
            logger.info(
                f"Proses dihentikan oleh pengguna. "
                f"{remaining} postingan belum diproses."
            )
            # Tandai sisa sebagai skipped
            summary["stopped_early"] = True
            summary["stop_reason"] = "Dihentikan oleh pengguna"
            summary["total_skipped"] = remaining
            break

        # Tandai running (in-memory only, batch save at end)
        df_queue = update_queue_status(df_queue, queue_id, "running")

        item_result = process_one_queue_item(
            scraper, queue_row, paths, comments_limit, existing_hashes
        )
        processed_count += 1

        if item_result["status"] == "completed":
            df_queue = update_queue_status(df_queue, queue_id, "completed")
            summary["total_completed"] += 1
            summary["total_new_comments"] += item_result["new_comments"]
        else:
            # Cek apakah gagal karena kuota habis
            error_msg = item_result.get("error_message", "")
            is_quota_error = any(kw in error_msg.lower() for kw in [
                "quota", "limit exceeded", "hard limit", "402", "403",
                "all_tokens_exhausted",
            ])

            if is_quota_error or item_result.get("api_status") in (
                "quota_exceeded", "all_tokens_exhausted"
            ):
                # AUTO-STOP: kuota habis = semua call berikutnya pasti gagal
                df_queue = update_queue_status(
                    df_queue, queue_id, "failed",
                    error_message="Kuota API habis",
                )
                summary["total_failed"] += 1

                remaining = len(pending) - processed_count
                logger.warning(
                    f"⚠️ Kuota API habis. Menghentikan {remaining} antrean tersisa. "
                    f"Data yang sudah diambil tetap tersimpan."
                )
                summary["stopped_early"] = True
                summary["stop_reason"] = (
                    f"Kuota API habis. {remaining} postingan belum diproses."
                )
                summary["total_skipped"] = remaining

                save_post_queue(df_queue, paths["post_queue_csv"])
                summary["items"].append(item_result)
                if on_item:
                    on_item(item_result)
                break
            else:
                df_queue = update_queue_status(
                    df_queue, queue_id, "failed",
                    error_message=item_result["error_message"],
                )
                summary["total_failed"] += 1

        summary["items"].append(item_result)

        if on_item:
            on_item(item_result)

    # Batch save: tulis CSV sekali di akhir (hemat I/O)
    save_post_queue(df_queue, paths["post_queue_csv"])

    logger.info(
        f"Antrean selesai: {summary['total_completed']} berhasil, "
        f"{summary['total_failed']} gagal, "
        f"{summary['total_skipped']} dilewati, "
        f"{summary['total_new_comments']} komentar baru"
    )
    return summary


def get_queue_rows(paths) -> list:
    df = load_post_queue(paths["post_queue_csv"])
    if df.empty:
        return []
    return df.fillna("").to_dict(orient="records")


def get_posts_rows(paths) -> list:
    df = load_csv_if_exists(paths["raw_posts_csv"])
    if df.empty:
        return []
    return df.fillna("").to_dict(orient="records")
