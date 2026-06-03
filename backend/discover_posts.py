"""
discover_posts.py — Stage 4 Post Discovery (Orchestrator).

Discover postingan baru dari akun sumber yang terdaftar di source_registry.csv.
Supports round-robin multi-token via ApifyScraper.
Multi-faktor relevance scoring dengan breakdown transparan.

Modul ini telah dipecah ke dalam package `discovery/`:
- discovery.registry (load akun sumber)
- discovery.fetcher (ambil dari Apify & normalisasi)
- discovery.tracking (deteksi update & simpan queue)
"""

import logging

from stage4_utils import load_csv_if_exists
from discovery.registry import load_source_registry, get_active_sources
from discovery.fetcher import discover_posts_for_source
from discovery.tracking import (
    detect_new_or_updated_posts,
    save_raw_instagram_posts,
    add_posts_to_queue,
)

logger = logging.getLogger("demo_monitor")

# Backward compatibility imports for routes/services that still expect them here
from discovery.registry import load_source_registry, get_active_sources


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
