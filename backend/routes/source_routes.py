"""
routes/source_routes.py — API endpoints untuk manajemen akun sumber.
"""

import re
import logging

import pandas as pd
from flask import Blueprint, jsonify, request

from discover_posts import load_source_registry, get_active_sources
from stage4_utils import safe_write_csv

logger = logging.getLogger("demo_monitor")

source_bp = Blueprint("source", __name__)

_deps = {}


def init_source_routes(paths, socketio):
    _deps["paths"] = paths
    _deps["socketio"] = socketio


def _emit_log(message, level="info"):
    from datetime import datetime, timezone
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "level": level,
    }
    _deps["socketio"].emit("log", entry)
    logger.info(f"[Dashboard] {message}")


@source_bp.route("/api/sources")
def api_sources():
    """Daftar semua akun sumber."""
    df = load_source_registry(_deps["paths"]["source_registry"])
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


@source_bp.route("/api/sources", methods=["POST"])
def api_add_source():
    """Tambah akun sumber baru. User bisa paste link profil IG atau halaman FB."""
    data = request.get_json()
    profile_url = data.get("profile_url", "").strip()
    priority = str(data.get("priority_level", "2")).strip()
    window_days = str(data.get("monitoring_window_days", "30")).strip()

    if not profile_url:
        return jsonify({"success": False, "error": "Link profil harus diisi"}), 400

    # Auto-detect platform dari URL
    platform, account_name = _detect_platform_and_extract(profile_url)

    if not account_name:
        return jsonify({
            "success": False,
            "error": "Link tidak valid. Gunakan format Instagram atau Facebook.",
        }), 400

    registry_path = _deps["paths"]["source_registry"]
    df = pd.DataFrame()
    if registry_path.exists():
        df = pd.read_csv(registry_path, dtype=str).fillna("")

    # Cek duplikat (perbandingan tanpa @)
    check_name = account_name.lstrip("@")
    if not df.empty:
        existing_accounts = df["source_account"].str.lstrip("@").str.lower()
        if check_name.lower() in existing_accounts.values:
            return jsonify({
                "success": False,
                "error": f"Akun {account_name} sudah ada di daftar",
            }), 409

    existing_ids = set(df["source_id"].tolist()) if not df.empty else set()
    new_num = 1
    while f"SRC{new_num:03d}" in existing_ids:
        new_num += 1
    new_id = f"SRC{new_num:03d}"

    # Format: IG pakai @username, FB pakai nama halaman tanpa @
    display_account = f"@{check_name}" if platform == "instagram" else check_name

    new_row = pd.DataFrame([{
        "source_id": new_id,
        "source_account": display_account,
        "platform": platform,
        "priority_level": priority,
        "monitoring_window_days": window_days,
        "status": "active",
        "notes": f"Ditambahkan via dashboard ({platform})",
    }])

    df = new_row if df.empty else pd.concat([df, new_row], ignore_index=True)
    safe_write_csv(df, registry_path, f"Tambah akun {display_account}", logger)

    platform_label = "Instagram" if platform == "instagram" else "Facebook"
    _emit_log(
        f"✅ Akun {display_account} ({platform_label}) ditambahkan ke daftar pantauan.",
        "success",
    )
    _deps["socketio"].emit("source_added", {
        "source_account": display_account, "source_id": new_id, "platform": platform
    })

    return jsonify({
        "success": True, "source_id": new_id,
        "username": check_name, "platform": platform,
    })


@source_bp.route("/api/sources/<source_id>", methods=["DELETE"])
def api_delete_source(source_id):
    """Hapus akun sumber berdasarkan source_id."""
    registry_path = _deps["paths"]["source_registry"]
    if not registry_path.exists():
        return jsonify({"success": False, "error": "Data tidak ditemukan"}), 404

    df = pd.read_csv(registry_path, dtype=str).fillna("")

    mask = df["source_id"] == source_id
    if not mask.any():
        return jsonify({"success": False, "error": f"Akun {source_id} tidak ditemukan"}), 404

    account_name = df.loc[mask, "source_account"].values[0]
    df = df[~mask]
    safe_write_csv(df, registry_path, f"Hapus akun {account_name}", logger)

    _emit_log(f"🗑️ Akun {account_name} dihapus dari daftar pantauan.", "warning")
    _deps["socketio"].emit("source_deleted", {"source_id": source_id})

    return jsonify({"success": True})


def _detect_platform_and_extract(url_or_input: str) -> tuple:
    """
    Auto-detect platform dari URL/input user.
    Return: (platform_str, account_name) atau ("", "") jika tidak valid.
    """
    cleaned = url_or_input.strip().rstrip("/")

    # Deteksi Facebook
    if "facebook.com" in cleaned or "fb.com" in cleaned:
        match = re.search(r"facebook\.com/([a-zA-Z0-9_.]+)", cleaned)
        if match:
            page_name = match.group(1)
            if page_name in ("profile.php", "pages", "groups", "watch", "events"):
                return ("", "")
            return ("facebook", page_name)
        return ("", "")

    # Deteksi Instagram (URL atau username langsung)
    username = _extract_ig_username(cleaned)
    if username:
        return ("instagram", username)

    return ("", "")


def _extract_ig_username(url_or_username: str) -> str:
    """Extract username dari URL Instagram atau username langsung."""
    url_or_username = url_or_username.strip().rstrip("/")

    if url_or_username.startswith("@"):
        return url_or_username.lstrip("@")

    match = re.search(r"instagram\.com/([a-zA-Z0-9_.]+)", url_or_username)
    if match:
        username = match.group(1)
        if username in ("p", "reel", "explore", "stories", "accounts", "tv"):
            return ""
        return username

    if re.match(r"^[a-zA-Z0-9_.]+$", url_or_username):
        return url_or_username

    return ""
