"""
scraper.py — Apify REST API wrapper untuk Instagram comment scraping.
Mendukung single/multi token dengan round-robin sederhana.
"""

import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger("demo_monitor")


class ApifyScraper:
    """Wrapper Apify REST API v2 untuk scraping komentar Instagram."""

    SYNC_ENDPOINT = (
        "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
    )

    def __init__(self, tokens: list, actor_id: str, timeout: int = 300):
        self.tokens = tokens
        self.actor_id = actor_id
        self.timeout = timeout
        self._call_count = 0
        self.total_api_calls = 0

    def _next_token(self) -> str:
        """Round-robin token selection."""
        token = self.tokens[self._call_count % len(self.tokens)]
        self._call_count += 1
        return token

    def _actor_id_to_url(self, actor_id: str) -> str:
        """Convert 'apify/instagram-scraper' ke 'apify~instagram-scraper'."""
        return actor_id.replace("/", "~")

    def scrape_comments(self, post_url: str, limit: int = 50) -> dict:
        """
        Scrape komentar dari satu post URL.

        Returns:
            dict dengan keys:
                - "comments": list of comment dicts
                - "token_index": token mana yang dipakai (1-based)
                - "api_status": "success" / "error"
                - "error_message": string jika error
                - "scraped_at": timestamp
        """
        token = self._next_token()
        token_index = self._call_count  # sudah di-increment

        actor_input = {
            "directUrls": [post_url],
            "resultsType": "comments",
            "resultsLimit": limit,
            "addParentData": True,
        }

        url = self.SYNC_ENDPOINT.format(
            actor_id=self._actor_id_to_url(self.actor_id)
        )
        params = {"token": token}
        headers = {"Content-Type": "application/json"}

        result = {
            "comments": [],
            "token_index": (token_index - 1) % len(self.tokens) + 1,
            "api_status": "success",
            "error_message": "",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            logger.info(
                f"[Scraper] Calling Apify (token #{result['token_index']}, "
                f"limit={limit})..."
            )
            resp = requests.post(
                url,
                json=actor_input,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            self.total_api_calls += 1

            if resp.status_code in (200, 201):
                items = resp.json()
                if isinstance(items, list):
                    # Filter: buang error items dari Apify
                    # (contoh: {"error":"no_items","errorDescription":"..."})
                    real_comments = [
                        item for item in items
                        if "error" not in item and item.get("text") is not None
                    ]
                    result["comments"] = real_comments

                    if len(real_comments) < len(items):
                        skipped = len(items) - len(real_comments)
                        logger.info(
                            f"[Scraper] {len(items)} items diterima, "
                            f"{skipped} error/non-comment dibuang, "
                            f"{len(real_comments)} komentar valid"
                        )
                    else:
                        logger.info(
                            f"[Scraper] Berhasil: {len(real_comments)} komentar diterima"
                        )
                else:
                    result["comments"] = []
                    logger.warning("[Scraper] Response bukan list")
            elif resp.status_code == 402:
                result["api_status"] = "quota_exceeded"
                result["error_message"] = (
                    "Apify usage limit tercapai untuk token ini. "
                    "Ini normal untuk free tier."
                )
                logger.warning(f"[Scraper] 402 — quota exceeded")
            else:
                result["api_status"] = "error"
                result["error_message"] = (
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
                logger.error(
                    f"[Scraper] Error HTTP {resp.status_code}"
                )

        except requests.Timeout:
            result["api_status"] = "timeout"
            result["error_message"] = (
                f"Request timeout setelah {self.timeout} detik"
            )
            logger.error("[Scraper] Timeout")

        except requests.ConnectionError:
            result["api_status"] = "connection_error"
            result["error_message"] = "Tidak bisa terhubung ke Apify API"
            logger.error("[Scraper] Connection error")

        except Exception as e:
            result["api_status"] = "error"
            result["error_message"] = str(e)[:300]
            logger.error(f"[Scraper] Unexpected error: {e}")

        return result
