"""
api/anime_archiver.py
Pipeline untuk download episode baru dari Samehadaku dan upload ke Cloudflare R2.

Flow:
1. Cek tabel r2_episodes di Supabase → return R2 URL kalau sudah ada (status=done)
2. Download video via yt-dlp ke temp dir
3. Upload ke R2 dengan key: anime/{anime_slug}/{episode_slug}.{ext}
4. Simpan metadata di Supabase r2_episodes
5. Cron: iterate bookmark → cek episode baru → trigger download_and_archive()

SQL untuk table r2_episodes (jalankan di Supabase SQL editor):
    CREATE TABLE IF NOT EXISTS r2_episodes (
        id            BIGSERIAL PRIMARY KEY,
        episode_url   TEXT NOT NULL,
        anime_url     TEXT NOT NULL,
        r2_key        TEXT NOT NULL DEFAULT '',
        r2_url        TEXT,
        file_size     BIGINT,
        duration      INT,
        quality       TEXT,
        status        TEXT NOT NULL DEFAULT 'pending',
        error_msg     TEXT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT r2_episodes_episode_url_key UNIQUE (episode_url)
    );
    CREATE INDEX IF NOT EXISTS r2_episodes_anime_url_idx ON r2_episodes (anime_url);
    CREATE INDEX IF NOT EXISTS r2_episodes_status_idx ON r2_episodes (status);
"""

from __future__ import annotations

import os
import re
import sys
import time
import logging
import tempfile
import threading

logger = logging.getLogger(__name__)

# ── Supabase helpers ─────────────────────────────────────────────────────────

try:
    from api.lib.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
except ImportError:
    try:
        from lib.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
    except ImportError:
        SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
        SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

ANIME_CRON_SECRET = os.environ.get("ANIME_CRON_SECRET", "").strip()

# Download hanya dilakukan jika R2 sudah dikonfigurasi
try:
    from api.r2_storage import upload_to_r2, get_public_url, is_configured as r2_is_configured
except ImportError:
    from r2_storage import upload_to_r2, get_public_url, is_configured as r2_is_configured

# Semaphore agar tidak ada lebih dari N download serentak
_download_semaphore = threading.Semaphore(2)

# Set untuk track episode yang sedang dalam proses download (hindari duplikat job)
_in_progress: set[str] = set()
_in_progress_lock = threading.Lock()


def _sb_headers(prefer: str = "") -> dict:
    h = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _supabase(method: str, table: str, *, params=None, payload=None, prefer: str = ""):
    import requests
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        raise RuntimeError("Supabase belum dikonfigurasi")
    resp = requests.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(prefer),
        params=params,
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase {table}: HTTP {resp.status_code} {resp.text[:300]}")
    if not resp.text:
        return []
    try:
        return resp.json()
    except ValueError:
        return []


# ── Slug helpers ─────────────────────────────────────────────────────────────

def _slugify(text: str, max_len: int = 80) -> str:
    """Buat slug URL-safe dari teks."""
    text = re.sub(r"https?://[^/]+", "", text)   # hapus domain
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    text = text.strip("-")
    return text[:max_len] or "episode"


def _r2_key(anime_url: str, episode_url: str, ext: str = "mp4") -> str:
    """
    Buat R2 key dari URL anime dan episode.
    Format: anime/{anime_slug}/{episode_slug}.{ext}
    """
    anime_slug = _slugify(anime_url)
    episode_slug = _slugify(episode_url)
    return f"anime/{anime_slug}/{episode_slug}.{ext}"


# ── Supabase r2_episodes CRUD ────────────────────────────────────────────────

def get_r2_episode(episode_url: str) -> dict | None:
    """
    Cek Supabase apakah episode sudah ada di R2.

    Returns:
        Dict row dari r2_episodes jika ditemukan, None jika belum ada.
    """
    try:
        rows = _supabase(
            "GET",
            "r2_episodes",
            params={
                "episode_url": f"eq.{episode_url}",
                "select": "*",
                "limit": "1",
            },
        )
        return rows[0] if rows else None
    except Exception as exc:
        logger.warning(f"[Archiver] get_r2_episode gagal: {exc}")
        return None


def _upsert_r2_episode(episode_url: str, anime_url: str, status: str, **kwargs):
    """Upsert row di tabel r2_episodes."""
    payload = {
        "episode_url": episode_url,
        "anime_url": anime_url,
        "status": status,
        "updated_at": "now()",
        **kwargs,
    }
    try:
        _supabase(
            "POST",
            "r2_episodes",
            params={"on_conflict": "episode_url"},
            payload=payload,
            prefer="resolution=merge-duplicates",
        )
    except Exception as exc:
        logger.error(f"[Archiver] upsert_r2_episode gagal: {exc}")


# ── Download + Upload pipeline ───────────────────────────────────────────────

WIBUFILE_HOSTS = ("wibufile.com", "api.wibufile.com", "s0.wibufile.com", "cdn.wibufile.com")


def _is_wibufile_url(url: str) -> bool:
    """Return True jika URL berasal dari domain Wibufile."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in WIBUFILE_HOSTS)
    except Exception:
        return False


def _resolve_best_stream_url(episode_url: str) -> tuple[str, str]:
    """
    Resolve direct video URL dari Wibufile saja — skip Blogger, Mega, VIP.

    Returns:
        (stream_url, ext) tuple. ext adalah 'mp4', 'm3u8', dll.
    """
    try:
        try:
            from api.anime import SamehadakuScraper
        except ImportError:
            from anime import SamehadakuScraper
        from urllib.parse import urlparse
        import html as html_lib

        scraper = SamehadakuScraper()
        soup = scraper._get_soup(episode_url)
        if not soup:
            return "", "mp4"

        opts = soup.select('.east_player_option, [id*="player-option"]')
        ajax_url = f"{scraper.base_url}/wp-admin/admin-ajax.php"
        seen_option_keys: set = set()

        wibufile_candidates: list[tuple[str, str]] = []  # (direct_url, quality_tag)

        for opt in opts:
            post_id = opt.get('data-post')
            nume = opt.get('data-num') or opt.get('data-nume')
            type_val = opt.get('data-type', 'schtml')
            server_name = opt.get_text(' ', strip=True).lower()

            if not post_id or not nume:
                continue

            option_key = (str(post_id), str(nume), str(type_val))
            if option_key in seen_option_keys:
                continue
            seen_option_keys.add(option_key)

            # Hanya proses server yang namanya mengandung 'wibu'
            if 'wibu' not in server_name:
                continue

            try:
                headers = scraper.headers.copy()
                headers['X-Requested-With'] = 'XMLHttpRequest'
                headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
                headers['Accept'] = '*/*'
                headers['Origin'] = scraper.base_url
                headers['Referer'] = episode_url

                res = scraper.scraper.post(
                    ajax_url,
                    data={'action': 'player_ajax', 'post': post_id, 'nume': nume, 'type': type_val},
                    headers=headers,
                    timeout=10,
                )
                if res.status_code != 200:
                    continue

                from bs4 import BeautifulSoup
                iframe_soup = BeautifulSoup(res.text, 'html.parser')

                # Cari direct URL: cek tag <source> atau <video> terlebih dahulu
                for tag in iframe_soup.find_all(['source', 'video'], src=True):
                    src = (tag.get('src') or '').strip()
                    if src and _is_wibufile_url(src) and src.endswith('.mp4'):
                        quality = server_name
                        wibufile_candidates.append((src, quality))

                # Cek iframe src (embed player wibufile)
                iframe = iframe_soup.find('iframe')
                if iframe:
                    src = html_lib.unescape((iframe.get('src') or iframe.get('data-src') or '')).strip()
                    if src.startswith('//'):
                        src = 'https:' + src
                    if _is_wibufile_url(src) or 'wibufile' in src:
                        # Kalau direct mp4
                        if src.endswith('.mp4'):
                            wibufile_candidates.append((src, server_name))
                        else:
                            # Coba fetch iframe untuk ambil source mp4
                            try:
                                iframe_res = scraper.scraper.get(src, headers=headers, timeout=10)
                                if iframe_res.status_code == 200:
                                    inner = BeautifulSoup(iframe_res.text, 'html.parser')
                                    for tag in inner.find_all(['source', 'video'], src=True):
                                        mp4_url = (tag.get('src') or '').strip()
                                        if mp4_url and mp4_url.endswith('.mp4'):
                                            wibufile_candidates.append((mp4_url, server_name))
                                            break
                                    # Coba cari juga dari script atau JSON
                                    for script in inner.find_all('script'):
                                        text = script.string or ''
                                        mp4_match = re.search(r'(https?://[^\s"\']+\.mp4)', text)
                                        if mp4_match and _is_wibufile_url(mp4_match.group(1)):
                                            wibufile_candidates.append((mp4_match.group(1), server_name))
                                            break
                            except Exception as inner_exc:
                                logger.warning(f"[Archiver] Gagal fetch Wibufile embed: {inner_exc}")

            except Exception as exc:
                logger.warning(f"[Archiver] Gagal proses Wibufile server '{server_name}': {exc}")
                continue

        if wibufile_candidates:
            # Prioritas: pilih yang nama servernya mengandung 'fullhd' atau '1080'
            # agar kualitas tertinggi yang diarsipkan
            for url, quality in wibufile_candidates:
                if any(q in quality for q in ('fullhd', '1080')):
                    logger.info(f"[Archiver] Wibufile 1080p: {url}")
                    return url, "mp4"
            # Kalau tidak ada 1080p, ambil yang pertama (biasanya terbaik dari scraping)
            url, quality = wibufile_candidates[0]
            logger.info(f"[Archiver] Wibufile ({quality}): {url}")
            return url, "mp4"

        # Fallback: jika tidak ada Wibufile sama sekali, gunakan get_stream_and_download biasa
        logger.warning("[Archiver] Tidak ada Wibufile ditemukan, fallback ke scraper umum")
        result = scraper.get_stream_and_download(episode_url)
        if not result:
            return "", "mp4"

        direct_url = result.get("direct_url") or ""
        direct_type = (result.get("direct_type") or "mp4").lower()
        for server in (result.get("servers") or []):
            srv_url = server.get("direct_url") or ""
            srv_type = (server.get("direct_type") or "mp4").lower()
            if srv_url and srv_type == "mp4":
                return srv_url, "mp4"

        return direct_url, direct_type
    except Exception as exc:
        logger.error(f"[Archiver] resolve stream gagal: {exc}")
        return "", "mp4"


def _fmt_speed(speed_bps: float | None) -> str:
    """Format kecepatan download menjadi string yang mudah dibaca."""
    if not speed_bps:
        return "?"
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if speed_bps < 1024:
            return f"{speed_bps:.1f} {unit}"
        speed_bps /= 1024
    return f"{speed_bps:.1f} TB/s"


def _download_with_ytdlp(
    stream_url: str,
    out_dir: str,
    episode_slug: str,
    episode_url: str = "",
    anime_url: str = "",
) -> tuple[str, str]:
    """
    Download video dengan yt-dlp ke out_dir.
    Progress di-update ke Supabase secara real-time via progress hook.

    Returns:
        (filepath, ext) tuple. filepath kosong jika gagal.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("[Archiver] yt-dlp tidak terinstall")
        return "", "mp4"

    out_template = os.path.join(out_dir, f"{episode_slug}.%(ext)s")

    # --- Progress hook: update Supabase setiap 5 detik ---
    _last_update: list[float] = [0.0]

    def _progress_hook(d: dict):
        now = time.time()
        if now - _last_update[0] < 5:   # throttle: update maks 1x per 5 detik
            return
        _last_update[0] = now

        status = d.get("status", "")
        if status == "downloading":
            try:
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                pct = round((downloaded / total) * 100, 1) if total > 0 else 0
                speed_str = _fmt_speed(d.get("speed"))
                eta = d.get("eta")

                logger.info(
                    f"[Archiver] Progress {episode_url}: {pct}% "
                    f"({downloaded:,}/{total:,} bytes) @ {speed_str} ETA {eta}s"
                )

                if episode_url and anime_url:
                    _upsert_r2_episode(
                        episode_url,
                        anime_url,
                        "downloading",
                        progress_pct=pct,
                        download_speed=speed_str,
                        eta_seconds=eta,
                    )
            except Exception as hook_err:
                logger.debug(f"[Archiver] progress hook error: {hook_err}")

        elif status == "finished":
            logger.info(f"[Archiver] Download selesai: {d.get('filename')}")
            if episode_url and anime_url:
                try:
                    _upsert_r2_episode(
                        episode_url,
                        anime_url,
                        "downloading",
                        progress_pct=100.0,
                        download_speed=None,
                        eta_seconds=0,
                    )
                except Exception:
                    pass

    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "socket_timeout": 30,
        "retries": 3,
        "progress_hooks": [_progress_hook],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://v2.samehadaku.how/",
        },
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(stream_url, download=True)
            if not info:
                return "", "mp4"

        # Cari file hasil download
        for fname in os.listdir(out_dir):
            fpath = os.path.join(out_dir, fname)
            if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "mp4"
                return fpath, ext
        return "", "mp4"
    except Exception as exc:
        logger.error(f"[Archiver] yt-dlp download gagal: {exc}")
        return "", "mp4"




def download_and_archive(episode_url: str, anime_url: str = "") -> dict:
    """
    Pipeline lengkap: resolve stream → download → upload R2 → update Supabase.

    Aman dipanggil dari thread background.

    Returns:
        Dict dengan 'status', 'r2_url', 'error' (jika gagal).
    """
    if not r2_is_configured():
        return {"status": "skipped", "reason": "R2 tidak dikonfigurasi"}

    # Cegah double download untuk episode yang sama
    with _in_progress_lock:
        if episode_url in _in_progress:
            return {"status": "already_queued"}
        _in_progress.add(episode_url)

    try:
        with _download_semaphore:
            return _do_download_and_archive(episode_url, anime_url)
    finally:
        with _in_progress_lock:
            _in_progress.discard(episode_url)


def _do_download_and_archive(episode_url: str, anime_url: str = "") -> dict:
    """Internal pipeline (dijalankan setelah semaphore acquired)."""
    # Resolve anime_url if empty
    if not anime_url:
        try:
            try:
                from api.anime import SamehadakuScraper
            except ImportError:
                from anime import SamehadakuScraper
            scraper = SamehadakuScraper()
            soup = scraper._get_soup(episode_url)
            if soup:
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/anime/" in href and not href.endswith("/anime/") and not "?" in href:
                        if href.startswith("/"):
                            href = scraper.base_url.rstrip("/") + href
                        anime_url = href
                        break
        except Exception as e:
            logger.warning(f"Gagal mendeteksi parent anime_url dari {episode_url}: {e}")
    
    if not anime_url:
        # Fallback agar key name tetap teratur
        anime_url = episode_url.rsplit("-episode-", 1)[0] if "-episode-" in episode_url else episode_url

    # Cek apakah sudah ada di DB
    existing = get_r2_episode(episode_url)
    if existing:
        status = existing.get("status", "")
        if status == "done":
            r2_url = existing.get("r2_url") or get_public_url(existing.get("r2_key", ""))
            return {"status": "already_done", "r2_url": r2_url}
        if status == "downloading":
            return {"status": "already_downloading"}

    # Mark sebagai "downloading"
    _upsert_r2_episode(episode_url, anime_url, "downloading")
    logger.info(f"[Archiver] Mulai download: {episode_url} dengan anime: {anime_url}")

    try:
        # 1. Resolve stream URL
        stream_url, detected_ext = _resolve_best_stream_url(episode_url)
        if not stream_url:
            _upsert_r2_episode(episode_url, anime_url, "error", error_msg="Tidak ada stream URL yang bisa didownload")
            return {"status": "error", "error": "No stream URL"}

        # 2. Download ke temp dir
        episode_slug = _slugify(episode_url)
        with tempfile.TemporaryDirectory(prefix="anime_archive_") as tmpdir:
            filepath, ext = _download_with_ytdlp(stream_url, tmpdir, episode_slug, episode_url, anime_url)

            if not filepath or not os.path.exists(filepath):
                _upsert_r2_episode(episode_url, anime_url, "error", error_msg="Download gagal: file tidak ditemukan")
                return {"status": "error", "error": "Download failed"}

            file_size = os.path.getsize(filepath)
            if file_size < 1024 * 100:  # < 100 KB — kemungkinan error page
                _upsert_r2_episode(episode_url, anime_url, "error", error_msg=f"File terlalu kecil ({file_size} bytes)")
                return {"status": "error", "error": "File too small"}

            # 3. Determine R2 key & MIME type
            content_type = "video/mp4" if ext == "mp4" else "application/vnd.apple.mpegurl" if ext == "m3u8" else f"video/{ext}"
            r2_key = _r2_key(anime_url, episode_url, ext)

            # 4. Upload ke R2
            logger.info(f"[Archiver] Uploading ke R2: {r2_key} ({file_size:,} bytes)")
            ok = upload_to_r2(filepath, r2_key, content_type)

        if not ok:
            _upsert_r2_episode(episode_url, anime_url, "error", error_msg="Upload ke R2 gagal")
            return {"status": "error", "error": "R2 upload failed"}

        # 5. Simpan ke Supabase dengan status done
        r2_url = get_public_url(r2_key)
        _upsert_r2_episode(
            episode_url,
            anime_url,
            "done",
            r2_key=r2_key,
            r2_url=r2_url,
            file_size=file_size,
            quality=ext,
            error_msg=None,
        )

        logger.info(f"[Archiver] Selesai: {episode_url} -> {r2_url}")
        return {"status": "done", "r2_url": r2_url, "r2_key": r2_key, "file_size": file_size}

    except Exception as exc:
        err_msg = str(exc)[:400]
        logger.error(f"[Archiver] Exception untuk {episode_url}: {err_msg}")
        _upsert_r2_episode(episode_url, anime_url, "error", error_msg=err_msg)
        return {"status": "error", "error": err_msg}


def trigger_background_archive(episode_url: str, anime_url: str = ""):
    """
    Spawn background thread untuk download_and_archive.
    Non-blocking — aman dipanggil dari request handler.
    """
    if not r2_is_configured():
        return
    t = threading.Thread(
        target=download_and_archive,
        args=(episode_url, anime_url),
        name=f"archive-{episode_url[-30:]}",
        daemon=True,
    )
    t.start()


# ── Cron: check all bookmarks for new episodes ───────────────────────────────

def _episode_number_float(episode: dict) -> float:
    """Parse episode number sebagai float untuk sorting."""
    val = str(episode.get("number") or episode.get("title") or "")
    m = re.search(r"(\d+(?:\.\d+)?)", val)
    try:
        return float(m.group(1)) if m else -1
    except (ValueError, TypeError):
        return -1


def _episodes_latest_first(episodes: list) -> list:
    return sorted(
        (ep for ep in (episodes or []) if isinstance(ep, dict)),
        key=_episode_number_float,
        reverse=True,
    )


def run_archiver_cron() -> dict:
    """
    Cek semua bookmark di Supabase dan download episode baru ke R2.

    Dipanggil oleh endpoint POST /api/anime/archive/cron.
    """
    if not r2_is_configured():
        return {"status": "skipped", "reason": "R2 tidak dikonfigurasi"}

    try:
        bookmarks = _supabase(
            "GET",
            "anime_bookmarks",
            params={"select": "*"},
        )
    except Exception as exc:
        return {"status": "error", "error": f"Gagal load bookmarks: {exc}"}

    checked = 0
    queued = 0
    skipped = 0
    errors = []

    try:
        from api.anime import SamehadakuScraper
    except ImportError:
        from anime import SamehadakuScraper

    scraper = SamehadakuScraper()

    for bookmark in bookmarks:
        anime_url = bookmark.get("anime_url", "")
        if not anime_url:
            continue
        checked += 1

        try:
            episodes = _episodes_latest_first(scraper.get_episodes(anime_url))
            if not episodes:
                continue

            last_ep_url = str(bookmark.get("last_episode_url") or "").strip()

            # Kumpulkan episode yang lebih baru dari last_episode_url
            new_episodes = []
            for ep in episodes:
                if ep.get("link") == last_ep_url:
                    break
                new_episodes.append(ep)

            # Jika semua episode dianggap "baru" (baseline belum di-set),
            # hanya ambil episode terbaru saja (1 episode pertama)
            if new_episodes and len(new_episodes) == len(episodes):
                new_episodes = episodes[:1]

            for ep in new_episodes:
                ep_url = str(ep.get("link") or "").strip()
                if not ep_url:
                    continue

                # Cek apakah sudah ada di R2
                existing = get_r2_episode(ep_url)
                if existing and existing.get("status") in ("done", "downloading"):
                    skipped += 1
                    continue

                # Trigger background download
                trigger_background_archive(ep_url, anime_url)
                queued += 1
                logger.info(f"[Archiver Cron] Queued: {ep_url}")

        except Exception as exc:
            errors.append({"anime_url": anime_url, "error": str(exc)[:300]})

    return {
        "status": "success",
        "checked": checked,
        "queued": queued,
        "skipped": skipped,
        "errors": errors,
    }
