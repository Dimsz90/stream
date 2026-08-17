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

def _resolve_best_stream_url(episode_url: str) -> tuple[str, str]:
    """
    Resolve direct video URL terbaik dari episode Samehadaku.

    Returns:
        (stream_url, ext) tuple. ext adalah 'mp4', 'm3u8', dll.
    """
    try:
        try:
            from api.anime import SamehadakuScraper
        except ImportError:
            from anime import SamehadakuScraper

        scraper = SamehadakuScraper()
        result = scraper.get_stream_and_download(episode_url)
        if not result:
            return "", "mp4"

        # Prioritas 1: Server yang namanya mengandung 'wibufile' atau 'wibu'
        for server in (result.get("servers") or []):
            name = (server.get("name") or "").lower()
            if "wibufile" in name or "wibu" in name:
                srv_url = server.get("direct_url") or server.get("url") or ""
                srv_type = (server.get("direct_type") or "mp4").lower()
                if srv_url:
                    logger.info(f"[Archiver] Menggunakan server prioritas (Wibufile): {server.get('name')}")
                    return srv_url, srv_type

        # Prioritas 2: direct URL (mp4 lebih baik dari m3u8 untuk download)
        direct_url = result.get("direct_url") or ""
        direct_type = (result.get("direct_type") or "mp4").lower()

        # Cari server dengan direct mp4
        for server in (result.get("servers") or []):
            srv_url = server.get("direct_url") or ""
            srv_type = (server.get("direct_type") or "").lower()
            if srv_url and srv_type == "mp4":
                return srv_url, "mp4"

        if direct_url:
            return direct_url, direct_type

        # Fallback ke m3u8 kalau tidak ada mp4
        for server in (result.get("servers") or []):
            srv_url = server.get("direct_url") or ""
            srv_type = (server.get("direct_type") or "").lower()
            if srv_url and srv_type in ("m3u8", "mp4"):
                return srv_url, srv_type

        return direct_url, direct_type
    except Exception as exc:
        logger.error(f"[Archiver] resolve stream gagal: {exc}")
        return "", "mp4"


def _download_with_ytdlp(stream_url: str, out_dir: str, episode_slug: str) -> tuple[str, str]:
    """
    Download video dengan yt-dlp ke out_dir.

    Returns:
        (filepath, ext) tuple. filepath kosong jika gagal.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("[Archiver] yt-dlp tidak terinstall")
        return "", "mp4"

    out_template = os.path.join(out_dir, f"{episode_slug}.%(ext)s")
    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "socket_timeout": 30,
        "retries": 3,
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
            filepath, ext = _download_with_ytdlp(stream_url, tmpdir, episode_slug)

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
