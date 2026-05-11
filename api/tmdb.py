"""
api/tmdb.py
TMDB metadata + Vaplayer stream resolver for movie and TV episodes.
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys
from urllib.parse import urlparse, parse_qs, quote

import requests

sys.path.insert(0, os.path.dirname(__file__))
from lib.config import TMDB_API_KEY, TMDB_BASE, VIDEO_SPOOF_HEADERS
from lib.cache import tmdb_cache

VAPLAYER_URL = "https://streamdata.vaplayer.ru/api.php"
IMG_BASE = "https://image.tmdb.org/t/p/w500"
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


def _pick_vaplayer_stream(streams):
    if not isinstance(streams, list):
        return None

    def score(url):
        s = str(url or "").replace("\\/", "/")

        # Deprioritaskan host yang bermasalah
        for bad in DEPRIORITIZED_HOSTS:
            if bad in s:
                return (-1, 0, 0)

        # Semua host Vaplayer CDN dapat base score sama — dynamic
        host_score = 50 if _is_vaplayer_stream(s) else 0
        ext_score = 10 if ".m3u8" in s else 0
        # master.m3u8 lebih baik karena punya multi-quality
        kind_score = 5 if "/master.m3u8" in s else 0
        return (host_score, ext_score + kind_score, -len(s))

    urls = [str(u or "").replace("\\/", "/") for u in streams if u]
    if not urls:
        return None
    return sorted(urls, key=score, reverse=True)[0]


def _get_json(url, params=None, ttl=3600):
    cache_key = f"tmdb:http:{url}:{json.dumps(params or {}, sort_keys=True)}"
    cached = tmdb_cache.get(cache_key)
    if cached is not None:
        return cached

    q = {"api_key": TMDB_API_KEY, "language": "id-ID"}
    if params:
        q.update(params)
    r = requests.get(url, params=q, timeout=8)
    if r.status_code != 200:
        return None
    data = r.json()
    tmdb_cache.set(cache_key, data, ttl=ttl)
    return data


def get_media_info(tmdb_id, media_type="movie"):
    return _get_json(
        f"{TMDB_BASE}/{media_type}/{tmdb_id}",
        {"append_to_response": "external_ids"},
        ttl=86400,
    )


def get_episode_info(tmdb_id, season, episode):
    return _get_json(
        f"{TMDB_BASE}/tv/{tmdb_id}/season/{season}/episode/{episode}",
        ttl=21600,
    )


def get_season_info(tmdb_id, season):
    return _get_json(
        f"{TMDB_BASE}/tv/{tmdb_id}/season/{season}",
        ttl=86400,
    )


def find_stream(tmdb_id, media_type="movie", season=None, episode=None):
    media_type = "tv" if media_type == "tv" else "movie"
    cache_key = f"stream:{media_type}:{tmdb_id}"
    params = {"tmdb": tmdb_id, "type": media_type}

    if media_type == "tv":
        cache_key += f":s{season}:e{episode}"
        params["season"] = season
        params["episode"] = episode

    cached = tmdb_cache.get(cache_key)
    if cached:
        return cached

    try:
        r = requests.get(
            VAPLAYER_URL,
            params=params,
            headers=VIDEO_SPOOF_HEADERS,
            timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        ok = str(data.get("status_code")) == "200" or data.get("status") == "success"
        streams = data.get("data", {}).get("stream_urls", [])
        if ok and streams:
            url = _pick_vaplayer_stream(streams)
            tmdb_cache.set(cache_key, url, ttl=30)
            return url
    except Exception:
        return None
    return None


def _poster(path):
    if not path:
        return ""
    if str(path).startswith("http"):
        return path
    return IMG_BASE + path


def build_stream_payload(tmdb_id, media_type="movie", season=1, episode=1, proxy_base=None):
    media_type = "tv" if media_type == "tv" else "movie"
    media = get_media_info(tmdb_id, media_type) or {}
    episode_info = None
    season_info = None

    if media_type == "tv":
        episode_info = get_episode_info(tmdb_id, season, episode) or {}
        season_info = get_season_info(tmdb_id, season) or {}

    stream_url = find_stream(tmdb_id, media_type, season, episode)
    proxied_url = stream_url
    if proxy_base and stream_url:
        try:
            from lib.proxy_signing import sign_proxy_url
            proxied_url = sign_proxy_url(stream_url, proxy_base)
        except Exception:
            proxied_url = f"{proxy_base}/api/proxy?url={quote(stream_url)}"

    title = media.get("title") or media.get("name") or "Unknown Title"
    ep_title = None
    if media_type == "tv":
        ep_title = episode_info.get("name") or f"Episode {episode}"

    return {
        "status": "success" if stream_url else "error",
        "success": bool(stream_url),
        "type": media_type,
        "title": title,
        "episodeTitle": ep_title,
        "poster": _poster(media.get("poster_path")),
        "streamUrl": proxied_url,
        "stream_url": proxied_url,
        "rawStreamUrl": stream_url,
        "link": proxied_url,
        "tmdbId": str(tmdb_id),
        "season": int(season) if media_type == "tv" else None,
        "episode": int(episode) if media_type == "tv" else None,
        "totalEpisodes": len(season_info.get("episodes") or []) if media_type == "tv" else None,
        "imdbId": (media.get("external_ids") or {}).get("imdb_id"),
        "runtime": media.get("runtime"),
        "releaseDate": media.get("release_date") or media.get("first_air_date"),
        "message": None if stream_url else "Stream URL tidak ditemukan",
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        tmdb_id = (params.get("id", [None])[0] or params.get("tmdb_id", [None])[0] or "").strip()
        media_type = (params.get("type", ["movie"])[0] or "movie").strip()
        season = int(params.get("s", params.get("season", ["1"]))[0] or 1)
        episode = int(params.get("e", params.get("episode", ["1"]))[0] or 1)

        if not tmdb_id:
            return self._send_json({"status": "error", "message": "TMDB ID kosong"}, 400)

        host = self.headers.get("Host", "")
        scheme = "http" if "localhost" in host or "127.0.0.1" in host else "https"
        proxy_base = f"{scheme}://{host}" if host else None

        try:
            data = build_stream_payload(tmdb_id, media_type, season, episode, proxy_base=proxy_base)
            return self._send_json(data, 200 if data["success"] else 404)
        except Exception as e:
            return self._send_json({"status": "error", "message": str(e)}, 500)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

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