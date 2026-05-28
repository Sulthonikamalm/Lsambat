# SurabayaSambat v2 — Live Comment Monitor Demo

Sistem incremental monitoring komentar Instagram untuk penelitian analisis keluhan warga Surabaya.

## Cara Menjalankan

### 1. Install dependencies

```bash
cd surabayasambat_demo_monitoring
pip install -r requirements.txt
```

### 2. Konfigurasi token Apify

Copy `.env.example` ke `.env` dan isi token Apify:

```bash
cp .env.example .env
```

Edit `.env`, isi minimal 1 token:
```
APIFY_TOKEN_1=apify_api_xxxxx
```

### 3. Jalankan server

```bash
python backend/app.py
```

### 4. Buka dashboard

Buka browser: **http://localhost:5000**

## Alur Demo

1. **Initialize Baseline** — Catat semua komentar lama
2. **Stabilize Baseline** — Pastikan komentar lama tidak bocor ke dataset (1-3x)
3. **Start Official Monitoring** — Mulai deteksi komentar baru
4. **Post komentar baru** di Instagram
5. **Run Once** — Buktikan sistem mendeteksi komentar baru
6. **Download CSV** — Dataset hanya berisi komentar baru

## Mode Sistem

| Mode | Komentar baru masuk CSV? |
|------|--------------------------|
| Idle | - |
| Warm-up | TIDAK (hanya ke baseline) |
| Official Monitoring | YA |
| Stopped | - |
