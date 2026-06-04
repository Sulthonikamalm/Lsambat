"""
acceptance_stage4.py — Acceptance test offline untuk Stage 4.

Menggunakan FakeScraper (mock Apify) supaya tidak memakai credit Apify.
Menjalankan 9 skenario sesuai spesifikasi, lalu mencetak laporan PASS/FAIL.

Jalankan: python tests/acceptance_stage4.py
"""

import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import discover_posts as dp
import process_queue as pq
from post_queue import load_post_queue
from stage4_utils import load_csv_if_exists
from discovery.fetcher import normalize_post_item


# ── Mock scraper ──────────────────────────────────────────────


class FakeScraper:
    """Mock ApifyScraper. Tidak ada panggilan jaringan."""

    def __init__(self):
        self.posts_by_profile = {}     # profile_url -> list of raw post items
        self.comments_by_url = {}      # post_url -> list of raw comment items
        self.fail_comments_for = set()  # post_url yang harus gagal (HTTP error)
        self.quota_exhausted = False   # jika True: semua scrape_comments → all_tokens_exhausted
        self.profile_calls = []        # (profile_url, only_newer_than)
        self.comment_calls = []        # post_url
        self.tokens = ["FAKE"]

    def scrape_profile_posts(self, profile_url, limit=10, only_newer_than=None):
        self.profile_calls.append((profile_url, only_newer_than))
        return {
            "posts": self.posts_by_profile.get(profile_url, []),
            "token_index": 1,
            "api_status": "success",
            "error_message": "",
            "scraped_at": "now",
        }

    def scrape_comments(self, post_url, limit=50, platform="instagram"):
        self.comment_calls.append(post_url)
        if self.quota_exhausted:
            return {
                "comments": [],
                "token_index": 1,
                "api_status": "all_tokens_exhausted",
                "error_message": "Semua token API sudah mencapai batas pemakaian.",
                "scraped_at": "now",
            }
        if post_url in self.fail_comments_for:
            return {
                "comments": [],
                "token_index": 1,
                "api_status": "error",
                "error_message": "HTTP 500: mock failure",
                "scraped_at": "now",
            }
        return {
            "comments": self.comments_by_url.get(post_url, []),
            "token_index": 1,
            "api_status": "success",
            "error_message": "",
            "scraped_at": "now",
        }


# ── Test harness ──────────────────────────────────────────────

_results = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    _results.append((name, condition, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def make_env(registry_rows, post_discovery=None):
    """Buat temp dir + paths + settings + tulis source_registry.csv."""
    tmp = Path(tempfile.mkdtemp(prefix="ss_stage4_"))
    paths = {
        "source_registry": tmp / "source_registry.csv",
        "raw_posts_csv": tmp / "raw_instagram_posts.csv",
        "post_queue_csv": tmp / "post_queue.csv",
        "raw_comments_csv": tmp / "raw_comments.csv",
        "data_dir": tmp,
    }
    header = "source_id,source_account,platform,priority_level,monitoring_window_days,status,notes\n"
    with open(paths["source_registry"], "w", encoding="utf-8") as f:
        f.write(header)
        for r in registry_rows:
            f.write(",".join(r) + "\n")
    settings = {
        "post_discovery": post_discovery or {
            "max_posts_per_source": 10,
            "only_active_sources": True,
            "use_monitoring_window": True,
            "comments_per_post": 100,
        }
    }
    return tmp, paths, settings


def post_item(url=None, shortcode=None, caption="", ts=None,
              comments=0, likes=0):
    item = {}
    if ts is None:
        from stage4_utils import now_utc
        ts = now_utc()
    if url is not None:
        item["url"] = url
        # Apify post item biasanya menyertakan shortCode juga
        if shortcode is None:
            from stage4_utils import url_shortcode as _sc
            sc = _sc(url)
            if sc:
                shortcode = sc
    if shortcode is not None:
        item["shortCode"] = shortcode
    item["caption"] = caption
    item["timestamp"] = ts
    item["commentsCount"] = comments
    item["likesCount"] = likes
    return item


def comment_item(cid, text, url=None, shortcode=None, ts="2026-05-21T00:00:00Z"):
    item = {"id": cid, "text": text, "timestamp": ts}
    if url:
        item["postUrl"] = url
    if shortcode:
        item["shortCode"] = shortcode
    return item


# ── Scenarios ─────────────────────────────────────────────────


def scenario_1_2():
    print("\n[Skenario 1 & 2] 1 akun aktif → CSV terbentuk, post baru, queue=new_post")
    _, paths, settings = make_env([
        ("SRC001", "@surabaya", "instagram", "1", "30", "active", "pemkot"),
    ])
    scraper = FakeScraper()
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url="https://www.instagram.com/p/AAA111/", caption="banjir di jalan", comments=5),
        post_item(url="https://www.instagram.com/p/BBB222/", caption="acara biasa", comments=2),
    ]
    summary = dp.run_post_discovery(scraper, settings, paths)

    check("S1: raw_instagram_posts.csv terbentuk", paths["raw_posts_csv"].exists())
    check("S1: post_queue.csv terbentuk", paths["post_queue_csv"].exists())
    check("S2: 2 post baru terdeteksi", summary["total_new_posts"] == 2,
          f"new={summary['total_new_posts']}")
    df_q = load_post_queue(paths["post_queue_csv"])
    all_new_post = (df_q["queue_reason"] == "new_post").all() and len(df_q) == 2
    check("S2: semua queue_reason=new_post", all_new_post)
    # relevance: caption 'banjir' → high
    df_p = load_csv_if_exists(paths["raw_posts_csv"])
    rel = df_p[df_p["post_shortcode"] == "AAA111"]["post_relevance"].iloc[0]
    check("S2: relevance 'banjir' = high", rel == "high", f"rel={rel}")
    # caption biasa + priority 1 → medium
    rel2 = df_p[df_p["post_shortcode"] == "BBB222"]["post_relevance"].iloc[0]
    check("S2: caption biasa + priority1 = medium", rel2 == "medium", f"rel={rel2}")


def scenario_3():
    print("\n[Skenario 3] Discovery ulang tanpa post baru → no duplikat, last_checked update")
    _, paths, settings = make_env([
        ("SRC001", "@surabaya", "instagram", "1", "30", "active", "pemkot"),
    ])
    scraper = FakeScraper()
    posts = [post_item(url="https://www.instagram.com/p/AAA111/", caption="x", comments=5)]
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = posts
    dp.run_post_discovery(scraper, settings, paths)

    # Tandai last_checked_at lama untuk deteksi update
    df_p = load_csv_if_exists(paths["raw_posts_csv"])
    df_p["last_checked_at"] = "OLD"
    df_p.to_csv(paths["raw_posts_csv"], index=False, encoding="utf-8-sig")

    summary2 = dp.run_post_discovery(scraper, settings, paths)
    df_p2 = load_csv_if_exists(paths["raw_posts_csv"])
    df_q = load_post_queue(paths["post_queue_csv"])

    check("S3: tidak ada post baru di run kedua", summary2["total_new_posts"] == 0)
    check("S3: jumlah baris post tetap 1 (no duplikat)", len(df_p2) == 1)
    check("S3: queue tidak bertambah", len(df_q) == 1, f"queue={len(df_q)}")
    check("S3: last_checked_at ter-update", df_p2["last_checked_at"].iloc[0] != "OLD")


def scenario_4():
    print("\n[Skenario 4] comment_count naik → queue comment_count_changed")
    # Set min_comment_increase=1 agar menguji MEKANISME re-queue, bukan nilai tuning.
    _, paths, settings = make_env(
        [("SRC001", "@surabaya", "instagram", "1", "30", "active", "pemkot")],
        post_discovery={"max_posts_per_source": 10, "only_active_sources": True,
                        "use_monitoring_window": True, "comments_per_post": 100,
                        "min_comment_increase": 1},
    )
    scraper = FakeScraper()
    url = "https://www.instagram.com/p/AAA111/"
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url=url, caption="x", comments=5)
    ]
    dp.run_post_discovery(scraper, settings, paths)

    # comment_count naik 5 → 9 (+4, >= min_comment_increase=1)
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url=url, caption="x", comments=9)
    ]
    summary2 = dp.run_post_discovery(scraper, settings, paths)

    df_q = load_post_queue(paths["post_queue_csv"])
    has_changed = (df_q["queue_reason"] == "comment_count_changed").any()
    check("S4: ada queue comment_count_changed", has_changed)
    check("S4: total_comment_changed == 1", summary2["total_comment_changed"] == 1)

    # comment_count turun → tidak crash, tidak ada queue changed baru
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url=url, caption="x", comments=3)
    ]
    try:
        summary3 = dp.run_post_discovery(scraper, settings, paths)
        check("S4: count turun tidak crash", True)
        check("S4: count turun → tidak ada changed baru",
              summary3["total_comment_changed"] == 0)
    except Exception as e:
        check("S4: count turun tidak crash", False, str(e))


def scenario_5():
    print("\n[Skenario 5] Proses queue pending → komentar masuk dataset, status completed")
    _, paths, settings = make_env([
        ("SRC001", "@surabaya", "instagram", "1", "30", "active", "pemkot"),
    ])
    scraper = FakeScraper()
    url = "https://www.instagram.com/p/AAA111/"
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url=url, caption="banjir", comments=2)
    ]
    # komentar: 1 cocok via shortcode (reel format), 1 via /p/, 1 duplikat id
    scraper.comments_by_url[url] = [
        comment_item("c1", "tolong diperbaiki", url="https://www.instagram.com/reel/AAA111/"),
        comment_item("c2", "setuju", shortcode="AAA111"),
    ]
    dp.run_post_discovery(scraper, settings, paths)

    summary = pq.process_pending_queue(scraper, settings, paths)
    df_c = load_csv_if_exists(paths["raw_comments_csv"])
    df_q = load_post_queue(paths["post_queue_csv"])

    check("S5: raw_comments.csv terbentuk", paths["raw_comments_csv"].exists())
    check("S5: 2 komentar masuk dataset (tersimpan, tagged)", len(df_c) == 2, f"comments={len(df_c)}")
    check("S5: status queue → completed",
          (df_q["status"] == "completed").all(), str(df_q["status"].tolist()))
    # Komentar bertanggal 2026-05-21 (sebelum post ditemukan 'sekarang') → baseline.
    check("S5: komentar lama ditandai baseline (bukan keluhan baru)",
          (df_c["is_baseline"] == "true").all(), str(df_c["is_baseline"].tolist()))
    check("S5: total_new_comments == 0 (semua baseline)", summary["total_new_comments"] == 0,
          f"new={summary['total_new_comments']}")
    check("S5: total_baseline_comments == 2", summary["total_baseline_comments"] == 2,
          f"baseline={summary['total_baseline_comments']}")

    # Proses lagi: tidak ada pending → tidak menambah komentar
    summary2 = pq.process_pending_queue(scraper, settings, paths)
    df_c2 = load_csv_if_exists(paths["raw_comments_csv"])
    check("S5: re-run tanpa pending tidak menambah komentar", len(df_c2) == 2)
    check("S5: total_pending kedua == 0", summary2["total_pending"] == 0)


def scenario_6():
    print("\n[Skenario 6] Akun inactive → tidak diproses, tidak ada Apify run")
    _, paths, settings = make_env([
        ("SRC001", "@nonaktif", "instagram", "3", "30", "inactive", "akun mati"),
    ])
    scraper = FakeScraper()
    summary = dp.run_post_discovery(scraper, settings, paths)
    check("S6: tidak ada panggilan scrape_profile_posts",
          len(scraper.profile_calls) == 0, f"calls={len(scraper.profile_calls)}")
    check("S6: summary error 'tidak ada akun aktif'",
          isinstance(summary, dict) and "error" in summary, str(summary))


def scenario_7():
    print("\n[Skenario 7] URL/post invalid → tidak crash, status failed + error_message")
    _, paths, settings = make_env([
        ("SRC001", "@surabaya", "instagram", "1", "30", "active", "pemkot"),
    ])
    scraper = FakeScraper()
    url = "https://www.instagram.com/p/FAIL99/"
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url=url, caption="x", comments=1)
    ]
    scraper.fail_comments_for.add(url)  # Apify gagal untuk url ini
    dp.run_post_discovery(scraper, settings, paths)

    try:
        pq.process_pending_queue(scraper, settings, paths)
        check("S7: proses queue tidak crash saat Apify gagal", True)
    except Exception as e:
        check("S7: proses queue tidak crash saat Apify gagal", False, str(e))
        return

    df_q = load_post_queue(paths["post_queue_csv"])
    failed_row = df_q[df_q["status"] == "failed"]
    check("S7: status queue → failed", len(failed_row) == 1)
    check("S7: error_message terisi",
          len(failed_row) == 1 and failed_row["error_message"].iloc[0].strip() != "",
          failed_row["error_message"].iloc[0] if len(failed_row) else "")

    # Post tanpa URL & shortcode → normalize_post_item return {} (di-skip, tidak crash)
    empty = normalize_post_item({"caption": "no url"},
                                {"source_id": "X", "source_account": "@a", "priority_level": "3"})
    check("S7: post tanpa url/shortcode di-skip ({}) ", empty == {})


def scenario_8():
    print("\n[Skenario 8] Window filter: onlyPostsNewerThan benar dari monitoring_window_days")
    _, paths, settings = make_env([
        ("SRC001", "@surabaya", "instagram", "1", "30", "active", "pemkot"),
        ("SRC002", "@komunitas", "instagram", "3", "14", "active", "komunitas"),
    ])
    scraper = FakeScraper()
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = []
    scraper.posts_by_profile["https://www.instagram.com/komunitas/"] = []
    dp.run_post_discovery(scraper, settings, paths)

    calls = dict(scraper.profile_calls)
    exp30 = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    exp14 = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    got30 = calls.get("https://www.instagram.com/surabaya/")
    got14 = calls.get("https://www.instagram.com/komunitas/")
    check("S8: window 30 hari benar", got30 == exp30, f"got={got30} exp={exp30}")
    check("S8: window 14 hari benar", got14 == exp14, f"got={got14} exp={exp14}")

    # Window dimatikan via setting → only_newer_than None
    _, paths2, settings2 = make_env(
        [("SRC001", "@surabaya", "instagram", "1", "30", "active", "x")],
        post_discovery={"max_posts_per_source": 10, "only_active_sources": True,
                        "use_monitoring_window": False, "comments_per_post": 100},
    )
    scraper2 = FakeScraper()
    scraper2.posts_by_profile["https://www.instagram.com/surabaya/"] = []
    dp.run_post_discovery(scraper2, settings2, paths2)
    check("S8: window dimatikan → onlyPostsNewerThan None",
          scraper2.profile_calls[0][1] is None, str(scraper2.profile_calls[0]))


def scenario_10_quota_retry():
    print("\n[Skenario 10] Kuota habis saat proses queue → item tetap PENDING (di-retry), bukan failed")
    _, paths, settings = make_env([
        ("SRC001", "@surabaya", "instagram", "1", "30", "active", "pemkot"),
    ])
    scraper = FakeScraper()
    url1 = "https://www.instagram.com/p/AAA111/"
    url2 = "https://www.instagram.com/p/BBB222/"
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url=url1, caption="banjir", comments=2),
        post_item(url=url2, caption="sampah menumpuk", comments=2),
    ]
    dp.run_post_discovery(scraper, settings, paths)

    # Simulasikan semua token habis saat memproses komentar
    scraper.quota_exhausted = True
    summary = pq.process_pending_queue(scraper, settings, paths)

    df_q = load_post_queue(paths["post_queue_csv"])
    n_pending = (df_q["status"] == "pending").sum()
    n_failed = (df_q["status"] == "failed").sum()

    check("S10: auto-stop terpicu (stopped_early)", summary["stopped_early"] is True)
    check("S10: tidak ada item ditandai 'failed'", n_failed == 0, f"failed={n_failed}")
    check("S10: semua item kembali 'pending' (bisa di-retry)", n_pending == 2,
          f"pending={n_pending}/{len(df_q)}")
    check("S10: total_skipped == 2", summary["total_skipped"] == 2,
          f"skipped={summary['total_skipped']}")
    check("S10: belum ada komentar tersimpan", not paths["raw_comments_csv"].exists()
          or len(load_csv_if_exists(paths["raw_comments_csv"])) == 0)

    # Kuota pulih → run berikutnya harus memproses item yang tadi tertunda
    scraper.quota_exhausted = False
    scraper.comments_by_url[url1] = [comment_item("c1", "tolong", shortcode="AAA111")]
    scraper.comments_by_url[url2] = [comment_item("c2", "diperbaiki", shortcode="BBB222")]
    summary2 = pq.process_pending_queue(scraper, settings, paths)
    df_q2 = load_post_queue(paths["post_queue_csv"])
    df_c2 = load_csv_if_exists(paths["raw_comments_csv"])

    check("S10: setelah kuota pulih, item ter-retry & completed",
          (df_q2["status"] == "completed").all(), str(df_q2["status"].tolist()))
    check("S10: komentar yang tadi tertunda akhirnya terambil", len(df_c2) == 2,
          f"comments={len(df_c2)}")


def scenario_11_new_comment_detection():
    print("\n[Skenario 11] Komentar dibuat SETELAH post ditemukan → ditandai BARU (bukan baseline)")
    _, paths, settings = make_env([
        ("SRC001", "@surabaya", "instagram", "1", "30", "active", "pemkot"),
    ])
    scraper = FakeScraper()
    url = "https://www.instagram.com/p/AAA111/"
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url=url, caption="banjir", comments=2)
    ]
    dp.run_post_discovery(scraper, settings, paths)

    # Paksa discovered_at ke masa lalu → komentar 2026-05-21 dianggap SETELAH itu
    df_p = load_csv_if_exists(paths["raw_posts_csv"])
    df_p["discovered_at"] = "2020-01-01T00:00:00Z"
    df_p.to_csv(paths["raw_posts_csv"], index=False, encoding="utf-8-sig")

    scraper.comments_by_url[url] = [
        comment_item("c1", "tolong perbaiki", shortcode="AAA111", ts="2026-05-21T00:00:00Z"),
    ]
    summary = pq.process_pending_queue(scraper, settings, paths)
    df_c = load_csv_if_exists(paths["raw_comments_csv"])

    check("S11: komentar ditandai BARU (is_baseline=false)",
          (df_c["is_baseline"] == "false").all(), str(df_c["is_baseline"].tolist()))
    check("S11: total_new_comments == 1", summary["total_new_comments"] == 1,
          f"new={summary['total_new_comments']}")
    check("S11: total_baseline_comments == 0", summary["total_baseline_comments"] == 0,
          f"baseline={summary['total_baseline_comments']}")


def _post_count(paths, shortcode):
    df = load_csv_if_exists(paths["raw_posts_csv"])
    rows = df[df["post_shortcode"] == shortcode]["comment_count_last_seen"]
    return rows.iloc[0] if len(rows) else None


def scenario_12_deferred_count():
    print("\n[Skenario 12] #3: comment_count_last_seen hanya maju SETELAH scrape sukses")
    _, paths, settings = make_env(
        [("SRC001", "@surabaya", "instagram", "1", "30", "active", "pemkot")],
        post_discovery={"max_posts_per_source": 10, "only_active_sources": True,
                        "use_monitoring_window": True, "comments_per_post": 100,
                        "min_comment_increase": 1},
    )
    scraper = FakeScraper()
    url = "https://www.instagram.com/p/AAA111/"
    # Run 1: count=5, new_post → proses sukses
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url=url, caption="banjir", comments=5)
    ]
    dp.run_post_discovery(scraper, settings, paths)
    scraper.comments_by_url[url] = [comment_item("c1", "a", shortcode="AAA111")]
    pq.process_pending_queue(scraper, settings, paths)
    check("S12: setelah run1, count = 5", _post_count(paths, "AAA111") == "5",
          f"count={_post_count(paths, 'AAA111')}")

    # Run 2: count naik 5 → 10 (comment_changed). Count HARUS tetap 5 (ditunda).
    scraper.posts_by_profile["https://www.instagram.com/surabaya/"] = [
        post_item(url=url, caption="banjir", comments=10)
    ]
    dp.run_post_discovery(scraper, settings, paths)
    check("S12: setelah discovery (sebelum scrape), count masih 5 (ditunda)",
          _post_count(paths, "AAA111") == "5", f"count={_post_count(paths, 'AAA111')}")

    # Proses queue saat KUOTA HABIS → gagal → count TIDAK boleh maju
    scraper.quota_exhausted = True
    pq.process_pending_queue(scraper, settings, paths)
    check("S12: scrape gagal (kuota) → count tetap 5 (tidak hilang sinyal)",
          _post_count(paths, "AAA111") == "5", f"count={_post_count(paths, 'AAA111')}")

    # Kuota pulih → scrape sukses → count maju ke 10
    scraper.quota_exhausted = False
    scraper.comments_by_url[url] = [
        comment_item("c1", "a", shortcode="AAA111"),
        comment_item("c2", "b", shortcode="AAA111"),
    ]
    pq.process_pending_queue(scraper, settings, paths)
    check("S12: setelah scrape sukses, count maju ke 10 (commit)",
          _post_count(paths, "AAA111") == "10", f"count={_post_count(paths, 'AAA111')}")


def scenario_9_flask():
    print("\n[Skenario 9] Smoke test Flask: API baru jalan + route lama tetap ada")
    try:
        import app as flask_app
    except SystemExit as e:
        check("S9: import app.py", False, f"SystemExit: {e}")
        return
    except Exception as e:
        check("S9: import app.py", False, str(e))
        return

    check("S9: import app.py sukses", True)
    client = flask_app.app.test_client()

    # Route baru
    for route in ["/api/sources", "/api/posts", "/api/queue", "/api/comments", "/api/usage"]:
        resp = client.get(route)
        check(f"S9: GET {route} == 200", resp.status_code == 200, f"code={resp.status_code}")

    # Cek format response status
    resp = client.get("/api/status")
    ok = resp.status_code == 200 and "system_active" in resp.get_json()
    check("S9: GET /api/status (fitur baru) tetap jalan dan format sesuai", ok)


def main():
    print("=" * 64)
    print("  ACCEPTANCE TEST STAGE 4 — SurabayaSambat v2 (mock Apify)")
    print("=" * 64)

    scenario_1_2()
    scenario_3()
    scenario_4()
    scenario_5()
    scenario_6()
    scenario_7()
    scenario_8()
    scenario_10_quota_retry()
    scenario_11_new_comment_detection()
    scenario_12_deferred_count()
    scenario_9_flask()

    print("\n" + "=" * 64)
    passed = sum(1 for _, c, _ in _results if c)
    total = len(_results)
    print(f"  HASIL: {passed}/{total} checks PASS")
    if passed != total:
        print("  GAGAL:")
        for name, c, detail in _results:
            if not c:
                print(f"    - {name} {detail}")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
