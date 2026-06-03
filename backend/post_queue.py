"""
post_queue.py — Manajemen antrean postingan untuk Stage 4 (porting dari
surabayasambat_stage1_apify/src/post_queue.py, gaya backend demo).

Antrean disimpan di data/post_queue.csv.
"""

import logging
import uuid

import pandas as pd

from stage4_utils import now_utc, load_csv_if_exists, safe_write_csv

logger = logging.getLogger("demo_monitor")

QUEUE_COLUMNS = [
    "queue_id", "post_id_hash", "source_id", "source_account",
    "post_url", "queue_reason", "priority_level", "scheduled_at",
    "last_run_at", "status", "error_message",
]

VALID_QUEUE_REASONS = {
    "new_post",
    "comment_count_changed",
    "scheduled_recheck",
    "manual_selected",
    "development_test",
}

VALID_STATUSES = {
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
}


def load_post_queue(queue_path) -> pd.DataFrame:
    df = load_csv_if_exists(queue_path)
    if df.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)
    return df


def save_post_queue(df, queue_path):
    df = df.reindex(columns=QUEUE_COLUMNS)
    safe_write_csv(df, queue_path, "post_queue", logger)


def queue_exists(df_queue, post_id_hash, queue_reason, status_filter=None) -> bool:
    if df_queue.empty:
        return False

    mask = (
        (df_queue["post_id_hash"] == post_id_hash)
        & (df_queue["queue_reason"] == queue_reason)
    )

    if status_filter is not None:
        if isinstance(status_filter, str):
            status_filter = [status_filter]
        mask = mask & (df_queue["status"].isin(status_filter))

    return bool(mask.any())


def add_to_post_queue(df_queue, post: dict, queue_reason: str) -> pd.DataFrame:
    if queue_reason not in VALID_QUEUE_REASONS:
        logger.warning(f"Invalid queue_reason: {queue_reason}, skipping")
        return df_queue

    post_id_hash = post.get("post_id_hash", "")

    # Anti-duplikat: jangan masukkan queue ganda untuk (post_id_hash, queue_reason)
    if queue_reason == "new_post":
        if queue_exists(
            df_queue, post_id_hash, "new_post",
            status_filter=["pending", "running", "completed"],
        ):
            logger.debug(f"Queue exists for {post_id_hash[:12]} new_post, skip")
            return df_queue

    if queue_reason == "comment_count_changed":
        if queue_exists(
            df_queue, post_id_hash, "comment_count_changed",
            status_filter=["pending", "running"],
        ):
            logger.debug(
                f"Pending queue exists for {post_id_hash[:12]} "
                f"comment_count_changed, skip"
            )
            return df_queue

    new_entry = {
        "queue_id": f"Q_{uuid.uuid4().hex[:12]}",
        "post_id_hash": post_id_hash,
        "source_id": post.get("source_id", ""),
        "source_account": post.get("source_account", ""),
        "post_url": post.get("post_url", ""),
        "queue_reason": queue_reason,
        "priority_level": post.get("priority_level", ""),
        "scheduled_at": now_utc(),
        "last_run_at": "",
        "status": "pending",
        "error_message": "",
    }

    new_row = pd.DataFrame([new_entry])
    df_queue = pd.concat([df_queue, new_row], ignore_index=True)
    logger.info(f"Queued: {post.get('post_url', '')} reason={queue_reason}")
    return df_queue


def get_pending_queue(df_queue) -> pd.DataFrame:
    if df_queue.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)
    return df_queue[df_queue["status"] == "pending"].copy()


def update_queue_status(df_queue, queue_id, status, error_message=None) -> pd.DataFrame:
    if status not in VALID_STATUSES:
        logger.warning(f"Invalid status: {status}")
        return df_queue

    mask = df_queue["queue_id"] == queue_id
    if not mask.any():
        logger.warning(f"Queue ID not found: {queue_id}")
        return df_queue

    df_queue.loc[mask, "status"] = status
    df_queue.loc[mask, "last_run_at"] = now_utc()

    if error_message is not None:
        df_queue.loc[mask, "error_message"] = str(error_message)[:500]

    return df_queue
