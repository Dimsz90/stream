"""
Vidgf.com video extractor — single source of truth.
Sebelumnya logic ini duplikat di api/imdb.py dan api/get-video.py.
"""
import re
import json
import base64
import requests


_BASE_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
}

_REFERERS = [
    "https://simemek.com/",
    "https://montok.live/",
    "https://vidgf.com/",
]


def extract(video_id: str) -> str | None:
    """
    Coba ekstrak direct video URL dari vidgf.com.
    Return URL string atau None.
    """
    endpoints = [
        f"https://vidgf.com/embed.php?id={video_id}",
        f"https://vidgf.com/d/{video_id}",
    ]

    for endpoint in endpoints:
        for referer in _REFERERS:
            try:
                r = requests.get(
                    endpoint,
                    headers={
                        **_BASE_HEADERS,
                        "Referer": referer,
                        "Origin":  referer.rstrip("/"),
                    },
                    timeout=10,
                )
                if r.status_code != 200 or len(r.text) < 50:
                    continue
                url = _parse_video_url(r.text)
                if url:
                    return url
            except Exception:
                continue
    return None


def _parse_video_url(content: str) -> str | None:
    """Parse HTML/JS content untuk menemukan URL video."""

    # 1. URL mp4/m3u8/webm langsung
    m = re.search(
        r"(https?://[^\s\"'<>\\]+?\.(?:mp4|m3u8|webm)(?:\?[^\s\"'<>\\]*)?)",
        content, re.I,
    )
    if m:
        return m.group(1).replace("\\/", "/")

    # 2. Variabel JS (file/src/url/source/stream/hls = "...")
    m = re.search(
        r'(?:file|src|url|source|stream|hls)\s*[:=]\s*["\']([^"\']{15,})["\']',
        content, re.I,
    )
    if m:
        u = m.group(1).replace("\\/", "/")
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("http"):
            return u

    # 3. JSON sources array
    m = re.search(r"sources\s*[:=]\s*(\[.*?\])", content, re.DOTALL | re.I)
    if m:
        try:
            for s in json.loads(m.group(1)):
                u = s.get("file") or s.get("src") or s.get("url") or ""
                if u.startswith("http"):
                    return u.replace("\\/", "/")
        except Exception:
            pass

    # 4. Base64 encoded URL
    for b64 in re.findall(r'["\']([A-Za-z0-9+/]{30,}={0,2})["\']', content):
        try:
            d = base64.b64decode(b64 + "==").decode("utf-8", errors="ignore")
            if any(ext in d for ext in [".mp4", ".m3u8", ".webm"]) or re.match(
                r"https?://", d
            ):
                return d.strip()
        except Exception:
            continue

    return None
