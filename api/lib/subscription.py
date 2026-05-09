"""
Supabase-backed username/PIN subscription gate.

Expected table: public.subscriptions
- username text unique
- pin text
- status text, active/trialing grant access
- valid_until timestamptz nullable
"""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from .config import (
    REQUIRE_SUBSCRIPTION,
    SUBSCRIPTION_SECRET,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)


ACTIVE_STATUSES = {"active", "trialing", "paid"}
SESSION_TTL = 60 * 60 * 24
DEFAULT_SUBSCRIPTION_SECRET = "streamvault-subscription-dev-secret"


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_error(message, code=403, extra=None):
    body = {
        "status": "error",
        "message": message,
        "subscription_required": True,
    }
    if extra:
        body.update(extra)
    return body, code


def subscription_enabled() -> bool:
    return REQUIRE_SUBSCRIPTION


def check_subscription(headers) -> tuple[bool, dict, int]:
    if not REQUIRE_SUBSCRIPTION:
        return True, {"status": "success", "subscription_required": False}, 200

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        payload, status_code = _json_error("Subscription belum dikonfigurasi di server.", 500)
        return False, payload, status_code
    if SUBSCRIPTION_SECRET == DEFAULT_SUBSCRIPTION_SECRET:
        payload, status_code = _json_error("SUBSCRIPTION_SECRET wajib diset saat subscription aktif.", 500)
        return False, payload, status_code

    token = (headers.get("X-Subscription-Token") if headers else "") or ""
    username = validate_session_token(token)
    if not username:
        payload, status_code = _json_error("Login langganan diperlukan.", 401)
        return False, payload, status_code

    sub, err = get_active_subscription(username)
    if err:
        payload, status_code = _json_error(err, 403, {"subscriber": {"username": username}})
        return False, payload, status_code

    return True, {
        "status": "success",
        "subscriber": {"username": username},
        "subscription": public_subscription(sub),
    }, 200


def login_subscriber(username: str, pin: str) -> tuple[dict, int]:
    if not REQUIRE_SUBSCRIPTION:
        return {"status": "success", "subscription_required": False}, 200

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        payload, status_code = _json_error("Subscription belum dikonfigurasi di server.", 500)
        return payload, status_code
    if SUBSCRIPTION_SECRET == DEFAULT_SUBSCRIPTION_SECRET:
        payload, status_code = _json_error("SUBSCRIPTION_SECRET wajib diset saat subscription aktif.", 500)
        return payload, status_code

    username = normalize_username(username)
    if not username or not pin:
        payload, status_code = _json_error("Username dan PIN wajib diisi.", 400)
        return payload, status_code

    sub, err = get_active_subscription(username)
    if err:
        payload, status_code = _json_error(err, 403)
        return payload, status_code

    stored_pin = str(sub.get("pin") or "")
    if not stored_pin or not hmac.compare_digest(stored_pin, str(pin)):
        payload, status_code = _json_error("Username atau PIN salah.", 401)
        return payload, status_code

    token = make_session_token(username)
    return {
        "status": "success",
        "token": token,
        "subscriber": {"username": username},
        "subscription": public_subscription(sub),
    }, 200


def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def make_session_token(username: str, ttl=SESSION_TTL) -> str:
    username = normalize_username(username)
    exp = int(time.time()) + int(ttl)
    msg = f"sub:{username}:{exp}"
    sig = hmac.new(SUBSCRIPTION_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}:{sig}"


def validate_session_token(token: str) -> str:
    try:
        parts = str(token or "").split(":")
        if len(parts) != 4 or parts[0] != "sub":
            return ""
        username = normalize_username(parts[1])
        exp = int(parts[2])
        sig = parts[3]
        if time.time() > exp:
            return ""
        msg = f"sub:{username}:{exp}"
        expected = hmac.new(SUBSCRIPTION_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return ""
        return username
    except Exception:
        return ""


def get_active_subscription(username: str) -> tuple[dict | None, str | None]:
    username = normalize_username(username)
    query = (
        f"username=eq.{quote(username)}"
        "&select=id,username,pin,status,plan,valid_until,created_at"
        "&limit=1"
    )
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/subscriptions?{query}",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            },
            timeout=8,
        )
    except Exception as exc:
        return None, f"Gagal mengecek langganan: {exc}"

    if resp.status_code != 200:
        detail = resp.text[:300] if getattr(resp, "text", "") else ""
        return None, f"Gagal membaca status langganan: HTTP {resp.status_code} {detail}".strip()

    rows = resp.json()
    if not rows:
        return None, "Langganan tidak ditemukan."

    row = rows[0]
    if str(row.get("status", "")).lower() not in ACTIVE_STATUSES:
        return None, "Langganan belum aktif."

    ends_at = _parse_dt(row.get("valid_until"))
    if ends_at and ends_at < datetime.now(timezone.utc):
        return None, "Langganan sudah kedaluwarsa."

    return row, None


def public_subscription(row: dict) -> dict:
    return {
        "status": row.get("status"),
        "plan": row.get("plan"),
        "valid_until": row.get("valid_until"),
    }
