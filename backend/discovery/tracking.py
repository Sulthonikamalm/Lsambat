"""
discovery/tracking.py — Logic to track changes, save CSVs, and queue posts.
"""

import logging
import pandas as pd

from stage4_utils import load_csv_if_exists, safe_write_csv, now_utc
from post_queue import load_post_queue, add_to_post_queue, save_post_queue

logger = logging.getLogger("demo_monitor")

POST_COLUMNS = [
    "post_id_hash", "source_id", "source_account", "post_url",
    "post_shortcode", "caption_raw", "post_created_at", "discovered_at",
    "last_checked_at", "comment_count_last_seen", "like_count_last_seen",
    "post_relevance", "relevance_score", "relevance_reasons",
    "monitoring_status", "raw_json",
]


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

        new_like = post.get("like_count_last_seen", "")
        if new_like:
            update_data["like_count_last_seen"] = new_like

        is_comment_changed = False
        if (
            new_count is not None
            and old_count is not None
            and new_count > old_count
        ):
            increase = new_count - old_count
            if increase >= min_increase:
                is_comment_changed = True
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

        # #3: JANGAN majukan comment_count_last_seen untuk post yang akan di-queue
        # ulang (comment_changed). Count baru di-commit HANYA setelah komentar
        # berhasil di-scrape (write-back di process_queue). Jika scrape gagal,
        # count tetap lama → kenaikan terdeteksi lagi pada run berikutnya (retry),
        # sehingga komentar tidak hilang. Untuk post yang TIDAK berubah/komentar
        # turun, count aman langsung diperbarui.
        if new_count is not None and not is_comment_changed:
            update_data["comment_count_last_seen"] = str(new_count)

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
