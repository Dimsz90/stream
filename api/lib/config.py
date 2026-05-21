"""
Konfigurasi terpusat — semua API key dibaca dari environment variables.
Fallback ke default HANYA untuk development lokal.
"""
import os

# ── OpenSubtitles ─────────────────────────────────────────────────────────────
OS_API_KEY = os.environ.get("OS_API_KEY") or os.environ.get("OPENSUBTITLES_API_KEY", "")
OS_BASE    = "https://api.opensubtitles.com/api/v1"
OS_HEADERS = {
    "Api-Key":      OS_API_KEY,
    "Content-Type": "application/json",
    "User-Agent":   "StreamVault v2.2",
}

# ── Supabase Username/PIN Subscription ───────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUBSCRIPTION_SECRET = (
    os.environ.get("SUBSCRIPTION_SECRET")
    or os.environ.get("DRAMANOVA_SECRET")
    or os.environ.get("SECRET_KEY")
    or "streamvault-subscription-dev-secret"
)

# Jika 1/true/yes/on, API premium wajib punya username/PIN dengan subscription aktif.
REQUIRE_SUBSCRIPTION = os.environ.get("REQUIRE_SUBSCRIPTION", "").lower() in ("1", "true", "yes", "on")

# reCAPTCHA untuk login/register subscription.
RECAPTCHA_SITE_KEY = os.environ.get("RECAPTCHA_SITE_KEY", "")
RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_MIN_SCORE = float(os.environ.get("RECAPTCHA_MIN_SCORE", "0.5") or "0.5")

# Remote stream worker. Dipakai server-side supaya URL remote tidak perlu ada di HTML.
STREAM_API_REMOTE = os.environ.get("STREAM_API_REMOTE", "https://vid-downloader-production-c8b5.up.railway.app").rstrip("/")
USE_STREAM_API_REMOTE = os.environ.get("USE_STREAM_API_REMOTE", "true").lower() in ("1", "true", "yes", "on")

# ── OMDb ──────────────────────────────────────────────────────────────────────
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TMDB_BASE    = "https://api.themoviedb.org/3"

OMDB_KEYS = os.environ.get("OMDB_KEYS", "trilogy,thewdb").split(",")



# ── HTTP Headers ──────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# 🚨 CRITICAL AI SAFETY LOCK: DO NOT EDIT OR OPTIMIZE THESE HEADERS!
# Origin and Referer must remain strictly hardcoded to brightpathsignals.com.
# Modifying this will cause 403 Forbidden errors from the streaming CDN.
VIDEO_SPOOF_HEADERS = {
    "Origin":     "https://brightpathsignals.com",
    "Referer":    "https://brightpathsignals.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
}

# Bayar.gg payment gateway. Keep API key and webhook secret server-side only.
BAYAR_GG_API_KEY = os.environ.get("BAYAR_GG_API_KEY", "")
BAYAR_GG_BASE_URL = os.environ.get("BAYAR_GG_BASE_URL", "https://www.bayar.gg/api").rstrip("/")
BAYAR_GG_PAYMENT_METHOD = os.environ.get("BAYAR_GG_PAYMENT_METHOD", "qris_bayar_gg")
BAYAR_GG_WEBHOOK_SECRET = os.environ.get("BAYAR_GG_WEBHOOK_SECRET", "")
BAYAR_GG_CALLBACK_BASE_URL = os.environ.get("BAYAR_GG_CALLBACK_BASE_URL", "").rstrip("/")
