"""
stage4_utils.py — Helper bersama untuk modul Stage 4
(discover_posts, post_queue, process_queue).

Ringan, hanya stdlib + pandas. Tidak mengubah modul monitoring 1-URL.
"""

import re
import hashlib
import json
from datetime import datetime, timezone

import pandas as pd

_SHORTCODE_RE = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value):
    """Parse timestamp ke datetime tz-aware (UTC). Return None jika gagal.

    Tahan beragam format Apify: ISO 8601 (dengan/ tanpa 'Z' atau milidetik) dan
    Unix epoch (detik) dalam bentuk int/float/str angka.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Unix epoch (mis. "1716249600" atau 1716249600)
    try:
        if s.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    # ISO 8601
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def make_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def extract_value_by_candidates(item: dict, candidate_fields: list, default=None):
    """Ambil nilai pertama yang ada dari beberapa kandidat nama field."""
    for field in candidate_fields:
        if field in item and item[field] is not None:
            return item[field]
    return default


def safe_json_dumps(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def load_csv_if_exists(path) -> pd.DataFrame:
    """Load CSV sebagai DataFrame string; return kosong jika belum ada."""
    if path.exists() and path.stat().st_size > 0:
        return pd.read_csv(path, dtype=str).fillna("")
    return pd.DataFrame()


def normalize_url(url: str) -> str:
    """Normalisasi URL Instagram: buang query, pastikan trailing slash."""
    if not url or not str(url).strip():
        return ""
    url = str(url).split("?")[0].strip().rstrip("/") + "/"
    url = url.replace("http://", "https://")
    if "www.instagram.com" not in url and "instagram.com" in url:
        url = url.replace("instagram.com", "www.instagram.com")
    return url


def url_shortcode(url: str) -> str:
    """Ambil shortcode dari URL /p/, /reel/, /reels/, /tv/ (tahan beda format)."""
    match = _SHORTCODE_RE.search(str(url))
    return match.group(1) if match else ""


def safe_write_csv(df: pd.DataFrame, path, label: str, logger):
    """Tulis CSV dengan retry jika file sedang dibuka program lain."""
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            df.to_csv(path, index=False, encoding="utf-8-sig")
            logger.info(f"Saved {label} -> {path}")
            return
        except PermissionError:
            if attempt < 2:
                logger.warning(
                    f"File locked: {path} — retry dalam 3 detik... "
                    f"(attempt {attempt + 1}/3)"
                )
                time.sleep(3)
            else:
                alt_path = path.with_stem(path.stem + "_backup")
                df.to_csv(alt_path, index=False, encoding="utf-8-sig")
                logger.error(
                    f"File locked setelah 3x retry. Data disimpan ke: {alt_path}"
                )
