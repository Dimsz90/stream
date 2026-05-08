"""
server.py — Production Entry Point untuk Railway
Berbasis Flask, mengintegrasikan seluruh fitur API dan SPA Routing.
"""
import importlib.util
import os
import sys
import re
import tempfile
import mimetypes
import traceback
from urllib.parse import quote, urljoin

from flask import Flask, send_file, request, jsonify, Response
import hmac, hashlib, time
from flask_cors import CORS

# Tambah folder root dan api/ ke sys.path agar bisa import modul
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
CORS(app)

# DRAMANOVA protection: set via environment
DRAMANOVA_PIN = os.environ.get("DRAMANOVA_PIN")
DRAMANOVA_SECRET = os.environ.get("DRAMANOVA_SECRET") or os.environ.get("SECRET_KEY") or "streamvault-default-secret"

def _make_dramanova_token(ttl=3600):
    exp = int(time.time()) + int(ttl)
    msg = f"dramanova:{exp}"
    sig = hmac.new(DRAMANOVA_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}:{sig}"

def _validate_dramanova_token(token: str) -> bool:
    try:
        parts = token.split(":")
        if len(parts) < 3:
            return False
        # format: dramanova:<exp>:<sig>
        exp = int(parts[1])
        sig = parts[2]
        msg = f"dramanova:{exp}"
        expected = hmac.new(DRAMANOVA_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False
        if time.time() > exp:
            return False
        return True
    except Exception:
        return False

def load(path):
    """Load modul Python secara dinamis dari file"""
    spec = importlib.util.spec_from_file_location("mod", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ── 1. STATIC FILES & SPA ROUTING ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_file("index.html")

@app.route("/favicon.ico")
def favicon():
    return "", 204

@app.route("/<path:filename>")
def static_files(filename):
    # 1. Cek file di root folder
    if os.path.exists(filename):
        return send_file(filename)
    
    # 2. Cek file di dalam folder public/
    public_path = os.path.join("public", filename)
    if os.path.exists(public_path):
        return send_file(public_path)
    
    # 3. Fallback untuk SPA (Single Page Application)
    if not request.path.startswith('/api/'):
        if os.path.exists("index.html"):
            return send_file("index.html")
            
    return jsonify({"error": "Not Found"}), 404


# ── 2. API ROUTES ─────────────────────────────────────────────────────────────

@app.route("/api/debug")
def debug():
    results = {"python": sys.version, "env": "production (railway)"}
    for pkg in ["requests", "bs4", "yt_dlp", "playwright", "PIL", "pillow_heif"]:
        try:
            mod = __import__(pkg)
            results[pkg] = f"OK ({getattr(mod, '__version__', '?')})"
        except ImportError as e:
            results[pkg] = f"MISSING: {e}"
    return jsonify(results)

@app.route("/api/get-video")
def get_video():
    video_id = request.args.get("id", "").strip()
    if not video_id:
        return jsonify({"status": "error", "message": "ID kosong"}), 400
    if "/" in video_id:
        video_id = video_id.strip("/").split("/")[-1].split("?")[0]

    try:
        from lib import vidgf
        url = vidgf.extract(video_id)
        if url:
            return jsonify({"status": "success", "link": url, "id": video_id})
        return jsonify({"status": "error", "message": "Tidak ditemukan"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tmdb-stream")
def tmdb_stream():
    tmdb_id = request.args.get("id", request.args.get("tmdb_id", "")).strip()
    media_type = request.args.get("type", "movie").strip()
    season = int(request.args.get("s", request.args.get("season", 1)) or 1)
    episode = int(request.args.get("e", request.args.get("episode", 1)) or 1)
    if not tmdb_id:
        return jsonify({"status": "error", "message": "TMDB ID kosong"}), 400

    try:
        mod = load("api/tmdb.py")
        host = request.host
        scheme = request.headers.get("X-Forwarded-Proto") or (
            "http" if "localhost" in host or "127.0.0.1" in host else "https"
        )
        data = mod.build_stream_payload(
            tmdb_id,
            media_type,
            season,
            episode,
            proxy_base=f"{scheme}://{host}",
        )
        return jsonify(data), 200 if data.get("success") else 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/dracin/<path:subpath>")
def dracin_api(subpath):
    try:
        # Protect Dramanova platform: require token if configured
        platform = request.args.get('platform', 'dramabox')
        # Allow auth endpoint through
        if DRAMANOVA_PIN and platform == 'dramanova':
            # accept token via header or query param
            token = request.headers.get('X-Dramanova-Token') or request.args.get('dramanova_token')
            if not token or not _validate_dramanova_token(token):
                return jsonify({"status": "error", "message": "Access to dramanova requires PIN authentication"}), 403

        mod = load("api/dracin.py")
        params = {k: request.args.getlist(k) for k in request.args.keys()}
        data, status_code = mod.build_response(f"/api/dracin/{subpath}", params)
        return jsonify(data), status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/dracin/auth', methods=['POST'])
def dracin_auth():
    if not DRAMANOVA_PIN:
        return jsonify({"status": "error", "message": "PIN auth not configured"}), 404
    data = request.get_json(silent=True) or {}
    pin = data.get('pin') or request.form.get('pin') or ''
    if not pin:
        return jsonify({"status": "error", "message": "PIN required"}), 400
    if pin == DRAMANOVA_PIN:
        token = _make_dramanova_token()
        return jsonify({"status": "success", "token": token}), 200
    return jsonify({"status": "error", "message": "PIN invalid"}), 403

@app.route("/api/scan", methods=["POST"])
def scan():
    data = request.get_json() or {}
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400
    try:
        mod    = load("api/scan.py")
        videos = mod.extract(url)
        return jsonify({"videos": videos, "count": len(videos)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/formats", methods=["POST"])
def formats():
    data = request.get_json() or {}
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400
    try:
        mod = load("api/formats.py")
        title, thumb, fmts = mod.get_formats(url)
        return jsonify({"title": title, "thumb": thumb, "formats": fmts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/download", methods=["POST"])
def download():
    data      = request.get_json() or {}
    url       = data.get("url", "").strip()
    format_id = data.get("format_id", "bestvideo+bestaudio/best")
    title     = data.get("title", "video")
    if not url:
        return jsonify({"error": "URL required"}), 400
    try:
        import yt_dlp
    except ImportError:
        return jsonify({"error": "yt-dlp tidak terinstall"}), 500

    with tempfile.TemporaryDirectory() as tmpdir:
        opts = {
            "format": format_id,
            "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
            "quiet": True,
            "merge_output_format": "mp4",
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            files = os.listdir(tmpdir)
            if not files:
                return jsonify({"error": "Download gagal"}), 500
            
            filepath   = os.path.join(tmpdir, files[0])
            ext        = os.path.splitext(files[0])[1]
            safe       = re.sub(r'[^\w\s-]', '', title)[:60].strip() or "video"
            fname      = f"{safe}{ext}"
            mime       = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
            
            with open(filepath, "rb") as f:
                data_bytes = f.read()
                
            return Response(data_bytes, headers={
                "Content-Type":        mime,
                "Content-Disposition": f'attachment; filename="{fname}"',
                "Content-Length":      str(len(data_bytes)),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

@app.route("/api/imdb")
def imdb_api():
    raw_id = request.args.get("id", "").strip()
    action = request.args.get("action", "info").strip()

    if not raw_id:
        return jsonify({"error": "Parameter ?id= diperlukan"}), 400

    try:
        mod = load("api/imdb.py")
        imdb_id = mod.extract_imdb_id(raw_id)
        if not imdb_id:
            return jsonify({"error": f"IMDB ID tidak valid: {raw_id}"}), 400

        info = mod.get_movie_info(imdb_id)

        if action == "stream":
            media_type = "tv" if info.get("type") == "series" else "movie"
            raw_url    = mod.get_fast_stream(imdb_id, media_type)
            if raw_url:
                scheme = request.headers.get('X-Forwarded-Proto', 'https')
                host = request.host
                info["stream_url"] = f"{scheme}://{host}/api/proxy?url={quote(raw_url)}"
            info["embed_url"] = f"https://streamimdb.ru/embed/movie/{imdb_id}"

        return jsonify({"status": "success", **info})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/proxy")
def proxy():
    import requests as req
    try:
        from lib.config import VIDEO_SPOOF_HEADERS
    except ImportError:
        VIDEO_SPOOF_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://imdb.com/"}

    target_url = request.args.get("url", "").strip()
    if not target_url:
        return "Missing url param", 400

    try:
        resp = req.get(target_url, headers=VIDEO_SPOOF_HEADERS, stream=True, timeout=15)
        content_type = resp.headers.get("Content-Type", "application/octet-stream")

        if "mpegurl" in content_type.lower() or target_url.endswith(".m3u8"):
            content = resp.text

            def rewrite(m):
                abs_link = urljoin(target_url, m.group(1))
                return f"/api/proxy?url={quote(abs_link)}"

            new_content = re.sub(r"^(?!#)(.+)$", rewrite, content, flags=re.MULTILINE)
            return Response(
                new_content.encode(),
                status=resp.status_code,
                headers={
                    "Content-Type":                content_type,
                    "Access-Control-Allow-Origin": "*",
                },
            )

        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                yield chunk

        return Response(
            generate(),
            status=resp.status_code,
            headers={
                "Content-Type":                content_type,
                "Access-Control-Allow-Origin": "*",
            },
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

PLATFORM_REFERERS = {
    "reelshort":       "https://reelshort.com/",
    "melolo":          "https://melolo.tv/",
    "fizzopic.org":    "https://www.dramabox.com/",
    "_default":        "https://www.dramabox.com/",
}

def _img_referer(url: str, override: str = "") -> str:
    if override:
        return override
    for domain, ref in PLATFORM_REFERERS.items():
        if domain in url:
            return ref
    return PLATFORM_REFERERS["_default"]

@app.route("/api/img-proxy")
def img_proxy():
    import io
    import requests as req

    target_url = request.args.get("url", "").strip()
    if not target_url:
        return "Missing url param", 400

    allowed_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif")
    path_lower = target_url.split("?")[0].lower()
    if not any(path_lower.endswith(e) for e in allowed_exts):
        if "tplv-" not in target_url and "image" not in path_lower:
            return "URL tidak tampak seperti gambar", 400

    headers = {
        "User-Agent":      "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Mobile Safari/537.36",
        "Referer":         _img_referer(target_url, request.args.get("ref", "")),
        "Accept":          "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest":  "image",
        "Sec-Fetch-Mode":  "no-cors",
        "Sec-Fetch-Site":  "cross-site",
    }

    try:
        resp = req.get(target_url, headers=headers, stream=True, timeout=15)
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        cache_ttl = 3600 * 6

        should_convert = (
            resp.ok and (
                "heic" in content_type.lower()
                or "heif" in content_type.lower()
                or "avif" in content_type.lower()
                or path_lower.endswith((".heic", ".avif"))
            )
        )

        if should_convert:
            try:
                from PIL import Image
                try:
                    import pillow_heif
                    pillow_heif.register_heif_opener()
                except Exception:
                    pass

                img = Image.open(io.BytesIO(resp.content))
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=88, optimize=True)
                return Response(
                    out.getvalue(),
                    status=200,
                    headers={
                        "Content-Type":                "image/jpeg",
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control":               f"public, max-age={cache_ttl}",
                        "X-Proxied-From":              target_url[:80],
                        "X-Image-Converted":           "1",
                    },
                )
            except Exception as conv_err:
                print(f"[IMG-PROXY] convert failed: {conv_err}")

        def generate():
            for chunk in resp.iter_content(chunk_size=32768):
                if chunk:
                    yield chunk

        return Response(
            generate(),
            status=resp.status_code,
            headers={
                "Content-Type":                content_type,
                "Access-Control-Allow-Origin": "*",
                "Cache-Control":               f"public, max-age={cache_ttl}",
                "X-Proxied-From":              target_url[:80],
            },
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 502

@app.route("/api/subtitle/search")
def subtitle_search():
    imdb_id    = request.args.get("imdb_id", "").strip() or None
    tmdb_id    = request.args.get("tmdb_id", "").strip() or None
    query      = request.args.get("query",   "").strip() or None
    lang       = request.args.get("lang",    "en").strip()
    media_type = request.args.get("type",    "movie").strip()
    season     = request.args.get("season",  "").strip() or None
    episode    = request.args.get("episode", "").strip() or None

    if not imdb_id and not tmdb_id and not query:
        return jsonify({"status": "error", "error": "imdb_id, tmdb_id, atau query wajib diisi"}), 400

    try:
        sub = load("api/subtitle.py")
        result = sub.search(
            imdb_id    = imdb_id,
            tmdb_id    = tmdb_id,
            query      = query,
            lang       = lang,
            media_type = media_type,
            season     = season,
            episode    = episode,
        )

        status_code = 200 if result["status"] == "success" else 503
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/subtitle/download")
def subtitle_download():
    file_id = request.args.get("file_id", "").strip()
    if not file_id:
        return jsonify({"error": "file_id wajib diisi"}), 400

    try:
        sub = load("api/subtitle.py")
        dl_url, err = sub.get_download_url(file_id)
        if err:
            return jsonify({"error": err}), 500

        srt_text, err = sub.fetch_srt(dl_url)
        if err:
            return jsonify({"error": err}), 500

        return Response(
            srt_text.encode("utf-8"),
            status=200,
            headers={
                "Content-Type":                "text/plain; charset=utf-8",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Tambahkan routes ini ke server.py ─────────────────────────────────────────


@app.route("/api/melolo/languages")
@app.route("/api/captain/<platform>/languages")
def captain_languages(platform="melolo"):
    from api.melolo import languages
    data, code = languages(platform)
    return jsonify(data), code


@app.route("/api/melolo/home")
@app.route("/api/captain/<platform>/home")
def captain_home(platform="melolo"):
    from api.melolo import home
    lang = request.args.get("lang", "en")
    data, code = home(platform, lang)
    return jsonify(data), code


@app.route("/api/melolo/tabs")
@app.route("/api/captain/<platform>/tabs")
def captain_tabs(platform="melolo"):
    from api.melolo import tabs
    gender = request.args.get("gender", "0")
    lang   = request.args.get("lang", "en")
    data, code = tabs(platform, gender, lang)
    return jsonify(data), code


@app.route("/api/melolo/categories")
@app.route("/api/captain/<platform>/categories")
def captain_categories(platform="melolo"):
    from api.melolo import categories
    gender = request.args.get("gender", "0")
    lang   = request.args.get("lang", "en")
    data, code = categories(platform, gender, lang)
    return jsonify(data), code


@app.route("/api/melolo/search")
@app.route("/api/captain/<platform>/search")
def captain_search(platform="melolo"):
    from api.melolo import search
    q      = request.args.get("q", "").strip()
    lang   = request.args.get("lang", "en")
    limit  = int(request.args.get("limit", 20))
    offset = int(request.args.get("offset", 0))
    if not q:
        return jsonify({"error": "Parameter ?q= diperlukan"}), 400
    data, code = search(q, platform, lang, limit, offset)
    return jsonify(data), code


@app.route("/api/melolo/suggest")
@app.route("/api/captain/<platform>/suggest")
def captain_suggest(platform="melolo"):
    from api.melolo import suggest
    q    = request.args.get("q", "").strip()
    lang = request.args.get("lang", "en")
    if not q:
        return jsonify({"error": "Parameter ?q= diperlukan"}), 400
    data, code = suggest(q, platform, lang)
    return jsonify(data), code


@app.route("/api/melolo/book")
@app.route("/api/captain/<platform>/book")
def captain_book(platform="melolo"):
    from api.melolo import book
    book_id = request.args.get("id", "").strip()
    lang    = request.args.get("lang", "en")
    if not book_id:
        return jsonify({"error": "Parameter ?id= diperlukan"}), 400
    data, code = book(book_id, platform, lang)
    return jsonify(data), code


@app.route("/api/melolo/series")
@app.route("/api/captain/<platform>/series")
def captain_series(platform="melolo"):
    from api.melolo import series
    book_id = request.args.get("id", "").strip()
    lang    = request.args.get("lang", "en")
    if not book_id:
        return jsonify({"error": "Parameter ?id= diperlukan"}), 400
    data, code = series(book_id, platform, lang)
    return jsonify(data), code


@app.route("/api/melolo/videos")
@app.route("/api/captain/<platform>/videos")
def captain_videos(platform="melolo"):
    from api.melolo import videos
    book_id = request.args.get("id", "").strip()
    lang    = request.args.get("lang", "en")
    if not book_id:
        return jsonify({"error": "Parameter ?id= diperlukan"}), 400
    data, code = videos(book_id, platform, lang)
    return jsonify(data), code
# ── RUN SERVER ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Mengambil port dari environment variable (Wajib untuk Railway)
    # Default ke 8000 jika dijalankan lokal
    port = int(os.environ.get("PORT", 8000))
    
    print(f"[SERVER] Binding to 0.0.0.0:{port}...", flush=True)
    
    # debug=False mencegah auto-reload yang bisa bikin bentrok port di prod
    app.run(host="0.0.0.0", port=port, debug=False)
