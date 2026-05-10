"""
Server-side Bayar.gg client.

Docs: https://www.bayar.gg/api-docs
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urljoin

import requests

from .config import (
    BAYAR_GG_API_KEY,
    BAYAR_GG_BASE_URL,
    BAYAR_GG_PAYMENT_METHOD,
    BAYAR_GG_WEBHOOK_SECRET,
)


CREATE_PAYMENT_FIELDS = {
    "amount",
    "description",
    "customer_name",
    "customer_email",
    "customer_phone",
    "callback_url",
    "redirect_url",
    "file_id",
    "content_id",
    "product_image_id",
    "payment_method",
    "use_qris_converter",
    "qris_string",
}

STATUS_VALUES = {"pending", "paid", "expired", "cancelled"}


class BayarGGError(Exception):
    def __init__(self, message: str, status_code: int = 400, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def is_configured() -> bool:
    return bool(BAYAR_GG_API_KEY)


def _endpoint(path: str) -> str:
    return urljoin(f"{BAYAR_GG_BASE_URL}/", path.lstrip("/"))


def _headers() -> dict:
    if not BAYAR_GG_API_KEY:
        raise BayarGGError("BAYAR_GG_API_KEY belum dikonfigurasi di server.", 500)
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-API-Key": BAYAR_GG_API_KEY,
    }


def _request(method: str, path: str, **kwargs) -> tuple[dict, int]:
    try:
        resp = requests.request(
            method,
            _endpoint(path),
            headers=_headers(),
            timeout=25,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise BayarGGError(f"Koneksi ke Bayar.gg gagal: {exc}", 502) from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise BayarGGError(
            f"Bayar.gg mengirim response non-JSON: HTTP {resp.status_code}",
            502,
            resp.text[:300],
        ) from exc

    if resp.status_code >= 400:
        message = data.get("message") or data.get("error") or "Request Bayar.gg gagal."
        raise BayarGGError(message, resp.status_code, data)
    if data.get("success") is False:
        message = data.get("message") or data.get("error") or "Request Bayar.gg gagal."
        raise BayarGGError(message, 400, data)

    return data, resp.status_code


def create_payment(payload: dict, base_url: str = "") -> tuple[dict, int]:
    body = {k: v for k, v in (payload or {}).items() if k in CREATE_PAYMENT_FIELDS and v not in (None, "")}

    try:
        amount = int(body.get("amount", 0))
    except (TypeError, ValueError) as exc:
        raise BayarGGError("Amount wajib berupa angka.", 400) from exc

    if amount < 1000:
        raise BayarGGError("Amount minimal Rp 1.000.", 400)

    body["amount"] = amount
    body.setdefault("payment_method", BAYAR_GG_PAYMENT_METHOD or "qris_bayar_gg")

    if base_url:
        body.setdefault("callback_url", f"{base_url.rstrip('/')}/api/payments/webhook")

    return _request("POST", "create-payment.php", json=body)


def check_payment(invoice: str) -> tuple[dict, int]:
    invoice = str(invoice or "").strip()
    if not invoice:
        raise BayarGGError("Parameter invoice wajib diisi.", 400)
    return _request("GET", "check-payment.php", params={"invoice": invoice})


def payment_methods() -> tuple[dict, int]:
    return _request("GET", "get-payment-methods.php")


def list_payments(params: dict) -> tuple[dict, int]:
    query = {}
    for key in ("search", "status", "payment_method", "paid_via", "start_date", "end_date", "page", "limit"):
        value = (params or {}).get(key)
        if value not in (None, ""):
            query[key] = value

    if query.get("status") and query["status"] not in STATUS_VALUES:
        raise BayarGGError("Status pembayaran tidak valid.", 400)

    return _request("GET", "list-payments.php", params=query)


def verify_webhook(payload: dict, headers) -> tuple[bool, str]:
    if not BAYAR_GG_WEBHOOK_SECRET:
        return False, "BAYAR_GG_WEBHOOK_SECRET belum dikonfigurasi."

    signature = (headers.get("X-Webhook-Signature") if headers else "") or ""
    timestamp = str((payload or {}).get("timestamp") or (headers.get("X-Webhook-Timestamp") if headers else "") or "")
    invoice_id = str((payload or {}).get("invoice_id") or "")
    status = str((payload or {}).get("status") or "")
    final_amount = str((payload or {}).get("final_amount") or "")

    if not signature or not timestamp or not invoice_id or not status or not final_amount:
        return False, "Payload webhook tidak lengkap."

    try:
        ts = int(timestamp)
    except ValueError:
        return False, "Timestamp webhook tidak valid."

    if abs(int(time.time()) - ts) > 60 * 10:
        return False, "Timestamp webhook sudah kedaluwarsa."

    signature_data = f"{invoice_id}|{status}|{final_amount}|{timestamp}"
    expected = hmac.new(
        BAYAR_GG_WEBHOOK_SECRET.encode(),
        signature_data.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return False, "Signature webhook tidak valid."

    return True, ""
