"""
scraper.py — Apify REST API Wrapper untuk SurabayaSambat v2.

Fitur:
- Round-robin token rotation
- Token failover otomatis (402/403 → coba token lain)
- Usage tracking per session (persist ke JSON)
- Cost estimation per API call
"""

import requests
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("demo_monitor")

    
class ApifyScraper:
    """Wrapper Apify REST API v2 dengan token failover dan usage tracking."""

    SYNC_ENDPOINT = (
        "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
    )

    def __init__(self, tokens: list, actor_id: str, timeout: int = 300,
                 cost_per_call: float = 0.032, usage_log_path: str = None):
        self.tokens = tokens
        self.actor_id = actor_id
        self.timeout = timeout
        self.cost_per_call = cost_per_call
        self._call_count = 0
        self.total_api_calls = 0
        self.exhausted_tokens = set()  # token indices yang sudah 402/403

        # Usage tracking
        self._usage_log_path = Path(usage_log_path) if usage_log_path else None
        self._usage_log = self._load_usage_log()

    # ── Usage Tracking ────────────────────────────────────────

    def _load_usage_log(self) -> list:
        """Load usage log dari file JSON."""
        if self._usage_log_path and self._usage_log_path.exists():
            try:
                with open(self._usage_log_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return []

    def _save_usage_log(self):
        """Persist usage log ke file JSON."""
        if not self._usage_log_path:
            return
        try:
            self._usage_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._usage_log_path, "w", encoding="utf-8") as f:
                json.dump(self._usage_log, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.warning(f"[Scraper] Gagal simpan usage log: {e}")

    def _record_usage(self, method: str, target_url: str, token_index: int,
                      status_code: int, result_count: int, api_status: str):
        """Catat satu API call ke usage log."""
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": method,
            "target_url": target_url,
            "token_index": token_index,
            "status_code": status_code,
            "result_count": result_count,
            "api_status": api_status,
            "estimated_cost": self.cost_per_call if status_code in (200, 201) else 0,
        }
        self._usage_log.append(entry)
        self._save_usage_log()

    def get_usage_summary(self) -> dict:
        """Ringkasan usage: total, hari ini, minggu ini, biaya."""
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")

        # Hitung awal minggu (Senin)
        weekday = now.weekday()  # 0=Monday
        week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        week_start = week_start - timedelta(days=weekday)
        week_start_str = week_start.strftime("%Y-%m-%dT%H:%M:%SZ")

        total_calls = len(self._usage_log)
        total_cost = sum(e.get("estimated_cost", 0) for e in self._usage_log)

        today_entries = [e for e in self._usage_log if e["timestamp"][:10] == today_str]
        today_calls = len(today_entries)
        today_cost = sum(e.get("estimated_cost", 0) for e in today_entries)

        week_entries = [e for e in self._usage_log if e["timestamp"] >= week_start_str]
        week_calls = len(week_entries)
        week_cost = sum(e.get("estimated_cost", 0) for e in week_entries)

        # Token status
        token_status = []
        for i in range(len(self.tokens)):
            token_status.append({
                "index": i + 1,
                "status": "exhausted" if i in self.exhausted_tokens else "active",
            })

        return {
            "total_calls": total_calls,
            "total_cost": round(total_cost, 4),
            "today_calls": today_calls,
            "today_cost": round(today_cost, 4),
            "week_calls": week_calls,
            "week_cost": round(week_cost, 4),
            "cost_per_call": self.cost_per_call,
            "token_status": token_status,
            "tokens_available": len(self.tokens) - len(self.exhausted_tokens),
            "tokens_total": len(self.tokens),
        }

    def is_any_token_available(self) -> bool:
        """Cek apakah masih ada token yang belum exhausted."""
        return len(self.exhausted_tokens) < len(self.tokens)

    def reset_exhausted_tokens(self):
        """Reset semua exhausted tokens (mis. saat hari/bulan baru)."""
        self.exhausted_tokens.clear()
        logger.info("[Scraper] Token quota di-reset")

    # ── Token Management ──────────────────────────────────────

    def _next_token(self) -> str:
        """Round-robin pemilihan token, skip yang exhausted."""
        for _ in range(len(self.tokens)):
            self._call_count += 1
            idx = (self._call_count - 1) % len(self.tokens)
            if idx not in self.exhausted_tokens:
                return self.tokens[idx]
        # Semua exhausted — return apapun (akan gagal)
        self._call_count += 1
        idx = (self._call_count - 1) % len(self.tokens)
        return self.tokens[idx]

    @staticmethod
    def _actor_id_to_url(actor_id: str) -> str:
        return actor_id.replace("/", "~")

    # ── Scrape Comments (dengan Token Failover) ───────────────

    def scrape_comments(self, post_url: str, limit: int = 50) -> dict:
        """
        Scrape komentar dari satu post URL.
        Token failover: jika 402/403, otomatis coba token berikutnya.

        Returns:
            dict dengan keys:
                - "comments": list of comment dicts
                - "token_index": token mana yang dipakai (1-based)
                - "api_status": "success" / "error" / "all_tokens_exhausted"
                - "error_message": string jika error
                - "scraped_at": timestamp
        """
        if not self.is_any_token_available():
            return {
                "comments": [],
                "token_index": 0,
                "api_status": "all_tokens_exhausted",
                "error_message": "Semua token API sudah mencapai batas pemakaian bulan ini.",
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

        actor_input = {
            "directUrls": [post_url],
            "resultsType": "comments",
            "resultsLimit": limit,
            "addParentData": True,
        }

        url = self.SYNC_ENDPOINT.format(
            actor_id=self._actor_id_to_url(self.actor_id)
        )
        headers = {"Content-Type": "application/json"}

        result = {
            "comments": [],
            "token_index": 0,
            "api_status": "error",
            "error_message": "",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        # Coba setiap token yang tersedia (failover)
        for attempt in range(len(self.tokens)):
            token = self._next_token()
            token_idx = (self._call_count - 1) % len(self.tokens)
            token_display = token_idx + 1
            result["token_index"] = token_display

            if token_idx in self.exhausted_tokens:
                continue

            try:
                logger.info(
                    f"[Scraper] Mengambil komentar (token #{token_display}, "
                    f"limit={limit})..."
                )
                resp = requests.post(
                    url,
                    json=actor_input,
                    params={"token": token},
                    headers=headers,
                    timeout=self.timeout,
                )
                self.total_api_calls += 1
                status_code = resp.status_code

                if status_code in (200, 201):
                    items = resp.json()
                    if isinstance(items, list):
                        real_comments = [
                            item for item in items
                            if "error" not in item and item.get("text") is not None
                        ]
                        result["comments"] = real_comments
                        result["api_status"] = "success"

                        if len(real_comments) < len(items):
                            skipped = len(items) - len(real_comments)
                            logger.info(
                                f"[Scraper] {len(items)} diterima, "
                                f"{skipped} dibuang, {len(real_comments)} komentar valid"
                            )
                        else:
                            logger.info(
                                f"[Scraper] Berhasil: {len(real_comments)} komentar"
                            )
                    else:
                        result["comments"] = []
                        result["api_status"] = "success"
                        logger.warning("[Scraper] Response bukan list")

                    self._record_usage("comments", post_url, token_display,
                                       status_code, len(result["comments"]), "success")
                    return result

                elif status_code in (402, 403):
                    # Token ini habis — tandai dan coba token lain
                    self.exhausted_tokens.add(token_idx)
                    logger.warning(
                        f"[Scraper] Token #{token_display} kuota habis "
                        f"(HTTP {status_code}). Mencoba token lain..."
                    )
                    self._record_usage("comments", post_url, token_display,
                                       status_code, 0, "quota_exceeded")
                    continue  # ← failover ke token berikutnya

                else:
                    result["api_status"] = "error"
                    result["error_message"] = (
                        f"HTTP {status_code}: {resp.text[:200]}"
                    )
                    logger.error(f"[Scraper] Error HTTP {status_code}")
                    self._record_usage("comments", post_url, token_display,
                                       status_code, 0, "error")
                    return result

            except requests.Timeout:
                result["api_status"] = "timeout"
                result["error_message"] = (
                    f"Koneksi timeout setelah {self.timeout} detik"
                )
                logger.error("[Scraper] Timeout")
                self._record_usage("comments", post_url, token_display,
                                   0, 0, "timeout")
                return result

            except requests.ConnectionError:
                result["api_status"] = "connection_error"
                result["error_message"] = "Tidak bisa terhubung ke server Apify"
                logger.error("[Scraper] Connection error")
                self._record_usage("comments", post_url, token_display,
                                   0, 0, "connection_error")
                return result

            except Exception as e:
                result["api_status"] = "error"
                result["error_message"] = str(e)[:300]
                logger.error(f"[Scraper] Error: {e}")
                self._record_usage("comments", post_url, token_display,
                                   0, 0, "error")
                return result

        # Semua token sudah dicoba dan gagal
        result["api_status"] = "all_tokens_exhausted"
        result["error_message"] = (
            "Semua token API sudah mencapai batas pemakaian. "
            "Tidak bisa mengambil data saat ini."
        )
        logger.error("[Scraper] Semua token exhausted")
        return result

    # ── Scrape Profile Posts (dengan Token Failover) ──────────

    def scrape_profile_posts(
        self,
        profile_url: str,
        limit: int = 10,
        only_newer_than: str = None,
    ) -> dict:
        """
        Scrape postingan terbaru dari satu profil Instagram.
        Token failover: jika 402/403, otomatis coba token berikutnya.

        Args:
            profile_url: URL profil, mis. https://www.instagram.com/surabaya/
            limit: jumlah maksimum post yang diambil
            only_newer_than: tanggal batas bawah (YYYY-MM-DD)

        Returns:
            dict dengan keys:
                - "posts": list of raw post dicts dari Apify
                - "token_index": token (1-based)
                - "api_status": "success" / "error" / "all_tokens_exhausted"
                - "error_message": string jika error
                - "scraped_at": timestamp
        """
        if not self.is_any_token_available():
            return {
                "posts": [],
                "token_index": 0,
                "api_status": "all_tokens_exhausted",
                "error_message": "Semua token API sudah mencapai batas pemakaian bulan ini.",
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

        actor_input = {
            "directUrls": [profile_url],
            "resultsType": "posts",
            "resultsLimit": limit,
            "addParentData": True,
        }
        if only_newer_than:
            actor_input["onlyPostsNewerThan"] = only_newer_than

        url = self.SYNC_ENDPOINT.format(
            actor_id=self._actor_id_to_url(self.actor_id)
        )
        headers = {"Content-Type": "application/json"}

        result = {
            "posts": [],
            "token_index": 0,
            "api_status": "error",
            "error_message": "",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        # Coba setiap token yang tersedia (failover)
        for attempt in range(len(self.tokens)):
            token = self._next_token()
            token_idx = (self._call_count - 1) % len(self.tokens)
            token_display = token_idx + 1
            result["token_index"] = token_display

            if token_idx in self.exhausted_tokens:
                continue

            try:
                logger.info(
                    f"[Scraper] Mencari postingan (token #{token_display}, "
                    f"limit={limit}, sejak={only_newer_than})..."
                )
                resp = requests.post(
                    url,
                    json=actor_input,
                    params={"token": token},
                    headers=headers,
                    timeout=self.timeout,
                )
                self.total_api_calls += 1
                status_code = resp.status_code

                if status_code in (200, 201):
                    items = resp.json()
                    if isinstance(items, list):
                        real_posts = [item for item in items if "error" not in item]
                        result["posts"] = real_posts
                        result["api_status"] = "success"
                        logger.info(
                            f"[Scraper] {len(items)} diterima, "
                            f"{len(real_posts)} postingan valid"
                        )
                    else:
                        result["api_status"] = "success"
                        logger.warning("[Scraper] Response posts bukan list")

                    self._record_usage("posts", profile_url, token_display,
                                       status_code, len(result["posts"]), "success")
                    return result

                elif status_code in (402, 403):
                    self.exhausted_tokens.add(token_idx)
                    logger.warning(
                        f"[Scraper] Token #{token_display} kuota habis "
                        f"(HTTP {status_code}). Mencoba token lain..."
                    )
                    self._record_usage("posts", profile_url, token_display,
                                       status_code, 0, "quota_exceeded")
                    continue

                else:
                    result["api_status"] = "error"
                    result["error_message"] = (
                        f"HTTP {status_code}: {resp.text[:200]}"
                    )
                    logger.error(f"[Scraper] Error HTTP {status_code}")
                    self._record_usage("posts", profile_url, token_display,
                                       status_code, 0, "error")
                    return result

            except requests.Timeout:
                result["api_status"] = "timeout"
                result["error_message"] = (
                    f"Koneksi timeout setelah {self.timeout} detik"
                )
                logger.error("[Scraper] Timeout (posts)")
                self._record_usage("posts", profile_url, token_display,
                                   0, 0, "timeout")
                return result

            except requests.ConnectionError:
                result["api_status"] = "connection_error"
                result["error_message"] = "Tidak bisa terhubung ke server Apify"
                logger.error("[Scraper] Connection error (posts)")
                self._record_usage("posts", profile_url, token_display,
                                   0, 0, "connection_error")
                return result

            except Exception as e:
                result["api_status"] = "error"
                result["error_message"] = str(e)[:300]
                logger.error(f"[Scraper] Error (posts): {e}")
                self._record_usage("posts", profile_url, token_display,
                                   0, 0, "error")
                return result

        # Semua token sudah dicoba dan gagal
        result["api_status"] = "all_tokens_exhausted"
        result["error_message"] = (
            "Semua token API sudah mencapai batas pemakaian. "
            "Tidak bisa mengambil data saat ini."
        )
        logger.error("[Scraper] Semua token exhausted (posts)")
        return result
