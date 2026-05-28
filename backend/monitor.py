"""
monitor.py — Core logic: Hybrid comment monitoring.

State machine modes:
  idle → collected → monitoring → stopped

Pendekatan Hybrid:
  - Kumpulkan Data Awal: SEMUA komentar masuk CSV + seen_comments.json
  - Live Monitoring: hanya komentar BARU masuk CSV (dedup via seen_comments.json)
"""

import hashlib
import json
import logging
import csv
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("demo_monitor")

# Mode constants
MODE_IDLE = "idle"
MODE_COLLECTING = "collecting"
MODE_COLLECTED = "collected"
MODE_MONITORING = "monitoring"
MODE_STOPPED = "stopped"

# CSV columns untuk new_comments.csv
CSV_COLUMNS = [
    "comment_id_hash",
    "comment_id",
    "post_url",
    "username",
    "comment_text",
    "comment_timestamp",
    "detected_at",
    "cycle_number",
    "source",
]

# CSV columns untuk monitoring_log.csv
LOG_COLUMNS = [
    "cycle_number",
    "mode",
    "started_at",
    "finished_at",
    "comments_scraped",
    "new_found",
    "duplicates_prevented",
    "status",
    "error_message",
]


def _now_iso() -> str:
    """Timestamp otomatis dari sistem, UTC."""
    return datetime.now(timezone.utc).isoformat()


def _make_hash(comment: dict) -> str:
    """Buat SHA-256 hash unik dari comment ID atau fallback."""
    comment_id = comment.get("id") or comment.get("cid") or ""
    if comment_id:
        raw = f"instagram|{comment_id}"
    else:
        # Fallback: hash dari text + username
        text = comment.get("text", "")
        owner = comment.get("ownerUsername", "") or comment.get("username", "")
        raw = f"instagram|{owner}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _extract_comment_data(comment: dict, post_url: str) -> dict:
    """Extract field-field penting dari raw Apify comment."""
    comment_id = (
        comment.get("id")
        or comment.get("cid")
        or comment.get("commentId")
        or ""
    )

    username = comment.get("ownerUsername") or comment.get("username") or ""
    if not username and isinstance(comment.get("owner"), dict):
        username = comment["owner"].get("username", "")

    text = comment.get("text") or comment.get("body") or ""

    timestamp = (
        comment.get("timestamp")
        or comment.get("createdAt")
        or comment.get("created_at")
        or ""
    )

    comment_post_url = comment.get("postUrl") or post_url

    return {
        "comment_id": str(comment_id),
        "username": str(username),
        "comment_text": str(text),
        "comment_timestamp": str(timestamp),
        "post_url": str(comment_post_url),
    }


class CommentMonitor:
    """
    State machine untuk hybrid comment monitoring.

    Lifecycle:
        idle → collect_initial() → collected
        collected → start_monitoring() → monitoring
        monitoring → run_monitoring_cycle() → monitoring
        monitoring → stop() → stopped
        any → reset() → idle
    """

    def __init__(self, scraper, post_url: str, paths: dict):
        self.scraper = scraper
        self.post_url = post_url
        self.paths = paths

        # State
        self.mode = MODE_IDLE
        self.seen_ids = {}
        self.collected_at = None
        self.monitoring_started_at = None
        self.cycle_number = 0

        # Counters
        self.total_collected = 0
        self.total_new_comments = 0
        self.total_duplicates_prevented = 0
        self.total_api_calls = 0

        # Ensure data dir exists
        self.paths["data_dir"].mkdir(parents=True, exist_ok=True)

        # Load existing state if available
        self._load_state()

    # ──────────────────────────────────────────────────────────
    # STEP 1: KUMPULKAN DATA AWAL
    # ──────────────────────────────────────────────────────────

    def collect_initial(self, results_limit: int = 1000) -> dict:
        """
        Scrape semua komentar yang ada → SEMUA masuk CSV + seen_comments.json.
        """
        self.mode = MODE_COLLECTING

        result = self.scraper.scrape_comments(self.post_url, limit=results_limit)
        self.total_api_calls += 1

        if result["api_status"] != "success":
            self.mode = MODE_IDLE
            return {
                "success": False,
                "error": result["error_message"],
                "api_status": result["api_status"],
            }

        comments = result["comments"]
        new_comments = []

        for comment in comments:
            hash_id = _make_hash(comment)
            if hash_id not in self.seen_ids:
                data = _extract_comment_data(comment, self.post_url)
                data["comment_id_hash"] = hash_id
                data["detected_at"] = _now_iso()
                data["cycle_number"] = 0  # initial collection = cycle 0
                data["source"] = "initial"
                new_comments.append(data)

                self.seen_ids[hash_id] = {
                    "first_seen": _now_iso(),
                    "source": "initial",
                }

        # Tulis SEMUA ke CSV
        if new_comments:
            self._append_csv(new_comments)

        self.collected_at = _now_iso()
        self.total_collected = len(new_comments)
        self.total_new_comments += len(new_comments)
        self.mode = MODE_COLLECTED

        self._save_state()
        self._log_cycle(
            cycle_number=0,
            mode="initial",
            comments_scraped=len(comments),
            new_found=len(new_comments),
            duplicates=0,
            status="collected",
        )

        return {
            "success": True,
            "collected_count": len(new_comments),
            "total_scraped": len(comments),
            "new_comments": new_comments,
            "token_used": result["token_index"],
            "mode": self.mode,
        }

    # ──────────────────────────────────────────────────────────
    # STEP 2: MULAI MONITORING
    # ──────────────────────────────────────────────────────────

    def start_monitoring(self) -> dict:
        """Aktifkan mode monitoring. Komentar baru masuk CSV."""
        if self.mode != MODE_COLLECTED:
            return {
                "success": False,
                "error": f"Start monitoring hanya dari mode collected, "
                         f"saat ini: {self.mode}",
            }

        self.mode = MODE_MONITORING
        self.monitoring_started_at = _now_iso()
        self.cycle_number = 0

        self._save_state()
        return {
            "success": True,
            "mode": self.mode,
            "monitoring_started_at": self.monitoring_started_at,
            "total_collected": self.total_collected,
        }

    def run_monitoring_cycle(self, results_limit: int = 50) -> dict:
        """
        Siklus monitoring: komentar baru MASUK CSV (dedup via seen_ids).
        """
        if self.mode != MODE_MONITORING:
            return {
                "success": False,
                "error": f"Monitoring hanya di mode monitoring, "
                         f"saat ini: {self.mode}",
            }

        self.cycle_number += 1
        cycle = self.cycle_number

        result = self.scraper.scrape_comments(self.post_url, limit=results_limit)
        self.total_api_calls += 1

        if result["api_status"] != "success":
            self._log_cycle(
                cycle_number=cycle,
                mode="monitoring",
                comments_scraped=0,
                new_found=0,
                duplicates=0,
                status="error",
                error_message=result["error_message"],
            )
            return {
                "success": False,
                "cycle": cycle,
                "error": result["error_message"],
                "api_status": result["api_status"],
            }

        comments = result["comments"]
        new_comments = []
        duplicates = 0

        for comment in comments:
            hash_id = _make_hash(comment)
            if hash_id in self.seen_ids:
                duplicates += 1
            else:
                data = _extract_comment_data(comment, self.post_url)
                data["comment_id_hash"] = hash_id
                data["detected_at"] = _now_iso()
                data["cycle_number"] = cycle
                data["source"] = "monitoring"
                new_comments.append(data)

                self.seen_ids[hash_id] = {
                    "first_seen": _now_iso(),
                    "source": "monitoring",
                }

        self.total_new_comments += len(new_comments)
        self.total_duplicates_prevented += duplicates

        if new_comments:
            self._append_csv(new_comments)

        self._save_state()
        self._log_cycle(
            cycle_number=cycle,
            mode="monitoring",
            comments_scraped=len(comments),
            new_found=len(new_comments),
            duplicates=duplicates,
            status="success",
        )

        return {
            "success": True,
            "cycle": cycle,
            "comments_scraped": len(comments),
            "new_found": len(new_comments),
            "duplicates_prevented": duplicates,
            "new_comments": new_comments,
            "token_used": result["token_index"],
            "mode": self.mode,
        }

    # ──────────────────────────────────────────────────────────
    # RUN ONCE (shortcut — hanya di mode monitoring)
    # ──────────────────────────────────────────────────────────

    def run_once(self, results_limit: int = 50) -> dict:
        """Run Once: jalankan satu siklus monitoring."""
        if self.mode == MODE_MONITORING:
            return self.run_monitoring_cycle(results_limit)
        else:
            return {
                "success": False,
                "error": f"Run Once tidak tersedia di mode {self.mode}. "
                         f"Kumpulkan data awal dan mulai monitoring terlebih dahulu.",
            }

    # ──────────────────────────────────────────────────────────
    # STOP & RESET
    # ──────────────────────────────────────────────────────────

    def stop(self) -> dict:
        """Stop monitoring."""
        prev_mode = self.mode
        self.mode = MODE_STOPPED
        self._save_state()
        return {
            "success": True,
            "mode": self.mode,
            "previous_mode": prev_mode,
        }

    def reset(self) -> dict:
        """Reset semua state ke awal."""
        self.mode = MODE_IDLE
        self.seen_ids = {}
        self.collected_at = None
        self.monitoring_started_at = None
        self.cycle_number = 0
        self.total_collected = 0
        self.total_new_comments = 0
        self.total_duplicates_prevented = 0

        # Hapus file data
        for key in ("seen_comments", "new_comments_csv", "monitoring_log_csv"):
            p = self.paths[key]
            if p.exists():
                p.unlink()

        self._save_state()
        return {"success": True, "mode": self.mode}

    # ──────────────────────────────────────────────────────────
    # ANALYTICS & STATUS
    # ──────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return current system status."""
        return {
            "mode": self.mode,
            "post_url": self.post_url,
            "collected_at": self.collected_at,
            "monitoring_started_at": self.monitoring_started_at,
            "total_seen": len(self.seen_ids),
            "total_collected": self.total_collected,
            "total_new_comments": self.total_new_comments,
            "total_duplicates_prevented": self.total_duplicates_prevented,
            "total_api_calls": self.total_api_calls,
            "cycle_number": self.cycle_number,
            "active_tokens": len(self.scraper.tokens),
        }

    def get_new_comments(self) -> list:
        """Read CSV dan return sebagai list of dict."""
        csv_path = self.paths["new_comments_csv"]
        if not csv_path.exists():
            return []
        rows = []
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows

    # ──────────────────────────────────────────────────────────
    # INTERNAL: State persistence
    # ──────────────────────────────────────────────────────────

    def _save_state(self):
        """Simpan state ke seen_comments.json."""
        state = {
            "post_url": self.post_url,
            "mode": self.mode,
            "collected_at": self.collected_at,
            "monitoring_started_at": self.monitoring_started_at,
            "total_seen": len(self.seen_ids),
            "total_collected": self.total_collected,
            "total_new_comments": self.total_new_comments,
            "cycle_number": self.cycle_number,
            "total_api_calls": self.total_api_calls,
            "total_duplicates_prevented": self.total_duplicates_prevented,
            "seen_ids": self.seen_ids,
        }
        path = self.paths["seen_comments"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def _load_state(self):
        """Load state dari seen_comments.json jika ada."""
        path = self.paths["seen_comments"]
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)

            self.seen_ids = state.get("seen_ids", {})
            self.mode = state.get("mode", MODE_IDLE)
            self.collected_at = state.get("collected_at")
            self.monitoring_started_at = state.get("monitoring_started_at")
            self.total_collected = state.get("total_collected", 0)
            self.total_new_comments = state.get("total_new_comments", 0)
            self.cycle_number = state.get("cycle_number", 0)
            self.total_api_calls = state.get("total_api_calls", 0)
            self.total_duplicates_prevented = state.get(
                "total_duplicates_prevented", 0
            )

            logger.info(
                f"[Monitor] State loaded: mode={self.mode}, "
                f"seen={len(self.seen_ids)}"
            )
        except Exception as e:
            logger.warning(f"[Monitor] Gagal load state: {e}")

    def _append_csv(self, comments: list):
        """Append komentar ke CSV."""
        csv_path = self.paths["new_comments_csv"]
        file_exists = csv_path.exists() and csv_path.stat().st_size > 0

        with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            if not file_exists:
                writer.writeheader()
            for comment in comments:
                row = {col: comment.get(col, "") for col in CSV_COLUMNS}
                writer.writerow(row)

        logger.info(
            f"[Monitor] {len(comments)} komentar disimpan ke CSV"
        )

    def _log_cycle(
        self, cycle_number, mode, comments_scraped,
        new_found, duplicates, status, error_message="",
    ):
        """Append log siklus ke monitoring_log.csv."""
        csv_path = self.paths["monitoring_log_csv"]
        file_exists = csv_path.exists() and csv_path.stat().st_size > 0

        row = {
            "cycle_number": cycle_number,
            "mode": mode,
            "started_at": _now_iso(),
            "finished_at": _now_iso(),
            "comments_scraped": comments_scraped,
            "new_found": new_found,
            "duplicates_prevented": duplicates,
            "status": status,
            "error_message": error_message,
        }

        with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
