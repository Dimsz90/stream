"""
Konfigurasi terpusat — semua API key dibaca dari environment variables.
Fallback ke default HANYA untuk development lokal.
"""
import os

# ── OpenSubtitles ─────────────────────────────────────────────────────────────
OS_API_KEY = os.environ.get("OPENSUBTITLES_KEY") or os.environ.get("OPENSUBTITLES_API_KEY", "ckRDaoR34bmwcz8Q6i95pfpVN9nMp9nN")
OS_BASE    = "https://api.opensubtitles.com/api/v1"
OS_HEADERS = {
    "Api-Key":      OS_API_KEY,
    "Content-Type": "application/json",
    "User-Agent":   "StreamVault v2.2",
}

# ── OMDb ──────────────────────────────────────────────────────────────────────
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "b354b15ec55ecfd9b1d511617d1e0688")
TMDB_BASE    = "https://api.themoviedb.org/3"

OMDB_KEYS = os.environ.get("OMDB_KEYS", "trilogy,thewdb").split(",")



# ── HTTP Headers ──────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

VIDEO_SPOOF_HEADERS = {
    "Origin":     "https://brightpathsignals.com",
    "Referer":    "https://brightpathsignals.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
}
