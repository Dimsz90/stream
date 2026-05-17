"""
api/dracin.py
Handler multi-platform untuk Short Dramas (DramaBox, ReelShort, dll).
Base URL: https://captain.sapimu.au/{platform_prefix}

Struktur response DramaBox v4:
  - Semua response dibungkus: { code, message, data: { data: {...}, status, ... } }
  - /api/home      → data.data.sections[].books[] + data.data.classifyBookList.records[]
  - /api/rank      → data.data.rankList[]
  - /api/search    → data.data.searchList[]
  - /api/drama/:id → data.data.list[] (episode list), data.data.performers[], dll
  - /api/drama/:id/episodes → data.episodes[], data.bookName, data.cover, dll
"""
import os, sys, json, requests, time, threading
import hmac, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Fallback Cache ───────────────────────────────────────────────────────────
try:
    from lib.cache import TTLCache
except ImportError:
    class TTLCache:
        """Simple in-memory TTL cache (fallback)."""
        def __init__(self, default_ttl=300, max_size=200):
            self._store = {}
            self._default_ttl = default_ttl
            self._max_size = max_size
            self._lock = threading.Lock()

        def get(self, key):
            with self._lock:
                entry = self._store.get(key)
                if entry and time.time() < entry[1]:
                    return entry[0]
                return None

        def set(self, key, value, ttl=None):
            with self._lock:
                if len(self._store) >= self._max_size:
                    # Hapus entry paling lama
                    oldest = min(self._store, key=lambda k: self._store[k][1])
                    del self._store[oldest]
                self._store[key] = (value, time.time() + (ttl or self._default_ttl))

# ── Fallback Headers ─────────────────────────────────────────────────────────
try:
    from lib.config import HEADERS
except ImportError:
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    }

# ── Konfigurasi ──────────────────────────────────────────────────────────────
CAPTAIN_ROOT = os.environ.get("CAPTAIN_BASE_URL", "https://captain.sapimu.au").rstrip("/")
DRACIN_BASE  = os.environ.get("DRACIN_BASE_URL", CAPTAIN_ROOT).rstrip("/")
DRACIN_TOKEN = os.environ.get("DRACIN_TOKEN") or os.environ.get("CAPTAIN_TOKEN", "")

# Konfigurasi per-platform
# Setiap platform mendefinisikan prefix dan field mapping-nya sendiri
PLATFORMS = {
    "all": {
        "label": "Semua",
        "icon":  "",
        "_engine": "aggregate",
        "ttl_home":  600,
        "ttl_rank":  1800,
        "ttl_search":120,
    },
    "dramabox": {
        "prefix":   "/dramaboxv4",
        "label":    "DramaBox",
        "icon":     "📱",
        # Field mapping buku
        "book_id":      "bookId",
        "book_name":    "bookName",
        "cover":        "coverWap",
        "introduction": "introduction",
        "tags":         "tags",          # array of string langsung
        "play_count":   "playCount",
        "chapter_count":"chapterCount",
        # Field mapping episode (dari /api/drama/:id/episodes)
        "ep_list":      "episodes",
        "ep_num":       "episode",       # nomor episode
        "ep_url":       "url",           # URL video
        "ep_pay":       "isPay",
        "ep_title":     "chapterName",
        # Field mapping chapter list (dari /api/drama/:id — detail)
        "ch_list":      "list",
        "ch_id":        "chapterId",
        "ch_index":     "chapterIndex",
        "ch_pay":       "isPay",
        # Nested data path di response
        # /api/home    → response["data"]["data"]["sections"] dan ["classifyBookList"]["records"]
        # /api/rank    → response["data"]["data"]["rankList"]
        # /api/search  → response["data"]["data"]["searchList"]
        # /api/drama/:id → response["data"]["data"]  (chapter list di .list[])
        # /api/drama/:id/episodes → response["data"]  (flat, episodes di .episodes[])
        "ttl_home":  600,
        "ttl_rank":  3600,
        "ttl_search":120,
        "ttl_detail":300,
        "ttl_ep":    300,
    },
    "reelshort": {
        "label":    "ReelShort",
        "icon":     "🎬",
        "_engine": "reelshort",
        "ttl_home":  600,
        "ttl_rank":  3600,
        "ttl_search":120,
        "ttl_detail":300,
        "ttl_ep":    300,
        "ttl_video": 120,
    },
    # ── Melolo — pakai Captain API v1 (endpoint berbeda) ──────────────────────
    # Tidak pakai prefix/dracin-style fetch, ditangani oleh _melolo_fetch()
    "melolo": {
        "label": "Melolo",
        "icon":  "🎭",
        "_engine": "melolo",   # flag: pakai engine captain-v1
        "ttl_home":  600,
        "ttl_rank":  3600,
        "ttl_search":120,
        "ttl_ep":    300,
    },
    "cubetv": {
        "label": "CubeTV",
        "icon":  "📺",
        "_engine": "cubetv",
        "ttl_home":  600,
        "ttl_rank":  1800,
        "ttl_search":120,
        "ttl_detail":300,
        "ttl_ep":    300,
        "ttl_video": 120,
    },
    "dramanova": {
        "label": "Dramanova",
        "icon":  "",
        "_engine": "dramanova",
        "ttl_home":  600,
        "ttl_rank":  1800,
        "ttl_search":120,
        "ttl_detail":300,
        "ttl_ep":    300,
        "ttl_video": 300,
    },
    "shortwave": {
        "label": "ShortWave",
        "icon":  "",
        "_engine": "shortwave",
        "ttl_home":  600,
        "ttl_rank":  1800,
        "ttl_search":120,
        "ttl_detail":300,
        "ttl_ep":    300,
        "ttl_video": 120,
    },
}

AGGREGATE_PLATFORMS = ("dramabox", "reelshort", "melolo", "shortwave", "cubetv")

# ── Melolo / Captain v1 config ────────────────────────────────────────────────
CAPTAIN_BASE  = CAPTAIN_ROOT
CAPTAIN_TOKEN = os.environ.get("CAPTAIN_TOKEN", DRACIN_TOKEN)  # fallback ke token dracin

def _melolo_headers() -> dict:
    h = HEADERS.copy()
    h["Authorization"] = f"Bearer {CAPTAIN_TOKEN}"
    h["Content-Type"] = "application/json"
    h["Accept"] = "application/json"
    return h

def _melolo_fetch(platform: str, path: str, params: dict | None = None, ttl: int = 300):
    """Fetch dari Captain API v1 (melolo, dll) dengan cache."""
    cache_key = f"melolo:{platform}:{path}:{json.dumps(params or {}, sort_keys=True)}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{CAPTAIN_BASE}/{platform}{path}"
    try:
        resp = requests.get(url, headers=_melolo_headers(), params=params, timeout=15)
        data = resp.json()
        _cache.set(cache_key, data, ttl)
        return data
    except Exception as e:
        print(f"[MELOLO:{platform}] ERROR {path}: {e}")
        return None


def _cubetv_fetch(path: str, params: dict | None = None, ttl: int = 300):
    """Fetch dari Captain API untuk CubeTV."""
    if params and "lang" in params:
        params = dict(params)
        if str(params.get("lang", "")).lower() in ("in", "id"):
            params["lang"] = "id"
    return _melolo_fetch("cubetv", path, params=params, ttl=ttl)



IMG_PROXY_PATH = "/api/img-proxy"   # path proxy di server kamu

def _proxy_img(url: str, ref: str = "") -> str:
    """
    Wrap URL gambar ke endpoint proxy lokal agar tidak kena CORS/ORB block.
    """
    if not url:
        return url
    if url.startswith(IMG_PROXY_PATH) or url.startswith("/"):
        return url
    from urllib.parse import quote
    proxied = f"{IMG_PROXY_PATH}?url={quote(url, safe='')}"
    if ref:
        proxied += f"&ref={quote(ref, safe='')}"
    return proxied

def _normalize_cover_url(url: str, ref: str = "") -> str:
    """
    Signed fizzopic URLs must be requested exactly as-is. Changing .heic to .jpeg
    can invalidate x-signature, so route them through the image proxy instead.
    """
    if not url:
        return url
    url_lower = url.lower()
    if "fizzopic.org" in url_lower or ".heic" in url_lower or ".avif" in url_lower:
        return _proxy_img(url, ref=ref)
    return url

def _heic_to_jpeg(url: str) -> str:
    """
    Konversi URL CDN ByteDance dari HEIC ke JPEG via URL transform.

    ByteDance ImageX CDN support on-the-fly format conversion — cukup ganti
    ekstensi di path transform, tidak perlu server-side processing.

    Dua format URL yang diketahui:
      1. ibyteimg.com (unsigned, browser-accessible):
           ...~tplv-shrink:640:0.heic
           → ...~tplv-shrink:640:0.jpeg
      2. fizzopic.org (signed, IP-whitelisted — jangan dipakai untuk cover):
           ...~tplv-836v1mcgsk-image-quality-ttk1-cp:570:810.heic?rk3s=...
           → ...~tplv-836v1mcgsk-image-quality-ttk1-cp:570:810.jpeg?rk3s=...
    """
    if not url:
        return url
    url_lower = url.lower()
    if ".heic" not in url_lower and ".avif" not in url_lower:
        return url
    import re
    if "?" in url:
        path_part, query_part = url.split("?", 1)
        path_part = re.sub(r"\.(heic|avif)$", ".jpeg", path_part, flags=re.IGNORECASE)
        return f"{path_part}?{query_part}"
    return re.sub(r"\.(heic|avif)$", ".jpeg", url, flags=re.IGNORECASE)


def _melolo_norm_book(raw: dict) -> dict:
    """
    Normalisasi item dari Captain v1 ke format dracin standar.

    Field yang diketahui dari response nyata:
      book_id, book_name, thumb_url, abstract, serial_count,
      last_chapter_index, category_info (JSON string array of {Name, ...})
    """
    # ── book_id ───────────────────────────────────────────────────────────────
    book_id = (raw.get("book_id") or raw.get("id") or raw.get("bookId") or "")

    # ── title ─────────────────────────────────────────────────────────────────
    title = (raw.get("book_name") or raw.get("title") or raw.get("name")
             or raw.get("bookName") or "Untitled")

    # ── cover ─────────────────────────────────────────────────────────────────
    cover = _normalize_cover_url(
        raw.get("thumb_url")
        or raw.get("cover")
        or raw.get("cover_url")
        or raw.get("coverUrl")
        or raw.get("thumbnail")
        or "",
        ref="https://melolo.tv/",
    )

    # ── synopsis ──────────────────────────────────────────────────────────────
    intro = (raw.get("abstract") or raw.get("description")
             or raw.get("introduction") or raw.get("sub_abstract") or "")

    # ── episode count ─────────────────────────────────────────────────────────
    # serial_count = total episode sudah publish; last_chapter_index = index terakhir
    chapter_count = int(
        raw.get("serial_count") or raw.get("last_chapter_index") or
        raw.get("episode_count") or raw.get("episodeCount") or
        raw.get("chapterCount") or 0
    )

    # ── tags dari category_info (JSON string) atau categories / tags ──────────
    tags = []
    cat_info = raw.get("category_info")
    if cat_info and isinstance(cat_info, str):
        try:
            import json as _json
            cats = _json.loads(cat_info)
            # Ambil hanya MainCategory=true atau semua jika tidak ada flag
            main_cats = [c for c in cats if c.get("MainCategory")]
            source = main_cats if main_cats else cats
            tags = [c.get("Name", "") for c in source if c.get("Name")]
        except Exception:
            pass
    if not tags:
        raw_tags = raw.get("categories") or raw.get("tags") or []
        if raw_tags and isinstance(raw_tags, list):
            if raw_tags and isinstance(raw_tags[0], dict):
                tags = [t.get("Name", t.get("name", t.get("tagName", "")))
                        for t in raw_tags if t.get("Name") or t.get("name")]
            else:
                tags = [str(t) for t in raw_tags]

    # ── play count ────────────────────────────────────────────────────────────
    # cover_stat_infos[stat_type=6] = view count dengan suffix M/B
    play_count = 0
    stat_infos = raw.get("cover_stat_infos") or []
    for s in stat_infos:
        if s.get("stat_type") == 6 and s.get("stat_value"):
            # "45.6M" → simpan sebagai string, konversi ke int jika perlu
            play_count = s["stat_value"]
            break
    if not play_count:
        play_count = raw.get("playCount") or raw.get("viewCount") or raw.get("views") or 0

    return {
        "bookId":       str(book_id),
        "bookName":     title,
        "cover":        cover,
        "introduction": intro,
        "tags":         tags,
        "playCount":    play_count,
        "chapterCount": chapter_count,
        # Field tambahan yang berguna di frontend
        "isDubbed":     raw.get("is_dubbed") == "1",
        "isHot":        raw.get("is_hot") == "1",
        "status":       raw.get("show_creation_status", ""),
        "language":     raw.get("language", ""),
        "statInfos":    raw.get("stat_infos") or [],
        "_raw":         raw,
    }

def _melolo_norm_episode(raw: dict, idx: int) -> dict:
    """Normalisasi episode dari Captain v1 ke format dracin standar."""
    ep_num = raw.get("episode", raw.get("episodeNum", raw.get("chapterIndex", idx) + 1))
    return {
        "episode":      ep_num,
        "chapterIndex": idx,
        "url":          raw.get("url", raw.get("videoUrl", raw.get("playUrl", raw.get("streamUrl", "")))),
        "isPay":        int(raw.get("isPay", raw.get("isLock", raw.get("lock", 0)))),
        "chapterName":  raw.get("title", raw.get("chapterName", f"Episode {ep_num}")),
        "_raw":         raw,
    }

def _melolo_extract_list(data) -> list:
    """
    Ekstrak list item dari berbagai bentuk response Captain v1.

    Prioritas:
    1. bookmall shape: data.cell.cell_data[].books[]
    2. flat list langsung
    3. key umum lainnya
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    # ── Shape: /api/v1/bookmall → { cell: { cell_data: [ { books: [...] } ] } }
    cell = data.get("cell")
    if isinstance(cell, dict):
        cell_data = cell.get("cell_data", [])
        if isinstance(cell_data, list):
            books = []
            for section in cell_data:
                for b in section.get("books", []):
                    books.append(b)
            if books:
                return books

    # ── Shape flat atau key umum
    for key in ("books", "list", "items", "results", "videos",
                "bookList", "seriesList", "videoList", "data"):
        val = data.get(key)
        if isinstance(val, list) and val:
            return val

    return []


def _dramanova_fetch(path: str, params: dict | None = None, ttl: int = 300):
    """Fetch Captain Dramanova endpoints."""
    return _melolo_fetch("dramanova", path, params=params, ttl=ttl)


def _dramanova_norm_book(raw: dict) -> dict:
    tags = raw.get("categoryNames") or raw.get("categories") or raw.get("tags") or []
    return {
        "bookId":       str(raw.get("id") or raw.get("bookId") or ""),
        "bookName":     raw.get("title") or raw.get("name") or "Untitled",
        "cover":        raw.get("cover") or raw.get("poster") or "",
        "introduction": raw.get("description") or "",
        "tags":         [str(t) for t in tags] if isinstance(tags, list) else [],
        "playCount":    raw.get("viewCount") or 0,
        "chapterCount": raw.get("episodes") or raw.get("totalEpisodes") or 0,
        "isCompleted":  raw.get("isCompleted"),
        "publishedAt":  raw.get("publishedAt", ""),
        "_raw":         raw,
    }


def _dramanova_pick_video_url(raw: dict) -> str:
    videos = raw.get("videos") or []
    if not isinstance(videos, list) or not videos:
        return ""

    def score(v):
        quality_score = {"higher": 3, "normal": 2, "lower": 1}.get(str(v.get("quality", "")).lower(), 0)
        definition = str(v.get("definition", ""))
        try:
            definition_score = int("".join(ch for ch in definition if ch.isdigit()) or 0)
        except Exception:
            definition_score = 0
        return (quality_score, definition_score, int(v.get("bitrate") or 0))

    best = sorted(videos, key=score, reverse=True)[0]
    return best.get("main_url") or best.get("backup_url") or ""


def get_dramanova_video(file_id: str) -> dict | None:
    if not file_id:
        return None
    raw = _dramanova_fetch("/api/video", {"id": file_id}, ttl=PLATFORMS["dramanova"]["ttl_video"])
    if not isinstance(raw, dict):
        return None
    direct_url = _dramanova_pick_video_url(raw)
    if direct_url:
        try:
            from lib.proxy_signing import sign_proxy_url
            proxied_url = sign_proxy_url(direct_url)
        except Exception:
            proxied_url = f"/api/proxy?url={quote(direct_url, safe='')}"
    else:
        proxied_url = ""
    return {
        "vid":       raw.get("vid") or file_id,
        "url":       proxied_url,
        "directUrl": direct_url,
        "poster":    raw.get("poster") or "",
        "duration":  raw.get("duration") or 0,
        "videos":    raw.get("videos") or [],
        "_raw":      raw,
    }


def _dramanova_norm_episode(raw: dict, idx: int) -> dict:
    ep_num = raw.get("number") or idx + 1
    file_id = raw.get("fileId") or raw.get("vid") or raw.get("id") or ""
    return {
        "episode":     ep_num,
        "chapterIndex":idx,
        "vid":         file_id,
        "fileId":      file_id,
        "episodeId":   str(raw.get("id") or ""),
        "url":         "",
        "isPay":       0 if raw.get("free", True) else 1,
        "title":       raw.get("title") or f"Episode {ep_num}",
        "cover":       raw.get("cover") or "",
        "subtitles":   raw.get("subtitles") or [],
        "_raw":        raw,
    }


def _reelshort_fetch(path: str, params: dict | None = None, ttl: int = 300):
    """Fetch Captain ReelShort endpoints."""
    return _melolo_fetch("reelshort", path, params=params, ttl=ttl)


def _reelshort_norm_book(raw: dict) -> dict:
    return {
        "bookId":       str(raw.get("book_id") or raw.get("bookId") or raw.get("id") or ""),
        "bookName":     raw.get("book_title") or raw.get("bookName") or raw.get("title") or "Untitled",
        "cover":        raw.get("book_pic") or raw.get("cover") or raw.get("coverImage") or "",
        "introduction": raw.get("special_desc") or raw.get("description") or raw.get("introduction") or "",
        "tags":         raw.get("tags") if isinstance(raw.get("tags"), list) else [],
        "playCount":    raw.get("read_count") or raw.get("collect_count") or raw.get("playCount") or 0,
        "chapterCount": raw.get("chapter_count") or raw.get("chapterCount") or 0,
        "collectCount": raw.get("collect_count") or 0,
        "isDubbed":     raw.get("is_dub") == 1,
        "firstChapterId": raw.get("first_chapter_id") or "",
        "_raw":         raw,
    }


def _reelshort_extract_books(raw: dict) -> list:
    data = raw.get("data") if isinstance(raw, dict) else {}
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    books = []
    for section in data.get("lists") or []:
        if isinstance(section, dict):
            books.extend(section.get("books") or [])
    if books:
        return books

    for key in ("books", "list", "rows", "records", "items", "dramas"):
        val = data.get(key)
        if isinstance(val, list) and val:
            return val
    return []


def _reelshort_extract_rank_books(raw: dict) -> list:
    data = raw.get("data") if isinstance(raw, dict) else {}
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    place_list = data.get("place_list") or data.get("placeList") or data.get("rankings") or []
    books = []
    for place in place_list:
        if isinstance(place, dict):
            books.extend(place.get("list") or place.get("books") or [])
    if books:
        return books
    return _reelshort_extract_books(raw)


def _reelshort_norm_episode(raw: dict, idx: int) -> dict:
    ep_num = raw.get("serial_number") or raw.get("episode") or idx + 1
    chapter_id = raw.get("chapter_id") or raw.get("chapterId") or raw.get("id") or ""
    return {
        "episode":     ep_num,
        "chapterIndex":idx,
        "chapterId":   str(chapter_id),
        "vid":         str(chapter_id),
        "url":         raw.get("url") or raw.get("videoUrl") or "",
        "isPay":       int(raw.get("is_lock") or raw.get("isLock") or raw.get("isCharge") or 0),
        "title":       raw.get("chapter_name") or raw.get("chapterName") or f"Episode {ep_num}",
        "duration":    raw.get("duration") or 0,
        "_raw":        raw,
    }


def _reelshort_pick_video_url(raw: dict) -> str:
    videos = raw.get("videos") or []
    if not isinstance(videos, list) or not videos:
        return ""

    def score(v):
        try:
            dpi = int(v.get("Dpi") or v.get("dpi") or 0)
        except Exception:
            dpi = 0
        encode = str(v.get("Encode") or v.get("encode") or "").upper()
        compat = 2 if encode in ("H264", "AVC", "AVC1") else 1 if encode == "H265" else 0
        return (compat, dpi)

    best = sorted(videos, key=score, reverse=True)[0]
    return best.get("PlayURL") or best.get("playUrl") or best.get("url") or ""


def get_reelshort_video(book_id: str, chapter_id: str, lang="in") -> dict | None:
    if not book_id or not chapter_id:
        return None
    raw = _reelshort_fetch(
        f"/api/v1/book/{book_id}/chapter/{chapter_id}/video",
        {"lang": lang},
        ttl=PLATFORMS["reelshort"]["ttl_video"],
    )
    data = raw.get("data") if isinstance(raw, dict) else {}
    if not isinstance(data, dict):
        return None
    direct_url = _reelshort_pick_video_url(data)
    return {
        "vid":       chapter_id,
        "chapterId": chapter_id,
        "url":       direct_url,
        "directUrl": direct_url,
        "locked":    data.get("locked", False),
        "videos":    data.get("videos") or [],
        "_raw":      data,
    }


def _shortwave_fetch(path: str, params: dict | None = None, ttl: int = 300):
    """Fetch Captain ShortWave endpoints."""
    return _melolo_fetch("shortwave", path, params=params, ttl=ttl)


def _shortwave_norm_book(raw: dict) -> dict:
    tags = raw.get("tags") or raw.get("categoryNames") or raw.get("categories") or []
    if isinstance(tags, str):
        tags = [tags]
    return {
        "bookId":       str(raw.get("drama_id") or raw.get("dramaId") or raw.get("id") or ""),
        "bookName":     raw.get("drama_title") or raw.get("title") or raw.get("name") or "Untitled",
        "cover":        raw.get("drama_cover") or raw.get("cover") or raw.get("poster") or "",
        "introduction": raw.get("description") or raw.get("drama_description") or "",
        "tags":         [str(t) for t in tags] if isinstance(tags, list) else [],
        "playCount":    raw.get("fav_count") or raw.get("viewCount") or raw.get("views") or 0,
        "chapterCount": raw.get("total_episodes") or raw.get("episode_count") or raw.get("chapterCount") or 0,
        "playUrl":      raw.get("play_url") or "",
        "_raw":         raw,
    }


def _shortwave_extract_top_list(raw: dict) -> list:
    data = raw.get("data") if isinstance(raw, dict) else {}
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    return (
        data.get("list")
        or data.get("rows")
        or data.get("dramas")
        or data.get("records")
        or data.get("items")
        or []
    )


def _shortwave_extract_rank_books(raw: dict) -> list:
    data = raw.get("data") if isinstance(raw, dict) else {}
    if not isinstance(data, dict):
        return []
    place_list = data.get("place_list") or data.get("placeList") or []
    books = []
    for place in place_list:
        if isinstance(place, dict):
            books.extend(place.get("list") or [])
    return books


def _shortwave_book_key(raw: dict) -> str:
    return str(
        raw.get("drama_id")
        or raw.get("dramaId")
        or raw.get("book_id")
        or raw.get("bookId")
        or raw.get("id")
        or raw.get("book_title")
        or raw.get("title")
        or raw.get("drama_title")
        or ""
    )


def _shortwave_merge_books(*groups: list) -> list:
    merged = []
    seen = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            key = _shortwave_book_key(item)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(item)
    return merged


def _shortwave_norm_episode(raw: dict, idx: int) -> dict:
    ep_num = raw.get("episode") or raw.get("chapter_index") or raw.get("chapterIndex") or idx + 1
    chapter_id = raw.get("chapter_id") or raw.get("chapterId") or raw.get("id") or ""
    return {
        "episode":     ep_num,
        "chapterIndex":idx,
        "chapterId":   str(chapter_id),
        "vid":         str(chapter_id),
        "url":         raw.get("stream_url") or raw.get("url") or "",
        "isPay":       0 if raw.get("is_free", True) else 1,
        "title":       raw.get("chapter_name") or raw.get("chapterName") or f"Episode {ep_num}",
        "cover":       raw.get("cover") or "",
        "duration":    raw.get("chapter_duration") or raw.get("duration") or 0,
        "subtitles":   raw.get("subtitles") or [],
        "nextChapterId": raw.get("next_chapter_id"),
        "prevChapterId": raw.get("prev_chapter_id"),
        "_raw":        raw,
    }


def get_shortwave_video(drama_id: str, chapter_id: str, lang="in") -> dict | None:
    if not drama_id or not chapter_id:
        return None
    raw = _shortwave_fetch(
        f"/api/stream/{drama_id}/{chapter_id}",
        {"lang": lang},
        ttl=PLATFORMS["shortwave"]["ttl_video"],
    )
    data = raw.get("data") if isinstance(raw, dict) else {}
    if not isinstance(data, dict):
        return None
    direct_url = data.get("stream_url") or ""
    return {
        "vid":       chapter_id,
        "chapterId": chapter_id,
        "url":       direct_url,
        "directUrl": direct_url,
        "duration":  data.get("chapter_duration") or 0,
        "subtitles": data.get("subtitles") or [],
        "nextChapterId": data.get("next_chapter_id"),
        "prevChapterId": data.get("prev_chapter_id"),
        "_raw":      data,
    }


def _cubetv_norm_book(raw: dict) -> dict:
    tags = raw.get("tagInfo") or raw.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return {
        "bookId":       str(raw.get("videoid") or raw.get("videoId") or raw.get("id") or ""),
        "bookName":     raw.get("videoName") or raw.get("video_name") or raw.get("title") or "Untitled",
        "cover":        raw.get("cover") or raw.get("coverUrl") or raw.get("poster") or "",
        "introduction": raw.get("summary") or raw.get("description") or "",
        "tags":         [str(t) for t in tags] if isinstance(tags, list) else [],
        "playCount":    raw.get("hotNum") or raw.get("watchUserNum") or raw.get("playCount") or 0,
        "chapterCount": raw.get("totalEpisodeNum") or raw.get("episodeCount") or raw.get("chapterCount") or 0,
        "label":        raw.get("label") or "",
        "releaseDate":  raw.get("releaseDate") or "",
        "isFree":       raw.get("isFree"),
        "isEnd":        raw.get("isEnd"),
        "latestEpisodeId": raw.get("latestEpisodeid") or "",
        "firstEpisodeId":  raw.get("firstEpisodeid") or "",
        "_raw":         raw,
    }


def _cubetv_extract_books(raw: dict) -> list:
    data = raw.get("data") if isinstance(raw, dict) else {}
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    modules = data.get("moduleVideo") or []
    books = []
    if isinstance(modules, list):
        for module in modules:
            if not isinstance(module, dict):
                continue
            for item in module.get("videoList") or []:
                if isinstance(item, dict):
                    books.append(item)
    if books:
        return books
    for key in ("list", "videoList", "videos", "items", "records"):
        val = data.get(key)
        if isinstance(val, list) and val:
            return val
    return []


def _cubetv_extract_page_info(raw: dict) -> dict:
    data = raw.get("data") if isinstance(raw, dict) else {}
    info = {"total": 0, "totalPage": 0, "page": 1}
    if not isinstance(data, dict):
        return info

    modules = data.get("moduleVideo") or []
    if isinstance(modules, list) and modules:
        first = next((m for m in modules if isinstance(m, dict)), None)
        if first:
            info["total"] = int(first.get("total") or 0)
            info["totalPage"] = int(first.get("totalPage") or 0)
            info["page"] = int(first.get("page") or 1)
            return info

    info["total"] = int(data.get("total") or data.get("count") or 0)
    info["totalPage"] = int(data.get("totalPage") or data.get("pages") or 0)
    info["page"] = int(data.get("page") or 1)
    return info


def _cubetv_book_key(raw: dict) -> str:
    return str(
        raw.get("videoid")
        or raw.get("videoId")
        or raw.get("id")
        or raw.get("videoName")
        or raw.get("title")
        or ""
    )


def _cubetv_merge_books(*groups: list) -> list:
    merged = []
    seen = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            key = _cubetv_book_key(item)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(item)
    return merged


def _cubetv_norm_episode(raw: dict, idx: int) -> dict:
    ep_num = raw.get("episodeNumber") or raw.get("episode") or idx + 1
    chapter_id = raw.get("episodeid") or raw.get("episodeId") or raw.get("id") or ""
    return {
        "episode":      ep_num,
        "chapterIndex": idx,
        "chapterId":    str(chapter_id),
        "episodeId":    str(chapter_id),
        "vid":          str(chapter_id),
        "url":          "",
        "isPay":        1 if int(raw.get("lockStatus") or 0) else 0,
        "title":        raw.get("episodeTitle") or f"Episode {ep_num}",
        "duration":     raw.get("duration") or 0,
        "commentCount": raw.get("commentCount") or 0,
        "_raw":         raw,
    }


def _cubetv_pick_video_url(raw: dict) -> str:
    links = raw.get("linkInfo") or []
    if not isinstance(links, list) or not links:
        return ""

    def score(link):
        rate = str(link.get("codeRate") or "").upper()
        rate_score = {
            "4K": 4,
            "UHD": 4,
            "FHD": 3,
            "HD": 2,
            "SD": 1,
        }.get(rate, 0)
        expire = int(link.get("expireTime") or 0)
        return (rate_score, expire)

    best = sorted(links, key=score, reverse=True)[0]
    return best.get("linkUrl") or best.get("url") or ""


def get_cubetv_video(video_id: str, episode_id: str, lang="in") -> dict | None:
    if not video_id or not episode_id:
        return None
    raw = _cubetv_fetch(
        f"/stream/{video_id}/{episode_id}",
        {"lang": lang},
        ttl=PLATFORMS["cubetv"]["ttl_video"],
    )
    data = raw.get("data") if isinstance(raw, dict) else {}
    if not isinstance(data, dict):
        return None
    direct_url = _cubetv_pick_video_url(data)
    subtitles = []
    for sub in data.get("videoCaption") or []:
        if isinstance(sub, dict):
            subtitles.append({
                "language_code": sub.get("language_code") or "",
                "url": sub.get("url") or "",
                "episodeid": sub.get("episodeid"),
            })
    return {
        "vid":         episode_id,
        "episodeId":   episode_id,
        "url":         direct_url,
        "directUrl":   direct_url,
        "subtitles":   subtitles,
        "videoCaption": data.get("videoCaption") or [],
        "linkInfo":    data.get("linkInfo") or [],
        "_raw":        data,
    }

_cache = TTLCache(default_ttl=300, max_size=1000)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _headers() -> dict:
    h = HEADERS.copy()
    if DRACIN_TOKEN:
        h["Authorization"] = f"Bearer {DRACIN_TOKEN}"
    return h


def _fetch(platform: str, endpoint: str, params: dict | None = None, ttl: int = 300):
    """
    Fetch dari API dengan caching in-memory.
    endpoint : path setelah prefix, misal '/api/home'
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

    cache_key = f"dracin:{platform}:{endpoint}:{json.dumps(params or {}, sort_keys=True)}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{DRACIN_BASE}{cfg['prefix']}{endpoint}"
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        # Hanya cache jika response sukses (code == 0)
        if data.get("code", -1) == 0:
            _cache.set(cache_key, data, ttl=ttl)
        return data
    except Exception as e:
        print(f"[DRACIN:{platform}] ERROR {endpoint}: {e}")
        return None


def _unwrap(raw: dict) -> dict | list | None:
    """
    DramaBox v4 membungkus data dalam response["data"]["data"].
    Beberapa endpoint mungkin hanya satu level: response["data"].
    """
    if not raw or raw.get("code", -1) != 0:
        return None
    outer = raw.get("data", {})
    # Coba ambil nested data.data dulu
    if isinstance(outer, dict) and "data" in outer:
        return outer["data"]
    return outer


def _norm_book(cfg: dict, raw: dict) -> dict:
    """
    Normalisasi satu item buku/drama ke format konsisten.
    Berdasarkan response nyata DramaBox v4:
      bookId, bookName, coverWap, chapterCount, playCount,
      introduction, tags (array string), tagV3s, corner, rankVo
    """
    tags = raw.get(cfg.get("tags", "tags"), [])
    # tags bisa array of string atau array of dict — normalisasi ke string
    if tags and isinstance(tags[0], dict):
        tags = [t.get("tagName", t.get("tagEnName", "")) for t in tags]

    return {
        "bookId":        raw.get(cfg["book_id"],       ""),
        "bookName":      raw.get(cfg["book_name"],     "Untitled"),
        "cover":         _normalize_cover_url(
            raw.get(cfg["cover"])
            or raw.get("cover")
            or raw.get("coverWap")
            or raw.get("coverImage")
            or raw.get("coverUrl")
            or raw.get("thumbnail")
            or ""
        ),
        "introduction":  raw.get(cfg.get("introduction", "introduction"), ""),
        "chapterCount":  raw.get(cfg.get("chapter_count", "chapterCount"), raw.get("episodeCount", 0)),
        "playCount":     raw.get(cfg.get("play_count",    "playCount"),    raw.get("viewCount", 0)),
        "tags":          tags,
        # Field tambahan DramaBox yang berguna di frontend
        "corner":        raw.get("corner"),
        "rankVo":        raw.get("rankVo"),
        "protagonist":   raw.get("protagonist", ""),
        "shelfTime":     raw.get("shelfTime", ""),
    }


def _norm_chapter(cfg: dict, ch: dict, idx: int) -> dict:
    """
    Normalisasi chapter dari /api/drama/:id (endpoint detail).
    Field: chapterId, chapterIndex, isPay, isCharge, chapterSizeVoList
    """
    ch_index = ch.get(cfg.get("ch_index", "chapterIndex"), idx)
    return {
        "chapterId":   ch.get(cfg.get("ch_id", "chapterId"), ""),
        "chapterIndex":ch_index,
        "episode":     ch_index + 1,          # 1-based untuk display
        "isPay":       int(ch.get(cfg.get("ch_pay", "isPay"), 0) or 0),
        "qualities":   [
            q.get("quality") for q in ch.get("chapterSizeVoList", [])
        ],
    }


def _norm_episode(cfg: dict, ep: dict, idx: int) -> dict:
    """
    Normalisasi episode dari /api/drama/:id/episodes.
    Field berbeda dari chapter list di detail endpoint.
    """
    ep_num = ep.get(cfg.get("ep_num", "episode"), idx + 1)
    return {
        "episode":  ep_num,
        "url":      ep.get(cfg.get("ep_url", "url"), ep.get("videoUrl", ep.get("url", ""))),
        "isPay":    int(ep.get(cfg.get("ep_pay", "isPay"), ep.get("isCharge", 0)) or 0),
        "title":    ep.get(cfg.get("ep_title", "chapterName"), ep.get("title", f"Episode {ep_num}")),
        "quality":  ep.get("quality", 0),
    }


def _book_source_key(book: dict) -> str:
    return f"{book.get('platform', '')}:{book.get('bookId') or book.get('bookName') or ''}"


def _stamp_books(books: list, platform: str) -> list:
    stamped = []
    for book in books or []:
        if not isinstance(book, dict):
            continue
        b = book.copy()
        b.setdefault("platform", platform)
        stamped.append(b)
    return stamped


def _merge_books(*groups: list) -> list:
    merged = []
    seen = set()
    for group in groups:
        for book in group or []:
            if not isinstance(book, dict):
                continue
            key = _book_source_key(book)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(book)
    return merged


def _interleave_books(*groups: list) -> list:
    normalized = [group or [] for group in groups]
    max_len = max((len(group) for group in normalized), default=0)
    woven = []
    for idx in range(max_len):
        for group in normalized:
            if idx < len(group):
                woven.append(group[idx])
    return _merge_books(woven)


def _aggregate_cache_get(kind: str, params: dict):
    cache_key = f"aggregate:{kind}:{json.dumps(params, sort_keys=True)}"
    return cache_key, _cache.get(cache_key)


def _run_parallel(tasks: dict, max_workers: int = 6) -> dict:
    """Run independent API calls concurrently and keep partial results on failure."""
    if not tasks:
        return {}
    results = {}
    workers = max(1, min(max_workers, len(tasks)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                print(f"[PARALLEL] {key} failed: {e}")
                results[key] = None
    return results


# ── API Public Functions ─────────────────────────────────────────────────────

def get_platforms() -> list:
    """Daftar platform yang tersedia."""
    return [
        {"id": k, "label": v["label"], "icon": v["icon"]}
        for k, v in PLATFORMS.items()
    ]


def get_home(platform="dramabox", page=1, size=20, lang="in") -> dict | None:
    """
    /api/home → data.data.sections[].books[]
              → data.data.classifyBookList.records[]  (grid bawah)
              → data.data.bannerList[]

    Untuk melolo: Captain v1 /api/v1/bookmall
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

    # ── Engine: melolo (Captain v1) ───────────────────────────────────────────
    if cfg.get("_engine") == "aggregate":
        try:
            page = int(page) if page else 1
            size = int(size) if size else 20
        except Exception:
            page = 1
            size = 20
        cache_key, cached = _aggregate_cache_get("home", {"page": page, "size": size, "lang": lang})
        if cached is not None:
            return cached

        data_by_source = _run_parallel({
            source: (lambda source=source: get_home(source, page=page, size=size, lang=lang))
            for source in AGGREGATE_PLATFORMS
        })
        groups = [
            _stamp_books(data_by_source[source].get("books", []), source)
            for source in AGGREGATE_PLATFORMS
            if data_by_source.get(source)
        ]
        books = _interleave_books(*groups)
        result = {
            "platform":   platform,
            "page":       page,
            "sections":   [],
            "books":      books,
            "hasMore":    any(len(group) >= size for group in groups),
            "bannerList": [],
            "total":      len(books),
        }
        _cache.set(cache_key, result, ttl=cfg["ttl_home"])
        return result

    if cfg.get("_engine") == "melolo":
        raw = _melolo_fetch(platform, "/api/v1/bookmall",
                            {"lang": lang}, ttl=cfg["ttl_home"])
        if not raw:
            return None
        # Coba dari root dulu, lalu dari data (beberapa response dibungkus)
        items = _melolo_extract_list(raw) or _melolo_extract_list(raw.get("data", {}))
        books_all = [_melolo_norm_book(b) for b in items]
        total = len(books_all)
        # Implement simple pagination
        try:
            page = int(page) if page else 1
            size = int(size) if size else 20
        except Exception:
            page = 1
            size = 20
        start = (page - 1) * size
        end = start + size
        books = books_all[start:end]
        has_more = end < total
        return {
            "platform": platform,
            "page":     page,
            "sections": [],
            "books":    books,
            "hasMore":  has_more,
            "bannerList": [],
            "total": total,
        }

    if cfg.get("_engine") == "cubetv":
        try:
            page = int(page) if page else 1
            size = int(size) if size else 20
        except Exception:
            page = 1
            size = 20
        params = {"page": page, "pageSize": size, "lang": lang}
        raw = _cubetv_fetch("/home/romance", params, ttl=cfg["ttl_home"])
        if not _cubetv_extract_books(raw or {}):
            raw = _cubetv_fetch("/shows", params, ttl=cfg["ttl_home"])
        if not raw:
            return None
        items = _cubetv_extract_books(raw)
        page_info = _cubetv_extract_page_info(raw)
        books_all = [_cubetv_norm_book(b) for b in items]
        total = page_info.get("total") or len(books_all)
        total_page = page_info.get("totalPage") or 0
        return {
            "platform": platform,
            "page":     page,
            "sections": [],
            "books":    books_all,
            "hasMore":  (page < total_page) if total_page else (len(books_all) >= size),
            "bannerList": [],
            "total":    total,
        }

    if cfg.get("_engine") == "dramanova":
        raw = _dramanova_fetch(
            "/api/v1/dramas",
            {"lang": lang, "page": page, "size": size},
            ttl=cfg["ttl_home"],
        )
        if not isinstance(raw, dict):
            return None
        rows = raw.get("rows") or []
        total = int(raw.get("total") or len(rows))
        return {
            "platform":   platform,
            "page":       page,
            "sections":   [],
            "books":      [_dramanova_norm_book(b) for b in rows],
            "hasMore":    page * size < total,
            "bannerList": [],
            "total":      total,
        }

    if cfg.get("_engine") == "reelshort":
        try:
            page = int(page) if page else 1
            size = int(size) if size else 20
        except Exception:
            page = 1
            size = 20

        raw = _run_parallel({
            "foryou": lambda: _reelshort_fetch("/api/v1/foryou", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "more": lambda: _reelshort_fetch(
                "/api/v1/more",
                {"lang": lang, "page": page, "page_size": size},
                ttl=cfg["ttl_home"],
            ),
            "all": lambda: _reelshort_fetch(
                "/api/all",
                {"lang": lang, "page": page, "page_size": size},
                ttl=cfg["ttl_rank"],
            ),
            "top": lambda: _reelshort_fetch("/api/top", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "rank": lambda: _reelshort_fetch("/api/v1/rankings", {"lang": lang}, ttl=cfg["ttl_rank"]),
        })
        foryou_raw = raw.get("foryou")
        more_raw = raw.get("more")
        all_raw = raw.get("all")
        top_raw = raw.get("top")
        rank_raw = raw.get("rank")

        items = _shortwave_merge_books(
            _reelshort_extract_rank_books(rank_raw or {}),
            _reelshort_extract_books(top_raw or {}),
            _reelshort_extract_books(foryou_raw or {}),
            _reelshort_extract_books(all_raw or {}),
            _reelshort_extract_books(more_raw or {}),
        )
        total = len(items)
        start = (page - 1) * size
        end = start + size
        books = items[start:end]
        more_items = _reelshort_extract_books(more_raw or {})
        if not books and more_items:
            books = more_items
        return {
            "platform":   platform,
            "page":       page,
            "sections":   [],
            "books":      [_reelshort_norm_book(b) for b in books],
            "hasMore":    end < total or len(more_items) >= size,
            "bannerList": [],
            "total":      total,
        }

    if cfg.get("_engine") == "shortwave":
        try:
            page = int(page) if page else 1
            size = int(size) if size else 20
        except Exception:
            page = 1
            size = 20

        raw = _run_parallel({
            "more": lambda: _shortwave_fetch(
                "/api/more",
                {"lang": lang, "page": page, "page_size": size},
                ttl=cfg["ttl_home"],
            ),
            "all": lambda: _shortwave_fetch("/api/all", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "top": lambda: _shortwave_fetch("/api/top", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "rank": lambda: _shortwave_fetch("/api/rankings", {"lang": lang}, ttl=cfg["ttl_rank"]),
        })
        more_raw = raw.get("more")
        all_raw = raw.get("all")
        top_raw = raw.get("top")
        rank_raw = raw.get("rank")
        rank_items = _shortwave_extract_rank_books(rank_raw or {})
        if not rank_items:
            rank_raw = _shortwave_fetch("/api/ranking", {"lang": lang}, ttl=cfg["ttl_rank"])
            rank_items = _shortwave_extract_rank_books(rank_raw or {})

        all_items = _shortwave_extract_top_list(all_raw or {})
        top_items = _shortwave_extract_top_list(top_raw or {})
        more_items = _shortwave_extract_top_list(more_raw or {})

        items = _shortwave_merge_books(rank_items, top_items, all_items, more_items)
        total = len(items)
        start = (page - 1) * size
        end = start + size
        books = items[start:end]
        if not books and more_items:
            books = _shortwave_merge_books(more_items)
        return {
            "platform":   platform,
            "page":       page,
            "sections":   [],
            "books":      [_shortwave_norm_book(b) for b in books],
            "hasMore":    end < total or len(more_items) >= size,
            "bannerList": [],
            "total":      total,
        }

    # ── Engine: dracin (DramaBox/ReelShort) ───────────────────────────────────
    raw = _fetch(platform, "/api/home",
                 {"page": page, "size": size, "lang": lang},
                 ttl=cfg["ttl_home"])
    inner = _unwrap(raw)
    if inner is None:
        return None

    # Sections (carousel / featured)
    sections = []
    for sec in inner.get("sections", []):
        raw_books = sec.get("books", [])
        sections.append({
            "id":     sec.get("id", ""),
            "title":  sec.get("title", ""),
            "style":  sec.get("style", ""),
            "type":   sec.get("type", ""),
            "books":  [_norm_book(cfg, b) for b in raw_books],
        })

    # classifyBookList.records (grid rekomendasi bawah)
    classify = inner.get("classifyBookList", {})
    classify_books = [
        _norm_book(cfg, b)
        for b in classify.get("records", [])
    ]

    return {
        "platform":     platform,
        "page":         page,
        "sections":     sections,
        "books":        classify_books,     # grid flat
        "hasMore":      classify.get("isMore", 0) == 1,
        "bannerList":   inner.get("bannerList", []),
    }


def get_rank(platform="dramabox", rank_type=1, lang="in", size=20) -> dict | None:
    """
    /api/rank → data.data.rankList[]
    Untuk melolo: Captain v1 /api/v1/bookmall (same endpoint, pakai items teratas)
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

    # ── Engine: melolo ────────────────────────────────────────────────────────
    if cfg.get("_engine") == "aggregate":
        try:
            size = int(size or 20)
        except Exception:
            size = 20
        cache_key, cached = _aggregate_cache_get("rank", {"rank_type": rank_type, "lang": lang, "size": size})
        if cached is not None:
            return cached

        data_by_source = _run_parallel({
            source: (lambda source=source: get_rank(source, rank_type=rank_type, lang=lang, size=size))
            for source in AGGREGATE_PLATFORMS
        })
        groups = [
            _stamp_books(data_by_source[source].get("books", []), source)
            for source in AGGREGATE_PLATFORMS
            if data_by_source.get(source)
        ]
        books = _interleave_books(*groups)[:size]
        result = {
            "platform":  platform,
            "rankType":  rank_type,
            "rankTypes": [],
            "books":     books,
        }
        _cache.set(cache_key, result, ttl=cfg["ttl_rank"])
        return result

    if cfg.get("_engine") == "melolo":
        raw = _melolo_fetch(platform, "/api/v1/bookmall",
                            {"lang": lang}, ttl=cfg["ttl_rank"])
        if not raw:
            return None
        items = _melolo_extract_list(raw) or _melolo_extract_list(raw.get("data", {}))
        try:
            size = int(size or 20)
        except Exception:
            size = 20
        books = [_melolo_norm_book(b) for b in items[:size]]
        return {
            "platform":  platform,
            "rankType":  rank_type,
            "rankTypes": [],
            "books":     books,
        }

    if cfg.get("_engine") == "cubetv":
        try:
            size = int(size or 20)
        except Exception:
            size = 20
        raw = _run_parallel({
            "recommendations": lambda: _cubetv_fetch("/home/recommendations", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "trending": lambda: _cubetv_fetch("/home/trending", {"lang": lang}, ttl=cfg["ttl_rank"]),
        }, max_workers=2)
        books = _cubetv_merge_books(
            _cubetv_extract_books(raw.get("recommendations") or {}),
            _cubetv_extract_books(raw.get("trending") or {}),
        )
        return {
            "platform":  platform,
            "rankType":  rank_type,
            "rankTypes": [],
            "books":     [_cubetv_norm_book(b) for b in books[:size]],
        }

    if cfg.get("_engine") == "dramanova":
        raw = _dramanova_fetch(
            "/api/v1/recommend",
            {"lang": lang, "categoryKey": "dramanova_hot", "page": 1, "size": 20, "limit": 20},
            ttl=cfg["ttl_rank"],
        )
        items = []
        if isinstance(raw, dict):
            items = raw.get("dramas") or raw.get("rows") or []
        if not items:
            fallback = _dramanova_fetch("/api/v1/dramas", {"lang": lang, "page": 1, "size": 20}, ttl=cfg["ttl_rank"])
            items = fallback.get("rows", []) if isinstance(fallback, dict) else []
        return {
            "platform":  platform,
            "rankType":  rank_type,
            "rankTypes": [],
            "books":     [_dramanova_norm_book(b) for b in items],
        }

    if cfg.get("_engine") == "reelshort":
        raw = _run_parallel({
            "rank": lambda: _reelshort_fetch("/api/v1/rankings", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "top": lambda: _reelshort_fetch("/api/top", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "foryou": lambda: _reelshort_fetch("/api/v1/foryou", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "all": lambda: _reelshort_fetch("/api/all", {"lang": lang}, ttl=cfg["ttl_rank"]),
        })
        rank_raw = raw.get("rank")
        top_raw = raw.get("top")
        foryou_raw = raw.get("foryou")
        all_raw = raw.get("all")
        items = _shortwave_merge_books(
            _reelshort_extract_rank_books(rank_raw or {}),
            _reelshort_extract_books(top_raw or {}),
            _reelshort_extract_books(foryou_raw or {}),
            _reelshort_extract_books(all_raw or {}),
        )
        try:
            size = int(size or 20)
        except Exception:
            size = 20
        return {
            "platform":  platform,
            "rankType":  rank_type,
            "rankTypes": [],
            "books":     [_reelshort_norm_book(b) for b in items[:size]],
        }

    if cfg.get("_engine") == "shortwave":
        raw = _run_parallel({
            "rank": lambda: _shortwave_fetch("/api/rankings", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "top": lambda: _shortwave_fetch("/api/top", {"lang": lang}, ttl=cfg["ttl_rank"]),
            "all": lambda: _shortwave_fetch("/api/all", {"lang": lang}, ttl=cfg["ttl_rank"]),
        })
        rank_raw = raw.get("rank")
        rank_items = _shortwave_extract_rank_books(rank_raw or {})
        if not rank_items:
            rank_raw = _shortwave_fetch("/api/ranking", {"lang": lang}, ttl=cfg["ttl_rank"])
            rank_items = _shortwave_extract_rank_books(rank_raw or {})
        top_raw = raw.get("top")
        all_raw = raw.get("all")
        items = _shortwave_merge_books(
            rank_items,
            _shortwave_extract_top_list(top_raw or {}),
            _shortwave_extract_top_list(all_raw or {}),
        )
        try:
            size = int(size or 20)
        except Exception:
            size = 20
        return {
            "platform":  platform,
            "rankType":  rank_type,
            "rankTypes": [],
            "books":     [_shortwave_norm_book(b) for b in items[:size]],
        }

    # ── Engine: dracin ────────────────────────────────────────────────────────
    raw = _fetch(platform, "/api/rank",
                 {"lang": lang},
                 ttl=cfg["ttl_rank"])
    inner = _unwrap(raw)
    if inner is None:
        return None

    rank_types = inner.get("rankTypeVoList", [])
    rank_list  = inner.get("rankList", [])

    books = [_norm_book(cfg, b) for b in rank_list]

    return {
        "platform":   platform,
        "rankType":   rank_type,
        "rankTypes":  rank_types,
        "books":      books,
    }


def search_drama(keyword: str, platform="dramabox", page=1, lang="in") -> dict | None:
    """
    Search drama. Untuk melolo: Captain v1 /api/v1/search
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

    # ── Engine: melolo ────────────────────────────────────────────────────────
    if cfg.get("_engine") == "aggregate":
        try:
            page = int(page or 1)
        except Exception:
            page = 1
        cache_key, cached = _aggregate_cache_get("search", {"keyword": keyword, "page": page, "lang": lang})
        if cached is not None:
            return cached

        data_by_source = _run_parallel({
            source: (lambda source=source: search_drama(keyword, platform=source, page=page, lang=lang))
            for source in AGGREGATE_PLATFORMS
        })
        groups = [
            _stamp_books(data_by_source[source].get("books", []), source)
            for source in AGGREGATE_PLATFORMS
            if data_by_source.get(source)
        ]
        books = _interleave_books(*groups)
        result = {
            "platform": platform,
            "keyword":  keyword,
            "page":     page,
            "books":    books,
            "hasMore":  any(len(group) >= 20 for group in groups),
            "total":    len(books),
        }
        _cache.set(cache_key, result, ttl=cfg["ttl_search"])
        return result

    if cfg.get("_engine") == "melolo":
        offset = (page - 1) * 20
        raw = _melolo_fetch(platform, "/api/v1/search",
                            {"q": keyword, "lang": lang, "limit": 20, "offset": offset},
                            ttl=cfg["ttl_search"])
        if not raw:
            return None
        items = _melolo_extract_list(raw) or _melolo_extract_list(raw.get("data", {}))
        books = [_melolo_norm_book(b) for b in items]
        return {
            "platform": platform,
            "keyword":  keyword,
            "page":     page,
            "books":    books,
            "hasMore":  len(items) >= 20,
        }

    if cfg.get("_engine") == "cubetv":
        try:
            page = int(page or 1)
        except Exception:
            page = 1
        raw = _cubetv_fetch(
            "/search",
            {
                "keyword": keyword,
                "page": page,
                "pageSize": 20,
                "moduleid": "PaEpZ7",
                "lang": lang,
            },
            ttl=cfg["ttl_search"],
        )
        if not raw:
            return None
        data = raw.get("data") if isinstance(raw, dict) else {}
        if isinstance(data, dict):
            rows = data.get("list") or []
            total = int(data.get("total") or len(rows))
        else:
            rows = []
            total = 0
        return {
            "platform": platform,
            "keyword":  keyword,
            "page":     page,
            "books":    [_cubetv_norm_book(b) for b in rows],
            "hasMore":  page * 20 < total,
            "total":    total,
        }

    if cfg.get("_engine") == "dramanova":
        raw = _dramanova_fetch(
            "/api/v1/search",
            {"q": keyword, "lang": lang},
            ttl=cfg["ttl_search"],
        )
        if not isinstance(raw, dict):
            return None
        rows = raw.get("rows") or []
        return {
            "platform": platform,
            "keyword":  keyword,
            "page":     page,
            "books":    [_dramanova_norm_book(b) for b in rows],
            "hasMore":  False,
            "total":    raw.get("total", len(rows)),
        }

    if cfg.get("_engine") == "reelshort":
        raw = _reelshort_fetch(
            f"/api/v1/search/{quote(keyword, safe='')}",
            {"lang": lang},
            ttl=cfg["ttl_search"],
        )
        rows = _reelshort_extract_books(raw or {})
        return {
            "platform": platform,
            "keyword":  keyword,
            "page":     page,
            "books":    [_reelshort_norm_book(b) for b in rows],
            "hasMore":  False,
            "total":    len(rows),
        }

    if cfg.get("_engine") == "shortwave":
        raw = _shortwave_fetch(
            f"/api/search/{quote(keyword, safe='')}",
            {"lang": lang},
            ttl=cfg["ttl_search"],
        )
        rows = raw.get("data") if isinstance(raw, dict) else []
        if not isinstance(rows, list):
            rows = []
        return {
            "platform": platform,
            "keyword":  keyword,
            "page":     page,
            "books":    [_shortwave_norm_book(b) for b in rows],
            "hasMore":  False,
            "total":    len(rows),
        }

    # ── Engine: dracin ────────────────────────────────────────────────────────
    raw = _fetch(platform, "/api/search",
                 {"keyword": keyword, "page": page, "lang": lang},
                 ttl=cfg["ttl_search"])
    inner = _unwrap(raw)
    if inner is None:
        return None

    # DramaBox v4: search result ada di 'searchList'
    raw_books = inner.get("searchList", inner.get("list", inner.get("books", [])))
    books = [_norm_book(cfg, b) for b in raw_books]

    return {
        "platform": platform,
        "keyword":  inner.get("keyword", keyword),
        "page":     page,
        "books":    books,
        "hasMore":  inner.get("isMore", 0) == 1,
    }


def get_detail(drama_id: str, platform="dramabox", lang="en") -> dict | None:
    """
    /api/drama/:id → detail drama + chapter list (untuk streaming).

    data.data berisi:
      - list[]         : chapter list (chapterId, chapterIndex, isPay, chapterSizeVoList)
      - performers[]   : info pemain
      - recommendList[]: rekomendasi drama lain
      - ratingConf     : info rating
      - bookStatus     : 1=complete, 0=ongoing
      - corner         : label (Members Only, dll)
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

    if cfg.get("_engine") == "dramanova":
        raw = _dramanova_fetch(f"/api/v1/drama/{drama_id}", {"lang": lang}, ttl=cfg["ttl_detail"])
        if not isinstance(raw, dict):
            return None
        episodes = raw.get("episodes") or []
        chapters = [_dramanova_norm_episode(ep, i) for i, ep in enumerate(episodes)]
        return {
            "bookId":        drama_id,
            "platform":      platform,
            "bookStatus":    1 if raw.get("isCompleted") else 0,
            "corner":        None,
            "chapters":      chapters,
            "freeCount":     len([c for c in chapters if c["isPay"] == 0]),
            "paidCount":     len([c for c in chapters if c["isPay"] == 1]),
            "totalChapters": len(chapters),
            "performers":    [],
            "rating":        {"show": False, "score": "", "count": ""},
            "recommends":    [],
            "downLoadQuality": [],
        }

    if cfg.get("_engine") == "reelshort":
        raw = _run_parallel({
            "book": lambda: _reelshort_fetch(f"/api/v1/book/{drama_id}", {"lang": lang}, ttl=cfg["ttl_detail"]),
            "chapters": lambda: _reelshort_fetch(f"/api/v1/book/{drama_id}/chapters", {"lang": lang}, ttl=cfg["ttl_ep"]),
        }, max_workers=2)
        book_raw = raw.get("book")
        chapters_raw = raw.get("chapters")
        bdata = book_raw.get("data") if isinstance(book_raw, dict) else {}
        chapter_data = chapters_raw.get("data") if isinstance(chapters_raw, dict) else {}
        if not isinstance(bdata, dict):
            return None
        raw_eps = chapter_data.get("chapters") if isinstance(chapter_data, dict) else []
        chapters = [_reelshort_norm_episode(ep, i) for i, ep in enumerate(raw_eps or [])]
        return {
            "bookId":        drama_id,
            "platform":      platform,
            "bookStatus":    1,
            "corner":        None,
            "chapters":      chapters,
            "freeCount":     len([c for c in chapters if c["isPay"] == 0]),
            "paidCount":     len([c for c in chapters if c["isPay"] == 1]),
            "totalChapters": bdata.get("chapter_count") or len(chapters),
            "performers":    [],
            "rating":        {"show": False, "score": "", "count": ""},
            "recommends":    [],
            "downLoadQuality": [],
        }

    if cfg.get("_engine") == "shortwave":
        raw = _shortwave_fetch(f"/api/drama/{drama_id}", {"lang": lang}, ttl=cfg["ttl_detail"])
        data = raw.get("data") if isinstance(raw, dict) else {}
        if not isinstance(data, dict):
            return None
        raw_eps = data.get("episodes") or []
        chapters = [_shortwave_norm_episode(ep, i) for i, ep in enumerate(raw_eps)]
        return {
            "bookId":        drama_id,
            "platform":      platform,
            "bookStatus":    1,
            "corner":        None,
            "chapters":      chapters,
            "freeCount":     len([c for c in chapters if c["isPay"] == 0]),
            "paidCount":     len([c for c in chapters if c["isPay"] == 1]),
            "totalChapters": data.get("total_episodes") or len(chapters),
            "performers":    [],
            "rating":        {"show": False, "score": "", "count": ""},
            "recommends":    [],
            "downLoadQuality": [],
        }

    if cfg.get("_engine") == "cubetv":
        meta_raw = _cubetv_fetch(f"/search/{drama_id}/episodes", {"lang": lang}, ttl=cfg["ttl_detail"])
        list_raw = _cubetv_fetch(f"/episode/{drama_id}/list", {"lang": lang}, ttl=cfg["ttl_ep"])
        meta = meta_raw.get("data") if isinstance(meta_raw, dict) else {}
        episodes_raw = list_raw.get("data") if isinstance(list_raw, dict) else []
        if not isinstance(meta, dict):
            return None
        if not isinstance(episodes_raw, list):
            episodes_raw = []
        chapters = [_cubetv_norm_episode(ep, i) for i, ep in enumerate(episodes_raw)]
        free_eps = [c for c in chapters if c["isPay"] == 0]
        return {
            "bookId":        str(meta.get("videoid") or drama_id),
            "platform":      platform,
            "bookStatus":    1 if meta.get("isEnd") else 0,
            "corner":        meta.get("label") or None,
            "chapters":      chapters,
            "freeCount":     len(free_eps),
            "paidCount":     len([c for c in chapters if c["isPay"] == 1]),
            "totalChapters": len(chapters),
            "performers":    [],
            "rating":        {"show": False, "score": "", "count": ""},
            "recommends":    [],
            "downLoadQuality": [],
        }

    raw = _fetch(platform, f"/api/drama/{drama_id}",
                 {"lang": lang},
                 ttl=cfg["ttl_detail"])
    inner = _unwrap(raw)
    if inner is None:
        return None

    # Chapter list untuk streaming
    ch_list = inner.get(cfg.get("ch_list", "list"), [])
    chapters = [_norm_chapter(cfg, ch, i) for i, ch in enumerate(ch_list)]

    # Pisahkan chapter gratis dan berbayar
    free_chapters = [c for c in chapters if c["isPay"] == 0]
    paid_chapters = [c for c in chapters if c["isPay"] == 1]

    # Performers
    performers = [
        {
            "id":     p.get("performerId", ""),
            "name":   p.get("performerName", ""),
            "avatar": p.get("performerAvatar", ""),
            "count":  p.get("videoCount", 0),
        }
        for p in inner.get("performers", [])
    ]

    # Rating
    rating_conf = inner.get("ratingConf", {})
    rating = {
        "show":   rating_conf.get("showRate", False),
        "score":  rating_conf.get("rate", ""),
        "count":  rating_conf.get("ratingCount", ""),
    }

    # Rekomendasi
    recommends = [
        _norm_book(cfg, b)
        for b in inner.get("recommendList", [])
    ]

    return {
        "bookId":       drama_id,
        "platform":     platform,
        "bookStatus":   inner.get("bookStatus", 0),   # 1=selesai
        "corner":       inner.get("corner"),
        "chapters":     chapters,
        "freeCount":    len(free_chapters),
        "paidCount":    len(paid_chapters),
        "totalChapters":len(chapters),
        "performers":   performers,
        "rating":       rating,
        "recommends":   recommends,
        "downLoadQuality": inner.get("downLoadQuality", []),
    }


def get_episodes(drama_id: str, platform="dramabox", lang="in") -> dict | None:
    """
    Episode list dengan URL video.
    Untuk melolo: Captain v1 /api/v1/multi-video + /api/v1/book
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

    # ── Engine: melolo ────────────────────────────────────────────────────────
    if cfg.get("_engine") == "melolo":
        raw = _run_parallel({
            "book": lambda: _melolo_fetch(
                platform, "/api/v1/book",
                {"id": drama_id, "lang": lang}, ttl=cfg["ttl_ep"]
            ),
            "multi": lambda: _melolo_fetch(
                platform, "/api/v1/multi-video",
                {"id": drama_id, "lang": lang}, ttl=cfg["ttl_ep"]
            ),
        }, max_workers=2)
        book_raw = raw.get("book")
        multi_raw = raw.get("multi")

        # Normalize book info
        bdata     = (book_raw or {})
        bdata     = bdata.get("book") or bdata.get("data") or bdata
        book_name = bdata.get("title", bdata.get("name", bdata.get("bookName", "Drama")))
        cover     = _normalize_cover_url(
            bdata.get("thumb_url")
            or bdata.get("cover")
            or bdata.get("coverUrl")
            or bdata.get("cover_url")
            or bdata.get("thumbnail")
            or "",
            ref="https://melolo.tv/",
        )
        desc      = bdata.get("description", bdata.get("introduction", ""))
        ep_count  = bdata.get("episode_count", bdata.get("episodeCount", 0))

        # multi-video response shape:
        # { "episodes": [ { "vid", "stream_url", "index", "duration", ... } ] }
        mv_data = multi_raw or {}
        raw_eps = (
            mv_data.get("episodes")
            or mv_data.get("data", {}).get("episodes")
            or []
        )

        # Build vid → stream_url map
        vid_url_map = {}
        for ep in raw_eps:
            v = str(ep.get("vid", ""))
            u = ep.get("stream_url") or ep.get("url") or ep.get("videoUrl") or ""
            if v and u:
                vid_url_map[v] = u

        # Normalize episodes
        episodes = []
        for i, ep in enumerate(raw_eps):
            ep_vid      = str(ep.get("vid", ""))
            idx         = ep.get("index", i + 1)
            need_unlock = ep.get("need_unlock", ep.get("needUnlock", False))
            ep_url      = vid_url_map.get(ep_vid, "")
            episodes.append({
                "episode":   idx,
                "vid":       ep_vid,
                "isPay":     1 if need_unlock else 0,
                "title":     ep.get("title", ep.get("chapterName", f"Episode {idx}")),
                "url":       ep_url,   # stream_url dari multi-video
                "_raw":      ep,
            })

        free_eps = [e for e in episodes if e["isPay"] == 0]

        return {
            "bookId":        drama_id,
            "bookName":      book_name,
            "cover":         cover,
            "description":   desc,
            "totalEpisodes": ep_count or len(episodes),
            "quality":       720,
            "platform":      platform,
            "episodes":      episodes,
            "freeCount":     len(free_eps),
        }

    if cfg.get("_engine") == "dramanova":
        raw = _dramanova_fetch(f"/api/v1/drama/{drama_id}", {"lang": lang}, ttl=cfg["ttl_ep"])
        if not isinstance(raw, dict):
            return None
        raw_eps = raw.get("episodes") or []
        episodes = [_dramanova_norm_episode(ep, i) for i, ep in enumerate(raw_eps)]
        free_eps = [e for e in episodes if e["isPay"] == 0]
        return {
            "bookId":        str(raw.get("id") or drama_id),
            "bookName":      raw.get("title") or "Drama",
            "cover":         raw.get("cover") or "",
            "description":   raw.get("description") or "",
            "totalEpisodes": raw.get("totalEpisodes") or len(episodes),
            "quality":       1080,
            "platform":      platform,
            "episodes":      episodes,
            "freeCount":     len(free_eps),
        }

    if cfg.get("_engine") == "reelshort":
        raw = _run_parallel({
            "book": lambda: _reelshort_fetch(f"/api/v1/book/{drama_id}", {"lang": lang}, ttl=cfg["ttl_detail"]),
            "chapters": lambda: _reelshort_fetch(f"/api/v1/book/{drama_id}/chapters", {"lang": lang}, ttl=cfg["ttl_ep"]),
        }, max_workers=2)
        book_raw = raw.get("book")
        chapters_raw = raw.get("chapters")
        bdata = book_raw.get("data") if isinstance(book_raw, dict) else {}
        chapter_data = chapters_raw.get("data") if isinstance(chapters_raw, dict) else {}
        if not isinstance(bdata, dict):
            return None

        raw_eps = chapter_data.get("chapters") if isinstance(chapter_data, dict) else []
        episodes = [_reelshort_norm_episode(ep, i) for i, ep in enumerate(raw_eps or [])]
        free_eps = [e for e in episodes if e["isPay"] == 0]
        return {
            "bookId":        drama_id,
            "bookName":      bdata.get("book_title") or "Drama",
            "cover":         bdata.get("book_pic") or "",
            "description":   bdata.get("special_desc") or "",
            "totalEpisodes": bdata.get("chapter_count") or len(episodes),
            "quality":       720,
            "platform":      platform,
            "episodes":      episodes,
            "freeCount":     len(free_eps),
        }

    if cfg.get("_engine") == "shortwave":
        raw = _shortwave_fetch(f"/api/drama/{drama_id}", {"lang": lang}, ttl=cfg["ttl_ep"])
        data = raw.get("data") if isinstance(raw, dict) else {}
        if not isinstance(data, dict):
            return None

        raw_eps = data.get("episodes") or []
        episodes = []
        for i, ep in enumerate(raw_eps):
            episodes.append(_shortwave_norm_episode(ep, i))

        free_eps = [e for e in episodes if e["isPay"] == 0]
        return {
            "bookId":        drama_id,
            "bookName":      data.get("drama_title") or data.get("title") or "Drama",
            "cover":         data.get("drama_cover") or data.get("cover") or "",
            "description":   data.get("drama_description") or data.get("description") or "",
            "totalEpisodes": data.get("total_episodes") or len(episodes),
            "quality":       720,
            "platform":      platform,
            "episodes":      episodes,
            "freeCount":     len(free_eps),
        }

    # ── Engine: dracin ────────────────────────────────────────────────────────
    if cfg.get("_engine") == "cubetv":
        meta_raw = _cubetv_fetch(f"/search/{drama_id}/episodes", {"lang": lang}, ttl=cfg["ttl_ep"])
        list_raw = _cubetv_fetch(f"/episode/{drama_id}/list", {"lang": lang}, ttl=cfg["ttl_ep"])
        meta = meta_raw.get("data") if isinstance(meta_raw, dict) else {}
        raw_eps = list_raw.get("data") if isinstance(list_raw, dict) else []
        if not isinstance(meta, dict):
            return None
        if not isinstance(raw_eps, list):
            raw_eps = []
        episodes = [_cubetv_norm_episode(ep, i) for i, ep in enumerate(raw_eps)]
        free_eps = [e for e in episodes if e["isPay"] == 0]
        return {
            "bookId":        str(meta.get("videoid") or drama_id),
            "bookName":      meta.get("videoName") or "Drama",
            "cover":         meta.get("cover") or "",
            "description":   meta.get("summary") or "",
            "totalEpisodes": meta.get("totalEpisodeNum") or len(episodes),
            "quality":       1080,
            "platform":      platform,
            "episodes":      episodes,
            "freeCount":     len(free_eps),
        }

    raw = _fetch(platform, f"/api/drama/{drama_id}/episodes",
                 {"lang": lang},
                 ttl=cfg["ttl_ep"])
    if not raw or raw.get("code", -1) != 0:
        return None

    # Response episode flat — ada di raw["data"] langsung (bukan raw["data"]["data"])
    data = raw.get("data", {})

    ep_list = data.get(cfg.get("ep_list", "episodes"), [])
    episodes = [_norm_episode(cfg, ep, i) for i, ep in enumerate(ep_list)]

    free_eps = [e for e in episodes if e["isPay"] == 0]

    return {
        "bookId":       drama_id,
        "bookName":     data.get(cfg["book_name"],  data.get("bookName", "Drama")),
        "cover":        data.get(cfg["cover"],      data.get("coverWap", data.get("cover", ""))),
        "description":  data.get("description", ""),
        "totalEpisodes":data.get("totalEpisodes",   len(episodes)),
        "quality":      data.get("quality", 720),
        "platform":     platform,
        "episodes":     episodes,
        "freeCount":    len(free_eps),
    }


def get_languages(platform="dramabox") -> list | None:
    """
    Daftar bahasa. Untuk melolo: Captain v1 /api/v1/languages
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

    if cfg.get("_engine") == "aggregate":
        return [
            {"code": "in", "label": "Indonesia", "flag": "ID"},
            {"code": "en", "label": "English", "flag": "EN"},
        ]

    if cfg.get("_engine") == "melolo":
        raw = _melolo_fetch(platform, "/api/v1/languages", ttl=86400)
        if not raw:
            return None
        return raw.get("data", raw) if isinstance(raw, dict) else raw

    if cfg.get("_engine") == "dramanova":
        raw = _dramanova_fetch("/api/v1/languages", ttl=86400)
        return raw if isinstance(raw, list) else None

    if cfg.get("_engine") == "reelshort":
        raw = _reelshort_fetch("/api/set-lang/in", ttl=86400)
        data = raw.get("data") if isinstance(raw, dict) else {}
        if isinstance(data, dict):
            return data.get("languages") or []
        return None

    if cfg.get("_engine") == "shortwave":
        raw = _shortwave_fetch("/api/set-lang/in", ttl=86400)
        data = raw.get("data") if isinstance(raw, dict) else {}
        if isinstance(data, dict):
            return data.get("languages") or []
        return None

    if cfg.get("_engine") == "cubetv":
        raw = _cubetv_fetch("/languages", ttl=86400)
        return raw.get("data", []) if isinstance(raw, dict) else None

    raw = _fetch(platform, "/api/languages", ttl=86400)  # cache 24 jam
    if not raw or raw.get("code", -1) != 0:
        return None

    return raw.get("data", [])


# ── Flask / Server build_response ────────────────────────────────────────────

def build_response(path: str, params: dict) -> tuple[dict, int]:
    """
    Dipanggil dari server.py / dev.py via Flask route /api/dracin/<subpath>.

    path   : string seperti '/api/dracin/rank'
    params : dict dari request.args.getlist(k)  → values berupa list
    returns: (json_dict, http_status_code)
    """
    def p(key, default=None):
        v = params.get(key, [default])
        return (v[0] if v else default) or default

    platform = p("platform", "all")
    lang     = p("lang",     "in")

    # Protect dramanova when configured: require dramanova_token query param
    DRAMANOVA_PIN = os.environ.get("DRAMANOVA_PIN")
    DRAMANOVA_SECRET = os.environ.get("DRAMANOVA_SECRET") or os.environ.get("SECRET_KEY") or "streamvault-default-secret"
    def _validate_token(tok: str) -> bool:
        try:
            parts = tok.split(":")
            if len(parts) < 3:
                return False
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

    if platform == 'dramanova' and DRAMANOVA_PIN:
        tok = p('dramanova_token', '')
        if not tok or not _validate_token(tok):
            return {"status": "error", "message": "Access to dramanova requires PIN authentication"}, 403

    # ── /api/dracin/platforms
    if path.endswith("/platforms"):
        return {"status": "success", "data": get_platforms()}, 200

    # ── /api/dracin/languages
    if path.endswith("/languages"):
        data = get_languages(platform=platform)
        if data is not None:
            return {"status": "success", "data": data}, 200
        return {"status": "error", "message": "Languages tidak tersedia"}, 503

    # ── /api/dracin/rank
    if path.endswith("/rank"):
        rank_type = int(p("rank_type", 1) or 1)
        data = get_rank(platform=platform, rank_type=rank_type, lang=lang)
        if data:
            return {"status": "success", "data": data}, 200
        return {"status": "error", "message": f"Rank tidak tersedia untuk platform '{platform}'"}, 503

    # ── /api/dracin/search
    if path.endswith("/search"):
        keyword = p("keyword", "")
        if not keyword:
            return {"status": "error", "message": "Parameter 'keyword' wajib diisi"}, 400
        page = int(p("page", 1) or 1)
        data = search_drama(keyword, platform=platform, page=page, lang=lang)
        if data:
            return {"status": "success", "data": data}, 200
        return {"status": "error", "message": "Tidak ada hasil pencarian"}, 404

    # ── /api/dracin/episodes  (URL video per episode)
    if path.endswith("/episodes"):
        drama_id = p("id", "")
        if not drama_id:
            return {"status": "error", "message": "Parameter 'id' wajib diisi"}, 400
        data = get_episodes(drama_id, platform=platform, lang=lang)
        if data:
            return {"status": "success", "data": data}, 200
        return {"status": "error", "message": "Episodes tidak ditemukan"}, 404

    if path.endswith("/video"):
        file_id = p("id", "")
        if not file_id:
            return {"status": "error", "message": "Parameter 'id' wajib diisi"}, 400
        if platform == "dramanova":
            data = get_dramanova_video(file_id)
            if data and data.get("url"):
                return {"status": "success", "data": data, "link": data["url"]}, 200
            return {"status": "error", "message": "Video tidak ditemukan"}, 404
        if platform == "reelshort":
            book_id = p("bookId", p("book_id", ""))
            data = get_reelshort_video(book_id, file_id, lang=lang)
            if data and data.get("url"):
                return {"status": "success", "data": data, "link": data["url"]}, 200
            return {"status": "error", "message": "Video tidak ditemukan"}, 404
        if platform == "shortwave":
            drama_id = p("bookId", p("drama_id", ""))
            data = get_shortwave_video(drama_id, file_id, lang=lang)
            if data and data.get("url"):
                return {"status": "success", "data": data, "link": data["url"]}, 200
            return {"status": "error", "message": "Video tidak ditemukan"}, 404
        if platform == "cubetv":
            book_id = p("bookId", p("video_id", p("drama_id", "")))
            data = get_cubetv_video(book_id, file_id, lang=lang)
            if data and data.get("url"):
                return {"status": "success", "data": data, "link": data["url"]}, 200
            return {"status": "error", "message": "Video tidak ditemukan"}, 404
        return {"status": "error", "message": f"Video resolver belum tersedia untuk '{platform}'"}, 400

    # ── /api/dracin/detail  (chapter list + metadata lengkap)
    if path.endswith("/detail"):
        drama_id = p("id", "")
        if not drama_id:
            return {"status": "error", "message": "Parameter 'id' wajib diisi"}, 400
        # Detail bisa pakai lang=en untuk nama pemain dll
        detail_lang = p("lang", "en")
        data = get_detail(drama_id, platform=platform, lang=detail_lang)
        if data:
            return {"status": "success", "data": data}, 200
        return {"status": "error", "message": "Drama tidak ditemukan"}, 404

    # ── /api/dracin/home  (default)
    if path.endswith("/home") or path.rstrip("/").endswith("/dracin"):
        page = int(p("page", 1) or 1)
        size = int(p("size", 20) or 20)
        data = get_home(platform=platform, page=page, size=size, lang=lang)
        if data:
            return {"status": "success", "data": data}, 200
        return {"status": "error", "message": f"Home tidak tersedia untuk platform '{platform}'"}, 503

    return {"status": "error", "message": f"Route tidak dikenal: {path}"}, 400


# ── Vercel Serverless Handler ─────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        params = {k: v for k, v in parse_qs(parsed.query).items()}
        data, code = build_response(path, params)
        self._send_json(data, code)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Dramanova-Token")

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass
