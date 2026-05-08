"""
api/melolo.py — Captain API (Melolo / DramaBox / ReelShort / dll)
Base URL : https://captain.sapimu.au/{platform}
Token    : sama untuk semua platform

Env vars:
  CAPTAIN_BASE_URL = https://captain.sapimu.au
  CAPTAIN_TOKEN    = <token kamu>

Struktur response yang diketahui:
  /api/v1/bookmall  → buku ada di cell.cell_data[].books[]
  /api/v1/book      → fields: book_id, title, cover, episode_count, categories[]
  /api/v1/series    → episodes ada di episodes[] dengan field vid, index, need_unlock (tidak ada url)
  /api/v1/search    → params: q, lang, limit, offset
"""
import os
import requests

CAPTAIN_BASE  = os.environ.get("CAPTAIN_BASE_URL", "https://captain.sapimu.au").rstrip("/")
CAPTAIN_TOKEN = os.environ.get("CAPTAIN_TOKEN", "")
TIMEOUT       = 15


# ── Internal helpers ──────────────────────────────────────────────────────────

def _headers():
    return {
        "Authorization": f"Bearer {CAPTAIN_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _get(platform: str, path: str, params: dict = None) -> tuple:
    """Raw GET ke Captain API. Kembalikan (data_dict, status_code)."""
    if not CAPTAIN_TOKEN:
        return {"error": "CAPTAIN_TOKEN belum dikonfigurasi"}, 500
    url = f"{CAPTAIN_BASE}/{platform}{path}"
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=TIMEOUT)
        try:
            data = resp.json()
        except Exception:
            data = {"error": f"Respons bukan JSON: {resp.text[:300]}"}
        return data, resp.status_code
    except requests.Timeout:
        return {"error": f"Timeout menghubungi {platform} API"}, 504
    except requests.RequestException as e:
        return {"error": str(e)}, 502


# ── Normalizer helpers ────────────────────────────────────────────────────────

import re as _re
from urllib.parse import quote as _quote

IMG_PROXY_PATH = "/api/img-proxy"
MELOLO_REFERER = "https://melolo.tv/"

def _proxy_thumb_url(thumb_url: str) -> str:
    """Return local proxy URL for signed/HEIC thumb_url without changing its signature."""
    if not thumb_url:
        return thumb_url
    if thumb_url.startswith(IMG_PROXY_PATH) or thumb_url.startswith("/"):
        return thumb_url
    thumb_lower = thumb_url.lower()
    if "fizzopic.org" in thumb_lower or ".heic" in thumb_lower or ".avif" in thumb_lower:
        return (
            f"{IMG_PROXY_PATH}?url={_quote(thumb_url, safe='')}"
            f"&ref={_quote(MELOLO_REFERER, safe='')}"
        )
    return thumb_url

def _heic_url_to_jpeg(url: str) -> str:
    """Ganti ekstensi HEIC/AVIF → JPEG di URL ByteDance ImageX CDN."""
    if not url:
        return url
    if ".heic" not in url.lower() and ".avif" not in url.lower():
        return url
    if "?" in url:
        path_part, query_part = url.split("?", 1)
        path_part = _re.sub(r"\.(heic|avif)$", ".jpeg", path_part, flags=_re.IGNORECASE)
        return f"{path_part}?{query_part}"
    return _re.sub(r"\.(heic|avif)$", ".jpeg", url, flags=_re.IGNORECASE)


def _best_cover(raw: dict) -> str:
    thumb_url = (
        raw.get("thumb_url")
        or raw.get("cover")
        or raw.get("cover_url")
        or raw.get("coverUrl")
        or raw.get("thumbnail")
        or ""
    )
    return _proxy_thumb_url(thumb_url)

def _normalize_book(raw: dict) -> dict:
    """
    Normalisasi satu objek buku dari berbagai konteks (bookmall, book detail, search).
    Fields yang dijamin ada di output: book_id, title, cover, episode_count, categories.
    """
    return {
        "book_id":       raw.get("book_id") or raw.get("id", ""),
        "title":         raw.get("title", ""),
        "cover":         _best_cover(raw),
        "episode_count": raw.get("episode_count", 0),
        "categories":    raw.get("categories") or [],
        # field tambahan yang mungkin ada, tidak di-hardcode agar tidak kehilangan data
        "_raw":          raw,
    }


def _normalize_episode(raw: dict) -> dict:
    """
    Normalisasi satu episode dari /api/v1/series.
    Field yang dijamin ada: vid, index, need_unlock.
    Tidak ada url video — hanya vid (item ID).
    """
    return {
        "vid":         raw.get("vid", ""),
        "index":       raw.get("index", 0),
        "need_unlock": raw.get("need_unlock", False),
        "_raw":        raw,
    }


# ── Public API functions ──────────────────────────────────────────────────────

def languages(platform="melolo"):
    """Daftar bahasa yang didukung platform."""
    return _get(platform, "/api/v1/languages")


def home(platform="melolo", lang="en") -> tuple:
    """
    Beranda / bookmall.
    Response shape: { cell: { cell_data: [ { books: [...] }, ... ] } }
    Return: ({"books": [normalized_book, ...]}, status_code)
    """
    raw, code = _get(platform, "/api/v1/bookmall", {"lang": lang})
    if code != 200 or "error" in raw:
        return raw, code

    books = []
    try:
        cell_data = raw.get("cell", {}).get("cell_data", [])
        for cell in cell_data:
            for b in cell.get("books", []):
                books.append(_normalize_book(b))
    except Exception as e:
        return {"error": f"Gagal parse bookmall: {e}", "raw": raw}, 500

    return {"books": books, "total": len(books)}, 200


def tabs(platform="melolo", gender="0", lang="en"):
    """Tab kategori di halaman bookmall."""
    return _get(platform, "/api/v1/bookmall/tabs", {"gender": gender, "lang": lang})


def categories(platform="melolo", gender="0", lang="en"):
    """Daftar kategori/genre."""
    return _get(platform, "/api/v1/categories", {"gender": gender, "lang": lang})


def search(q: str, platform="melolo", lang="en", limit=20, offset=0) -> tuple:
    """
    Pencarian buku.
    Params: q, lang, limit, offset
    Response shape belum dipastikan — dikembalikan apa adanya sambil mencoba
    normalisasi jika ditemukan list buku.
    """
    raw, code = _get(
        platform,
        "/api/v1/search",
        {"q": q, "lang": lang, "limit": limit, "offset": offset},
    )
    if code != 200 or "error" in raw:
        return raw, code

    # Coba normalisasi — beberapa kemungkinan letak list hasil pencarian
    candidates = (
        raw.get("books")
        or raw.get("data", {}).get("books")
        or raw.get("data", {}).get("list")
        or raw.get("list")
        or []
    )

    if candidates:
        return {
            "books": [_normalize_book(b) for b in candidates],
            "total": len(candidates),
            "_raw":  raw,
        }, 200

    # Jika struktur tidak dikenali, kembalikan raw agar bisa di-inspect
    return raw, code


def suggest(q: str, platform="melolo", lang="en"):
    """Saran kata kunci pencarian."""
    return _get(platform, "/api/v1/search/suggest", {"q": q, "lang": lang})


def book(book_id: str, platform="melolo", lang="en") -> tuple:
    """
    Detail satu buku.
    Response fields: book_id, title, cover, episode_count, categories[]
    """
    raw, code = _get(platform, "/api/v1/book", {"id": book_id, "lang": lang})
    if code != 200 or "error" in raw:
        return raw, code

    # API mungkin membungkus dalam key "book" atau "data"
    book_data = raw.get("book") or raw.get("data") or raw
    return _normalize_book(book_data), 200


def series(book_id: str, platform="melolo", lang="en") -> tuple:
    """
    Daftar episode suatu buku/series.
    Response shape: { episodes: [ { vid, index, need_unlock, ... }, ... ] }
    Catatan: tidak ada field url video — gunakan vid untuk request video terpisah.
    """
    raw, code = _get(platform, "/api/v1/series", {"id": book_id, "lang": lang})
    if code != 200 or "error" in raw:
        return raw, code

    try:
        raw_episodes = raw.get("episodes", [])
        episodes = [_normalize_episode(ep) for ep in raw_episodes]
    except Exception as e:
        return {"error": f"Gagal parse episodes: {e}", "raw": raw}, 500

    return {
        "book_id":  book_id,
        "episodes": episodes,
        "total":    len(episodes),
    }, 200


def videos(book_id: str, platform="melolo", lang="en"):
    """
    Multi-video endpoint (jika tersedia).
    Berbeda dengan series() — endpoint ini mungkin mengembalikan URL video langsung
    untuk beberapa episode sekaligus.
    """
    return _get(platform, "/api/v1/multi-video", {"id": book_id, "lang": lang})
