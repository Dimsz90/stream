# StreamVault / MasdiFox

Streaming web app + PWA untuk browse film, serial, drama pendek, subtitle, dan stream extractor dalam satu tempat. Project ini memakai frontend HTML/CSS/JS ringan dengan backend Flask untuk API, proxy stream, subscription gate, payment callback, dan integrasi provider eksternal.

> Catatan: gunakan project ini secara bertanggung jawab dan pastikan penggunaan sumber konten/API mengikuti aturan layanan masing-masing provider.

## Fitur Utama

- Browse film dan serial menggunakan metadata TMDB.
- Tab Dracin untuk drama pendek dari beberapa platform seperti DramaBox, ReelShort, Melolo, dan Dramanova.
- Player HLS dengan dukungan `hls.js` dan Plyr.
- Continue watching berbasis `localStorage`.
- Subtitle search dan download via OpenSubtitles.
- Stream extractor untuk scan URL dan mengambil format video.
- Proxy stream dengan signing token saat subscription diaktifkan.
- Subscription gate dengan Supabase dan integrasi pembayaran Bayar.gg.
- PWA installable dengan manifest, service worker, ikon, dan screenshot.
- Siap deploy ke Railway, serta contoh konfigurasi Vercel.

## Struktur Project

```text
.
|-- index.html                 # SPA utama untuk film/serial/dracin
|-- dracin-player.html         # Player khusus drama pendek
|-- extractor.html             # Halaman extractor stream
|-- server.py                  # Backend Flask utama untuk Railway/local
|-- dev.py                     # Server development alternatif
|-- api/                       # Modul API dan handler serverless
|   |-- dracin.py              # Integrasi provider drama pendek
|   |-- imdb.py                # Resolver IMDb/stream
|   |-- tmdb.py                # Metadata dan stream payload TMDB
|   |-- subtitle.py            # OpenSubtitles
|   |-- payment.py             # Payment endpoint
|   `-- lib/                   # Config, cache, subscription, signing
|-- public/                    # PWA assets, service worker, icons
|-- requirements.txt           # Python dependencies
|-- railway.json               # Railway config
|-- procfile                   # Gunicorn start command
`-- DEPLOY_RAILWAY.md          # Detail deployment Railway
```

## Kebutuhan

- Python 3.10+ direkomendasikan.
- `pip`.
- Koneksi internet untuk API eksternal.
- TMDB API key jika ingin metadata film/serial berjalan penuh.
- Opsional: Supabase, Bayar.gg, OpenSubtitles, dan token provider Dracin.

## Instalasi Lokal

1. Clone atau buka folder project.

```bash
cd streaming
```

2. Buat virtual environment.

```bash
python -m venv .venv
```

3. Aktifkan virtual environment.

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
source .venv/bin/activate
```

4. Install dependency.

```bash
pip install -r requirements.txt
```

5. Buat file `.env`.

```env
PORT=8000
TMDB_API_KEY=your_tmdb_api_key
OPENSUBTITLES_API_KEY=your_opensubtitles_key

# Optional: Dracin / Captain upstream
CAPTAIN_BASE_URL=https://captain.sapimu.au
CAPTAIN_TOKEN=your_captain_token
DRACIN_TOKEN=your_dracin_token

# Optional: Dramanova PIN gate
DRAMANOVA_PIN=1234
DRAMANOVA_SECRET=change-this-secret

# Optional: subscription gate
REQUIRE_SUBSCRIPTION=false
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
SUBSCRIPTION_SECRET=change-this-secret

# Optional: payment
BAYAR_GG_API_KEY=your_bayar_gg_api_key
BAYAR_GG_WEBHOOK_SECRET=your_webhook_secret
BAYAR_GG_PAYMENT_METHOD=qris_bayar_gg
BAYAR_GG_CALLBACK_BASE_URL=https://your-domain.com

# Optional: remote stream worker
USE_STREAM_API_REMOTE=false
STREAM_API_REMOTE=https://your-remote-worker.example.com
```

6. Jalankan server.

```bash
python server.py
```

7. Buka aplikasi.

```text
http://localhost:8000
```

Jika memakai `dev.py`, default port biasanya `5000` kecuali `PORT` diubah.

## Konfigurasi Penting

### TMDB

Backend membaca `TMDB_API_KEY` dari environment. Di frontend, `index.html` juga memiliki konstanta `TMDB_KEY` untuk request discover/search langsung dari browser. Jika mengganti key, pastikan nilainya disesuaikan.

Untuk keamanan produksi, lebih baik semua request TMDB dipindahkan lewat backend agar API key tidak terekspos di browser.

### Subscription

Jika `REQUIRE_SUBSCRIPTION=true`, beberapa endpoint premium akan meminta token subscription melalui header:

```text
X-Subscription-Token: <token>
```

Token dibuat setelah user login/register melalui endpoint subscription.

### Supabase

Subscription memakai Supabase sebagai storage akun dan masa aktif. Service role key hanya boleh disimpan di server/environment, jangan pernah expose ke frontend.

Referensi schema dan langkah detail tersedia di [DEPLOY_RAILWAY.md](./DEPLOY_RAILWAY.md).

### Dramanova PIN

Platform Dramanova dapat dilindungi dengan:

```env
DRAMANOVA_PIN=1234
DRAMANOVA_SECRET=change-this-secret
```

User akan diminta PIN sebelum membuka tab/platform tersebut.

### Proxy Stream

Endpoint `/api/proxy` membantu memutar stream yang butuh header/origin tertentu. Saat subscription aktif, URL proxy akan ditandatangani dan memiliki masa berlaku.

## Endpoint API Ringkas

| Endpoint | Fungsi |
| --- | --- |
| `/api/health` | Health check backend |
| `/api/imdb` | Metadata/stream resolver berbasis IMDb |
| `/api/tmdb-stream` | Payload stream berbasis TMDB ID |
| `/api/get-video` | Resolver video alternatif |
| `/api/dracin/*` | Home, rank, search, detail, episode drama pendek |
| `/api/subtitle/search` | Cari subtitle |
| `/api/subtitle/download` | Download subtitle |
| `/api/scan` | Scan halaman untuk URL video |
| `/api/formats` | Ambil format video |
| `/api/download` | Download media |
| `/api/proxy` | Proxy stream/media |
| `/api/proxy/sign` | Buat signed proxy URL |
| `/api/subscription/*` | Login, register, status, plan, payment |
| `/api/payments/*` | Payment create/check/methods/webhook |

## PWA

Asset PWA berada di folder `public/`:

- `public/manifest.json`
- `public/sw.js`
- `public/icons/`
- `public/screenshots/`
- `public/js/orientation-lock.js`

Shortcut PWA tersedia untuk Film, Dracin, dan Extractor.

## Deploy ke Railway

Railway akan membaca project sebagai Python service. Start command yang disarankan:

```bash
python server.py
```

Atau production dengan Gunicorn:

```bash
gunicorn -w 1 --threads 8 -b 0.0.0.0:$PORT server:app --timeout 120
```

Jika memakai fitur `/api/proxy-browser`, install browser Playwright di build step:

```bash
pip install -r requirements.txt
playwright install chromium
```

Untuk panduan lebih lengkap, lihat [DEPLOY_RAILWAY.md](./DEPLOY_RAILWAY.md).

## Deploy ke Vercel

Project menyediakan contoh konfigurasi di `vercel.json.example`. Jika ingin deploy ke Vercel:

1. Salin `vercel.json.example` menjadi `vercel.json`.
2. Isi environment variable yang diperlukan di dashboard Vercel.
3. Pastikan route API yang dipakai cocok dengan mode serverless Vercel.

Catatan: Railway lebih cocok untuk fitur yang butuh proses backend panjang, proxy streaming, dan Playwright.

## Development Notes

- Jangan commit file `.env`.
- Jangan expose `SUPABASE_SERVICE_ROLE_KEY`, `SUBSCRIPTION_SECRET`, atau token provider ke frontend.
- File `index.html` saat ini cukup besar karena membawa UI, state, dan logic client dalam satu file.
- Untuk produksi jangka panjang, pertimbangkan memecah frontend menjadi modul agar lebih mudah dirawat.
- Jika stream gagal, cek endpoint `/api/debug`, `/api/health`, dan log server.

## Troubleshooting

### TMDB tidak memuat data

- Pastikan `TMDB_API_KEY` valid.
- Cek juga konstanta `TMDB_KEY` di `index.html`.
- Pastikan browser/server punya akses internet.

### Dracin kosong atau gagal load

- Cek `CAPTAIN_BASE_URL`, `CAPTAIN_TOKEN`, atau `DRACIN_TOKEN`.
- Coba ganti platform antara DramaBox, ReelShort, Melolo, dan Dramanova.
- Jika Dramanova, pastikan PIN benar.

### Subtitle gagal

- Pastikan `OPENSUBTITLES_API_KEY` atau `OS_API_KEY` sudah diisi.
- Cek limit/rate limit dari OpenSubtitles.

### Proxy stream gagal

- Pastikan URL stream valid dan belum expired.
- Jika `REQUIRE_SUBSCRIPTION=true`, pastikan request membawa token subscription.
- Untuk fallback browser, pastikan Playwright Chromium sudah terinstall.

## Keamanan

- Simpan seluruh secret di environment variable.
- Gunakan `REQUIRE_SUBSCRIPTION=true` jika endpoint premium tidak boleh publik.
- Jangan menaruh service role key Supabase di file frontend.
- Rotasi key jika pernah terlanjur terekspos.
- Batasi akses dashboard provider dan payment webhook.

## Lisensi

Belum ada lisensi eksplisit di repository ini. Tambahkan file `LICENSE` jika project akan dibagikan secara publik.
