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
from flask_cors import CORS

# Tambah folder root dan api/ ke sys.path agar bisa import modul
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
CORS(app)

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
    for pkg in ["requests", "bs4", "yt_dlp", "playwright"]:
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
        mod = load("api/dracin.py")
        params = {k: request.args.getlist(k) for k in request.args.keys()}
        data, status_code = mod.build_response(f"/api/dracin/{subpath}", params)
        return jsonify(data), status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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


# ── RUN SERVER ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Mengambil port dari environment variable (Wajib untuk Railway)
    # Default ke 8000 jika dijalankan lokal
    port = int(os.environ.get("PORT", 8000))
    
    print(f"[SERVER] Binding to 0.0.0.0:{port}...", flush=True)
    
    # debug=False mencegah auto-reload yang bisa bikin bentrok port di prod
    app.run(host="0.0.0.0", port=port, debug=False)
