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
import secrets
from urllib.parse import quote, urljoin, urlparse

from flask import Flask, send_file, request, jsonify, Response
import hmac, hashlib, time
from flask_cors import CORS

# Tambah folder root dan api/ ke sys.path agar bisa import modul
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
CORS(app)

REMOTE_PROXY_CACHE = {}
REMOTE_PROXY_TTL = 60 * 60 * 4
BRIGHTPATH_STREAM_HOSTS = {
    "leadgenerationblueprint.site",
    "tmstrd.justhd.tv",
}
BRIGHTPATH_ORIGIN = "https://brightpathsignals.com"

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

def require_subscription():
    from lib.subscription import check_subscription

    ok, payload, status_code = check_subscription(request.headers)
    if ok:
        return None
    return jsonify(payload), status_code

def sign_proxy_url(url, base=""):
    from lib.proxy_signing import sign_proxy_url as _sign_proxy_url

    return _sign_proxy_url(url, base=base)

def require_proxy_signature(target_url):
    from lib.proxy_signing import validate_proxy_signature

    if validate_proxy_signature(target_url, request.args.get("exp", ""), request.args.get("sig", "")):
        return None
    return jsonify({"status": "error", "message": "Proxy URL tidak valid atau sudah kedaluwarsa"}), 403

def remote_api_enabled():
    from lib.config import STREAM_API_REMOTE, USE_STREAM_API_REMOTE

    return USE_STREAM_API_REMOTE and bool(STREAM_API_REMOTE)

def remote_base():
    from lib.config import STREAM_API_REMOTE

    return STREAM_API_REMOTE.rstrip("/")

def remote_proxy_secret():
    from lib.config import SUBSCRIPTION_SECRET

    return SUBSCRIPTION_SECRET

def remote_forward_headers():
    headers = {}
    for name in ("X-Subscription-Token", "X-Dramanova-Token"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers

def public_base_url():
    host = request.host
    scheme = request.headers.get("X-Forwarded-Proto") or (
        "http" if "localhost" in host or "127.0.0.1" in host else "https"
    )
    return f"{scheme}://{host}"

def _stream_spoof_origin(target_url: str) -> str:
    try:
        host = urlparse(target_url).hostname or ""
    except Exception:
        return ""
    return BRIGHTPATH_ORIGIN if host.lower() in BRIGHTPATH_STREAM_HOSTS else ""

def bayar_gg_error_response(exc):
    detail = getattr(exc, "detail", None)
    payload = {"status": "error", "message": str(exc)}
    if detail:
        payload["detail"] = detail
    return jsonify(payload), getattr(exc, "status_code", 500)

def cache_remote_proxy_url(url):
    exp = int(time.time()) + REMOTE_PROXY_TTL
    token = secrets.token_urlsafe(18)
    REMOTE_PROXY_CACHE[token] = {"url": url, "exp": exp}
    sig = hmac.new(remote_proxy_secret().encode(), f"remote-proxy:{token}:{exp}".encode(), hashlib.sha256).hexdigest()
    return f"/api/remote-proxy?id={token}&exp={exp}&sig={sig}"

def get_cached_remote_proxy_url(token, exp, sig):
    try:
        exp = int(exp)
    except Exception:
        return ""
    expected = hmac.new(remote_proxy_secret().encode(), f"remote-proxy:{token}:{exp}".encode(), hashlib.sha256).hexdigest()
    if time.time() > exp or not hmac.compare_digest(expected, str(sig or "")):
        return ""
    item = REMOTE_PROXY_CACHE.get(token)
    if not item or item.get("exp", 0) < time.time():
        REMOTE_PROXY_CACHE.pop(token, None)
        return ""
    return item.get("url", "")

def rewrite_remote_urls(obj):
    base = remote_base()
    if isinstance(obj, dict):
        return {k: rewrite_remote_urls(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rewrite_remote_urls(v) for v in obj]
    if isinstance(obj, str) and obj.startswith(base):
        return cache_remote_proxy_url(obj)
    if isinstance(obj, str) and obj.startswith(("/api/proxy", "/api/remote-proxy")):
        return cache_remote_proxy_url(f"{base}{obj}")
    return obj

def remote_api_json(path):
    import requests as req

    target = f"{remote_base()}{path}"
    if request.query_string:
        target = f"{target}?{request.query_string.decode('utf-8')}"
    headers = remote_forward_headers()
    try:
        resp = req.request(request.method, target, headers=headers, json=request.get_json(silent=True), timeout=25)
        data = resp.json()
        return jsonify(rewrite_remote_urls(data)), resp.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": f"Remote API gagal: {e}"}), 502

def remote_api_passthrough(path):
    import requests as req

    target = f"{remote_base()}{path}"
    if request.query_string:
        target = f"{target}?{request.query_string.decode('utf-8')}"
    headers = remote_forward_headers()
    try:
        resp = req.request(request.method, target, headers=headers, json=request.get_json(silent=True), stream=True, timeout=25)
        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        return Response(generate(), status=resp.status_code, headers={
            "Content-Type": resp.headers.get("Content-Type", "application/octet-stream"),
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"Remote API gagal: {e}"}), 502

def is_safe_static_path(filename):
    parts = filename.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        return False
    ext = os.path.splitext(filename)[1].lower()
    blocked_exts = {".py", ".pyc", ".pyo", ".env", ".md", ".log", ".db", ".sqlite", ".sqlite3"}
    blocked_names = {
        "procfile",
        "requirements.txt",
        "railway.json",
        "vercel.json",
        "vercel.json.example",
    }
    return ext not in blocked_exts and os.path.basename(filename).lower() not in blocked_names

# ── 1. STATIC FILES & SPA ROUTING ─────────────────────────────────────────────

@app.route("/")
def index():
    resp = send_file("index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/favicon.ico")
def favicon():
    return "", 204

@app.route("/healthz")
@app.route("/api/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/api/subscription/config")
def subscription_config():
    from lib.config import REQUIRE_SUBSCRIPTION
    from lib.subscription import captcha_config

    return jsonify({
        "enabled": REQUIRE_SUBSCRIPTION,
        "captcha": captcha_config(),
    }), 200

@app.route("/api/subscription/login", methods=["POST"])
def subscription_login():
    from lib.subscription import login_subscriber

    data = request.get_json(silent=True) or {}
    body, status_code = login_subscriber(
        data.get("username", ""),
        data.get("password", data.get("pin", "")),
        data.get("captcha_token", ""),
    )
    return jsonify(body), status_code

@app.route("/api/subscription/register", methods=["POST"])
def subscription_register():
    from lib.subscription import register_subscriber

    data = request.get_json(silent=True) or {}
    body, status_code = register_subscriber(
        data.get("username", ""),
        data.get("password", ""),
        data.get("captcha_token", ""),
    )
    return jsonify(body), status_code

@app.route("/api/subscription/me")
def subscription_me():
    from lib.subscription import me

    body, status_code = me(request.headers)
    return jsonify(body), status_code

@app.route("/api/subscription/plans")
def subscription_plans():
    from lib.subscription import SUBSCRIPTION_PLANS

    return jsonify({"status": "success", "plans": list(SUBSCRIPTION_PLANS.values())}), 200

@app.route("/api/subscription/payment/create", methods=["POST"])
def subscription_payment_create():
    from lib.subscription import create_subscription_payment

    data = request.get_json(silent=True) or {}
    body, status_code = create_subscription_payment(
        request.headers,
        data.get("plan", ""),
        public_base_url(),
        data.get("redirect_url", ""),
    )
    return jsonify(body), status_code

@app.route("/api/subscription/payment/verify", methods=["POST"])
def subscription_payment_verify():
    from lib.subscription import verify_subscription_payment

    data = request.get_json(silent=True) or {}
    body, status_code = verify_subscription_payment(
        request.headers,
        data.get("invoice", ""),
        data.get("payment_token", ""),
    )
    return jsonify(body), status_code

@app.route("/api/payments/create", methods=["POST"])
@app.route("/api/payment/create", methods=["POST"])
@app.route("/api/bayargg/create", methods=["POST"])
def payment_create():
    try:
        from lib.bayar_gg import create_payment

        data, status_code = create_payment(request.get_json(silent=True) or {}, public_base_url())
        return jsonify(data), status_code
    except Exception as exc:
        return bayar_gg_error_response(exc)

@app.route("/api/payments/check")
@app.route("/api/payment/check")
@app.route("/api/bayargg/check")
def payment_check():
    try:
        from lib.bayar_gg import check_payment

        data, status_code = check_payment(request.args.get("invoice", ""))
        return jsonify(data), status_code
    except Exception as exc:
        return bayar_gg_error_response(exc)

@app.route("/api/payments/methods")
@app.route("/api/payment/methods")
@app.route("/api/bayargg/methods")
def payment_methods():
    try:
        from lib.bayar_gg import payment_methods as get_payment_methods

        data, status_code = get_payment_methods()
        return jsonify(data), status_code
    except Exception as exc:
        return bayar_gg_error_response(exc)

@app.route("/api/payments/webhook", methods=["POST"])
@app.route("/api/payment/webhook", methods=["POST"])
@app.route("/api/bayargg/webhook", methods=["POST"])
@app.route("/webhook/payment", methods=["POST"])
def payment_webhook():
    try:
        from lib.bayar_gg import verify_webhook
        from lib.subscription import handle_subscription_webhook

        payload = request.get_json(silent=True) or {}
        valid, message = verify_webhook(payload, request.headers)
        if not valid:
            return jsonify({"status": "error", "message": message}), 401
        sub_body, sub_code = handle_subscription_webhook(payload)
        if sub_body.get("status") == "success":
            return jsonify(sub_body), sub_code
        return jsonify({
            "status": "success",
            "event": payload.get("event"),
            "invoice_id": payload.get("invoice_id"),
            "payment_status": payload.get("status"),
            "subscription": sub_body,
        }), 200
    except Exception as exc:
        return bayar_gg_error_response(exc)

@app.route("/api/proxy/sign")
def proxy_sign():
    denied = require_subscription()
    if denied:
        return denied

    target_url = request.args.get("url", "").strip()
    if not target_url:
        return jsonify({"error": "Missing url param"}), 400
    parsed = urlparse(target_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return jsonify({"error": "Invalid url param"}), 400
    if remote_api_enabled():
        return remote_api_json("/api/proxy/sign")

    return jsonify({"status": "success", "url": sign_proxy_url(target_url, public_base_url())})

@app.route("/api/remote-proxy")
def remote_proxy():
    import requests as req

    target_url = get_cached_remote_proxy_url(
        request.args.get("id", ""),
        request.args.get("exp", ""),
        request.args.get("sig", ""),
    )
    if not target_url:
        return jsonify({"status": "error", "message": "Remote proxy URL tidak valid atau sudah kedaluwarsa"}), 403

    try:
        resp = req.get(target_url, stream=True, timeout=25)
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        is_playlist = "mpegurl" in content_type.lower() or target_url.lower().split("?", 1)[0].endswith(".m3u8")

        if is_playlist:
            text = resp.text
            def rewrite(m):
                abs_link = urljoin(target_url, m.group(1).strip())
                return cache_remote_proxy_url(abs_link)
            text = re.sub(r"^(?!#)(?!\s*$)(.+)$", rewrite, text, flags=re.MULTILINE)
            return Response(text.encode(), status=resp.status_code, headers={
                "Content-Type": content_type,
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-store",
            })

        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return Response(generate(), status=resp.status_code, headers={
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502

@app.route("/<path:filename>")
def static_files(filename):
    if not is_safe_static_path(filename):
        return jsonify({"error": "Not Found"}), 404

    # 1. Cek file di root folder
    if os.path.exists(filename):
        resp = send_file(filename)
        if filename in ("index.html", "sw.js") or filename.endswith(".html"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
    
    # 2. Cek file di dalam folder public/
    public_path = os.path.join("public", filename)
    if os.path.exists(public_path):
        resp = send_file(public_path)
        if filename == "sw.js":
            resp.headers["Cache-Control"] = "no-store"
        return resp
    
    # 3. Fallback untuk SPA (Single Page Application)
    if not request.path.startswith('/api/'):
        if os.path.exists("index.html"):
            resp = send_file("index.html")
            resp.headers["Cache-Control"] = "no-store"
            return resp
            
    return jsonify({"error": "Not Found"}), 404


# ── 2. API ROUTES ─────────────────────────────────────────────────────────────

@app.route("/api/debug")
def debug():
    results = {"python": sys.version, "env": "production (railway)"}

    def _get_pkg_version(name: str):
        try:
            mod = __import__(name)
        except Exception as imp_err:
            return False, str(imp_err)

        # Common version attributes
        for attr in ("__version__", "version", "VERSION"):
            v = getattr(mod, attr, None)
            if isinstance(v, str):
                return True, v
            if hasattr(v, "__version__"):
                return True, getattr(v, "__version__", "?")

        # Package-specific fallbacks
        if name == "yt_dlp":
            try:
                import yt_dlp as y
                ver = getattr(y, "__version__", None) or getattr(y, "version", None)
                if isinstance(ver, str):
                    return True, ver
            except Exception:
                pass

        if name == "playwright":
            try:
                import importlib.metadata as md
                ver = md.version("playwright")
                return True, ver
            except Exception:
                pass

        # Try importlib.metadata with the module name as distribution name
        try:
            import importlib.metadata as md
            ver = md.version(name)
            return True, ver
        except Exception:
            pass

        return True, "?"

    for pkg in ["requests", "bs4", "yt_dlp", "playwright", "PIL", "pillow_heif"]:
        ok, info = _get_pkg_version(pkg)
        if not ok:
            results[pkg] = f"MISSING: {info}"
        else:
            results[pkg] = f"OK ({info})"

    return jsonify(results)

@app.route("/api/get-video")
def get_video():
    denied = require_subscription()
    if denied:
        return denied
    if remote_api_enabled():
        return remote_api_json("/api/get-video")

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
    denied = require_subscription()
    if denied:
        return denied
    if remote_api_enabled():
        return remote_api_json("/api/tmdb-stream")

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
            proxy_base=None,
        )
        if data.get("rawStreamUrl"):
            data["streamUrl"] = sign_proxy_url(data["rawStreamUrl"], f"{scheme}://{host}")
            data["stream_url"] = data["streamUrl"]
            data["link"] = data["streamUrl"]
        return jsonify(data), 200 if data.get("success") else 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/dracin", defaults={"subpath": ""})
@app.route("/api/dracin/<path:subpath>")
def dracin_api(subpath):
    denied = require_subscription()
    if denied:
        return denied

    try:
        # Protect Dramanova platform: require token if configured
        platform = request.args.get('platform', 'all')
        # Allow auth endpoint through
        if DRAMANOVA_PIN and platform == 'dramanova':
            # accept token via header or query param
            token = request.headers.get('X-Dramanova-Token') or request.args.get('dramanova_token') or request.cookies.get('dramanova_token')
            if not token or not _validate_dramanova_token(token):
                return jsonify({"status": "error", "message": "Access to dramanova requires PIN authentication"}), 403
        mod = load("api/dracin.py")
        params = {k: request.args.getlist(k) for k in request.args.keys()}
        # ensure validated token is forwarded to build_response which validates query params
        if DRAMANOVA_PIN and platform == 'dramanova' and token:
            params['dramanova_token'] = [token]
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
        resp = jsonify({"status": "success", "token": token})
        # set cookie so browsers will send token automatically (fallback when headers/JS are blocked)
        resp.set_cookie(
            'dramanova_token', token,
            max_age=3600, secure=True, httponly=True, samesite='Lax'
        )
        return resp, 200
    return jsonify({"status": "error", "message": "PIN invalid"}), 403

@app.route("/api/scan", methods=["POST"])
def scan():
    denied = require_subscription()
    if denied:
        return denied
    if remote_api_enabled():
        return remote_api_json("/api/scan")

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
    denied = require_subscription()
    if denied:
        return denied
    if remote_api_enabled():
        return remote_api_json("/api/formats")

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
    denied = require_subscription()
    if denied:
        return denied

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
    denied = require_subscription()
    if denied:
        return denied
    if remote_api_enabled():
        return remote_api_json("/api/imdb")

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
            season = request.args.get("s") or request.args.get("season") or "1"
            episode = request.args.get("e") or request.args.get("episode") or "1"
            raw_url = mod.get_fast_stream(imdb_id, media_type, season, episode)
            if raw_url:
                scheme = request.headers.get('X-Forwarded-Proto', 'https')
                host = request.host
                info["stream_url"] = sign_proxy_url(raw_url, f"{scheme}://{host}")
                info["rawStreamUrl"] = raw_url
                info["streamResolver"] = "imdb-vaplayer"
                info["season"] = int(season) if str(season).isdigit() else season
                info["episode"] = int(episode) if str(episode).isdigit() else episode
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
    denied = require_proxy_signature(target_url)
    if denied:
        return denied

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
        configured = {**common, **VIDEO_SPOOF_HEADERS}
        origin_ref = {**common, "Referer": f"{origin}/", "Origin": origin}
        no_origin = {**common, "Referer": f"{origin}/"}
        player_ref_origin = spoof_origin or "https://streamdata.vaplayer.ru"
        player_ref = {**common, "Referer": f"{player_ref_origin}/", "Origin": player_ref_origin}

        variants = []
        for name, headers in (
            ("dev", VIDEO_SPOOF_HEADERS),
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

        session = req.Session()
        for name, headers in _header_variants(url):
            resp = session.get(
                url,
                headers=headers,
                proxies=proxy_cfg,
                stream=True,
                timeout=15 if name == "dev" else 20,
            )
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

        try:
            from curl_cffi import requests as curl_req
            for browser in ("chrome124", "chrome120", "chrome101"):
                resp = curl_req.get(
                    url,
                    headers=VIDEO_SPOOF_HEADERS,
                    impersonate=browser,
                    proxies=proxy_cfg,
                    stream=True,
                    timeout=20,
                )
                last_resp = resp
                attempts.append({"client": "curl_cffi", "headers": "dev", "impersonate": browser, "status": resp.status_code, "outbound_proxy": bool(stream_proxy)})
                if not is_playlist:
                    if resp.ok:
                        return resp, attempts, None
                    continue

                content_type, content, sample, looks_like_playlist, looks_like_html = _playlist_state(resp)
                last_playlist_text = content
                if resp.ok and looks_like_playlist and not looks_like_html:
                    return resp, attempts, content
        except Exception as err:
            attempts.append({"client": "curl_cffi", "error": str(err)})

        return last_resp, attempts, last_playlist_text

    try:
        is_playlist_url = target_url.lower().split("?", 1)[0].endswith(".m3u8")
        resp, attempts, cached_playlist_text = _fetch_video(target_url, is_playlist_url)
        if resp is None:
            return jsonify({"status": "error", "message": "Proxy request failed", "attempts": attempts}), 502
        content_type = resp.headers.get("Content-Type", "application/octet-stream")

        if "mpegurl" in content_type.lower() or is_playlist_url:
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
                return sign_proxy_url(abs_link)

            new_content = re.sub(r"^(?!#)(?!\s*$)(.+)$", rewrite, content, flags=re.MULTILINE)
            return Response(
                new_content.encode(),
                status=resp.status_code,
                headers={
                    "Content-Type":                content_type,
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control":               "no-store",
                },
            )

        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return Response(
            generate(),
            status=resp.status_code,
            headers={
                "Content-Type":                content_type,
                "Access-Control-Allow-Origin": "*",
                "Cache-Control":               "no-store",
            },
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proxy-browser")
def proxy_browser():
    denied = require_subscription()
    if denied:
        return denied

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return jsonify({"error": "playwright not installed. Install playwright and run `playwright install` on the host."}), 501

    target_url = request.args.get("url", "").strip()
    if not target_url:
        return "Missing url param", 400

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            # render page and wait for network to settle
            page.goto(target_url, timeout=20000, wait_until="networkidle")
            html = page.content()

            # try to find .m3u8 URL in rendered HTML
            import re
            m = re.search(r'https?://[^"\'"\s>]+\\.m3u8[^"\'"\s>]*', html)
            if m:
                m3u8_url = m.group(0)
                try:
                    import requests as req
                    try:
                        from lib.config import VIDEO_SPOOF_HEADERS
                    except Exception:
                        VIDEO_SPOOF_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://imdb.com/"}

                    r2 = req.get(m3u8_url, headers=VIDEO_SPOOF_HEADERS, stream=True, timeout=20)
                    content_type = r2.headers.get("Content-Type", "application/vnd.apple.mpegurl")

                    if r2.status_code == 200:
                        content = r2.text

                        def rewrite(m):
                            abs_link = urljoin(m3u8_url, m.group(1))
                            return sign_proxy_url(abs_link)

                        new_content = re.sub(r"^(?!#)(.+)$", rewrite, content, flags=re.MULTILINE)
                        browser.close()
                        return Response(
                            new_content.encode(),
                            status=200,
                            headers={"Content-Type": content_type, "Access-Control-Allow-Origin": "*"},
                        )
                    else:
                        browser.close()
                        return jsonify({"error": "failed fetching m3u8", "status": r2.status_code}), 502
                except Exception as err:
                    browser.close()
                    return jsonify({"error": str(err)}), 502

            # no m3u8 found in page
            browser.close()
            return jsonify({"status": "no_m3u8", "message": "No .m3u8 found in rendered page"}), 404

    except Exception as e:
        traceback.print_exc()
        msg = str(e)
        status = 501 if "Executable doesn't exist" in msg or "playwright install" in msg else 500
        return jsonify({"error": msg}), status

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
    denied = require_subscription()
    if denied:
        return denied
    if remote_api_enabled():
        return remote_api_json("/api/subtitle/search")

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
    denied = require_subscription()
    if denied:
        return denied
    if remote_api_enabled():
        return remote_api_passthrough("/api/subtitle/download")

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
    denied = require_subscription()
    if denied:
        return denied

    from api.melolo import languages
    data, code = languages(platform)
    return jsonify(data), code


@app.route("/api/melolo/home")
@app.route("/api/captain/<platform>/home")
def captain_home(platform="melolo"):
    denied = require_subscription()
    if denied:
        return denied

    from api.melolo import home
    lang = request.args.get("lang", "en")
    data, code = home(platform, lang)
    return jsonify(data), code


@app.route("/api/melolo/tabs")
@app.route("/api/captain/<platform>/tabs")
def captain_tabs(platform="melolo"):
    denied = require_subscription()
    if denied:
        return denied

    from api.melolo import tabs
    gender = request.args.get("gender", "0")
    lang   = request.args.get("lang", "en")
    data, code = tabs(platform, gender, lang)
    return jsonify(data), code


@app.route("/api/melolo/categories")
@app.route("/api/captain/<platform>/categories")
def captain_categories(platform="melolo"):
    denied = require_subscription()
    if denied:
        return denied

    from api.melolo import categories
    gender = request.args.get("gender", "0")
    lang   = request.args.get("lang", "en")
    data, code = categories(platform, gender, lang)
    return jsonify(data), code


@app.route("/api/melolo/search")
@app.route("/api/captain/<platform>/search")
def captain_search(platform="melolo"):
    denied = require_subscription()
    if denied:
        return denied

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
    denied = require_subscription()
    if denied:
        return denied

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
    denied = require_subscription()
    if denied:
        return denied

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
    denied = require_subscription()
    if denied:
        return denied

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
    denied = require_subscription()
    if denied:
        return denied

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
