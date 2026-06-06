<div align="center">

<img src="https://img.shields.io/badge/-📡_SurabayaSambat-1e293b?style=for-the-badge&labelColor=0f172a" alt="logo" height="80"/>

# SurabayaSambat v2

### *Monitoring Keluhan Masyarakat Surabaya — Berbasis Lokasi & Prioritas*

<p>
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-3.0-000000?style=flat-square&logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/Socket.IO-Realtime-010101?style=flat-square&logo=socket.io&logoColor=white" />
  <img src="https://img.shields.io/badge/Apify-Scraper-7B61FF?style=flat-square&logo=apify&logoColor=white" />
  <img src="https://img.shields.io/badge/Tests-63%2F63%20PASS-22c55e?style=flat-square" />
  <img src="https://img.shields.io/badge/Status-Demo%20Ready-0ea5e9?style=flat-square" />
</p>

<p><i>Sistem otomatis yang memantau komentar warga Surabaya di Instagram & Facebook,<br/>
menyaring keluhan berbasis kata-kunci, lokasi, dan prioritas akun — siap untuk handover ke mitra.</i></p>

<img src="https://user-images.githubusercontent.com/74038190/212284100-561aa473-3905-4a80-b561-0d28506553ee.gif" width="100%" />

</div>

---

## ✨ Highlights

<table>
<tr>
<td width="50%" valign="top">

### 🎯 Multi-Sumber Cerdas
- Instagram **+** Facebook dalam 1 sistem
- 5+ akun pantauan paralel
- Auto-deteksi platform dari URL

### 💰 Hemat Biaya by Design
- Multi-token rotation (round-robin)
- Skip post 0-komentar otomatis
- Filter relevansi keyword keluhan
- Tier budget mingguan

</td>
<td width="50%" valign="top">

### 📊 Skor Relevansi Transparan
- 4 faktor: keyword · prioritas · engagement · recency
- Score 0–100 dengan alasan terbuka (auditable)
- Label `high` / `medium` / `low`

### 🛡️ Tahan Gagal
- Auto-retry saat kuota habis (data tidak hilang)
- Dedup SHA-256 antar-run
- CSV-lock retry + backup
- Baseline tagging vs keluhan baru

</td>
</tr>
</table>

---

## 🎬 Dashboard Demo

<div align="center">

> Toggle ON → sistem auto-scrape tiap 2 jam · Live progress panel berdenyut · Status badge dinamis

```
┌─ ● Sedang berjalan…  [Mengambil komentar (50 postingan)]   2m 14s ┐
│  ████████████████████░░░░░░░░░░░░░░░░░░░░░░░░  46%               │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐ ┌─────────────────┐ │
│  │ DIPROSES │ │ KOMENTAR │ │ BARU/BASELINE│ │ AKSES DATA      │ │
│  │  23/50   │ │   415    │ │   35 / 380   │ │ 23 · Rp 11.776  │ │
│  └──────────┘ └──────────┘ └──────────────┘ └─────────────────┘ │
└───────────────────────────────────────────────────────────────────┘
```

</div>

---

## 🚀 Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Konfigurasi token Apify
```bash
cp .env.example .env
```

Edit `.env`, minimal 1 token (mode demo bisa hingga 5):
```env
APIFY_TOKEN_1=apify_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
APIFY_TOKEN_2=apify_api_yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
# ... s/d APIFY_TOKEN_5
```

### 3. Verifikasi token (opsional, tidak makan credit)
```bash
python tests/verify_tokens.py
```
<sub>📋 Output: `5/5 token valid, total kuota tersisa ~$25.0000`</sub>

### 4. Jalankan server
```bash
python backend/app.py
```

### 5. Buka dashboard
🌐 **http://localhost:5000**

---

## 🏗 Arsitektur

```
surabayasambat_demo_monitoring/
├── backend/
│   ├── app.py                    ⚙️  Entry point Flask + SocketIO
│   ├── config.py                 📦  Loader settings.yaml + .env
│   ├── scraper.py                🔌  Wrapper Apify (multi-token + failover)
│   │
│   ├── discovery/                🔎  Stage 1: cari postingan baru
│   │   ├── registry.py           ↳ load daftar akun
│   │   ├── fetcher.py            ↳ ambil + normalisasi dari Apify
│   │   ├── scoring.py            ↳ skor relevansi multi-faktor
│   │   └── tracking.py           ↳ deteksi new / changed posts
│   │
│   ├── post_queue.py             📋  Antrean post → komentar
│   ├── process_queue.py          ⚡  Stage 2: ambil komentar + tag baseline
│   │
│   ├── routes/                   🌐  Flask API endpoints
│   ├── services/                 🧠  Business logic (scrape, scheduler)
│   └── helpers/                  🛠️  Rate limiter, history
│
├── frontend/                     💻  Dashboard (Vanilla JS + SocketIO)
├── config/settings.yaml          ⚙️  Konfigurasi sistem
├── data/                         💾  CSV output (gitignored)
└── tests/acceptance_stage4.py    ✅  63 acceptance tests
```

**Flow inti:**
```
toggle ON → scheduler/manual → run_unified_scrape
              ↓                       ↓
         every 2 jam        ┌─→ discover_posts (per akun)
                            │         ↓
                            │   filter relevansi + 0-komentar
                            │         ↓
                            └─→ process_queue (komentar)
                                      ↓
                              tag baseline / baru
                                      ↓
                              dedup → raw_comments.csv
```

---

## 📊 Skema Relevansi Komentar (transparan, auditable)

| Faktor | Bobot | Detail |
|--------|-------|--------|
| **Keyword keluhan** | 0–40 | `banjir`, `jalan rusak`, `sampah`, `pdam`, `wargaku`, dst (~40 kata kunci Surabaya-spesifik) |
| **Prioritas akun** | 0–25 | Akun resmi Pemkot lebih tinggi dari komunitas |
| **Engagement** | 0–20 | Jumlah komentar = banyak warga merespons |
| **Recency** | 0–15 | Post baru lebih relevan dari yang lama |
| **TOTAL** | **0–100** | `≥60` HIGH · `30–59` MEDIUM · `<30` LOW |

> 💡 Setiap skor disimpan dengan **breakdown alasan** (kolom `relevance_reasons`) — bisa dijelaskan ke siapa saja.

---

## 🛡️ Baseline vs Keluhan Baru

Tiap komentar otomatis ditandai:

| Tag | Arti |
|-----|------|
| `is_baseline=true` | Komentar yang sudah ada di post **sebelum** kami mulai memantau (`comment_created_at < post.discovered_at`) |
| `is_baseline=false` | Komentar **baru** yang muncul setelah pemantauan dimulai |

> **Dataset riset bersih**: cukup filter `is_baseline=false` untuk dapat "hanya keluhan baru".  
> Semua komentar tetap disimpan (tidak dibuang) → tetap auditable.

---

## 💸 Estimasi Biaya Operasional

### Mode Demo (5 token free tier · 1 jam panen)
```
5 akun IG × 50 post × window 60 hari
  ↓ filter relevansi + 0-komentar
~95 API call total = ~$3 dari $25 budget
  ↓
~2.000–4.000 komentar tersimpan
```

### Mode Produksi (1 token berbayar · scrape mingguan)
```
3 akun × 10 post baru/minggu × 100 komentar
  ↓
~12 API call/minggu = ~$0.40 → $1.60/bulan
```

> 📉 Sistem **hanya menghitung** panggilan API yang sukses (HTTP 200/201).  
> Panggilan gagal karena kuota = tidak menghabiskan jatah rate-limit mingguan.

---

## ⚙️ Konfigurasi Utama

File: [`config/settings.yaml`](config/settings.yaml)

| Setting | Demo | Produksi | Catatan |
|---------|------|----------|---------|
| `token_envs` | 5 token | 1 token | Sistem otomatis round-robin |
| `rate_limit.max_scrapes_per_week` | 100 | 4 | Cap mingguan |
| `tier 1.posts_per_source` | 50 | 10 | Post per akun per scrape |
| `tier 1.comments_per_post` | 100 | 100 | Komentar per post |
| `post_discovery.min_comment_increase` | 1 | 1–2 | Re-queue post lama saat naik N komentar |
| `post_discovery.queue_only_relevant` | true | true | Skip post LOW relevance |
| `post_discovery.skip_zero_comments` | true | true | Skip post 0-komentar (hemat call) |
| `DEMO_INTERVAL_MINUTES` | 120 (2 jam) | — | Untuk produksi: switch ke `auto_scrape_day/hour` |

---

## 🧪 Quality Assurance

```bash
python tests/acceptance_stage4.py
```

```
================================================================
  HASIL: 63/63 checks PASS
================================================================
```

**14 skenario** mencakup:
- ✅ Discovery & dedup multi-akun
- ✅ Antrean prioritas + reason
- ✅ Skor relevansi multi-faktor
- ✅ Baseline tagging
- ✅ Auto-retry saat kuota habis
- ✅ Skip post 0-komentar
- ✅ Comment count deferred (tidak hilang saat gagal)
- ✅ Smoke test Flask end-to-end

> 🧬 Test pakai **FakeScraper** (mock Apify) → tidak makan credit.

---

## 📦 Data Output

| File | Isi |
|------|-----|
| `data/source_registry.csv` | Daftar akun pantauan (input) |
| `data/raw_instagram_posts.csv` | Postingan + skor relevansi + alasan |
| `data/post_queue.csv` | Antrean pengambilan komentar (audit) |
| `data/raw_comments.csv` | Komentar + tag baseline + kaitan post |
| `data/api_usage_log.json` | Log tiap panggilan API + biaya |
| `data/scrape_history.json` | Riwayat sesi scraping |

> 🔒 Semua file di `data/` di-`.gitignore` (kecuali `source_registry.csv` yang adalah input).

---

## 🐛 Bug Logika Tersembunyi yang Diperbaiki

Hasil **deep flow-analysis** menemukan & memperbaiki bug-bug yang **tidak memunculkan error** tapi membuat sistem menyimpang dari desain:

| # | Bug | Akibat sebelum | Status |
|---|-----|----------------|--------|
| 1 | Rate-limit menghitung scrape gagal | Sistem terkunci 4/4 padahal 0 data | ✅ Fixed |
| 2 | Auto-stop kuota tidak terpicu | Komentar gagal di-queue hilang permanen | ✅ Fixed |
| 3 | Komentar lama tercampur keluhan baru | Dataset riset kotor | ✅ Fixed (baseline tag) |
| 4 | Actor Facebook salah (pakai posts-scraper) | Komentar FB 0 | ✅ Fixed |
| 5 | Post 0-komentar tetap dipanggil | Boros call per post | ✅ Fixed |
| 6 | Comment count maju sebelum scrape | Sinyal kenaikan hilang saat gagal | ✅ Fixed |
| 7 | UI tidak menampilkan progress live | User kira sistem mati | ✅ Fixed |

---

## 🔒 Sekuriti

| Item | Status |
|------|--------|
| Token Apify | `.env` (di-`.gitignore`, tidak pernah ke git history) |
| Validasi audit | `git ls-files` + `git grep` membersih dari pattern token |
| `source_registry.csv` | Aman di-track (hanya berisi username publik) |
| CORS | Allow all (mode lokal/demo) — **ubah saat deploy publik** |

> ⚠️ **Sebelum handover ke mitra:** rotate semua token demo via Apify Console → Settings → Integrations.

---

## 🛣 Roadmap — PR untuk Setelah Demo (Transisi ke Mitra)

> Daftar perubahan **konkret** yang harus dilakukan saat sistem akan diserah-terimakan untuk operasional resmi mitra (Pemkot / Komunitas).  
> Tujuan: dari **mode demo blast** → **mode produksi konservatif**.

### PR-1 · 🔑 Sekuriti & Token Migration
- [ ] **Rotate** 5 token Apify free tier demo (di Apify Console → regenerate)
- [ ] Hapus 4 entry `APIFY_TOKEN_2..5` dari `.env`, sisakan `APIFY_TOKEN_1` (akun berbayar mitra)
- [ ] Update `settings.yaml`: kurangi `token_envs` jadi 1 entry
- [ ] Verifikasi: `python tests/verify_tokens.py` → `1/1 token valid`
- [ ] Audit ulang: `git ls-files | grep .env` harus kosong

### PR-2 · ⚙️ Switch Profil Demo → Produksi
- [ ] [`config/settings.yaml`](config/settings.yaml):
  - `rate_limit.max_scrapes_per_week`: **100 → 4**
  - `budget_tiers[0].posts_per_source`: **50 → 10**
  - `budget_tiers[0].comments_per_post`: tetap 100
  - `post_discovery.min_comment_increase`: 1 → **1 atau 2** (sesuai preferensi mitra)
- [ ] [`backend/services/scheduler_service.py`](backend/services/scheduler_service.py):
  - `DEMO_INTERVAL_MINUTES = 120` → ganti mekanisme jadi **mingguan** (pakai `schedule.auto_scrape_day` & `auto_scrape_hour` di `settings.yaml` yang sudah ada tapi belum dipakai)

### PR-3 · 📅 Aktifkan Scheduler Mingguan
- [ ] Implementasikan scheduler berbasis hari/jam (sudah ada slot config-nya):
  ```yaml
  schedule:
    auto_scrape_day: "monday"    # ← sudah ada
    auto_scrape_hour: 8          # ← sudah ada
    auto_scrape_enabled: true    # ← aktifkan
  ```
- [ ] Loop `_loop()` di [`scheduler_service.py`](backend/services/scheduler_service.py) cek `now.weekday() == target_day and now.hour == target_hour`
- [ ] Test: simulasi tanggal → verifikasi triggering

### PR-4 · 🗂 Dataset & Migration
- [ ] Putuskan: dataset demo **dilanjutkan** (mitra dapat baseline ~2k komentar) atau **direset** (mitra mulai fresh)
- [ ] Kalau lanjut: bersihkan `api_usage_log.json` & `scrape_history.json` agar counter biaya mitra mulai dari 0
- [ ] Kalau reset: jalankan `Reset` via dashboard

### PR-5 · 📝 Dokumentasi untuk Mitra
- [ ] Tambah section di README: "Untuk Mitra (Operasional)"
- [ ] Buat `docs/MITRA_GUIDE.md`:
  - Cara dapat akun Apify berbayar (signup, billing)
  - Cara isi `.env` saat ganti token
  - Cara tambah/hapus akun pantauan via dashboard
  - Cara backup/restore CSV
  - Cara monitor biaya & tahu kapan kuota mau habis
  - Troubleshooting umum (kuota habis, akun banned IG, dll)
- [ ] Sertakan **video screencast singkat** (5 menit) — opsional tapi sangat membantu

### PR-6 · 🌐 Production Hardening
- [ ] Ganti `app.config["SECRET_KEY"]` dari hardcoded → env var (`FLASK_SECRET_KEY`)
- [ ] CORS: dari `cors_allowed_origins="*"` → whitelist domain mitra
- [ ] Ganti `socketio.run(...)` (Werkzeug dev server) → **production WSGI** (gunicorn/eventlet)
- [ ] Tambah HTTPS reverse proxy (nginx) bila deploy ke server publik
- [ ] Tambah auth dasar untuk dashboard (Basic Auth atau OAuth)

### PR-7 · 📊 Analytics Lanjut (opsional, lifecycle berikutnya)
- [ ] Klasifikasi kategori keluhan (banjir/jalan/sampah/dll) via NLP
- [ ] Ekstraksi lokasi (kelurahan/kecamatan) dari teks komentar
- [ ] Visualisasi spasial peta sebaran keluhan
- [ ] Notifikasi otomatis ke OPD terkait saat ada lonjakan keluhan di topik/wilayah

---

## 🤝 Kontribusi

Project ini bagian dari penelitian akademik. Pull request & saran perbaikan dipersilakan via [GitHub Issues](https://github.com/Sulthonikamalm/Lsambat/issues).

Sebelum submit PR:
1. Pastikan `python tests/acceptance_stage4.py` → **63/63 PASS**
2. Tambah test baru untuk perilaku yang Anda ubah
3. Ikuti konvensi struktur folder (`backend/discovery/`, `backend/services/`, dll.)

---

## 📜 Lisensi

© 2026 Lab L-Sambat (lsambat__) — penelitian akademik untuk Surabaya.

---

<div align="center">

<sub>Dibangun dengan ❤️ untuk warga Surabaya · v2.0 · Demo-Ready</sub>

<sub>**63/63 tests PASS** · 5/5 token verified · Zero token leaks</sub>

</div>
