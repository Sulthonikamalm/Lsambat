"""
routes/data_routes.py — API endpoints untuk data: comments, posts, download.
"""

import logging

from flask import Blueprint, jsonify, request, send_file

from stage4_utils import load_csv_if_exists
from process_queue import get_queue_rows, get_posts_rows

logger = logging.getLogger("demo_monitor")

data_bp = Blueprint("data", __name__)

_deps = {}


def init_data_routes(paths):
    _deps["paths"] = paths


@data_bp.route("/api/posts")
def api_posts():
    return jsonify(get_posts_rows(_deps["paths"]))


@data_bp.route("/api/queue")
def api_queue():
    return jsonify(get_queue_rows(_deps["paths"]))


@data_bp.route("/api/comments")
def api_comments():
    """Daftar komentar yang sudah di-scrape (dengan paginasi)."""
    df = load_csv_if_exists(_deps["paths"]["raw_comments_csv"])
    if df.empty:
        return jsonify({"comments": [], "total": 0, "page": 1, "per_page": 50})

    total_rows = len(df)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = min(per_page, 200)

    df = df.sort_values("scraped_at", ascending=False)
    offset = (page - 1) * per_page
    df_page = df.iloc[offset:offset + per_page]

    return jsonify({
        "comments": df_page.fillna("").to_dict(orient="records"),
        "total": total_rows,
        "page": page,
        "per_page": per_page,
    })


@data_bp.route("/api/download")
def api_download():
    """Download semua komentar sebagai CSV."""
    csv_path = _deps["paths"]["raw_comments_csv"]
    if not csv_path.exists():
        return jsonify({"error": "Belum ada data komentar"}), 404
    return send_file(
        csv_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="surabayasambat_komentar.csv",
    )
