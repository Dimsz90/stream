"""
Simple in-memory TTL cache.
Thread-safe, cocok untuk Vercel / dev server.
"""
import time
import threading


class TTLCache:
    """Key-value store dengan waktu kedaluwarsa per-entry."""

    def __init__(self, default_ttl: int = 600, max_size: int = 500):
        self._store: dict = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl
        self._max_size = max_size

    def get(self, key: str):
        """Return value jika ada dan belum expired, else None."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.time() > expiry:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value, ttl: int = None):
        """Simpan value dengan TTL (detik)."""
        with self._lock:
            # Evict expired jika hampir penuh
            if len(self._store) >= self._max_size:
                self._evict()
            self._store[key] = (value, time.time() + (ttl or self._default_ttl))

    def _evict(self):
        """Hapus semua entry expired, lalu yang paling tua jika masih penuh."""
        now = time.time()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
        # Kalau masih penuh, hapus setengah (LRU sederhana)
        if len(self._store) >= self._max_size:
            keys_to_remove = list(self._store.keys())[: self._max_size // 2]
            for k in keys_to_remove:
                del self._store[k]

    def clear(self):
        with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


# ── Singleton instances ───────────────────────────────────────────────────────
imdb_cache = TTLCache(default_ttl=3600, max_size=200)      # 1 jam
subtitle_cache = TTLCache(default_ttl=1800, max_size=100)  # 30 menit
tmdb_cache = TTLCache(default_ttl=3600, max_size=200)      # 1 jam
