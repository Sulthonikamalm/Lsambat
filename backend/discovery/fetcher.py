"""
discovery/fetcher.py — Fetching and normalization logic for posts.
"""

import json
import logging
from datetime import datetime, timezone

from stage4_utils import (
    make_sha256,
    now_utc,
    extract_value_by_candidates,
    safe_json_dumps,
)
from discovery.registry import username_to_profile_url, calc_window_date
from discovery.scoring import score_post_relevance

logger = logging.getLogger("demo_monitor")


def _fb_page_to_url(source_account: str) -> str:
    """Konversi nama halaman FB ke URL."""
    page_name = source_account.strip().lstrip("@")
    return f"https://www.facebook.com/{page_name}/"

POST_URL_CANDIDATES = ["url", "postUrl", "permalink", "displayUrl"]
CAPTION_CANDIDATES = ["caption", "text", "description", "alt"]
TIMESTAMP_CANDIDATES = ["timestamp", "createdAt", "takenAt", "date"]
SHORTCODE_CANDIDATES = ["shortCode", "shortcode", "code"]
COMMENT_COUNT_CANDIDATES = ["commentsCount", "commentCount", "comments_count"]
LIKE_COUNT_CANDIDATES = ["likesCount", "likeCount", "likes_count"]


def discover_posts_for_source(
    scraper, source_row, max_posts, use_window=True
) -> tuple:
    """
    Ambil postingan satu akun via Apify, normalisasi jadi list of dict.
    Mendukung multi-platform (instagram / facebook).
    Return: (list_of_posts, api_status_string)
    """
    source_id = str(source_row.get("source_id", "")).strip()
    source_account = str(source_row.get("source_account", "")).strip()
    priority_level = str(source_row.get("priority_level", "3")).strip()
    monitoring_window_days = str(source_row.get("monitoring_window_days", "")).strip()
    platform = str(source_row.get("platform", "instagram")).strip().lower()

    window_date = None
    if use_window and monitoring_window_days and platform == "instagram":
        window_date = calc_window_date(monitoring_window_days)
        if window_date:
            logger.info(
                f"[{source_id}] Filter: postingan setelah {window_date} "
                f"({monitoring_window_days} hari)"
            )
        else:
            logger.warning(
                f"[{source_id}] monitoring_window_days tidak valid: "
                f"'{monitoring_window_days}', window dimatikan"
            )

    logger.info(f"[{source_id}] Mencari postingan dari {source_account} (platform: {platform})")

    # Platform routing: pilih method scraper yang sesuai
    if platform == "facebook":
        fb_url = _fb_page_to_url(source_account)
        result = scraper.scrape_fb_posts(fb_url, limit=max_posts)
    else:
        profile_url = username_to_profile_url(source_account)
        result = scraper.scrape_profile_posts(
            profile_url, limit=max_posts, only_newer_than=window_date
        )

    api_status = result.get("api_status", "error")

    if api_status != "success":
        logger.error(
            f"[{source_id}] Gagal mengambil postingan: {result.get('error_message')}"
        )
        return [], api_status

    items = result["posts"]
    logger.info(f"[{source_id}] {len(items)} items dari Apify ({platform})")

    posts = []
    relevance_settings = source_row.get("_relevance_settings", {})
    for item in items:
        normalized = normalize_post_item(item, {
            "source_id": source_id,
            "source_account": source_account,
            "priority_level": priority_level,
            "relevance_settings": relevance_settings,
            "platform": platform,
        })
        if normalized:
            posts.append(normalized)

    logger.info(f"[{source_id}] {len(posts)} post valid setelah normalisasi")
    return posts, result.get("api_status", "success")


def normalize_post_item(item: dict, source_context: dict) -> dict:
    """Normalisasi item Apify (tahan variasi nama field). {} jika tak ada URL/shortcode."""
    post_url = extract_value_by_candidates(item, POST_URL_CANDIDATES)
    post_url = str(post_url).strip() if post_url else ""

    shortcode = extract_value_by_candidates(item, SHORTCODE_CANDIDATES)
    shortcode = str(shortcode).strip() if shortcode else ""

    if not post_url and shortcode:
        post_url = f"https://www.instagram.com/p/{shortcode}/"

    if not post_url and not shortcode:
        logger.debug("Post tanpa URL & shortcode, skip")
        return {}

    caption = extract_value_by_candidates(item, CAPTION_CANDIDATES)
    caption = str(caption).strip() if caption else ""

    timestamp = extract_value_by_candidates(item, TIMESTAMP_CANDIDATES)
    timestamp = str(timestamp).strip() if timestamp else ""

    comment_count = extract_value_by_candidates(item, COMMENT_COUNT_CANDIDATES)
    comment_count = str(comment_count) if comment_count is not None else ""

    like_count = extract_value_by_candidates(item, LIKE_COUNT_CANDIDATES)
    like_count = str(like_count) if like_count is not None else ""

    post_id_hash = _make_post_id_hash(
        post_url, shortcode, source_context, caption, timestamp
    )

    priority_level = source_context.get("priority_level", "3")
    relevance_settings = source_context.get("relevance_settings", {})

    # Hitung umur postingan (hari)
    post_age_days = _calc_post_age_days(timestamp)

    # Hitung comment count sebagai int
    comment_count_int = 0
    try:
        comment_count_int = int(float(comment_count)) if comment_count else 0
    except (ValueError, TypeError):
        pass

    # Multi-faktor scoring
    rel_result = score_post_relevance(
        caption, priority_level, comment_count_int, post_age_days,
        relevance_settings
    )

    return {
        "post_id_hash": post_id_hash,
        "source_id": source_context.get("source_id", ""),
        "source_account": source_context.get("source_account", ""),
        "post_url": post_url,
        "post_shortcode": shortcode,
        "caption_raw": caption,
        "post_created_at": timestamp,
        "discovered_at": now_utc(),
        "last_checked_at": now_utc(),
        "comment_count_last_seen": comment_count,
        "like_count_last_seen": like_count,
        "post_relevance": rel_result["label"],
        "relevance_score": str(rel_result["score"]),
        "relevance_reasons": json.dumps(rel_result["reasons"], ensure_ascii=False),
        "monitoring_status": "new",
        "raw_json": safe_json_dumps(item),
        "priority_level": priority_level,
    }


def _make_post_id_hash(post_url, shortcode, source_context, caption, timestamp) -> str:
    platform = source_context.get("platform", "instagram")
    if post_url:
        return make_sha256(f"{platform}|{post_url}")
    if shortcode:
        return make_sha256(f"{platform}|{shortcode}")
    source_account = source_context.get("source_account", "")
    return make_sha256(f"{platform}|{source_account}|{caption}|{timestamp}")


def _calc_post_age_days(timestamp_str: str) -> int:
    """Hitung umur postingan dalam hari dari string timestamp."""
    if not timestamp_str or not timestamp_str.strip():
        return 999  # unknown → dianggap lama
    try:
        # Coba parse ISO format
        ts = timestamp_str.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 999
