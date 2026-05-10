"""
Supabase-backed account and subscription gate.

Expected table: public.subscriptions
- username text unique
- pin text (stores legacy PIN or hashed password)
- status text, active/trialing grant access
- valid_until timestamptz nullable
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from .config import (
    BAYAR_GG_CALLBACK_BASE_URL,
    REQUIRE_SUBSCRIPTION,
    SUBSCRIPTION_SECRET,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)


ACTIVE_STATUSES = {"active", "trialing", "paid"}
SESSION_TTL = 60 * 60 * 24
DEFAULT_SUBSCRIPTION_SECRET = "streamvault-subscription-dev-secret"
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERS = 180000
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
SUBSCRIPTION_PLANS = {
    "weekly": {"id": "weekly", "label": "Mingguan", "amount": 7000, "days": 7},
    "monthly": {"id": "monthly", "label": "Bulanan", "amount": 20000, "days": 30},
}


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


def _account_error(message, code=400, extra=None):
    body = {"status": "error", "message": message}
    if extra:
        body.update(extra)
    return body, code


def _supabase_headers(prefer: str = "") -> dict:
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _ensure_configured() -> tuple[bool, dict | None, int]:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        payload, status_code = _account_error("Subscription belum dikonfigurasi di server.", 500)
        return False, payload, status_code
    if SUBSCRIPTION_SECRET == DEFAULT_SUBSCRIPTION_SECRET:
        payload, status_code = _account_error("SUBSCRIPTION_SECRET wajib diset saat subscription aktif.", 500)
        return False, payload, status_code
    return True, None, 200


def subscription_enabled() -> bool:
    return REQUIRE_SUBSCRIPTION


def check_subscription(headers) -> tuple[bool, dict, int]:
    if not REQUIRE_SUBSCRIPTION:
        return True, {"status": "success", "subscription_required": False}, 200

    ok, payload, status_code = _ensure_configured()
    if not ok:
        payload["subscription_required"] = True
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

    ok, payload, status_code = _ensure_configured()
    if not ok:
        return payload, status_code

    username = normalize_username(username)
    if not username or not pin:
        payload, status_code = _account_error("Username dan password wajib diisi.", 400)
        return payload, status_code

    sub, err = get_subscription(username)
    if err:
        payload, status_code = _account_error(err, 401)
        return payload, status_code

    stored_pin = str(sub.get("pin") or "")
    if not verify_password(str(pin), stored_pin):
        payload, status_code = _account_error("Username atau password salah.", 401)
        return payload, status_code

    token = make_session_token(username)
    return {
        "status": "success",
        "token": token,
        "subscriber": {"username": username},
        "subscription": public_subscription(sub),
    }, 200


def register_subscriber(username: str, password: str) -> tuple[dict, int]:
    if not REQUIRE_SUBSCRIPTION:
        return {"status": "success", "subscription_required": False}, 200

    ok, payload, status_code = _ensure_configured()
    if not ok:
        return payload, status_code

    username = normalize_username(username)
    if not USERNAME_RE.match(username):
        return _account_error("Username 3-32 karakter, hanya huruf kecil, angka, titik, underscore, atau minus.", 400)
    if len(str(password or "")) < 6:
        return _account_error("Password minimal 6 karakter.", 400)

    body = {
        "username": username,
        "pin": hash_password(password),
        "status": "inactive",
        "plan": "free",
        "valid_until": None,
    }
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/subscriptions",
            headers=_supabase_headers("return=representation"),
            json=body,
            timeout=8,
        )
    except Exception as exc:
        return _account_error(f"Gagal membuat akun: {exc}", 500)

    if resp.status_code == 409:
        return _account_error("Username sudah terdaftar.", 409)
    if resp.status_code not in (200, 201):
        detail = resp.text[:300] if getattr(resp, "text", "") else ""
        return _account_error(f"Gagal membuat akun: HTTP {resp.status_code} {detail}".strip(), 500)

    row = (resp.json() or [{}])[0]
    token = make_session_token(username)
    return {
        "status": "success",
        "token": token,
        "subscriber": {"username": username},
        "subscription": public_subscription(row),
    }, 201


def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_urlsafe(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode(), salt.encode(), PASSWORD_ITERS).hex()
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith(f"{PASSWORD_SCHEME}$"):
        try:
            _, iters, salt, expected = stored.split("$", 3)
            digest = hashlib.pbkdf2_hmac("sha256", str(password).encode(), salt.encode(), int(iters)).hex()
            return hmac.compare_digest(digest, expected)
        except Exception:
            return False
    return hmac.compare_digest(str(password), stored)


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


def get_session_account(headers) -> tuple[dict | None, dict, int]:
    ok, payload, status_code = _ensure_configured()
    if not ok:
        return None, payload, status_code

    token = (headers.get("X-Subscription-Token") if headers else "") or ""
    username = validate_session_token(token)
    if not username:
        payload, status_code = _account_error("Login diperlukan.", 401)
        return None, payload, status_code

    sub, err = get_subscription(username)
    if err:
        payload, status_code = _account_error(err, 401)
        return None, payload, status_code
    return sub, {}, 200


def me(headers) -> tuple[dict, int]:
    if not REQUIRE_SUBSCRIPTION:
        return {"status": "success", "subscription_required": False}, 200
    sub, payload, status_code = get_session_account(headers)
    if not sub:
        return payload, status_code
    return {
        "status": "success",
        "subscriber": {"username": sub.get("username")},
        "subscription": public_subscription(sub),
    }, 200


def get_subscription(username: str) -> tuple[dict | None, str | None]:
    username = normalize_username(username)
    query = (
        f"username=eq.{quote(username)}"
        "&select=id,username,pin,status,plan,valid_until,created_at"
        "&limit=1"
    )
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/subscriptions?{query}",
            headers=_supabase_headers(),
            timeout=8,
        )
    except Exception as exc:
        return None, f"Gagal mengecek akun: {exc}"

    if resp.status_code != 200:
        detail = resp.text[:300] if getattr(resp, "text", "") else ""
        return None, f"Gagal membaca akun: HTTP {resp.status_code} {detail}".strip()

    rows = resp.json()
    if not rows:
        return None, "Akun tidak ditemukan."
    return rows[0], None


def get_active_subscription(username: str) -> tuple[dict | None, str | None]:
    row, err = get_subscription(username)
    if err:
        return None, err
    if str(row.get("status", "")).lower() not in ACTIVE_STATUSES:
        return None, "Langganan belum aktif."

    ends_at = _parse_dt(row.get("valid_until"))
    if ends_at and ends_at < datetime.now(timezone.utc):
        return None, "Langganan sudah kedaluwarsa."

    return row, None


def activate_subscription(username: str, plan_id: str) -> tuple[dict, int]:
    plan = SUBSCRIPTION_PLANS.get(plan_id)
    if not plan:
        return _account_error("Paket langganan tidak valid.", 400)

    sub, err = get_subscription(username)
    if err:
        return _account_error(err, 404)

    now = datetime.now(timezone.utc)
    current_until = _parse_dt(sub.get("valid_until"))
    start = current_until if current_until and current_until > now else now
    valid_until = start + timedelta(days=plan["days"])
    body = {
        "status": "paid",
        "plan": plan_id,
        "valid_until": valid_until.isoformat(),
    }

    try:
        resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/subscriptions?username=eq.{quote(normalize_username(username))}",
            headers=_supabase_headers("return=representation"),
            json=body,
            timeout=8,
        )
    except Exception as exc:
        return _account_error(f"Gagal mengaktifkan langganan: {exc}", 500)

    if resp.status_code not in (200, 204):
        detail = resp.text[:300] if getattr(resp, "text", "") else ""
        return _account_error(f"Gagal update langganan: HTTP {resp.status_code} {detail}".strip(), 500)

    rows = resp.json() if resp.text else []
    row = rows[0] if rows else {**sub, **body}
    return {
        "status": "success",
        "subscriber": {"username": row.get("username") or username},
        "subscription": public_subscription(row),
    }, 200


def _make_payment_token(username: str, plan_id: str, amount: int, issued: int | None = None, nonce: str = "") -> str:
    issued = int(issued or time.time())
    nonce = nonce or secrets.token_urlsafe(12)
    msg = f"pay:{normalize_username(username)}:{plan_id}:{int(amount)}:{issued}:{nonce}"
    sig = hmac.new(SUBSCRIPTION_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}:{sig}"


def _read_payment_token(token: str) -> tuple[dict | None, str]:
    try:
        parts = str(token or "").split(":")
        if len(parts) != 7 or parts[0] != "pay":
            return None, "Token pembayaran tidak valid."
        _, username, plan_id, amount, issued, nonce, sig = parts
        issued_int = int(issued)
        if time.time() - issued_int > 60 * 60 * 6:
            return None, "Token pembayaran kedaluwarsa."
        msg = f"pay:{username}:{plan_id}:{int(amount)}:{issued_int}:{nonce}"
        expected = hmac.new(SUBSCRIPTION_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None, "Token pembayaran tidak valid."
        return {
            "username": normalize_username(username),
            "plan": plan_id,
            "amount": int(amount),
            "issued": issued_int,
            "nonce": nonce,
        }, ""
    except Exception:
        return None, "Token pembayaran tidak valid."


def create_subscription_payment(headers, plan_id: str, base_url: str = "", redirect_url: str = "") -> tuple[dict, int]:
    sub, payload, status_code = get_session_account(headers)
    if not sub:
        return payload, status_code

    plan = SUBSCRIPTION_PLANS.get(plan_id)
    if not plan:
        return _account_error("Paket langganan tidak valid.", 400)

    from .bayar_gg import create_payment

    username = normalize_username(sub.get("username"))
    payment_token = _make_payment_token(username, plan_id, plan["amount"])
    description = f"StreamVault subscription {plan_id} SVSUB:{payment_token}"
    callback_base = BAYAR_GG_CALLBACK_BASE_URL or base_url
    payment_payload = {
        "amount": plan["amount"],
        "description": description,
        "customer_name": username,
        "callback_url": f"{callback_base.rstrip('/')}/api/payments/webhook" if callback_base else "",
        "redirect_url": redirect_url or base_url or "",
    }
    try:
        data, code = create_payment(payment_payload, base_url)
    except Exception as exc:
        return _account_error(
            str(exc),
            getattr(exc, "status_code", 500),
            {"detail": getattr(exc, "detail", None)} if getattr(exc, "detail", None) else None,
        )
    payment = data.get("data") if isinstance(data.get("data"), dict) else data.get("payment", {})
    if not payment:
        payment = data
    return {
        "status": "success",
        "plan": plan,
        "payment_token": payment_token,
        "payment": payment,
        "raw": data,
    }, code


def verify_subscription_payment(headers, invoice: str, payment_token: str) -> tuple[dict, int]:
    sub, payload, status_code = get_session_account(headers)
    if not sub:
        return payload, status_code

    token_data, err = _read_payment_token(payment_token)
    if err:
        return _account_error(err, 400)
    if token_data["username"] != normalize_username(sub.get("username")):
        return _account_error("Invoice ini tidak cocok dengan akun login.", 403)

    plan = SUBSCRIPTION_PLANS.get(token_data["plan"])
    if not plan or int(plan["amount"]) != int(token_data["amount"]):
        return _account_error("Data paket pembayaran tidak valid.", 400)

    from .bayar_gg import check_payment

    try:
        data, _ = check_payment(invoice)
    except Exception as exc:
        return _account_error(
            str(exc),
            getattr(exc, "status_code", 500),
            {"detail": getattr(exc, "detail", None)} if getattr(exc, "detail", None) else None,
        )
    status = str(data.get("status") or data.get("data", {}).get("status") or "").lower()
    amount = int(
        data.get("amount")
        or data.get("final_amount")
        or data.get("data", {}).get("amount")
        or data.get("data", {}).get("final_amount")
        or 0
    )
    if amount and amount != int(plan["amount"]):
        return _account_error("Nominal invoice tidak cocok dengan paket.", 400, {"payment": data})

    if status != "paid":
        return {
            "status": "pending",
            "payment_status": status or "pending",
            "payment": data,
        }, 200

    activated, code = activate_subscription(token_data["username"], token_data["plan"])
    if activated.get("status") == "success":
        activated["payment"] = data
    return activated, code


def handle_subscription_webhook(payload: dict) -> tuple[dict, int]:
    description = str((payload or {}).get("description") or "")
    marker = "SVSUB:"
    if marker not in description:
        return {"status": "ignored", "message": "Bukan pembayaran subscription StreamVault."}, 200

    payment_token = description.split(marker, 1)[1].split()[0].strip()
    token_data, err = _read_payment_token(payment_token)
    if err:
        return _account_error(err, 400)

    plan = SUBSCRIPTION_PLANS.get(token_data["plan"])
    if not plan:
        return _account_error("Paket langganan tidak valid.", 400)

    status = str((payload or {}).get("status") or "").lower()
    amount = int((payload or {}).get("amount") or (payload or {}).get("final_amount") or 0)
    if amount and amount != int(plan["amount"]):
        return _account_error("Nominal webhook tidak cocok dengan paket.", 400)
    if status != "paid":
        return {"status": "pending", "payment_status": status or "pending"}, 200

    return activate_subscription(token_data["username"], token_data["plan"])


def public_subscription(row: dict) -> dict:
    active = False
    ends_at = _parse_dt(row.get("valid_until"))
    if str(row.get("status", "")).lower() in ACTIVE_STATUSES and (not ends_at or ends_at >= datetime.now(timezone.utc)):
        active = True
    return {
        "status": row.get("status"),
        "plan": row.get("plan"),
        "valid_until": row.get("valid_until"),
        "active": active,
    }
