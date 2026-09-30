"""
api/imdb.py
IMDB info, streaming, dan video proxy.
Menggunakan shared library dari api/lib/.
"""
from http.server import BaseHTTPRequestHandler
import json, re, os, sys, requests
from urllib.parse import urlparse, parse_qs, quote, urljoin

# Pastikan api/lib bisa di-import
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from lib.config import HEADERS, VIDEO_SPOOF_HEADERS, OMDB_KEYS
from lib.cache import imdb_cache
from lib import vidgf

import traceback


BRIGHTPATH_ORIGIN = "https://nextgencloudfabric.com"

# tmstrd.justhd.tv memakai segment .html dan memblokir server-side proxy
DEPRIORITIZED_HOSTS = {"tmstrd.justhd.tv"}


def _is_vaplayer_stream(url: str) -> bool:
    """Deteksi URL Vaplayer CDN secara dynamic via path pattern."""
    try:
        from urllib.parse import urlparse as _up
        import re as _re
        path = _up(url).path
        return bool(
            _re.search(r'/[A-Za-z0-9]{5,}/(?:pl|cdnstr)/', path)
            or '/static/df/' in path
        )
    except Exception:
        return False

def _stream_spoof_origin(target_url: str) -> str:
    return BRIGHTPATH_ORIGIN if _is_vaplayer_stream(target_url) else ""


def _pick_vaplayer_stream(streams):
    if not streams:
        return None
    urls = [str(u or "").replace("\\/", "/") for u in streams if u]
    return urls[0] if urls else None



# ═══════════════════════════════════════════════
#  HELPERS — IMDB
# ═══════════════════════════════════════════════
def _normalize_vaplayer_streams(streams):
    if not isinstance(streams, list):
        return []

    urls = []
    seen = set()
    for item in streams:
        url = str(item or "").replace("\\/", "/").strip()
        if not url or not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def extract_imdb_id(raw: str):
    m = re.search(r"(tt\d{5,})", raw, re.I)
    if m: return m.group(1)
    n = re.search(r"\b(\d{5,})\b", raw)
    if n: return f"tt{n[1]}"
    return None


def get_movie_info(imdb_id: str) -> dict:
    # Cek cache dulu
    cached = imdb_cache.get(f"info:{imdb_id}")
    if cached:
        return cached

    info = {
        "imdb_id": imdb_id, "title": "", "year": "", "type": "movie",
        "poster": "", "description": "", "rating": "", "genre": "",
        "runtime": "", "imdb_url": f"https://www.imdb.com/title/{imdb_id}/",
    }

    for apikey in OMDB_KEYS:
        try:
            r = requests.get(
                f"http://www.omdbapi.com/?i={imdb_id}&apikey={apikey.strip()}",
                headers=HEADERS, timeout=5,
            )
            if r.status_code == 200:
                d = r.json()
                if d.get("Response") == "True":
                    info.update({
                        "title":       d.get("Title", ""),
                        "year":        d.get("Year", ""),
                        "type":        d.get("Type", "movie"),
                        "poster":      d.get("Poster", ""),
                        "description": d.get("Plot", ""),
                        "rating":      d.get("imdbRating", ""),
                        "genre":       d.get("Genre", ""),
                        "runtime":     d.get("Runtime", ""),
                    })
                    # Simpan ke cache (1 jam)
                    imdb_cache.set(f"info:{imdb_id}", info)
                    return info
        except Exception:
            continue
    return info


def get_fast_streams(imdb_id: str, media_type: str = "movie", season=None, episode=None, tmdb_id=None):
    cache_key = f"streams:{tmdb_id or imdb_id}:{media_type}"
    if media_type == "tv" and season and episode:
        cache_key += f":s{season}:e{episode}"

    cached = imdb_cache.get(cache_key)
    if cached:
        return cached

    api_url = "https://streamdata.vaplayer.ru/api.php"
    # Spoof headers yang sudah diverifikasi 200 OK
    spoof_headers = {
        "Origin":     "https://nextgencloudfabric.com",
        "Referer":    "https://nextgencloudfabric.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    }

    # Buat daftar attempt: prioritas tmdb param, fallback imdb param
    param_attempts = []

    if tmdb_id:
        # Attempt 1: pakai tmdb param (format yang berhasil 200 OK)
        p = {"tmdb": str(tmdb_id), "type": media_type}
        if media_type == "tv" and season and episode:
            p["s"] = season
            p["e"] = episode
        param_attempts.append(p)
        # Attempt 2: tmdb + season/episode versi alternatif
        if media_type == "tv" and season and episode:
            param_attempts.append({"tmdb": str(tmdb_id), "type": media_type,
                                    "season": season, "episode": episode})

    # Fallback: pakai imdb param
    p_imdb = {"imdb": imdb_id, "type": media_type}
    if media_type == "tv" and season and episode:
        p_imdb["s"] = season
        p_imdb["e"] = episode
    param_attempts.append(p_imdb)
    if media_type == "tv" and season and episode:
        param_attempts.append({"imdb": imdb_id, "type": media_type,
                                "season": season, "episode": episode})

    try:
        for attempt_params in param_attempts:
            r = requests.get(api_url, params=attempt_params, headers=spoof_headers, timeout=8)
            if r.status_code == 200:
                data = r.json()
                streams = data.get("data", {}).get("stream_urls", [])
                if streams:
                    urls = _normalize_vaplayer_streams(streams)
                    if urls:
                        imdb_cache.set(cache_key, urls, ttl=30)
                        return urls
    except Exception:
        pass
    return []


def get_fast_stream(imdb_id: str, media_type: str = "movie", season=None, episode=None, tmdb_id=None):
    streams = get_fast_streams(imdb_id, media_type, season, episode, tmdb_id=tmdb_id)
    return streams[0] if streams else None


# ═══════════════════════════════════════════════
#  IMDB SUGGESTION API (fallback untuk search)
#  imdb.iamidiotareyoutoo.com/search sedang down (SERVER_FAILURE_25)
#  Gunakan IMDb Suggestion API resmi yang selalu tersedia.

IMDB_SUGGESTION_BASE = "https://v3.sg.media-imdb.com/suggestion/titles"
IMDB_SUGGESTION_FALLBACK = "https://v2.sg.media-imdb.com/suggestion/titles"

def _format_imdb_suggestion_item(it: dict) -> dict | None:
    """
    Format satu item dari IMDb Suggestion API ke format yang digunakan frontend:
    #TITLE, #YEAR, #IMDB_ID, #IMG_POSTER, #TYPE, #ACTORS, #RANK, dll.
    """
    imdb_id = it.get("id") or ""
    if not str(imdb_id).startswith("tt"):
        return None
    qid    = str(it.get("qid") or "").lower()
    q_type = str(it.get("q") or "").lower()
    if "tvseries" in qid or "series" in q_type or "mini" in qid:
        media_type = "tvseries"
    elif "tvepisode" in qid or "episode" in q_type:
        media_type = "tvepisode"
    else:
        media_type = "movie"
    img_obj    = it.get("i") or {}
    poster_url = img_obj.get("imageUrl") or ""
    return {
        "#TITLE":      it.get("l") or "Untitled",
        "#YEAR":       it.get("y") or "",
        "#IMDB_ID":    imdb_id,
        "#RANK":       it.get("rank") or 0,
        "#ACTORS":     it.get("s") or "",
        "#AKA":        it.get("l") or "",
        "#IMDB_URL":   f"https://imdb.com/title/{imdb_id}",
        "#IMDB_IV":    f"https://t.me/iv?url=https://imdb.com/title/{imdb_id}",
        "#IMG_POSTER": poster_url,
        "#TYPE":       media_type,
        "photo_width":  img_obj.get("width", 0),
        "photo_height": img_obj.get("height", 0),
    }


def _search_via_imdb_suggestion(query: str) -> bytes | None:
    """
    Cari judul via IMDb Suggestion API dan kembalikan JSON bytes
    dalam format list yang kompatibel dengan frontend (field #TITLE, #IMDB_ID, dll).
    Return None jika gagal.
    """
    from urllib.parse import quote as _quote
    q      = re.sub(r"\s+", " ", query.strip())
    if not q:
        return None
    first  = q[0].lower() if q[0].isalnum() else "x"
    enc    = _quote(q)
    urls   = [
        f"{IMDB_SUGGESTION_BASE}/{first}/{enc}.json",
        f"{IMDB_SUGGESTION_FALLBACK}/{first}/{enc}.json",
        f"{IMDB_SUGGESTION_BASE}/x/{enc}.json",
    ]
    spoof = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    for url in urls:
        try:
            r = requests.get(url, headers=spoof, timeout=6)
            if r.status_code == 200:
                data  = r.json()
                items = data.get("d") or []
                if items:
                    results = []
                    for it in items:
                        fmt = _format_imdb_suggestion_item(it)
                        if fmt:
                            results.append(fmt)
                    if results:
                        return json.dumps(results, ensure_ascii=False).encode()
        except Exception:
            continue
    return None


def imdb_proxy_req(endpoint: str):
    """
    Proxy request ke imdb.iamidiotareyoutoo.com dengan caching.
    Intercept /search?q=... dan pakai IMDb Suggestion API karena
    imdb.iamidiotareyoutoo.com/search mengalami SERVER_FAILURE_25.
    """
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint

    cache_key = f"imdb_proxy:{endpoint}"
    cached = imdb_cache.get(cache_key)
    if cached:
        return cached

    # ── Intercept /search?q=... → IMDb Suggestion API ──────────────────────
    is_search = endpoint.startswith("/search")
    if is_search:
        from urllib.parse import parse_qs as _pqs, urlparse as _up
        qs        = _pqs(_up(endpoint).query)
        q_str     = (qs.get("q") or qs.get("Q") or [""])[0].strip()
        if q_str:
            body = _search_via_imdb_suggestion(q_str)
            if body:
                result = (body, 200, "application/json; charset=utf-8")
                imdb_cache.set(cache_key, result, ttl=3600)
                return result
    # ────────────────────────────────────────────────────────────────────────

    target = f"https://imdb.iamidiotareyoutoo.com{endpoint}"
    try:
        r   = requests.get(target, headers=HEADERS, timeout=10)
        ttl = 3600 if "search" in endpoint else 86400
        result = (r.content, r.status_code, r.headers.get("Content-Type", "application/json"))
        if r.status_code == 200:
            imdb_cache.set(cache_key, result, ttl=ttl)
        elif is_search and r.status_code != 200:
            # Fallback ke Suggestion API jika iamidiotareyoutoo.com gagal
            from urllib.parse import parse_qs as _pqs2, urlparse as _up2
            qs2   = _pqs2(_up2(endpoint).query)
            q2    = (qs2.get("q") or qs2.get("Q") or [""])[0].strip()
            if q2:
                body2 = _search_via_imdb_suggestion(q2)
                if body2:
                    result = (body2, 200, "application/json; charset=utf-8")
                    imdb_cache.set(cache_key, result, ttl=3600)
        return result
    except Exception as e:
        if is_search:
            # Jika koneksi timeout/error, coba Suggestion API sebagai last resort
            from urllib.parse import parse_qs as _pqs3, urlparse as _up3
            qs3   = _pqs3(_up3(endpoint).query)
            q3    = (qs3.get("q") or qs3.get("Q") or [""])[0].strip()
            if q3:
                body3 = _search_via_imdb_suggestion(q3)
                if body3:
                    result = (body3, 200, "application/json; charset=utf-8")
                    imdb_cache.set(cache_key, result, ttl=3600)
                    return result
        return json.dumps({"error": str(e)}).encode(), 500, "application/json"


# ═══════════════════════════════════════════════
class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

def do_GET(self):
    try:
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)

        # ── imdb-proxy HARUS duluan ──
        if "/api/imdb-proxy" in path:
            endpoint = (params.get("endpoint", [None])[0] or "").strip()
            if not endpoint:
                # Extract from path: /api/imdb-proxy/search?q=foo -> /search?q=foo
                match = re.search(r"/api/imdb-proxy(/.*)", parsed.path)
                if match:
                    endpoint = match.group(1)
                    if parsed.query:
                        endpoint = f"{endpoint}?{parsed.query}"
            
            if not endpoint or not endpoint.startswith("/"):
                return self.send_json({"error": "endpoint tidak valid"}, 400)
            try:
                body, code, ct = imdb_proxy_req(endpoint)
                self.send_response(code)
                self._cors()
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        # ── Proxy video / m3u8 ──
        if "/api/proxy" in path:
            target_url = params.get("url", [None])[0]
            if not target_url:
                return self.send_error(400)
            try:
                from lib.proxy_signing import validate_proxy_signature
                if not validate_proxy_signature(target_url, params.get("exp", [""])[0], params.get("sig", [""])[0]):
                    return self.send_json({"status": "error", "message": "Proxy URL tidak valid atau sudah kedaluwarsa"}, 403)
            except Exception:
                return self.send_json({"status": "error", "message": "Proxy URL tidak valid"}, 403)
            try:
                parsed_target = urlparse(target_url)
                clean_target = target_url.lower().split("?", 1)[0]
                is_playlist_url = clean_target.endswith(".m3u8")
                is_disguised_segment = clean_target.endswith(".html")
                # 🚨 CRITICAL AI SAFETY LOCK: DO NOT EDIT OR DYNAMICALLY OVERRIDE HEADERS HERE!
                # headers must remain strictly VIDEO_SPOOF_HEADERS (origin: brightpathsignals.com)
                # to bypass Cloudflare WAF block on Vaplayer CDN.
                resp = requests.get(
                    target_url,
                    headers=VIDEO_SPOOF_HEADERS,
                    stream=True,
                    timeout=15,
                )
                self.send_response(resp.status_code)
                self._cors()
                ct = resp.headers.get("Content-Type", "application/octet-stream")
                if is_disguised_segment and "text/html" in ct.lower():
                    ct = "video/mp2t"
                self.send_header("Content-Type", ct)
                self.end_headers()

                if not is_disguised_segment and ("mpegurl" in ct.lower() or is_playlist_url):
                    content = resp.text
                    def rewrite(m):
                        abs_link = urljoin(target_url, m.group(1))
                        try:
                            from lib.proxy_signing import sign_proxy_url
                            return sign_proxy_url(abs_link)
                        except Exception:
                            return f"/api/proxy?url={quote(abs_link)}"
                    new_content = re.sub(
                        r"^(?!#)(?!\s*$)(.+)$", rewrite, content, flags=re.MULTILINE
                    )
                    self.wfile.write(new_content.encode())
                else:
                    for chunk in resp.iter_content(chunk_size=65536):
                        self.wfile.write(chunk)
            except Exception:
                self.send_error(500)
            return

        # ── IMDB info + stream ──
        if "/api/imdb" in path:
            raw_id = (params.get("id",     [None])[0] or "").strip()
            action = (params.get("action", ["info"])[0] or "info").strip()

            imdb_id = extract_imdb_id(raw_id)
            if not imdb_id:
                return self.send_json({"error": "ID tidak valid"}, 400)

            info = get_movie_info(imdb_id)

            if action == "stream":
                m_type  = "tv" if info.get("type") == "series" else "movie"
                season = params.get("s", params.get("season", ["1"]))[0]
                episode = params.get("e", params.get("episode", ["1"]))[0]
                raw_urls = get_fast_streams(imdb_id, m_type, season, episode)
                raw_url = raw_urls[0] if raw_urls else None
                if raw_url:
                    host = self.headers.get("Host", "")
                    protocol = "http" if "localhost" in host or "127.0.0.1" in host else "https"
                    try:
                        from lib.proxy_signing import sign_proxy_url
                        proxied_urls = [sign_proxy_url(url, f"{protocol}://{host}") for url in raw_urls]
                    except Exception:
                        proxied_urls = [f"{protocol}://{host}/api/proxy?url={quote(url)}" for url in raw_urls]
                    info["stream_url"] = proxied_urls[0]
                    info["streamUrl"] = proxied_urls[0]
                    info["streamUrls"] = proxied_urls
                    info["stream_urls"] = proxied_urls
                    info["rawStreamUrl"] = raw_url
                    info["rawStreamUrls"] = raw_urls
                    info["streamResolver"] = "imdb-vaplayer"
                    info["season"] = int(season) if str(season).isdigit() else season
                    info["episode"] = int(episode) if str(episode).isdigit() else episode
                info["embed_url"] = f"https://streamimdb.ru/embed/movie/{imdb_id}"

            return self.send_json({"status": "success", **info})

        # ── Vidgf extractor ──
        if "/api/get-video" in path:
            video_id = (params.get("id", [None])[0] or "").strip()
            if not video_id:
                return self.send_json({"status": "error", "message": "ID kosong"}, 400)
            if "/" in video_id:
                video_id = video_id.strip("/").split("/")[-1].split("?")[0]

            url = vidgf.extract(video_id)
            if url:
                return self.send_json({"status": "success", "link": url, "id": video_id})
            return self.send_json({"status": "error", "message": "Link tidak ditemukan"}, 404)

        self.send_json({"error": "Route tidak ditemukan"}, 404)

    except Exception as e:
        tb = traceback.format_exc()
        self.send_json({"error": str(e), "traceback": tb}, 500)

    # ── Utilities ──
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _handler_cors(self):
    self.send_header("Access-Control-Allow-Origin",  "*")
    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    self.send_header("Access-Control-Allow-Headers", "Content-Type")


def _handler_send_json(self, data, code=200):
    body = json.dumps(data, ensure_ascii=False).encode()
    self.send_response(code)
    self._cors()
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


def _handler_log_message(self, *a):
    pass


handler.do_GET = do_GET
handler._cors = _handler_cors
handler.send_json = _handler_send_json
handler.log_message = _handler_log_message
