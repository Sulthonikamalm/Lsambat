"""
discover_posts.py — Stage 4: discovery postingan baru dari banyak akun sumber.
discover_posts.py — Stage 4 Post Discovery.

Discover postingan baru dari akun sumber yang terdaftar di source_registry.csv.
Supports round-robin multi-token via ApifyScraper.
Multi-faktor relevance scoring dengan breakdown transparan.
"""

import logging
import json
import re
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

from stage4_utils import (
    make_sha256,
    now_utc,
    extract_value_by_candidates,
    safe_json_dumps,
    load_csv_if_exists,
    safe_write_csv,
)
from post_queue import (
    load_post_queue,
    add_to_post_queue,
    save_post_queue,
)

logger = logging.getLogger("demo_monitor")

POST_COLUMNS = [
    "post_id_hash", "source_id", "source_account", "post_url",
    "post_shortcode", "caption_raw", "post_created_at", "discovered_at",
    "last_checked_at", "comment_count_last_seen", "like_count_last_seen",
    "post_relevance", "relevance_score", "relevance_reasons",
    "monitoring_status", "raw_json",
]

POST_URL_CANDIDATES = ["url", "postUrl", "permalink", "displayUrl"]
CAPTION_CANDIDATES = ["caption", "text", "description", "alt"]
TIMESTAMP_CANDIDATES = ["timestamp", "createdAt", "takenAt", "date"]
SHORTCODE_CANDIDATES = ["shortCode", "shortcode", "code"]
COMMENT_COUNT_CANDIDATES = ["commentsCount", "commentCount", "comments_count"]
LIKE_COUNT_CANDIDATES = ["likesCount", "likeCount", "likes_count"]

DEFAULT_KEYWORDS = [
    "banjir", "jalan rusak", "macet", "sampah", "pdam", "air mati",
    "lampu jalan", "pju", "drainase", "parkir", "trotoar", "pelayanan",
    "puskesmas", "pemkot", "dishub", "dlh", "bpbd", "wargaku",
    "keluhan", "lapor", "tolong", "rusak", "lubang", "aduan", "hotline",
    "jukir", "liar", "pembongkaran", "penertiban",
]



# ── Source registry ───────────────────────────────────────────


def load_source_registry(registry_path) -> pd.DataFrame:
    if not registry_path.exists():
        logger.error(f"source_registry.csv tidak ditemukan: {registry_path}")
        return pd.DataFrame()

    df = pd.read_csv(registry_path, dtype=str).fillna("")
    required_cols = {"source_id", "source_account", "platform", "status"}
    missing = required_cols - set(df.columns)
    if missing:
        logger.error(f"Kolom wajib tidak ada di source_registry.csv: {missing}")
        return pd.DataFrame()

    logger.info(f"Loaded {len(df)} akun dari source_registry.csv")
    return df


def get_active_sources(df_sources) -> pd.DataFrame:
    """Filter: status=active & platform=instagram; source_account kosong → invalid."""
    if df_sources.empty:
        return df_sources

    active = df_sources[
        (df_sources["status"].str.strip().str.lower() == "active")
        & (df_sources["platform"].str.strip().str.lower() == "instagram")
    ].copy()

    invalid_mask = active["source_account"].str.strip() == ""
    if invalid_mask.any():
        for _, row in active[invalid_mask].iterrows():
            logger.warning(
                f"[{row['source_id']}] invalid_source: source_account kosong"
            )
    active = active[~invalid_mask]

    logger.info(f"Active instagram sources: {len(active)}")
    return active


def _username_to_profile_url(source_account: str) -> str:
    username = source_account.strip().lstrip("@")
    return f"https://www.instagram.com/{username}/"


def calc_window_date(monitoring_window_days) -> str:
    """Hitung onlyPostsNewerThan (YYYY-MM-DD) dari monitoring_window_days."""
    try:
        days = int(str(monitoring_window_days).strip())
        if days <= 0:
            return None
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return cutoff.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ── Discovery per source ──────────────────────────────────────


def discover_posts_for_source(
    scraper, source_row, max_posts, use_window=True
) -> tuple:
    """
    Ambil postingan satu akun via Apify, normalisasi jadi list of dict.
    Return: (list_of_posts, api_status_string)
    """
    source_id = str(source_row.get("source_id", "")).strip()
    source_account = str(source_row.get("source_account", "")).strip()
    priority_level = str(source_row.get("priority_level", "3")).strip()
    monitoring_window_days = str(source_row.get("monitoring_window_days", "")).strip()

    window_date = None
    if use_window and monitoring_window_days:
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

    profile_url = _username_to_profile_url(source_account)
    logger.info(f"[{source_id}] Mencari postingan dari {source_account}")

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
    logger.info(f"[{source_id}] {len(items)} items dari Apify")

    posts = []
    relevance_settings = source_row.get("_relevance_settings", {})
    for item in items:
        normalized = normalize_post_item(item, {
            "source_id": source_id,
            "source_account": source_account,
            "priority_level": priority_level,
            "relevance_settings": relevance_settings,
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
    if post_url:
        return make_sha256(f"instagram|{post_url}")
    if shortcode:
        return make_sha256(f"instagram|{shortcode}")
    source_account = source_context.get("source_account", "")
    return make_sha256(f"instagram|{source_account}|{caption}|{timestamp}")


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


def score_post_relevance(
    caption: str, priority_level: str,
    comment_count: int, post_age_days: int,
    relevance_settings: dict = None,
) -> dict:
    """
    Skor relevansi multi-faktor. Return dict lengkap agar transparan.

    Faktor:
    1. Keyword match (0-40): berapa kata keluhan yang cocok di caption
    2. Prioritas akun (0-25): akun pemerintah vs komunitas
    3. Engagement/komentar (0-20): banyak komentar = banyak warga merespons
    4. Umur postingan (0-15): postingan baru lebih relevan

    Total: 0-100
    """
    if relevance_settings is None:
        relevance_settings = {}

    # Load config atau fallback
    keywords = relevance_settings.get("keywords", DEFAULT_KEYWORDS)
    weights = relevance_settings.get("weights", {})
    thresholds = relevance_settings.get("thresholds", {})

    kw_per_match = weights.get("keyword_per_match", 10)
    kw_max = weights.get("keyword_max", 40)
    p1_score = weights.get("priority_level_1", 25)
    p2_score = weights.get("priority_level_2", 15)
    p3_score = weights.get("priority_level_3", 5)
    eng_50 = weights.get("engagement_50_plus", 20)
    eng_20 = weights.get("engagement_20_plus", 15)
    eng_5 = weights.get("engagement_5_plus", 10)
    rec_7 = weights.get("recency_7_days", 15)
    rec_14 = weights.get("recency_14_days", 10)
    rec_30 = weights.get("recency_30_days", 5)
    th_high = thresholds.get("high", 60)
    th_medium = thresholds.get("medium", 30)

    score = 0
    reasons = []

    # Jika caption kosong
    if not caption or not caption.strip():
        reasons.append("caption tidak tersedia")
        # Tetap hitung faktor lain
    else:
        # Faktor 1: Keyword match
        caption_lower = caption.lower()
        matched = [kw for kw in keywords if kw in caption_lower]
        keyword_score = min(len(matched) * kw_per_match, kw_max)
        score += keyword_score
        if matched:
            kw_display = ", ".join(matched[:4])
            if len(matched) > 4:
                kw_display += f" (+{len(matched)-4} lagi)"
            reasons.append(f"kata keluhan: {kw_display} (+{keyword_score})")
        else:
            reasons.append("tidak ada kata keluhan (+0)")

    # Faktor 2: Prioritas akun
    try:
        pl = int(priority_level)
    except (ValueError, TypeError):
        pl = 3
    priority_map = {1: p1_score, 2: p2_score, 3: p3_score}
    priority_score = priority_map.get(pl, 0)
    score += priority_score
    reasons.append(f"prioritas akun: level {pl} (+{priority_score})")

    # Faktor 3: Engagement (jumlah komentar)
    if comment_count >= 50:
        engagement_score = eng_50
    elif comment_count >= 20:
        engagement_score = eng_20
    elif comment_count >= 5:
        engagement_score = eng_5
    else:
        engagement_score = 0
    score += engagement_score
    reasons.append(f"komentar: {comment_count} (+{engagement_score})")

    # Faktor 4: Recency (umur postingan)
    if post_age_days <= 7:
        recency_score = rec_7
    elif post_age_days <= 14:
        recency_score = rec_14
    elif post_age_days <= 30:
        recency_score = rec_30
    else:
        recency_score = 0
    score += recency_score
    if post_age_days < 999:
        reasons.append(f"umur: {post_age_days} hari (+{recency_score})")
    else:
        reasons.append(f"umur: tidak diketahui (+{recency_score})")

    # Tentukan label
    if score >= th_high:
        label = "high"
    elif score >= th_medium:
        label = "medium"
    else:
        label = "low"

    return {
        "score": score,
        "label": label,
        "reasons": reasons,
        "matched_keywords": matched if caption and caption.strip() else [],
    }


# ── Deteksi baru / update ─────────────────────────────────────


def detect_new_or_updated_posts(discovered_posts: list, existing_df, min_increase: int = 5) -> dict:
    if existing_df.empty:
        existing_hashes = set()
        existing_map = {}
    else:
        existing_hashes = set(existing_df["post_id_hash"].tolist())
        existing_map = {
            row["post_id_hash"]: row for _, row in existing_df.iterrows()
        }

    new_posts = []
    updated_posts = []
    comment_changed_posts = []

    for post in discovered_posts:
        pid = post["post_id_hash"]

        if pid not in existing_hashes:
            new_posts.append(post)
            continue

        old_row = existing_map.get(pid)
        if old_row is None:
            continue

        old_count = _parse_count(old_row.get("comment_count_last_seen", ""))
        new_count = _parse_count(post.get("comment_count_last_seen", ""))

        update_data = {"post_id_hash": pid, "last_checked_at": now_utc()}

        if new_count is not None:
            update_data["comment_count_last_seen"] = str(new_count)

        new_like = post.get("like_count_last_seen", "")
        if new_like:
            update_data["like_count_last_seen"] = new_like

        if (
            new_count is not None
            and old_count is not None
            and new_count > old_count
        ):
            increase = new_count - old_count
            if increase >= min_increase:
                comment_changed_posts.append(post)
                logger.info(
                    f"comment_count_changed {pid[:12]}: {old_count} -> {new_count} "
                    f"(+{increase}, threshold={min_increase})"
                )
            else:
                logger.debug(
                    f"comment_increase_below_threshold {pid[:12]}: "
                    f"+{increase} < {min_increase}, skipped"
                )

        elif (
            new_count is not None
            and old_count is not None
            and new_count < old_count
        ):
            logger.info(
                f"count_decreased_or_hidden {pid[:12]}: {old_count} -> {new_count}"
            )

        updated_posts.append(update_data)

    return {
        "new_posts": new_posts,
        "updated_posts": updated_posts,
        "comment_changed_posts": comment_changed_posts,
    }


def _parse_count(val):
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return None


# ── Persist ───────────────────────────────────────────────────


def save_raw_instagram_posts(new_posts, updated_posts, posts_csv_path):
    existing_df = load_csv_if_exists(posts_csv_path)

    if new_posts:
        df_new = pd.DataFrame(new_posts)
        df_new["monitoring_status"] = "new"
        df_new = df_new.reindex(columns=POST_COLUMNS)

        if existing_df.empty:
            existing_df = df_new
        else:
            existing_df = pd.concat([existing_df, df_new], ignore_index=True)

        logger.info(f"Saved {len(new_posts)} new posts to raw_instagram_posts.csv")

    if updated_posts and not existing_df.empty:
        for update in updated_posts:
            pid = update["post_id_hash"]
            mask = existing_df["post_id_hash"] == pid
            if mask.any():
                for key, val in update.items():
                    if key != "post_id_hash":
                        existing_df.loc[mask, key] = val
        logger.info(f"Updated {len(updated_posts)} existing posts")

    if not existing_df.empty:
        existing_df = existing_df.reindex(columns=POST_COLUMNS)
        safe_write_csv(existing_df, posts_csv_path, "raw_instagram_posts", logger)


def add_posts_to_queue(posts: list, queue_reason: str, queue_path) -> int:
    df_queue = load_post_queue(queue_path)

    added_count = 0
    for post in posts:
        old_len = len(df_queue)
        df_queue = add_to_post_queue(df_queue, post, queue_reason)
        if len(df_queue) > old_len:
            added_count += 1

    save_post_queue(df_queue, queue_path)
    logger.info(
        f"Added {added_count}/{len(posts)} posts to queue (reason={queue_reason})"
    )
    return added_count


# ── Orchestrator ──────────────────────────────────────────────


def run_post_discovery(scraper, settings, paths, stop_event=None) -> dict:
    """
    Jalankan discovery untuk semua akun aktif.

    Args:
        scraper: instance ApifyScraper
        settings: dict hasil load_settings()
        paths: dict hasil get_paths()
        stop_event: threading.Event, jika set() maka discovery berhenti
    """
    pd_settings = settings.get("post_discovery", {})
    max_posts = pd_settings.get("max_posts_per_source", 10)
    only_active = pd_settings.get("only_active_sources", True)
    use_window = pd_settings.get("use_monitoring_window", True)
    relevance_settings = settings.get("relevance", {})

    df_sources = load_source_registry(paths["source_registry"])
    if df_sources.empty:
        return {"error": "source_registry.csv tidak ditemukan atau kosong"}

    if only_active:
        df_sources = get_active_sources(df_sources)

    if df_sources.empty:
        return {"error": "Tidak ada akun aktif di source_registry.csv"}

    existing_posts_df = load_csv_if_exists(paths["raw_posts_csv"])

    all_discovered = []
    summary = {
        "total_sources": len(df_sources),
        "total_posts_discovered": 0,
        "total_new_posts": 0,
        "total_updated_posts": 0,
        "total_comment_changed": 0,
        "total_queued": 0,
        "new_posts": [],
        "stopped_early": False,
    }

    for _, source_row in df_sources.iterrows():
        # Cek apakah diminta berhenti
        if stop_event and stop_event.is_set():
            logger.info("Discovery dihentikan oleh pengguna")
            summary["stopped_early"] = True
            break

        # Inject relevance settings ke source_row
        source_row_dict = source_row.to_dict() if hasattr(source_row, 'to_dict') else dict(source_row)
        source_row_dict["_relevance_settings"] = relevance_settings

        posts, api_status = discover_posts_for_source(
            scraper, source_row_dict, max_posts, use_window=use_window
        )

        # Auto-stop jika semua token habis
        if api_status == "all_tokens_exhausted":
            logger.warning(
                "Semua token API habis. Menghentikan discovery akun tersisa."
            )
            summary["stopped_early"] = True
            break

        all_discovered.extend(posts)
        summary["total_posts_discovered"] += len(posts)

    if not all_discovered:
        logger.info("Tidak ada postingan ditemukan dari semua akun")
        return summary

    min_increase = pd_settings.get("min_comment_increase", 5)
    result = detect_new_or_updated_posts(all_discovered, existing_posts_df, min_increase=min_increase)
    new_posts = result["new_posts"]
    updated_posts = result["updated_posts"]
    comment_changed = result["comment_changed_posts"]

    summary["total_new_posts"] = len(new_posts)
    summary["total_updated_posts"] = len(updated_posts)
    summary["total_comment_changed"] = len(comment_changed)
    summary["new_posts"] = new_posts

    save_raw_instagram_posts(new_posts, updated_posts, paths["raw_posts_csv"])

    # Q2: Filter postingan LOW dari antrean (hemat API call)
    queue_only_relevant = pd_settings.get("queue_only_relevant", True)
    if new_posts:
        if queue_only_relevant:
            relevant_posts = [p for p in new_posts if p.get("post_relevance") in ("high", "medium")]
            skipped_low = len(new_posts) - len(relevant_posts)
            if skipped_low:
                logger.info(
                    f"Filter relevansi: {skipped_low} postingan LOW dilewati "
                    f"(hemat {skipped_low} API call)"
                )
                summary["skipped_low_relevance"] = skipped_low
        else:
            relevant_posts = new_posts

        summary["total_queued"] += add_posts_to_queue(
            relevant_posts, "new_post", paths["post_queue_csv"]
        )

    if comment_changed:
        if queue_only_relevant:
            relevant_changed = [p for p in comment_changed if p.get("post_relevance") in ("high", "medium")]
        else:
            relevant_changed = comment_changed
        summary["total_queued"] += add_posts_to_queue(
            relevant_changed, "comment_count_changed", paths["post_queue_csv"]
        )

    logger.info(
        f"Discovery selesai: {summary['total_posts_discovered']} ditemukan, "
        f"{summary['total_new_posts']} baru, "
        f"{summary['total_comment_changed']} komentar berubah"
    )

    return summary
