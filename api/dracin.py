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
import os, sys, json, requests, time
import hmac, hashlib
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

        def get(self, key):
            entry = self._store.get(key)
            if entry and time.time() < entry[1]:
                return entry[0]
            return None

        def set(self, key, value, ttl=None):
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
DRACIN_BASE  = "https://captain.sapimu.au"
DRACIN_TOKEN = "b426511825c6dac73f4d897eb0bf7471036c75f4d8314329540d5850bd70deaa"

# Konfigurasi per-platform
# Setiap platform mendefinisikan prefix dan field mapping-nya sendiri
PLATFORMS = {
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
        "prefix":   "/reelshortv2",
        "label":    "ReelShort",
        "icon":     "🎬",
        "book_id":      "bookId",
        "book_name":    "bookName",
        "cover":        "coverImage",
        "introduction": "introduction",
        "tags":         "tags",
        "play_count":   "playCount",
        "chapter_count":"chapterCount",
        "ep_list":      "episodes",
        "ep_num":       "chapterIndex",
        "ep_url":       "videoUrl",
        "ep_pay":       "isCharge",
        "ep_title":     "title",
        "ch_list":      "list",
        "ch_id":        "chapterId",
        "ch_index":     "chapterIndex",
        "ch_pay":       "isCharge",
        "ttl_home":  600,
        "ttl_rank":  3600,
        "ttl_search":120,
        "ttl_detail":300,
        "ttl_ep":    300,
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
}

# ── Melolo / Captain v1 config ────────────────────────────────────────────────
CAPTAIN_BASE  = os.environ.get("CAPTAIN_BASE_URL", "https://captain.sapimu.au").rstrip("/")
CAPTAIN_TOKEN = os.environ.get("CAPTAIN_TOKEN", DRACIN_TOKEN)  # fallback ke token dracin

def _melolo_headers() -> dict:
    return {
        "Authorization": f"Bearer {CAPTAIN_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

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
    proxied_url = f"/api/proxy?url={quote(direct_url, safe='')}" if direct_url else ""
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

_cache = TTLCache(default_ttl=300, max_size=200)


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
        "cover":         _normalize_cover_url(raw.get(cfg["cover"], raw.get("coverWap", raw.get("coverImage", "")))),
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
        # Fetch paralel: book info, series (episode list), multi-video (stream URLs)
        book_raw   = _melolo_fetch(platform, "/api/v1/book",
                                   {"id": drama_id, "lang": lang}, ttl=cfg["ttl_ep"])
        multi_raw  = _melolo_fetch(platform, "/api/v1/multi-video",
                                   {"id": drama_id, "lang": lang}, ttl=cfg["ttl_ep"])

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

    # ── Engine: dracin ────────────────────────────────────────────────────────
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

    if cfg.get("_engine") == "melolo":
        raw = _melolo_fetch(platform, "/api/v1/languages", ttl=86400)
        if not raw:
            return None
        return raw.get("data", raw) if isinstance(raw, dict) else raw

    if cfg.get("_engine") == "dramanova":
        raw = _dramanova_fetch("/api/v1/languages", ttl=86400)
        return raw if isinstance(raw, list) else None

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

    platform = p("platform", "dramabox")
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
