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
import time
from urllib.parse import quote, urljoin, urlparse

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

BRIGHTPATH_ORIGIN = "https://brightpathsignals.com"

def _is_vaplayer_stream(url: str) -> bool:
    try:
        path = urlparse(url).path
        return bool(
            re.search(r'/[A-Za-z0-9]{5,}/(?:pl|cdnstr|content)/', path)
            or '/static/df/' in path
        )
    except Exception:
        return False

def _stream_spoof_origin(target_url: str) -> str:
    return BRIGHTPATH_ORIGIN if _is_vaplayer_stream(target_url) else ""

def _forward_video_request_headers():
    headers = {}
    for name in ("Range", "If-Range", "If-None-Match", "If-Modified-Since"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers

def _video_response_headers(resp, content_type, include_length=True):
    headers = {
        "Content-Type":                content_type,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        "Cache-Control":               "no-store",
    }
    passthrough = ("Content-Length", "Content-Range", "Accept-Ranges") if include_length else ("Content-Range", "Accept-Ranges")
    for name in passthrough:
        value = resp.headers.get(name)
        if value:
            headers[name] = value
    return headers

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

    use_advanced_fetch = os.environ.get("PROXY_USE_ADVANCED_FETCH", "").lower() in ("1", "true", "yes", "on")
    if not use_advanced_fetch:
        try:
            is_playlist_url = target_url.lower().split("?", 1)[0].endswith(".m3u8")
            clean_target = target_url.split("?")[0]
            is_disguised_segment = clean_target.endswith(".html")
            
            # 🚨 CRITICAL AI SAFETY LOCK: DO NOT EDIT OR DYNAMICALLY OPTIMIZE THIS BLOCK!
            # Referer and Origin MUST remain hardcoded to brightpathsignals.com to bypass
            # Cloudflare WAF on the Vaplayer CDN. Any dynamic replacement will cause 403/502 errors.
            headers = {
                **VIDEO_SPOOF_HEADERS,
                "Referer": "https://brightpathsignals.com/",
                "Origin": "https://brightpathsignals.com",
                **_forward_video_request_headers(),
            }
            resp = req.get(target_url, headers=headers, stream=True, timeout=15)
            content_type = resp.headers.get("Content-Type", "application/octet-stream")

            if is_disguised_segment and "text/html" in content_type.lower():
                if not resp.ok:
                    sample = resp.text[:2000].lower()
                    return jsonify({
                        "status": "error",
                        "message": "Upstream segment blocked or invalid",
                        "upstream_status": resp.status_code,
                        "content_type": content_type,
                        "blocked_by": "cloudflare" if "cloudflare" in sample or "you have been blocked" in sample else "",
                    }), 502
                content_type = "video/mp2t"

            if not is_disguised_segment and ("mpegurl" in content_type.lower() or is_playlist_url):
                content = resp.text
                sample = content[:3000].lower()
                looks_like_playlist = "#extm3u" in sample or "#ext-x-" in sample
                looks_like_html = (
                    "<!doctype html" in sample
                    or "<html" in sample
                    or "<head" in sample
                    or "cloudflare" in sample
                    or "attention required" in sample
                    or "you have been blocked" in sample
                    or "cf-error" in sample
                )
                if not resp.ok or looks_like_html or not looks_like_playlist:
                    return jsonify({
                        "status": "error",
                        "message": "Upstream playlist blocked or invalid",
                        "upstream_status": resp.status_code,
                        "content_type": content_type,
                        "blocked_by": "cloudflare" if "cloudflare" in sample or "you have been blocked" in sample else "",
                    }), 502

                def rewrite(m):
                    abs_link = urljoin(target_url, m.group(1))
                    if urlparse(abs_link).netloc == "tmstrd.justhd.tv" and abs_link.split("?")[0].endswith(".html"):
                        return abs_link
                    return f"/api/proxy?url={quote(abs_link)}"

                new_content = re.sub(r"^(?!#)(?!\s*$)(.+)$", rewrite, content, flags=re.MULTILINE)
                return Response(
                    new_content.encode(),
                    status=resp.status_code,
                    headers=_video_response_headers(resp, content_type, include_length=False),
                )

            def generate():
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk

            return Response(
                generate(),
                status=resp.status_code,
                headers=_video_response_headers(resp, content_type),
            )

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def _playlist_state(resp, body=None):
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        text = body if body is not None else resp.text
        sample = text[:3000].lower()
        looks_like_playlist = "#extm3u" in sample or "#ext-x-" in sample
        looks_like_html = (
            "<!doctype html" in sample
            or "<html" in sample
            or "<head" in sample
            or "cloudflare" in sample
            or "attention required" in sample
            or "you have been blocked" in sample
            or "cf-error" in sample
        )
        return content_type, text, sample, looks_like_playlist, looks_like_html

    def _header_variants(url):
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        spoof_origin = _stream_spoof_origin(url)
        if spoof_origin:
            origin = spoof_origin
        forwarded = _forward_video_request_headers()
        common = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,video/mp2t,video/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }
        configured = {**common, **VIDEO_SPOOF_HEADERS, **forwarded}
        origin_ref = {**common, "Referer": f"{origin}/", "Origin": origin, **forwarded}
        no_origin = {**common, "Referer": f"{origin}/", **forwarded}
        player_ref_origin = spoof_origin or "https://streamdata.vaplayer.ru"
        player_ref = {**common, "Referer": f"{player_ref_origin}/", "Origin": player_ref_origin, **forwarded}

        variants = []
        for name, headers in (
            ("dev", {**VIDEO_SPOOF_HEADERS, **forwarded}),
            ("configured", configured),
            ("origin", origin_ref),
            ("no_origin", no_origin),
            ("vaplayer", player_ref),
        ):
            if not any(existing == headers for _, existing in variants):
                variants.append((name, headers))
        return variants

    def _fetch_video(url, is_playlist):
        attempts = []
        last_resp = None
        last_playlist_text = None
        stream_proxy = os.environ.get("STREAM_PROXY_URL", "").strip()
        proxy_cfg = {"http": stream_proxy, "https": stream_proxy} if stream_proxy else None
        enable_curl_fallback = os.environ.get("PROXY_ENABLE_CURL_CFFI", "").lower() in ("1", "true", "yes", "on")

        started_at = time.monotonic()
        max_total_seconds = 22 if is_playlist else 45

        def _deadline_exceeded():
            return (time.monotonic() - started_at) >= max_total_seconds

        variants = _header_variants(url)
        if is_playlist:
            variants = variants[:4]

        session = req.Session()
        hard_blocked = False
        for name, headers in variants:
            if _deadline_exceeded():
                attempts.append({"client": "requests", "headers": name, "error": "deadline_exceeded", "outbound_proxy": bool(stream_proxy)})
                break
            remaining = max_total_seconds - (time.monotonic() - started_at)
            if remaining <= 1.0:
                attempts.append({"client": "requests", "headers": name, "error": "deadline_exceeded", "outbound_proxy": bool(stream_proxy)})
                break
            try:
                if is_playlist:
                    read_timeout = max(3.0, min((10 if name == "dev" else 12), remaining - 0.6))
                    timeout = (4, read_timeout)
                else:
                    read_timeout = max(4.0, min(20, remaining - 0.6))
                    timeout = (6, read_timeout)
                resp = session.get(
                    url,
                    headers=headers,
                    proxies=proxy_cfg,
                    stream=True,
                    timeout=timeout,
                )
            except req.RequestException as err:
                attempts.append({"client": "requests", "headers": name, "error": str(err), "outbound_proxy": bool(stream_proxy)})
                continue

            last_resp = resp
            attempts.append({"client": "requests", "headers": name, "status": resp.status_code, "outbound_proxy": bool(stream_proxy)})
            if not is_playlist:
                if resp.ok:
                    return resp, attempts, None
                continue

            content_type, content, sample, looks_like_playlist, looks_like_html = _playlist_state(resp)
            last_playlist_text = content
            if resp.ok and looks_like_playlist and not looks_like_html:
                return resp, attempts, content
            if resp.status_code in (401, 403, 429, 451) and looks_like_html:
                hard_blocked = True
                try:
                    resp.close()
                except Exception:
                    pass
                break
            try:
                resp.close()
            except Exception:
                pass

        if (not is_playlist) and enable_curl_fallback and not hard_blocked and not _deadline_exceeded():
            try:
                from curl_cffi import requests as curl_req
                for name, headers in variants:
                    for browser in ("chrome124", "chrome120"):
                        if _deadline_exceeded():
                            attempts.append({"client": "curl_cffi", "headers": name, "impersonate": browser, "error": "deadline_exceeded", "outbound_proxy": bool(stream_proxy)})
                            break
                        try:
                            resp = curl_req.get(
                                url,
                                headers=headers,
                                impersonate=browser,
                                proxies=proxy_cfg,
                                stream=True,
                                timeout=12 if is_playlist else 20,
                            )
                        except Exception as err:
                            attempts.append({"client": "curl_cffi", "headers": name, "impersonate": browser, "error": str(err), "outbound_proxy": bool(stream_proxy)})
                            continue

                        last_resp = resp
                        attempts.append({"client": "curl_cffi", "headers": name, "impersonate": browser, "status": resp.status_code, "outbound_proxy": bool(stream_proxy)})
                        if resp.ok:
                            return resp, attempts, None
                        try:
                            resp.close()
                        except Exception:
                            pass
                    if hard_blocked:
                        break
            except Exception as err:
                attempts.append({"client": "curl_cffi", "error": str(err)})

        return last_resp, attempts, last_playlist_text

    try:
        is_playlist_url = target_url.lower().split("?", 1)[0].endswith(".m3u8")
        clean_target = target_url.split("?")[0]
        is_disguised_segment = clean_target.endswith(".html")

        resp, attempts, cached_playlist_text = _fetch_video(target_url, is_playlist_url and not is_disguised_segment)
        if resp is None:
            return jsonify({"status": "error", "message": "Proxy request failed", "attempts": attempts}), 502

        content_type = resp.headers.get("Content-Type", "application/octet-stream")

        if is_disguised_segment and "text/html" in content_type.lower():
            if not resp.ok:
                sample = resp.text[:2000].lower()
                return jsonify({
                    "status": "error",
                    "message": "Upstream segment blocked or invalid",
                    "upstream_status": resp.status_code,
                    "content_type": content_type,
                    "blocked_by": "cloudflare" if "cloudflare" in sample or "you have been blocked" in sample else "",
                    "attempts": attempts,
                }), 502
            content_type = "video/mp2t"

        if not is_disguised_segment and ("mpegurl" in content_type.lower() or is_playlist_url):
            content_type, content, sample, looks_like_playlist, looks_like_html = _playlist_state(
                resp,
                cached_playlist_text,
            )
            if not resp.ok or looks_like_html or not looks_like_playlist:
                return jsonify({
                    "status": "error",
                    "message": "Upstream playlist blocked or invalid",
                    "upstream_status": resp.status_code,
                    "content_type": content_type,
                    "blocked_by": "cloudflare" if "cloudflare" in sample or "you have been blocked" in sample else "",
                    "attempts": attempts,
                }), 502

            def rewrite(m):
                abs_link = urljoin(target_url, m.group(1))
                if urlparse(abs_link).netloc == "tmstrd.justhd.tv" and abs_link.split("?")[0].endswith(".html"):
                    return abs_link
                return f"/api/proxy?url={quote(abs_link)}"

            new_content = re.sub(r"^(?!#)(?!\s*$)(.+)$", rewrite, content, flags=re.MULTILINE)
            return Response(
                new_content.encode(),
                status=resp.status_code,
                headers=_video_response_headers(resp, content_type, include_length=False),
            )

        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return Response(
            generate(),
            status=resp.status_code,
            headers=_video_response_headers(resp, content_type),
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/subtitle/search")
def subtitle_search():
    imdb_id    = request.args.get("imdb_id", "").strip() or None
    query      = request.args.get("query",   "").strip() or None
    lang       = request.args.get("lang",    "en").strip()
    media_type = request.args.get("type",    "movie").strip()

    if not imdb_id and not query:
        return jsonify({"status": "error", "error": "imdb_id atau query wajib diisi"}), 400

    try:
        sub = load("api/subtitle.py")
        result = sub.search(
            imdb_id    = imdb_id,
            query      = query,
            lang       = lang,
            media_type = media_type,
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
