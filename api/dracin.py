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
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

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
        "cover":         raw.get(cfg["cover"],         raw.get("coverWap", raw.get("coverImage", ""))),
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

    Mengembalikan dict berisi sections (untuk carousel) dan
    books flat (untuk grid).
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

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


def get_rank(platform="dramabox", rank_type=1, lang="in") -> dict | None:
    """
    /api/rank → data.data.rankList[]
             → data.data.rankTypeVoList[]  (tipe ranking)

    rank_type:
      1 = Sedang Tren
      2 = Pencarian Populer
      3 = Terbaru
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

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
    /api/search → data.data.searchList[]
               → data.data.keyword
               → data.data.isMore
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

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
    /api/drama/:id/episodes → list episode dengan URL video.

    Response flat (bukan nested data.data):
      data.bookId, data.bookName, data.cover, data.description,
      data.totalEpisodes, data.quality, data.episodes[]

    Endpoint ini khusus untuk mendapatkan URL video tiap episode.
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

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
    /api/languages → list bahasa yang didukung.
    Response: data[] → [{code, name, flag}]
    """
    cfg = PLATFORMS.get(platform)
    if not cfg:
        return None

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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

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