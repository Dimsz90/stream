"""
api/subtitle.py
Pencarian dan download subtitle via OpenSubtitles REST API v1.
Menggunakan shared config dari api/lib/.
"""
import os, sys, requests

sys.path.insert(0, os.path.dirname(__file__))
from lib.config import OS_BASE, OS_HEADERS, HEADERS
from lib.cache import subtitle_cache


# ── Search ────────────────────────────────────────────────────────────────────
def search(
    imdb_id: str  = None,
    tmdb_id: str  = None,
    query: str    = None,
    lang: str     = "en",
    media_type: str = "movie",
    season: int = None,
    episode: int = None,
) -> dict:
    """
    Cari subtitle.
    Return dict: {"status": "success"|"error", "data": [...], "count": int}
    """
    # Cek cache
    cache_key = f"sub:{imdb_id or tmdb_id or query}:{lang}:{media_type}:s{season}:e{episode}"
    cached = subtitle_cache.get(cache_key)
    if cached:
        return cached

    params: dict = {"languages": lang}
    if media_type == "tv":
        params["type"] = "episode"
    else:
        params["type"] = media_type

    if media_type == "tv" and tmdb_id and season and episode:
        params["parent_tmdb_id"] = int(tmdb_id)
        params["season_number"] = int(season)
        params["episode_number"] = int(episode)
    elif imdb_id:
        params["imdb_id"] = imdb_id.replace("tt", "")
    elif tmdb_id:
        params["tmdb_id"] = int(tmdb_id)
    elif query:
        params["query"] = query
    else:
        return {"status": "error", "error": "imdb_id, tmdb_id, atau query wajib diisi", "data": [], "count": 0}

    try:
        r = requests.get(
            f"{OS_BASE}/subtitles",
            headers=OS_HEADERS,
            params=params,
            timeout=8,
        )
    except Exception as e:
        return {"status": "error", "error": f"Koneksi gagal: {e}", "data": [], "count": 0}

    if r.status_code == 401:
        return {
            "status": "error",
            "error":  "API key tidak valid atau belum dikonfigurasi. "
                      "Daftar di opensubtitles.com/en/consumers",
            "data": [], "count": 0,
        }
    if r.status_code == 429:
        return {"status": "error", "error": "Rate limit OpenSubtitles. Coba lagi nanti.", "data": [], "count": 0}
    if r.status_code != 200:
        return {"status": "error", "error": f"OpenSubtitles error: HTTP {r.status_code}", "data": [], "count": 0}

    results = r.json().get("data", [])
    out = []
    for item in results[:30]:
        attrs = item.get("attributes", {})
        files = attrs.get("files", [])
        if not files:
            continue
        f = files[0]
        out.append({
            "file_id":          f.get("file_id"),
            "file_name":        f.get("file_name", ""),
            "lang":             attrs.get("language", ""),
            "lang_name":        attrs.get("language", "").upper(),
            "downloads":        attrs.get("download_count", 0),
            "rating":           attrs.get("ratings", 0),
            "release":          attrs.get("release", ""),
            "hearing_impaired": attrs.get("hearing_impaired", False),
            "fps":              attrs.get("fps", 0),
            "upload_date":      attrs.get("upload_date", ""),
        })

    out.sort(key=lambda x: x["downloads"], reverse=True)
    result = {"status": "success", "data": out, "count": len(out)}

    # Cache hasilnya (30 menit)
    subtitle_cache.set(cache_key, result)
    return result


# ── Download ──────────────────────────────────────────────────────────────────
def get_download_url(file_id) -> tuple[str | None, str | None]:
    """
    Minta link download sementara dari OS (~1 jam valid).
    Return: (url, error_message)
    """
    try:
        r = requests.post(
            f"{OS_BASE}/download",
            headers=OS_HEADERS,
            json={"file_id": int(file_id), "sub_format": "srt"},
            timeout=8,
        )
        if r.status_code == 200:
            link = r.json().get("link")
            if link:
                return link, None
            return None, "Link kosong dari OpenSubtitles"
        if r.status_code == 406:
            return None, "Kuota download harian habis (5/hari untuk akun gratis)"
        if r.status_code == 401:
            return None, "API key tidak valid"
        return None, f"OpenSubtitles error: HTTP {r.status_code}"
    except Exception as e:
        return None, f"Koneksi gagal: {e}"


def fetch_srt(dl_url: str) -> tuple[str | None, str | None]:
    """
    Download konten .srt dari URL yang diberikan OS.
    Return: (srt_text, error_message)
    """
    try:
        r = requests.get(dl_url, headers=HEADERS, timeout=12)
        r.encoding = r.apparent_encoding or "utf-8"
        if r.status_code != 200:
            return None, f"Gagal download file: HTTP {r.status_code}"
        if len(r.text.strip()) < 10:
            return None, "File subtitle kosong"
        return r.text, None
    except Exception as e:
        return None, f"Koneksi gagal: {e}"


# ═══════════════════════════════════════════════
#  HTTP HANDLER (untuk Vercel router dispatch)
# ═══════════════════════════════════════════════
from http.server import BaseHTTPRequestHandler
import json
from urllib.parse import urlparse, parse_qs


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)

        # /api/subtitle/search
        if "search" in path:
            imdb_id    = (params.get("imdb_id", [None])[0] or "").strip() or None
            tmdb_id    = (params.get("tmdb_id", [None])[0] or "").strip() or None
            query      = (params.get("query", [None])[0] or "").strip() or None
            lang       = (params.get("lang", ["en"])[0] or "en").strip()
            media_type = (params.get("type", ["movie"])[0] or "movie").strip()
            season     = (params.get("season", [None])[0] or "").strip() or None
            episode    = (params.get("episode", [None])[0] or "").strip() or None

            result = search(
                imdb_id=imdb_id,
                tmdb_id=tmdb_id,
                query=query,
                lang=lang,
                media_type=media_type,
                season=season,
                episode=episode,
            )
            code = 200 if result["status"] == "success" else 503
            return self._send_json(result, code)

        # /api/subtitle/download
        if "download" in path:
            file_id = (params.get("file_id", [None])[0] or "").strip()
            if not file_id:
                return self._send_json({"error": "file_id wajib diisi"}, 400)

            dl_url, err = get_download_url(file_id)
            if err:
                return self._send_json({"error": err}, 500)

            srt_text, err = fetch_srt(dl_url)
            if err:
                return self._send_json({"error": err}, 500)

            body = srt_text.encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self._send_json({"error": "Route tidak ditemukan"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Subscription-Token")

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass
