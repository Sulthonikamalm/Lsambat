"""
discovery/registry.py — Registry utilities.
"""

import logging
from datetime import datetime, timezone, timedelta
import pandas as pd

logger = logging.getLogger("demo_monitor")


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

    # debug: dipanggil tiap polling dashboard → kalau pakai info terminal banjir
    logger.debug(f"Loaded {len(df)} akun dari source_registry.csv")
    return df


def get_active_sources(df_sources) -> pd.DataFrame:
    """Filter: status=active & platform in (instagram, facebook); source_account kosong → invalid."""
    if df_sources.empty:
        return df_sources

    active = df_sources[
        (df_sources["status"].str.strip().str.lower() == "active")
        & (df_sources["platform"].str.strip().str.lower().isin(["instagram", "facebook"]))
    ].copy()

    invalid_mask = active["source_account"].str.strip() == ""
    if invalid_mask.any():
        for _, row in active[invalid_mask].iterrows():
            logger.warning(
                f"[{row['source_id']}] invalid_source: source_account kosong"
            )
    active = active[~invalid_mask]

    logger.debug(f"Active sources (instagram+facebook): {len(active)}")
    return active


def username_to_profile_url(source_account: str) -> str:
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
