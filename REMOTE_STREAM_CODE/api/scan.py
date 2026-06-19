from http.server import BaseHTTPRequestHandler
import json, re, requests
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

try:
    import yt_dlp
except ImportError:
    yt_dlp = None


def is_video_url(url):
    exts = {'.mp4','.webm','.ogg','.mov','.avi','.mkv','.flv','.m3u8','.ts'}
    return any(urlparse(url).path.lower().endswith(e) for e in exts)


def detect_platform(url):
    m = {"youtube.com":"YouTube","youtu.be":"YouTube","instagram.com":"Instagram",
         "tiktok.com":"TikTok","twitter.com":"Twitter/X","x.com":"Twitter/X",
         "vimeo.com":"Vimeo","facebook.com":"Facebook","dailymotion.com":"Dailymotion",
         "twitch.tv":"Twitch","reddit.com":"Reddit"}
    for k, v in m.items():
        if k in url: return v
    return "Direct file" if is_video_url(url) else "Web embed"


def format_duration(secs):
    if not secs: return ""
    secs = int(secs)
    h, m, s = secs//3600, (secs%3600)//60, secs%60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def extract(page_url):
    found = {}

    def add(url, title="", source="html", thumb="", duration=""):
        if not url or len(url) < 10: return
        url = urljoin(page_url, url)
        if url not in found:
            found[url] = {"url": url,
                          "title": title or url.split("/")[-1] or "Video",
                          "source": source, "platform": detect_platform(url),
                          "thumb": thumb, "duration": duration}

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    iframe_urls = []

    try:
        resp = requests.get(page_url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup.find_all(["video","source"]):
            src = tag.get("src") or tag.get("data-src") or tag.get("data-url")
            if src: add(src, source="<video>", thumb=tag.get("poster",""))

        for a in soup.find_all("a", href=True):
            if is_video_url(a["href"]): add(a["href"], title=a.get_text(strip=True), source="<a>")

        for meta in soup.find_all("meta"):
            prop = (meta.get("property") or meta.get("name") or "").lower()
            if "video" in prop:
                c = meta.get("content","")
                if c.startswith("http"): add(c, source="og:meta")

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, list): data = data[0]
                for key in ("contentUrl","embedUrl","url"):
                    v = data.get(key,"")
                    if v and ("video" in v or is_video_url(v)):
                        add(v, title=data.get("name",""), source="JSON-LD",
                            thumb=data.get("thumbnailUrl",""), duration=data.get("duration",""))
            except Exception: pass

        for tag in soup.find_all(True):
            for attr, val in tag.attrs.items():
                if isinstance(val, str) and "video" in attr.lower() and val.startswith("http"):
                    if is_video_url(val) or "video" in val: add(val, source="data-attr")

        for iframe in soup.find_all("iframe"):
            src = iframe.get("src") or iframe.get("data-src","")
            if src: iframe_urls.append(urljoin(page_url, src))

    except Exception as e:
        print(f"[scan] error: {e}")

    if yt_dlp:
        null_log = type("L",(),{"debug":lambda s,m:None,"info":lambda s,m:None,
                                 "warning":lambda s,m:None,"error":lambda s,m:None})()
        ydl_opts = {"quiet":True,"no_warnings":True,"extract_flat":"in_playlist",
                    "skip_download":True,"noplaylist":False,"ignoreerrors":True,
                    "logger":null_log,"nocheckcertificate":True,
                    "socket_timeout":8,"retries":2,
                    "http_headers":{"User-Agent": headers["User-Agent"]}}
        for target in [page_url] + iframe_urls:
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(target, download=False)
                    if not info: continue
                    for entry in (info.get("entries") or [info]):
                        if not entry: continue
                        url = entry.get("url") or entry.get("webpage_url","")
                        if url and url not in found:
                            add(url, title=entry.get("title",""), source="yt-dlp",
                                thumb=entry.get("thumbnail",""),
                                duration=format_duration(entry.get("duration")))
            except Exception: pass

    return list(found.values())


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length) or b"{}")
        url    = body.get("url","").strip()
        if not url:
            return self.send_json({"error": "URL required"}, 400)
        try:
            videos = extract(url)
            self.send_json({"videos": videos, "count": len(videos)})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass