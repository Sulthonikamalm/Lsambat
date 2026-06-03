# 🔬 Deep Analysis & Enterprise Roadmap — SurabayaSambat v2

Dokumen ini adalah hasil **deep checking, deep analysis, deep reading, dan deep best practice** terhadap seluruh kode sistem. Setiap file dibedah, setiap skenario "IF-THEN" dipetakan, dan solusinya dirancang agar sistem siap skala **enterprise** — dari hulu (user klik tombol) sampai hilir (data tersimpan aman).

---

## 📊 Audit Ukuran File (Kondisi Saat Ini)

> [!CAUTION]
> **Aturan emas: Tidak boleh ada file yang melebihi 300 baris kode.**
> File yang besar = sulit diaudit, sulit di-debug, dan rawan konflik saat kolaborasi tim.

| File | Baris | Status | Keterangan |
|------|------:|--------|------------|
| `app.py` | **689** | 🔴 KRITIS | **2.3x batas!** Monolith — campur route, scheduler, helper, logic |
| `discover_posts.py` | **643** | 🔴 KRITIS | **2.1x batas!** Campur scoring, persistence, orchestrator |
| `scraper.py` | **458** | 🟡 OVER | **1.5x batas!** 60% kode duplikasi antara `scrape_comments` dan `scrape_profile_posts` |
| `process_queue.py` | **312** | 🟡 OVER | Sedikit di atas, tapi bisa dioptimasi |
| `app.js` | **725** | 🔴 KRITIS | **2.4x batas!** Semua UI logic dalam 1 file |
| `index.html` | **495** | 🟡 OVER | Terlalu panjang untuk 1 halaman HTML |
| `style.css` | **1626** | 🔴 KRITIS | **5.4x batas!** Tidak modular |
| `post_queue.py` | 139 | 🟢 OK | — |
| `stage4_utils.py` | 88 | 🟢 OK | — |
| `config.py` | 55 | 🟢 OK | — |

---

## 🏗️ BAGIAN 1: Arsitektur & Refactoring Kode

### 1.1 Pecah `app.py` (689 baris → 5 file × ~130 baris)

**Best Practice**: Flask Application Factory + Blueprint Pattern.

```
backend/
├── app.py                    # (~50 baris) Factory: create_app(), main entry
├── routes/
│   ├── __init__.py
│   ├── scrape_routes.py      # (~120) /api/scrape, /api/stop-scrape
│   ├── source_routes.py      # (~120) /api/sources, tambah/hapus akun
│   ├── data_routes.py        # (~80) /api/comments, /api/posts, /api/download
│   └── system_routes.py      # (~80) /api/status, /api/toggle, /api/reset
├── services/
│   ├── scrape_service.py     # (~150) _run_unified_scrape(), _record_scrape()
│   └── scheduler_service.py  # (~80) _auto_scrape_loop(), _calculate_next()
├── helpers/
│   ├── rate_limiter.py       # (~60) _get_current_tier(), _get_week_scrape_count()
│   └── history.py            # (~40) _load_scrape_history(), _save_scrape_history()
```

**IF** kita tidak refactor → **THEN** setiap kali menambah fitur baru, developer harus scroll 689 baris dan mencari posisi yang tepat, rawan salah tempel kode.

### 1.2 Pecah `discover_posts.py` (643 baris → 3 file)

```
backend/
├── discovery/
│   ├── source_registry.py    # (~70) load_source_registry(), get_active_sources()
│   ├── relevance_scoring.py  # (~120) score_post_relevance(), _calc_post_age_days()
│   ├── post_normalizer.py    # (~100) normalize_post_item(), _make_post_id_hash()
│   └── orchestrator.py       # (~120) run_post_discovery(), detect_new_or_updated()
```

### 1.3 Hapus Duplikasi di `scraper.py` (458 → ~200 baris)

**Masalah**: `scrape_comments()` (baris 153-303) dan `scrape_profile_posts()` (baris 307-457) memiliki **~80% kode yang identik** — keduanya melakukan token failover, error handling, dan usage recording dengan pola yang sama persis.

**Solusi**: Buat method generik `_call_apify()` yang menangani semua logika token failover, lalu `scrape_comments()` dan `scrape_profile_posts()` hanya perlu mengirimkan `actor_input` dan `result_key` yang berbeda.

```python
# SEBELUM: 2 method × 150 baris = 300 baris duplikasi
# SESUDAH: 1 method generik + 2 wrapper = ~120 baris total

def _call_apify(self, actor_input: dict, method: str, target: str) -> dict:
    """Generik API call dengan token failover."""
    # ... semua logic failover di sini (~100 baris)

def scrape_comments(self, post_url, limit=50):
    return self._call_apify({"directUrls": [post_url], ...}, "comments", post_url)

def scrape_profile_posts(self, profile_url, limit=10, ...):
    return self._call_apify({"directUrls": [profile_url], ...}, "posts", profile_url)
```

### 1.4 Pecah Frontend (`app.js` 725 baris → modul terpisah)

```
frontend/js/
├── app.js              # (~60) init, socket setup, main orchestrator
├── ui-status.js        # (~80) applyStatusData(), countdown timer
├── ui-usage.js         # (~80) fetchUsage(), updateUsagePanel()
├── ui-sources.js       # (~100) fetchSources(), renderSourceCards()
├── ui-comments.js      # (~80) fetchComments(), pagination
├── ui-scrape.js        # (~100) doScrapeNow(), doStopScrape(), log handler
└── ui-utils.js         # (~50) showToast(), formatDate(), helpers
```

### 1.5 Pecah `style.css` (1626 baris → modul CSS)

```
frontend/css/
├── base.css            # (~100) reset, :root vars, typography
├── layout.css          # (~80) container, grid, footer
├── components.css      # (~200) panel, card, button, badge, toggle
├── tables.css          # (~100) data-table, table-empty
├── console.css         # (~80) terminal/log styling
└── responsive.css      # (~60) @media queries
```

---

## ⚙️ BAGIAN 2: Logika Bisnis — Pemetaan Skenario IF-THEN

### 2.1 Skenario Kegagalan yang Belum Ditangani

| # | IF (Kondisi) | THEN (Yang Terjadi Sekarang) | SHOULD (Yang Seharusnya) |
|---|---|---|---|
| 1 | Server restart saat scraping berjalan | Data in-memory hilang, queue stuck "running" selamanya | Saat startup, scan queue: ubah semua "running" → "pending" (auto-recovery) |
| 2 | Internet mati di tengah scraping | Thread hang sampai timeout (300 detik) | Timeout 30 detik + retry 1x + emit pesan jelas ke user: "Koneksi terputus" |
| 3 | File CSV sedang dibuka di Excel saat write | `safe_write_csv` retry 3x → simpan ke `_backup` | Kirim notifikasi real-time ke dashboard: "File sedang dibuka program lain" |
| 4 | Apify mengembalikan format tak terduga | Komentar hilang tanpa pesan error yang jelas | Validasi schema + log lengkap ke dashboard + simpan raw untuk audit |
| 5 | Dua user klik "Ambil Data" bersamaan | Yang kedua mendapat HTTP 409 (text error saja) | UI: disable tombol + pesan visual "Ada proses yang sedang berjalan" |
| 6 | Semua token API habis di tengah proses | Proses berhenti, komentar yang sudah diambil tersimpan | Tambahkan notifikasi visual (lonceng merah) di navbar "Token habis" + email alert |
| 7 | Rate limit tercapai (4/4 minggu ini) | Tombol "Ambil Data" return 429, user bingung | Tombol berubah menjadi abu-abu + teks: "Kuota minggu ini sudah habis" |
| 8 | CSV data tumbuh >100MB | Pandas `read_csv` jadi lambat, server bisa hang | Migrasi ke SQLite (minimal) atau PostgreSQL |
| 9 | User menghapus akun yang sedang di-queue | Queue tetap coba scrape akun yang dihapus | Cascade: hapus akun → hapus queue pending terkait |
| 10 | Toggle "Aktif" tapi tidak ada akun terdaftar | Scheduler jalan percuma, buang resource | Cek: jika 0 akun aktif → tampilkan peringatan + jangan start scheduler |

### 2.2 Optimasi Penghematan API Call

| Strategi | Detail | Estimasi Hemat |
|---|---|---|
| **Caching Response** | Simpan hash response terakhir. Jika Instagram belum update (comment_count sama) → skip scrape detail | 30-50% API call |
| **Smart Scheduling** | Jangan scrape akun yang sudah di-scrape <24 jam lalu (kecuali urgent) | 20% API call |
| **Batch Request** | Gabungkan beberapa post URL dalam 1 API call Apify (field `directUrls` sudah support array) | 40% API call |
| **Deduplication Layer** | Bloom filter untuk cek komentar yang sudah ada sebelum memanggil API | Hindari re-fetch |
| **Conditional Scrape** | Bandingkan `comment_count_last_seen` sebelum scrape. Jika tidak berubah → skip | 50% API call |

### 2.3 Ketahanan Data & Persistensi

**Masalah Kritis**: Seluruh data disimpan di file CSV. Ini memiliki risiko serius:

| Risiko | Dampak |
|---|---|
| Race condition (2 proses tulis bersamaan) | Data corrupt / baris hilang |
| Tidak ada index | Pencarian lambat di >10.000 baris |
| Tidak ada transaksi (rollback) | Gagal di tengah = data setengah jadi |
| File terkunci oleh Excel | Write gagal → data hilang |

**Solusi Bertahap**:
1. **Fase 1 (Cepat)**: Migrasi ke **SQLite** — file-based, zero config, tapi mendukung transaksi dan index.
2. **Fase 2 (Production)**: Migrasi ke **PostgreSQL** — untuk multi-user dan concurrent access.

---

## 🎨 BAGIAN 3: UI/UX — Humanisasi untuk Pengguna Non-IT

### 3.1 Terjemahan Istilah Teknis

| Istilah Sekarang | Masalah | Ganti Menjadi |
|---|---|---|
| "API Calls" | Mitra tidak tahu apa itu API | "Jumlah Akses Data" |
| "Panggilan API hari ini" | Teknis | "Data diambil hari ini" |
| "Biaya minggu ini: $0.064" | Mitra pikirnya dolar | "Estimasi Biaya: Rp 1.024" (auto konversi) |
| "Tier 2: maks 5 post/akun" | Mitra bingung tier apa | "Pengambilan ke-2 minggu ini (hemat data)" |
| "Status Token API: Active" | Mitra tidak tahu token | "Status Koneksi: Tersambung ✅" |
| "Rate Limit" | Jargon developer | "Batas Penggunaan Mingguan" |
| "Queue" | Jargon developer | "Antrean Proses" |
| "HTTP 429" | Error code | "Kuota minggu ini sudah habis. Coba lagi minggu depan." |

### 3.2 Perbaikan Visual & Interaksi

| Perbaikan | Detail |
|---|---|
| **Progress Bar Berlapis** | Ganti log teks panjang → progress bar 3 langkah (Mencari Akun → Mengunduh Komentar → Selesai) dengan persentase (%) |
| **Empty State yang Ramah** | Saat belum ada data, tampilkan ilustrasi + teks "Klik tombol hijau di atas untuk mulai" — bukan tabel kosong |
| **Tooltip Penjelasan** | Setiap angka metrik harus punya ikon ℹ️ yang jika di-hover/klik menampilkan penjelasan singkat |
| **Tombol Kondisional** | Jika kuota habis → tombol berubah abu-abu + teks berubah. Bukan muncul error setelah diklik |
| **Warna Bermakna** | Hijau = aman, Kuning = peringatan, Merah = darurat. Jangan gunakan warna untuk dekorasi |
| **Konfirmasi Tindakan Berbahaya** | "Reset Data" harus muncul dialog: "Apakah Anda yakin? Semua data akan dihapus permanen." |

### 3.3 Fitur Baru untuk Mitra

| Fitur | Prioritas | Deskripsi |
|---|---|---|
| **Pencarian & Filter Komentar** | 🔴 Tinggi | Mitra bisa cari "banjir" di tabel komentar tanpa download CSV |
| **Grafik Tren Keluhan** | 🔴 Tinggi | Line chart: jumlah keluhan per kategori per minggu (banjir, macet, sampah) |
| **Sistem Peringatan Dini** | 🟡 Sedang | Lonceng notifikasi jika ada lonjakan keluhan >20 dalam 24 jam |
| **Export PDF Laporan** | 🟡 Sedang | Tombol "Buat Laporan" → generate PDF ringkasan bulanan |
| **Multi-User Role** | 🟢 Rendah | Admin vs Viewer (mitra tidak boleh bisa reset data) |

---

## 🖥️ BAGIAN 4: Efisiensi Server & Best Practice

### 4.1 Mengurangi Beban Server

| Masalah Saat Ini | Dampak | Solusi |
|---|---|---|
| `/api/dashboard-init` memuat SEMUA data sekaligus | Lambat jika data besar | Lazy loading: muat status dulu, tabel komentar dimuat saat scroll |
| Setiap refresh halaman memanggil 5+ endpoint | Boros bandwidth | Sudah ada `dashboard-init` (bagus!), tapi komentarnya juga dimuat terpisah — gabungkan |
| `load_csv_if_exists()` dipanggil berkali-kali | Re-read file untuk setiap request | Implementasi in-memory cache dengan TTL 30 detik |
| Pandas DataFrame untuk setiap operasi | Memori boros untuk data sederhana | Gunakan dict/list biasa untuk operasi ringan; Pandas hanya untuk analisis berat |
| `scrape_history.json` dibaca/tulis setiap sesi | I/O disk berulang | Cache di memori, flush ke disk setiap 5 menit atau saat shutdown |

### 4.2 Keamanan (Security Hardening)

| Risiko | Mitigasi |
|---|---|
| Token API ada di `.env` tapi tidak ada validasi CORS | Tambahkan whitelist CORS origin |
| Tidak ada autentikasi dashboard | Siapa saja yang tahu URL bisa akses + reset data | Tambahkan login sederhana (username/password) |
| `/api/reset` bisa dipanggil tanpa konfirmasi | Tambahkan header khusus atau token CSRF |
| Input `profile_url` tidak di-sanitize secara menyeluruh | Tambahkan regex validation yang lebih ketat + rate limit per IP |

### 4.3 Deployment Best Practice

| Aspek | Rekomendasi |
|---|---|
| Web Server | Jangan pakai Werkzeug di production! Gunakan **Gunicorn** (Linux) atau **Waitress** (Windows) |
| Process Manager | Gunakan **systemd** atau **Docker** agar server auto-restart jika crash |
| Reverse Proxy | Taruh **Nginx** di depan Flask untuk handle static files + SSL |
| Monitoring | Tambahkan health check endpoint `/api/health` untuk uptime monitoring |
| Logging | Rotasi log file (jangan biarkan log membengkak tanpa batas) |

---

## 📋 BAGIAN 5: Prioritas Implementasi

| Fase | Durasi | Deliverable |
|---|---|---|
| **Fase 1: Quick Wins** | 1-2 hari | Terjemahkan semua jargon IT → bahasa awam. Fix skenario IF-THEN #5, #7, #10 |
| **Fase 2: Refactoring** | 3-5 hari | Pecah `app.py`, `discover_posts.py`, `scraper.py` sesuai aturan 300 baris. Hapus duplikasi |
| **Fase 3: Data Layer** | 2-3 hari | Migrasi CSV → SQLite. Implementasi auto-recovery (skenario #1) |
| **Fase 4: UX Polish** | 2-3 hari | Progress bar visual, tooltip, pencarian komentar, grafik tren |
| **Fase 5: Production** | 3-5 hari | Autentikasi, Gunicorn/Docker, PostgreSQL, monitoring |

---

## ⚠️ User Review Required

Dokumen ini dirancang sebagai **Pekerjaan Rumah (PR)** komprehensif. Silakan tinjau dan beri tahu:
1. Apakah urutan prioritas fase di atas sudah sesuai dengan kebutuhan proyek Anda?
2. Fase mana yang ingin Anda kerjakan terlebih dahulu?
3. Apakah ada skenario IF-THEN lain yang terlewat dari pengalaman Anda di lapangan?
