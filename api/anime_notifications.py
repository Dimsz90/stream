"""Bookmark synchronization and Telegram notifications for anime episodes."""
from __future__ import annotations

import os
import re
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import requests

try:
    from api.anime import SamehadakuScraper
    from api.lib.subscription import validate_session_token
    from api.lib.config import REQUIRE_SUBSCRIPTION, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
except ImportError:
    from anime import SamehadakuScraper
    from lib.subscription import validate_session_token
    from lib.config import REQUIRE_SUBSCRIPTION, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ANIME_CRON_SECRET = os.environ.get("ANIME_CRON_SECRET", "").strip()
ANIME_BOOKMARK_USERNAME = os.environ.get("ANIME_BOOKMARK_USERNAME", "default").strip().lower() or "default"
TELEGRAM_API = "https://api.telegram.org/bot{}/{}"


def _headers(prefer=""):
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _configured():
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _supabase(method, table, *, params=None, payload=None, prefer=""):
    if not _configured():
        raise RuntimeError("Supabase belum dikonfigurasi")
    response = requests.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_headers(prefer),
        params=params,
        json=payload,
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase {table}: HTTP {response.status_code} {response.text[:300]}")
    if not response.text:
        return []
    try:
        return response.json()
    except ValueError:
        return []


def _eq(value):
    # requests encodes query values itself. Pre-quoting here would turn `%2F`
    # into `%252F` and make URL filters fail in PostgREST.
    return f"eq.{str(value or '')}"


def _username_from_headers(headers):
    token = (headers.get("X-Subscription-Token") if headers else "") or ""
    username = validate_session_token(token)
    return username


def _account_or_error(headers):
    username = _username_from_headers(headers)
    if not username and not REQUIRE_SUBSCRIPTION:
        username = ANIME_BOOKMARK_USERNAME
    if not username:
        return None, {"status": "error", "message": "Login diperlukan.", "subscription_required": True}, 401
    return username, None, 200


def _normalize_bookmark(item):
    if not isinstance(item, dict):
        return None
    title = str(item.get("anime_title") or item.get("title") or "Anime").strip()[:300]
    url = str(item.get("anime_url") or item.get("link") or "").strip()
    parsed = urlparse(url)
    if not url or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return {
        "anime_title": title,
        "anime_url": url,
        "thumbnail": str(item.get("thumbnail") or "").strip()[:1000],
        "notifications_enabled": bool(item.get("notifications_enabled", True)),
    }


def list_bookmarks(username):
    return _supabase(
        "GET",
        "anime_bookmarks",
        params={"username": _eq(username), "select": "*", "order": "created_at.desc"},
    )


def replace_bookmarks(username, bookmarks):
    normalized = [x for x in (_normalize_bookmark(item) for item in (bookmarks or [])) if x]
    existing = list_bookmarks(username)
    existing_by_url = {row.get("anime_url"): row for row in existing}
    wanted = {item["anime_url"]: item for item in normalized}

    for row in existing:
        if row.get("anime_url") not in wanted:
            _supabase("DELETE", "anime_bookmarks", params={"username": _eq(username), "anime_url": _eq(row.get("anime_url"))})

    scraper = SamehadakuScraper()
    rows = []
    for item in normalized:
        row = {"username": username, **item}
        previous = existing_by_url.get(item["anime_url"])
        if previous:
            row["last_episode_number"] = previous.get("last_episode_number")
            row["last_episode_url"] = previous.get("last_episode_url")
        else:
            try:
                latest = (scraper.get_episodes(item["anime_url"]) or [None])[0]
                if latest:
                    row["last_episode_number"] = _episode_number(latest)
                    row["last_episode_url"] = latest.get("link")
            except Exception:
                pass
        rows.append(row)
    if rows:
        _supabase("POST", "anime_bookmarks", payload=rows, prefer="resolution=merge-duplicates")
    return list_bookmarks(username)


def save_bookmark(username, item):
    normalized = _normalize_bookmark(item)
    if not normalized:
        return None
    row = {"username": username, **normalized}
    try:
        latest = (SamehadakuScraper().get_episodes(normalized["anime_url"]) or [None])[0]
        if latest:
            row["last_episode_number"] = _episode_number(latest)
            row["last_episode_url"] = latest.get("link")
    except Exception:
        pass
    result = _supabase("POST", "anime_bookmarks", payload=row, prefer="resolution=merge-duplicates,return=representation")
    return result[0] if isinstance(result, list) and result else row


def delete_bookmark(username, anime_url):
    _supabase("DELETE", "anime_bookmarks", params={"username": _eq(username), "anime_url": _eq(anime_url)})
    return True


def telegram_connection(username):
    rows = _supabase("GET", "telegram_connections", params={"username": _eq(username), "select": "*", "limit": "1"})
    return rows[0] if rows else None


def send_telegram(chat_id, text):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN belum dikonfigurasi")
    response = requests.post(
        TELEGRAM_API.format(TELEGRAM_BOT_TOKEN, "sendMessage"),
        json={"chat_id": str(chat_id), "text": text, "disable_web_page_preview": False},
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram menolak pesan: {data}")
    return data


def _episode_number(episode):
    value = str(episode.get("number") or episode.get("title") or "").strip()
    match = re.search(r"(?:episode|eps|ep)\s*([0-9]+(?:\.[0-9]+)?)", value, re.I) or re.search(r"([0-9]+(?:\.[0-9]+)?)", value)
    return match.group(1) if match else value


def _notification_text(bookmark, episode):
    title = bookmark.get("anime_title") or bookmark.get("title") or "Anime"
    number = _episode_number(episode)
    url = episode.get("link") or bookmark.get("anime_url")
    return f"🎬 Episode baru tersedia\n\n{title}\nEpisode {number}\n\nTonton: {url}"


def check_for_new_episodes():
    """Check all enabled bookmarks and send each unseen episode once."""
    scraper = SamehadakuScraper()
    bookmarks = _supabase("GET", "anime_bookmarks", params={"notifications_enabled": "eq.true", "select": "*"})
    sent = 0
    checked = 0
    errors = []
    for bookmark in bookmarks:
        checked += 1
        username = bookmark.get("username") or ""
        try:
            connection = telegram_connection(username)
            chat_id = connection.get("telegram_chat_id") if connection and connection.get("enabled", True) else None
            if not chat_id:
                continue
            episodes = scraper.get_episodes(bookmark.get("anime_url")) or []
            if not episodes:
                continue
            latest = episodes[0]
            last_episode_url = str(bookmark.get("last_episode_url") or "").strip()
            if not last_episode_url:
                # First observation becomes the baseline. Historical episodes
                # are never sent as new notifications.
                _supabase(
                    "PATCH",
                    "anime_bookmarks",
                    params={"id": _eq(bookmark.get("id"))},
                    payload={"last_episode_number": _episode_number(latest), "last_episode_url": latest.get("link")},
                )
                continue

            new_episodes = []
            for episode in episodes:
                if episode.get("link") == last_episode_url:
                    break
                new_episodes.append(episode)

            # If the old URL disappeared due to source-site changes, avoid
            # flooding the user and simply reset the baseline.
            if new_episodes and len(new_episodes) == len(episodes):
                _supabase(
                    "PATCH",
                    "anime_bookmarks",
                    params={"id": _eq(bookmark.get("id"))},
                    payload={"last_episode_number": _episode_number(latest), "last_episode_url": latest.get("link")},
                )
                continue

            for episode in reversed(new_episodes):
                episode_url = str(episode.get("link") or "").strip()
                if not episode_url:
                    continue
                known = _supabase("GET", "anime_notifications", params={"username": _eq(username), "episode_url": _eq(episode_url), "select": "id", "limit": "1"})
                if known:
                    continue
                send_telegram(chat_id, _notification_text(bookmark, episode))
                _supabase(
                    "POST",
                    "anime_notifications",
                    payload={"username": username, "anime_url": bookmark.get("anime_url"), "episode_url": episode_url, "episode_number": _episode_number(episode)},
                    prefer="resolution=ignore-duplicates",
                )
                sent += 1
            if new_episodes:
                _supabase(
                    "PATCH",
                    "anime_bookmarks",
                    params={"id": _eq(bookmark.get("id"))},
                    payload={"last_episode_number": _episode_number(latest), "last_episode_url": latest.get("link")},
                )
        except Exception as exc:
            errors.append({"username": username, "anime_url": bookmark.get("anime_url"), "error": str(exc)[:300]})
    return {"status": "success", "checked": checked, "sent": sent, "errors": errors}


def build_response(path, headers, body=None):
    if path.endswith("/bookmarks"):
        username, error, code = _account_or_error(headers)
        if error:
            return error, code
        if body and body.get("bookmarks") is not None:
            return {"status": "success", "data": replace_bookmarks(username, body.get("bookmarks"))}, 200
        return {"status": "success", "data": list_bookmarks(username)}, 200
    if path.endswith("/telegram/test"):
        username, error, code = _account_or_error(headers)
        if error:
            return error, code
        connection = telegram_connection(username)
        if not connection or not connection.get("telegram_chat_id"):
            return {"status": "error", "message": "Telegram belum terhubung."}, 400
        send_telegram(connection["telegram_chat_id"], "✅ Notifikasi Telegram StreamVault berhasil dihubungkan.")
        return {"status": "success", "message": "Pesan Telegram terkirim."}, 200
    return {"status": "error", "message": f"Route tidak dikenal: {path}"}, 404


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.endswith("/cron/check"):
            supplied = self.headers.get("X-Cron-Secret") or ""
            if not ANIME_CRON_SECRET or supplied != ANIME_CRON_SECRET:
                return self._send_json({"status": "error", "message": "Cron secret tidak valid"}, 403)
            try:
                return self._send_json(check_for_new_episodes(), 200)
            except Exception as exc:
                return self._send_json({"status": "error", "message": str(exc)}, 500)
        data, code = build_response(parsed.path, self.headers, {})
        return self._send_json(data, code)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.endswith("/cron/check"):
            return self.do_GET()
        content_length = int(self.headers.get("Content-Length", 0) or 0)
        body = {}
        if content_length:
            try:
                body = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (TypeError, ValueError, json.JSONDecodeError):
                body = {}
        data, code = build_response(parsed.path, self.headers, body)
        return self._send_json(data, code)

    def _send_json(self, data, code=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass
