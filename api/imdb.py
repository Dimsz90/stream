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



# ═══════════════════════════════════════════════
#  HELPERS — IMDB
# ═══════════════════════════════════════════════
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


def get_fast_stream(imdb_id: str, media_type: str = "movie"):
    cache_key = f"stream:{imdb_id}:{media_type}"
    cached = imdb_cache.get(cache_key)
    if cached:
        return cached

    api_url = f"https://streamdata.vaplayer.ru/api.php?imdb={imdb_id}&type={media_type}"
    try:
        r = requests.get(api_url, headers=VIDEO_SPOOF_HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            streams = data.get("data", {}).get("stream_urls", [])
            if streams:
                url = streams[0].replace("\\/", "/")
                imdb_cache.set(cache_key, url, ttl=30)
                return url
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════
#  HTTP HANDLER
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
            if not endpoint or not endpoint.startswith("/"):
                return self.send_json({"error": "endpoint tidak valid"}, 400)
            try:
                target = f"https://imdb.iamidiotareyoutoo.com{endpoint}"
                r = requests.get(target, headers=HEADERS, timeout=8)
                body = r.content
                self.send_response(r.status_code)
                self._cors()
                self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
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
                parsed_target = urlparse(target_url)
                spoof = {
                    **VIDEO_SPOOF_HEADERS,
                    "Referer": f"{parsed_target.scheme}://{parsed_target.netloc}/",
                    "Origin":  f"{parsed_target.scheme}://{parsed_target.netloc}",
                }
                resp = requests.get(
                    target_url,
                    headers=spoof,
                    stream=True,
                    timeout=15,
                )
                self.send_response(resp.status_code)
                self._cors()
                ct = resp.headers.get("Content-Type", "application/octet-stream")
                self.send_header("Content-Type", ct)
                self.end_headers()

                if "mpegurl" in ct.lower() or target_url.endswith(".m3u8"):
                    content = resp.text
                    def rewrite(m):
                        abs_link = urljoin(target_url, m.group(1))
                        return f"/api/proxy?url={quote(abs_link)}"
                    new_content = re.sub(
                        r"^(?!#)(.+)$", rewrite, content, flags=re.MULTILINE
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
                raw_url = get_fast_stream(imdb_id, m_type)
                if raw_url:
                    host = self.headers.get("Host", "")
                    protocol = "http" if "localhost" in host or "127.0.0.1" in host else "https"
                    info["stream_url"] = f"{protocol}://{host}/api/proxy?url={quote(raw_url)}"
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
