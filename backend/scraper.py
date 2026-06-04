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
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("demo_monitor")


class ApifyScraper:
    """Wrapper Apify REST API v2 dengan token failover dan usage tracking.
    Mendukung multi-platform (Instagram + Facebook) via actor_ids dict."""

    SYNC_ENDPOINT = (
        "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
    )

    def __init__(self, tokens: list, actor_ids: dict, timeout: int = 300,
                 cost_per_call: float = 0.032, usage_log_path: str = None):
        self.tokens = tokens
        self.actor_ids = actor_ids  # {"instagram": "...", "facebook": "..."}
        self.timeout = timeout
        self.cost_per_call = cost_per_call
        self._call_count = 0
        self.total_api_calls = 0
        self.successful_api_calls = 0  # hanya panggilan billable (HTTP 200/201)
        self.exhausted_tokens = set()

        self._usage_log_path = Path(usage_log_path) if usage_log_path else None
        self._usage_log = self._load_usage_log()

    # ── Usage Tracking ────────────────────────────────────────

    def _load_usage_log(self) -> list:
        if self._usage_log_path and self._usage_log_path.exists():
            try:
                with open(self._usage_log_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return []

    def _save_usage_log(self):
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

        weekday = now.weekday()
        week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
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

        token_status = [
            {"index": i + 1, "status": "exhausted" if i in self.exhausted_tokens else "active"}
            for i in range(len(self.tokens))
        ]

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
        return len(self.exhausted_tokens) < len(self.tokens)

    def reset_exhausted_tokens(self):
        self.exhausted_tokens.clear()
        logger.info("[Scraper] Token quota di-reset")

    # ── Token Management ──────────────────────────────────────

    def _next_token(self) -> tuple:
        """Round-robin pemilihan token, skip yang exhausted. Return (token, index)."""
        for _ in range(len(self.tokens)):
            self._call_count += 1
            idx = (self._call_count - 1) % len(self.tokens)
            if idx not in self.exhausted_tokens:
                return self.tokens[idx], idx
        self._call_count += 1
        idx = (self._call_count - 1) % len(self.tokens)
        return self.tokens[idx], idx

    @staticmethod
    def _actor_id_to_url(actor_id: str) -> str:
        return actor_id.replace("/", "~")

    # ── Generic Apify Call (menghilangkan duplikasi) ──────────

    def _call_apify(self, actor_input: dict, method: str, target: str,
                    platform: str = "instagram") -> dict:
        """
        Generik API call dengan token failover.
        
        Args:
            actor_input: body JSON untuk Apify actor
            method: "comments" atau "posts" (untuk logging/usage)
            target: URL target (post_url atau profile_url)
            platform: "instagram" atau "facebook" (memilih Actor ID)
            
        Returns:
            dict dengan keys: items, token_index, api_status, error_message, scraped_at
        """
        if not self.is_any_token_available():
            return {
                "items": [],
                "token_index": 0,
                "api_status": "all_tokens_exhausted",
                "error_message": "Semua token API sudah mencapai batas pemakaian bulan ini.",
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

        actor_id = self.actor_ids.get(platform, self.actor_ids.get("instagram", ""))
        url = self.SYNC_ENDPOINT.format(actor_id=self._actor_id_to_url(actor_id))
        headers = {"Content-Type": "application/json"}

        result = {
            "items": [],
            "token_index": 0,
            "api_status": "error",
            "error_message": "",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        for _attempt in range(len(self.tokens)):
            token, token_idx = self._next_token()
            token_display = token_idx + 1
            result["token_index"] = token_display

            if token_idx in self.exhausted_tokens:
                continue

            try:
                logger.info(f"[Scraper] {method} (token #{token_display}) → {target[:60]}")
                resp = requests.post(
                    url, json=actor_input,
                    params={"token": token},
                    headers=headers,
                    timeout=self.timeout,
                )
                self.total_api_calls += 1
                status_code = resp.status_code

                if status_code in (200, 201):
                    self.successful_api_calls += 1
                    items = resp.json()
                    if isinstance(items, list):
                        valid = [i for i in items if "error" not in i]
                        if method == "comments":
                            valid = [i for i in valid if i.get("text") is not None]
                        result["items"] = valid
                        result["api_status"] = "success"
                        logger.info(f"[Scraper] {len(valid)} {method} valid")
                    else:
                        result["api_status"] = "success"
                        logger.warning(f"[Scraper] Response {method} bukan list")

                    self._record_usage(method, target, token_display,
                                       status_code, len(result["items"]), "success")
                    return result

                elif status_code in (402, 403):
                    self.exhausted_tokens.add(token_idx)
                    logger.warning(
                        f"[Scraper] Token #{token_display} kuota habis "
                        f"(HTTP {status_code}). Mencoba token lain..."
                    )
                    self._record_usage(method, target, token_display,
                                       status_code, 0, "quota_exceeded")
                    continue

                else:
                    result["api_status"] = "error"
                    result["error_message"] = f"HTTP {status_code}: {resp.text[:200]}"
                    logger.error(f"[Scraper] Error HTTP {status_code}")
                    self._record_usage(method, target, token_display,
                                       status_code, 0, "error")
                    return result

            except requests.Timeout:
                result["api_status"] = "timeout"
                result["error_message"] = f"Koneksi timeout setelah {self.timeout} detik"
                logger.error(f"[Scraper] Timeout ({method})")
                self._record_usage(method, target, token_display, 0, 0, "timeout")
                return result

            except requests.ConnectionError:
                result["api_status"] = "connection_error"
                result["error_message"] = "Tidak bisa terhubung ke server Apify"
                logger.error(f"[Scraper] Connection error ({method})")
                self._record_usage(method, target, token_display, 0, 0, "connection_error")
                return result

            except Exception as e:
                result["api_status"] = "error"
                result["error_message"] = str(e)[:300]
                logger.error(f"[Scraper] Error ({method}): {e}")
                self._record_usage(method, target, token_display, 0, 0, "error")
                return result

        result["api_status"] = "all_tokens_exhausted"
        result["error_message"] = (
            "Semua token API sudah mencapai batas pemakaian. "
            "Tidak bisa mengambil data saat ini."
        )
        logger.error(f"[Scraper] Semua token exhausted ({method})")
        return result

    # ── Public API: Instagram Scrape ────────────────────────────

    def scrape_comments(self, post_url: str, limit: int = 50,
                        platform: str = "instagram") -> dict:
        """Scrape komentar dari satu post URL (IG atau FB)."""
        if platform == "facebook":
            return self.scrape_fb_comments(post_url, limit)
        actor_input = {
            "directUrls": [post_url],
            "resultsType": "comments",
            "resultsLimit": limit,
            "addParentData": True,
        }
        result = self._call_apify(actor_input, "comments", post_url,
                                  platform="instagram")
        result["comments"] = result.pop("items")
        return result

    def scrape_profile_posts(self, profile_url: str, limit: int = 10,
                             only_newer_than: str = None) -> dict:
        """Scrape postingan dari profil Instagram."""
        actor_input = {
            "directUrls": [profile_url],
            "resultsType": "posts",
            "resultsLimit": limit,
            "addParentData": True,
        }
        if only_newer_than:
            actor_input["onlyPostsNewerThan"] = only_newer_than

        result = self._call_apify(actor_input, "posts", profile_url,
                                  platform="instagram")
        result["posts"] = result.pop("items")
        return result

    # ── Public API: Facebook Scrape ────────────────────────────

    def scrape_fb_posts(self, page_url: str, limit: int = 10) -> dict:
        """Scrape postingan dari halaman Facebook."""
        actor_input = {
            "startUrls": [{"url": page_url}],
            "resultsLimit": limit,
        }
        result = self._call_apify(actor_input, "posts", page_url,
                                  platform="facebook")
        result["posts"] = result.pop("items")
        return result

    def scrape_fb_comments(self, post_url: str, limit: int = 50) -> dict:
        """Scrape komentar dari satu post Facebook.

        Memakai actor KOMENTAR khusus (apify/facebook-comments-scraper) via
        platform key 'facebook_comments' — berbeda dari actor discovery postingan.
        Input cocok: startUrls + resultsLimit.
        """
        actor_input = {
            "startUrls": [{"url": post_url}],
            "resultsLimit": limit,
        }
        result = self._call_apify(actor_input, "comments", post_url,
                                  platform="facebook_comments")
        result["comments"] = result.pop("items")
        return result
